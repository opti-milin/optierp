"""Tax computation document CRUD — Tier 3 services."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models import statutory as stat
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED
from app.models.tax_computation import (
    TaxComputation,
    TaxComputationAdjustmentLine,
    TaxComputationIncomeLine,
    TaxComputationRun,
)
from app.schemas.taxation import (
    TaxComputationAdjustmentLineIn,
    TaxComputationCreate,
    TaxComputationIncomeLineIn,
    TaxComputationOut,
    TaxComputationResultOut,
    TaxComputationRunOut,
    TaxComputationUpdate,
)
from app.services import gl
from app.services.taxation.accounts import (
    resolve_tax_expense_account,
    resolve_tax_payable_account,
)
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.registration import get_election, get_or_bootstrap_registration
from app.services.taxation.resolve import resolve_ruleset

PROVISION_VOUCHER = "Tax Computation"


async def list_computations(db: AsyncSession, company_id: uuid.UUID) -> list[TaxComputation]:
    result = await db.scalars(
        select(TaxComputation)
        .where(TaxComputation.company_id == company_id)
        .order_by(TaxComputation.ay_code.desc(), TaxComputation.creation.desc())
    )
    return list(result.all())


async def get_computation(
    db: AsyncSession, company_id: uuid.UUID, computation_id: uuid.UUID
) -> TaxComputation:
    doc = await db.scalar(
        select(TaxComputation)
        .where(
            TaxComputation.id == computation_id,
            TaxComputation.company_id == company_id,
        )
        .options(
            selectinload(TaxComputation.income_lines),
            selectinload(TaxComputation.adjustment_lines),
            selectinload(TaxComputation.runs),
        )
    )
    if doc is None:
        raise NotFoundError("Tax computation not found")
    return doc


def _line_net(gross: Decimal, deductions: Decimal, net: Decimal | None) -> Decimal:
    if net is not None:
        return q(money(net))
    return q(money(gross) - money(deductions))


async def create_computation(
    db: AsyncSession, payload: TaxComputationCreate, user: CurrentUser
) -> TaxComputation:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")

    ay = await db.scalar(select(stat.AssessmentYear).where(stat.AssessmentYear.code == payload.ay_code))
    if ay is None:
        raise ValidationError(
            f"Unknown assessment year '{payload.ay_code}'",
            code="unknown_ay",
            field="ay_code",
        )

    from_date = payload.from_date or ay.fy_start
    to_date = payload.to_date or ay.fy_end
    if from_date > to_date:
        raise ValidationError("from_date must be on or before to_date", field="from_date")

    from app.services.taxation.filings import validate_computation_chain

    await validate_computation_chain(
        db,
        company_id=user.company_id,
        filing_type=payload.filing_type,
        revises_computation_id=payload.revises_computation_id,
    )

    reg = await get_or_bootstrap_registration(db, user.company_id)
    assessee_class = payload.assessee_class_code or reg.assessee_class_code
    election = await get_election(db, user.company_id, payload.ay_code)
    regime = payload.regime_code
    election_id = None
    if election is not None:
        election_id = election.id
        if not regime:
            regime = election.regime_code
        assessee_class = election.assessee_class_code or assessee_class

    ruleset = await resolve_ruleset(
        db,
        company_id=user.company_id,
        ay_code=payload.ay_code,
        assessee_class_code=assessee_class,
        regime_code=regime,
    )

    name = await get_next_name(db, "TC-.YYYY.-", user.company_id, on_date=from_date)

    doc = TaxComputation(
        company_id=user.company_id,
        name=name,
        ay_code=payload.ay_code,
        assessee_class_code=ruleset.assessee_class_code,
        regime_election_id=election_id,
        regime_code=ruleset.regime_code,
        filing_type=payload.filing_type,
        revises_computation_id=payload.revises_computation_id,
        from_date=from_date,
        to_date=to_date,
        finance_act_version_id=ruleset.finance_act_version_id,
        status="Draft",
        remarks=payload.remarks,
        book_profit_115jb=(
            q(money(payload.book_profit_115jb))
            if payload.book_profit_115jb is not None
            else None
        ),
        return_filed_date=payload.return_filed_date,
        audit_applicable=payload.audit_applicable,
        itr_due_date_override=payload.itr_due_date_override,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(doc)
    await db.flush()

    for i, line in enumerate(payload.income_lines):
        db.add(_income_orm(doc, line, i, user))
    for line in payload.adjustment_lines:
        if line.run_id is not None:
            continue
        db.add(_adj_orm(doc, line, user))

    await db.commit()
    return await get_computation(db, user.company_id, doc.id)


def _income_orm(
    doc: TaxComputation, line: TaxComputationIncomeLineIn, seq: int, user: CurrentUser
) -> TaxComputationIncomeLine:
    return TaxComputationIncomeLine(
        company_id=doc.company_id,
        computation_id=doc.id,
        seq=line.seq if line.seq is not None else seq,
        head=line.head,
        income_character_code=line.income_character_code,
        sub_ref=line.sub_ref,
        gross=q(money(line.gross)),
        deductions=q(money(line.deductions)),
        net=_line_net(line.gross, line.deductions, line.net),
        source_doc_type=line.source_doc_type,
        source_doc_id=line.source_doc_id,
        owner=user.id,
        modified_by=user.id,
    )


def _adj_orm(
    doc: TaxComputation, line: TaxComputationAdjustmentLineIn, user: CurrentUser
) -> TaxComputationAdjustmentLine:
    amount = q(money(line.amount))
    override = q(money(line.override_amount)) if line.override_amount is not None else None
    final = override if override is not None else amount
    return TaxComputationAdjustmentLine(
        company_id=doc.company_id,
        computation_id=doc.id,
        run_id=None,
        provision_section_code=line.provision_section_code,
        rule_code=line.rule_code,
        section_code=line.section_code,
        stage=line.stage,
        description=line.description,
        direction=line.direction,
        amount=amount,
        override_amount=override,
        final_amount=final,
        status=line.status,
        explanation={},
        owner=user.id,
        modified_by=user.id,
    )


async def update_computation(
    db: AsyncSession,
    computation_id: uuid.UUID,
    payload: TaxComputationUpdate,
    user: CurrentUser,
) -> TaxComputation:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    doc = await get_computation(db, user.company_id, computation_id)
    if doc.docstatus != 0:
        raise ValidationError("Only draft computations can be edited", code="not_draft")

    if payload.remarks is not None:
        doc.remarks = payload.remarks
    if payload.filing_type is not None:
        doc.filing_type = payload.filing_type
    if payload.book_profit_115jb is not None:
        doc.book_profit_115jb = q(money(payload.book_profit_115jb))
    if payload.return_filed_date is not None:
        doc.return_filed_date = payload.return_filed_date
    if payload.audit_applicable is not None:
        doc.audit_applicable = payload.audit_applicable
    if payload.itr_due_date_override is not None:
        doc.itr_due_date_override = payload.itr_due_date_override

    if payload.income_lines is not None:
        for old in list(doc.income_lines):
            await db.delete(old)
        await db.flush()
        for i, line in enumerate(payload.income_lines):
            db.add(_income_orm(doc, line, i, user))

    if payload.adjustment_lines is not None:
        # Replace draft (run_id IS NULL) lines only — evaluated run lines stay append-only.
        for old in list(doc.adjustment_lines):
            if old.run_id is None:
                await db.delete(old)
        await db.flush()
        for line in payload.adjustment_lines:
            # An editor that echoes the full list back would otherwise turn each historic
            # run's audit rows into fresh manual rows, duplicating every adjustment.
            if line.run_id is not None:
                continue
            db.add(_adj_orm(doc, line, user))

    doc.modified_by = user.id
    await db.commit()
    return await get_computation(db, user.company_id, doc.id)


async def submit_computation(
    db: AsyncSession,
    computation_id: uuid.UUID,
    user: CurrentUser,
    *,
    provision_expense_account_id: uuid.UUID | None = None,
    provision_liability_account_id: uuid.UUID | None = None,
    post_provision: bool = True,
) -> TaxComputation:
    """Submit worksheet and optionally post current-tax provision JE (Dr expense / Cr payable)."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    doc = await get_computation(db, user.company_id, computation_id)
    if doc.docstatus != DOCSTATUS_DRAFT:
        raise ValidationError("Already submitted or cancelled", code="not_draft")
    if doc.current_run_id is None:
        raise ValidationError(
            "Run a computation before submit", code="no_run", field="current_run_id"
        )

    # The Review panel is advisory only until this check exists: submitting freezes the
    # worksheet and posts the tax provision to the general ledger, so a blocking issue has
    # to stop it here rather than relying on the button being disabled in the browser.
    # Imported here because validation reaches back into this module via filings.
    from app.services.taxation import validation as validation_service

    validation = await validation_service.validate_computation(
        db, company_id=user.company_id, computation_id=computation_id
    )
    if validation.blocking_count:
        first = next(i for i in validation.issues if i.severity == "blocking")
        raise ValidationError(
            f"{validation.blocking_count} issue(s) must be resolved before this "
            f"computation can be submitted. {first.message}",
            code="validation_blocked",
            field=first.field,
        )

    run = await db.scalar(
        select(TaxComputationRun)
        .where(TaxComputationRun.id == doc.current_run_id)
        .options(selectinload(TaxComputationRun.result))
    )
    if run is None or run.result is None:
        raise ValidationError("Current run has no result", code="no_result")

    net_payable = q(money(run.result.net_payable))
    if post_provision and net_payable > ZERO:
        expense = await resolve_tax_expense_account(
            db, user.company_id, provision_expense_account_id or doc.provision_expense_account_id
        )
        liability = await resolve_tax_payable_account(
            db,
            user.company_id,
            provision_liability_account_id or doc.provision_liability_account_id,
        )
        doc.provision_expense_account_id = expense.id
        doc.provision_liability_account_id = liability.id
        await gl.make_gl_entries(
            db,
            company_id=doc.company_id,
            voucher_type=PROVISION_VOUCHER,
            voucher_id=doc.id,
            voucher_no=doc.name,
            posting_date=doc.to_date,
            rows=[
                gl.GLRow(
                    account_id=expense.id,
                    debit=net_payable,
                    remarks=f"Current tax provision {doc.ay_code}",
                ),
                gl.GLRow(
                    account_id=liability.id,
                    credit=net_payable,
                    remarks=f"Current tax provision {doc.ay_code}",
                ),
            ],
            user_id=user.id,
            remarks=f"Income-tax provision for {doc.name}",
        )
        doc.provision_gl_posted = True

    doc.docstatus = DOCSTATUS_SUBMITTED
    doc.status = "Submitted"
    doc.modified_by = user.id
    await db.commit()
    return await get_computation(db, user.company_id, doc.id)


