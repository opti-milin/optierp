"""Tax filing persistence — generate ITR JSON, hash, acknowledge, chain returns."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models import statutory as stat
from app.models.base import DOCSTATUS_SUBMITTED
from app.models.tax_computation import TaxComputation, TaxComputationResult, TaxComputationRun
from app.models.tax_filings import TaxFiling
from app.schemas.taxation import (
    TaxComputationAdjustmentLineIn,
    TaxComputationCreate,
    TaxComputationIncomeLineIn,
    TaxFilingAckIn,
    TaxFilingChainIn,
    TaxFilingGenerateIn,
    TaxFilingOut,
)
from app.services.taxation.catalogue import get_current_finance_act
from app.services.taxation.computations import create_computation, get_computation
from app.services.taxation.forms.generator import FieldMapEntry, payload_sha256
from app.services.taxation.forms.itr6 import (
    NON_ORIGINAL_TYPES,
    build_canonical_values,
    generate_itr6_payload,
)
from app.services.taxation.registration import get_or_bootstrap_registration


def filing_out(row: TaxFiling, *, include_payload: bool = True) -> TaxFilingOut:
    return TaxFilingOut(
        id=row.id,
        name=row.name,
        computation_id=row.computation_id,
        run_id=row.run_id,
        ay_code=row.ay_code,
        form_code=row.form_code,
        schema_version=row.schema_version,
        filing_type=row.filing_type,
        revises_filing_id=row.revises_filing_id,
        payload=row.payload if include_payload else {},
        payload_sha256=row.payload_sha256,
        canonical_snapshot=row.canonical_snapshot if include_payload else {},
        status=row.status,
        ack_no=row.ack_no,
        filed_on=row.filed_on,
        verification_mode=row.verification_mode,
        provider=row.provider,
        provider_response=row.provider_response if include_payload else {},
        remarks=row.remarks,
        docstatus=row.docstatus,
        creation=row.creation,
        modified=row.modified,
    )


async def list_filings(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    ay_code: str | None = None,
    computation_id: uuid.UUID | None = None,
) -> list[TaxFiling]:
    stmt = (
        select(TaxFiling)
        .where(TaxFiling.company_id == company_id)
        .order_by(TaxFiling.creation.desc())
    )
    if ay_code:
        stmt = stmt.where(TaxFiling.ay_code == ay_code)
    if computation_id:
        stmt = stmt.where(TaxFiling.computation_id == computation_id)
    result = await db.scalars(stmt)
    return list(result.all())


async def get_filing(
    db: AsyncSession, company_id: uuid.UUID, filing_id: uuid.UUID
) -> TaxFiling:
    row = await db.scalar(
        select(TaxFiling).where(
            TaxFiling.id == filing_id,
            TaxFiling.company_id == company_id,
        )
    )
    if row is None:
        raise NotFoundError("Tax filing not found")
    return row


async def _load_itr_form(
    db: AsyncSession,
    *,
    ay_code: str,
    form_code: str,
) -> stat.ItrForm:
    fav = await get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        raise ValidationError(
            f"No Finance Act version for AY {ay_code}",
            code="missing_finance_act",
            field="ay_code",
        )
    form = await db.scalar(
        select(stat.ItrForm)
        .where(
            stat.ItrForm.finance_act_version_id == fav.id,
            stat.ItrForm.form_code == form_code,
        )
        .options(selectinload(stat.ItrForm.field_maps))
    )
    if form is None:
        raise ValidationError(
            f"ITR form '{form_code}' not in statutory catalogue for AY {ay_code}",
            code="unknown_itr_form",
            field="form_code",
        )
    return form


async def _latest_run_result(
    db: AsyncSession, computation: TaxComputation
) -> tuple[TaxComputationRun | None, TaxComputationResult | None]:
    run_id = computation.current_run_id
    if run_id is None:
        # Fall back to highest run_no
        run = await db.scalar(
            select(TaxComputationRun)
            .where(
                TaxComputationRun.computation_id == computation.id,
                TaxComputationRun.company_id == computation.company_id,
            )
            .order_by(TaxComputationRun.run_no.desc())
            .limit(1)
            .options(selectinload(TaxComputationRun.result))
        )
        if run is None:
            return None, None
        return run, run.result

    run = await db.scalar(
        select(TaxComputationRun)
        .where(
            TaxComputationRun.id == run_id,
            TaxComputationRun.company_id == computation.company_id,
        )
        .options(selectinload(TaxComputationRun.result))
    )
    if run is None:
        return None, None
    return run, run.result


async def _prior_filing_for_chain(
    db: AsyncSession, computation: TaxComputation
) -> TaxFiling | None:
    if computation.revises_computation_id is None:
        return None
    return await db.scalar(
        select(TaxFiling)
        .where(
            TaxFiling.company_id == computation.company_id,
            TaxFiling.computation_id == computation.revises_computation_id,
            TaxFiling.ack_no.is_not(None),
        )
        .order_by(TaxFiling.filed_on.desc().nullslast(), TaxFiling.creation.desc())
        .limit(1)
    )


async def generate_filing(
    db: AsyncSession,
    payload: TaxFilingGenerateIn,
    user: CurrentUser,
) -> TaxFiling:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")

    doc = await get_computation(db, user.company_id, payload.computation_id)
    if doc.docstatus != DOCSTATUS_SUBMITTED and not payload.allow_draft:
        raise ValidationError(
            "Computation must be submitted before generating an ITR filing "
            "(pass allow_draft=true for preview)",
            code="computation_not_submitted",
            field="computation_id",
        )

    form_code = payload.form_code
    if not form_code:
        # Default from assessee class
        klass = await db.scalar(
            select(stat.AssesseeClass).where(
                stat.AssesseeClass.code == doc.assessee_class_code
            )
        )
        form_code = (klass.default_itr_form if klass else None) or "ITR-6"

    if form_code != "ITR-6":
        raise ValidationError(
            f"Phase 8 supports ITR-6 only (got '{form_code}')",
            code="form_not_supported",
            field="form_code",
        )

    itr_form = await _load_itr_form(db, ay_code=doc.ay_code, form_code=form_code)
    run, result = await _latest_run_result(db, doc)
    if result is None and not payload.allow_draft:
        raise ValidationError(
            "Computation has no evaluation run — recompute before filing",
            code="missing_run",
            field="computation_id",
        )

    reg = await get_or_bootstrap_registration(db, user.company_id)
    values = build_canonical_values(computation=doc, result=result, registration=reg)
    field_maps = [
        FieldMapEntry(
            canonical_field=fm.canonical_field,
            cbdt_json_path=fm.cbdt_json_path,
            transform=fm.transform,
        )
        for fm in itr_form.field_maps
    ]
    cbdt = generate_itr6_payload(field_maps=field_maps, values=values)
    digest = payload_sha256(cbdt)
    prior = await _prior_filing_for_chain(db, doc)

    name = await get_next_name(db, "TF-.YYYY.-", user.company_id, on_date=doc.to_date)
    row = TaxFiling(
        company_id=user.company_id,
        name=name,
        computation_id=doc.id,
        run_id=run.id if run else None,
        ay_code=doc.ay_code,
        form_code=itr_form.form_code,
        schema_version=itr_form.schema_version,
        filing_type=doc.filing_type,
        revises_filing_id=prior.id if prior else None,
        payload=cbdt,
        payload_sha256=digest,
        canonical_snapshot={k: (str(v) if hasattr(v, "as_tuple") else v) for k, v in values.items()},
        status="Generated",
        provider=reg.itr_efile_provider,
        owner=user.id,
        modified_by=user.id,
        remarks=payload.remarks,
    )
    db.add(row)
    await db.commit()
    return await get_filing(db, user.company_id, row.id)


async def acknowledge_filing(
    db: AsyncSession,
    filing_id: uuid.UUID,
    payload: TaxFilingAckIn,
    user: CurrentUser,
) -> TaxFiling:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    row = await get_filing(db, user.company_id, filing_id)
    if row.ack_no and row.ack_no != payload.ack_no:
        raise ValidationError(
            f"Filing already acknowledged as {row.ack_no}",
            code="already_acknowledged",
            field="ack_no",
        )

    row.ack_no = payload.ack_no
    row.filed_on = payload.filed_on or date.today()
    row.verification_mode = payload.verification_mode
    row.status = "Filed"
    row.docstatus = DOCSTATUS_SUBMITTED
    row.modified_by = user.id
    if payload.provider_response:
        row.provider_response = payload.provider_response

    # Stamp return_filed_date on the computation for 234A.
    comp = await get_computation(db, user.company_id, row.computation_id)
    if comp.return_filed_date is None:
        comp.return_filed_date = row.filed_on
        comp.modified_by = user.id

    await db.commit()
    return await get_filing(db, user.company_id, row.id)


async def efile_sandbox(
    db: AsyncSession,
    filing_id: uuid.UUID,
    user: CurrentUser,
) -> TaxFiling:
    """Deterministic sandbox acknowledgement — proves the seam without portal HTTPS."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    row = await get_filing(db, user.company_id, filing_id)
    row.provider = "sandbox"
    digest = row.payload_sha256[:10].upper()
    now = datetime.now(timezone.utc)
    ack = f"SANDBOX-ACK-{digest}"
    response: dict[str, Any] = {
        "ack_no": ack,
        "acknowledgement_date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "Accepted",
        "form": row.form_code,
        "mode": "sandbox",
        "payload_sha256": row.payload_sha256,
        "message": "Sandbox stub — not filed with the Income-tax portal.",
    }
    return await acknowledge_filing(
        db,
        filing_id,
        TaxFilingAckIn(
            ack_no=ack,
            filed_on=now.date(),
            verification_mode="EVC",
            provider_response=response,
        ),
        user,
    )