async def cancel_computation(
    db: AsyncSession, computation_id: uuid.UUID, user: CurrentUser
) -> TaxComputation:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    doc = await get_computation(db, user.company_id, computation_id)
    if doc.docstatus == DOCSTATUS_CANCELLED:
        raise ValidationError("Already cancelled", code="already_cancelled")
    if doc.provision_gl_posted:
        await gl.make_reverse_gl_entries(
            db, voucher_type=PROVISION_VOUCHER, voucher_id=doc.id, user_id=user.id
        )
        doc.provision_gl_posted = False
    doc.docstatus = DOCSTATUS_CANCELLED
    doc.status = "Cancelled"
    doc.modified_by = user.id
    await db.commit()
    return await get_computation(db, user.company_id, doc.id)


def result_out(result) -> TaxComputationResultOut | None:
    if result is None:
        return None
    return TaxComputationResultOut.model_validate(result)


def run_out(run) -> TaxComputationRunOut:
    return TaxComputationRunOut(
        id=run.id,
        run_no=run.run_no,
        trigger=run.trigger,
        engine_version=run.engine_version,
        finance_act_version_id=run.finance_act_version_id,
        ruleset_hash=run.ruleset_hash,
        input_hash=run.input_hash,
        duration_ms=run.duration_ms,
        user_id=run.user_id,
        superseded_at=run.superseded_at,
        creation=run.creation,
        result=result_out(run.result),
    )


def computation_out(doc: TaxComputation) -> TaxComputationOut:
    return TaxComputationOut.model_validate(doc)