async def chain_return(
    db: AsyncSession,
    payload: TaxFilingChainIn,
    user: CurrentUser,
) -> TaxComputation:
    """Create a Revised / Belated / Updated computation revising a prior one."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    if payload.filing_type not in NON_ORIGINAL_TYPES:
        raise ValidationError(
            f"filing_type must be one of {sorted(NON_ORIGINAL_TYPES)}",
            code="invalid_filing_type",
            field="filing_type",
        )

    prior = await get_computation(db, user.company_id, payload.revises_computation_id)
    if prior.docstatus != DOCSTATUS_SUBMITTED:
        raise ValidationError(
            "Can only revise a submitted computation",
            code="prior_not_submitted",
            field="revises_computation_id",
        )
    if prior.ay_code != (payload.ay_code or prior.ay_code):
        raise ValidationError(
            "Chained return must stay on the same assessment year",
            code="ay_mismatch",
            field="ay_code",
        )

    # Copy income / adjustment lines as a starting worksheet.
    income = [
        TaxComputationIncomeLineIn(
            seq=ln.seq,
            head=ln.head,
            income_character_code=ln.income_character_code,
            sub_ref=ln.sub_ref,
            gross=ln.gross,
            deductions=ln.deductions,
            net=ln.net,
            source_doc_type=ln.source_doc_type,
            source_doc_id=ln.source_doc_id,
        )
        for ln in prior.income_lines
    ]
    adjustments = [
        TaxComputationAdjustmentLineIn(
            section_code=ln.section_code,
            stage=ln.stage,
            description=ln.description,
            direction=ln.direction,
            amount=ln.amount,
            override_amount=ln.override_amount,
            provision_section_code=ln.provision_section_code,
            rule_code=ln.rule_code,
            status="Manual",
        )
        for ln in prior.adjustment_lines
        if ln.status == "Manual" or ln.override_amount is not None
    ]

    create = TaxComputationCreate(
        ay_code=prior.ay_code,
        assessee_class_code=prior.assessee_class_code,
        regime_code=prior.regime_code,
        filing_type=payload.filing_type,
        revises_computation_id=prior.id,
        from_date=prior.from_date,
        to_date=prior.to_date,
        remarks=payload.remarks or f"{payload.filing_type} of {prior.name}",
        book_profit_115jb=prior.book_profit_115jb,
        audit_applicable=prior.audit_applicable,
        income_lines=income,
        adjustment_lines=adjustments,
    )
    return await create_computation(db, create, user)


async def validate_computation_chain(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    filing_type: str,
    revises_computation_id: uuid.UUID | None,
) -> None:
    """Enforce Revised/Belated/Updated must point at a prior submitted computation."""
    if filing_type in NON_ORIGINAL_TYPES:
        if revises_computation_id is None:
            raise ValidationError(
                f"{filing_type} returns require revises_computation_id",
                code="revises_required",
                field="revises_computation_id",
            )
        prior = await db.scalar(
            select(TaxComputation).where(
                TaxComputation.id == revises_computation_id,
                TaxComputation.company_id == company_id,
            )
        )
        if prior is None:
            raise NotFoundError("Prior computation not found")
        if prior.docstatus != DOCSTATUS_SUBMITTED:
            raise ValidationError(
                "Prior computation must be submitted",
                code="prior_not_submitted",
                field="revises_computation_id",
            )
    elif revises_computation_id is not None:
        raise ValidationError(
            "revises_computation_id is only valid for Revised/Belated/Updated",
            code="revises_not_allowed",
            field="revises_computation_id",
        )
