"""Income Tax Computation — EntityBooks + IndividualHeads worksheets.

Reads book profit from P&L (entity) or income heads (individual), applies
adjustments, runs the data-driven tax engine (policy → flat/slab/rule-based),
credits TDS / TCS / salary TDS / advance tax. No GL posting — submit locks.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.compliance import (
    IncomeTaxAdjustmentLine,
    IncomeTaxComputation,
    IncomeTaxRateTable,
    IncomeTaxSpecialIncomeLine,
    SpecialIncomeTaxRate,
    TaxAdjustmentCategory,
    TaxAdjustmentProvision,
)
from app.schemas.compliance import (
    IncomeTaxAdjustmentLineIn,
    IncomeTaxComputationCreate,
    IncomeTaxComputationUpdate,
    IncomeTaxSpecialIncomeLineIn,
)
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.financial_reports.statements import profit_and_loss
from app.services import income_tax_masters as masters
from app.services.income_tax_engine import (
    SpecialIncomeInput,
    TaxEngineInput,
    TaxEngineResult,
    run_pipeline,
)
from app.services.income_tax_engine.context import ResolvedTaxRules, ZERO, q
from app.services.income_tax_engine.resolve import lookup_special_rate
from app.services.income_tax_settings import get_income_tax_settings
from app.services.pagination import paginate
from app.services.tax_adjustment_engine import (
    adjustment_snapshot,
    run_adjustment_engine,
)
from app.services.tax_adjustment_engine.context import AdjustmentEngineInput, AdjustmentLineResult
from app.services.tax_adjustment_engine.phase_d import phase_d_breakdown_slice
from app.services.tds_returns import tds_26q

_SERIES = "ITR-COMP-.YYYY.-"


def net_adjustments_of(lines: list[tuple[str, Decimal]]) -> Decimal:
    from app.services.income_tax_engine.pipeline import net_adjustments_of as _net

    return _net(lines)


def tax_from_slabs(
    taxable: Decimal,
    slabs: list[tuple[Decimal, Decimal | None, Decimal]],
) -> Decimal:
    from app.services.income_tax_engine.context import SlabBand
    from app.services.income_tax_engine.strategies.slab_based import tax_from_slabs as _slabs

    bands = [SlabBand(from_amount=a, to_amount=b, rate_percent=c) for a, b, c in slabs]
    tax, _ = _slabs(taxable, bands)
    return tax


def individual_heads_base(
    *,
    salary_income: Decimal,
    house_property_income: Decimal,
    other_sources_income: Decimal,
    capital_gains_income: Decimal,
    chapter_via_deduction: Decimal,
    standard_deduction: Decimal,
) -> Decimal:
    """Gross heads minus Chapter VI-A and standard deduction."""
    gross = (
        q(salary_income)
        + q(house_property_income)
        + q(other_sources_income)
        + q(capital_gains_income)
    )
    return q(gross - q(chapter_via_deduction) - q(standard_deduction))


def default_assessee_mode(entity_type: str) -> str:
    return "IndividualHeads" if entity_type == "Individual" else "EntityBooks"


def compute_tax_pack(
    *,
    book_profit: Decimal,
    adjustments: list[tuple[str, Decimal]],
    tax_rate: Decimal,
    surcharge_rate: Decimal,
    cess_rate: Decimal,
    tds_credit: Decimal,
    advance_tax_paid: Decimal,
    slabs: list[tuple[Decimal, Decimal | None, Decimal]] | None = None,
    filing_regime: str = "Normal",
    apply_rebate_87a: bool = False,
    tcs_credit: Decimal = ZERO,
    rebate_max_taxable: Decimal | None = None,
    rebate_max_amount: Decimal | None = None,
) -> TaxEngineResult:
    """Backward-compatible pure helper used by unit tests.

    Builds an ephemeral ResolvedTaxRules from the provided rates (no DB).
    """
    from app.services.income_tax_engine.context import SlabBand, SurchargeBand

    method = "SlabBased" if slabs else "FlatRate"
    rules = ResolvedTaxRules(
        policy_id=None,
        computation_method=method,
        ordinary_method=method,
        filing_regime=filing_regime,
        entity_type="Company",
        flat_tax_rate=q(tax_rate),
        slabs=[
            SlabBand(from_amount=a, to_amount=b, rate_percent=c) for a, b, c in (slabs or [])
        ],
        surcharge_brackets=[
            SurchargeBand(income_from=ZERO, income_to=None, rate_percent=q(surcharge_rate))
        ]
        if q(surcharge_rate) > ZERO
        else [],
        marginal_relief_enabled=False,
        cess_rate=q(cess_rate),
        rebate_section="87A" if apply_rebate_87a else None,
        rebate_max_taxable=q(rebate_max_taxable)
        if rebate_max_taxable is not None
        else (Decimal("700000") if filing_regime == "New" else Decimal("500000")),
        rebate_max_amount=q(rebate_max_amount)
        if rebate_max_amount is not None
        else (Decimal("25000") if filing_regime == "New" else Decimal("12500")),
    )
    # Note: rebate ceilings above are test-fixture defaults only when apply_rebate_87a
    # is True without explicit amounts; production always loads RebateRule masters.
    return run_pipeline(
        TaxEngineInput(
            book_profit=book_profit,
            adjustments=adjustments,
            tds_credit=tds_credit,
            tcs_credit=tcs_credit,
            advance_tax_paid=advance_tax_paid,
        ),
        rules,
    )


async def _rate_table(
    db: AsyncSession, rate_table_id: uuid.UUID | None, company_id: uuid.UUID
) -> IncomeTaxRateTable | None:
    if rate_table_id is None:
        return None
    row = await db.get(IncomeTaxRateTable, rate_table_id)
    if row is None or row.company_id != company_id or row.disabled:
        raise NotFoundError("Income tax rate table not found")
    return row


async def _seed_books(
    db: AsyncSession, company_id: uuid.UUID, from_date: date, to_date: date
) -> tuple[Decimal, Decimal]:
    pl = await profit_and_loss(db, company_id, from_date=from_date, to_date=to_date)
    book_profit = q(pl["net_profit"])
    company = await get_company(db, company_id)
    report = await tds_26q(db, company, from_date=from_date, to_date=to_date)
    tds_credit = q(report.summary.total_tds)
    return book_profit, tds_credit


def _apply_result(
    doc: IncomeTaxComputation,
    result: TaxEngineResult,
    *,
    adjustment_audit: list[dict] | None = None,
) -> None:
    doc.net_adjustments = result.net_adjustments
    doc.taxable_income = result.taxable_income
    doc.tax_amount = result.tax_amount
    doc.surcharge_amount = result.surcharge_amount
    doc.cess_amount = result.cess_amount
    doc.rebate_amount = result.rebate_amount
    doc.rebate_87a = result.rebate_amount
    doc.marginal_relief_amount = result.marginal_relief_amount
    doc.total_tax = result.total_tax
    doc.tax_payable = result.tax_payable
    doc.computation_method = result.computation_method
    doc.policy_id = result.policy_id
    if result.rate_table_id is not None:
        doc.rate_table_id = result.rate_table_id
    breakdown = dict(result.breakdown or {})
    if adjustment_audit is not None:
        breakdown["adjustments"] = adjustment_audit
    # Phase D informational ICDS / set-off / MAT slice (does not change tax)
    if "phase_d" not in breakdown:
        breakdown["phase_d"] = phase_d_breakdown_slice()
    doc.tax_breakdown = breakdown


def _existing_adj_dicts(doc: IncomeTaxComputation) -> list[dict]:
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(doc)
    if "adjustments" in insp.unloaded:
        return []
    out: list[dict] = []
    for ln in doc.adjustments:
        out.append(
            {
                "category_id": ln.category_id,
                "provision_id": ln.provision_id,
                "rule_id": ln.rule_id,
                "section_code": ln.section_code or "",
                "stage": ln.stage or "PGBP",
                "description": ln.description,
                "direction": ln.direction,
                "amount": ln.amount,
                "base_amount": getattr(ln, "base_amount", None) or ln.amount,
                "computed_amount": getattr(ln, "computed_amount", None) or ln.amount,
                "override_amount": getattr(ln, "override_amount", None),
                "final_amount": getattr(ln, "final_amount", None) or ln.amount,
                "status": getattr(ln, "status", None) or "Manual",
                "explanation": dict(getattr(ln, "explanation", None) or {}),
                "inputs": dict(getattr(ln, "inputs", None) or {}),
                "source_refs": dict(getattr(ln, "source_refs", None) or {}),
            }
        )
    return out


async def _persist_adjustment_results(
    db: AsyncSession,
    doc: IncomeTaxComputation,
    results: list[AdjustmentLineResult],
    user: CurrentUser,
) -> list[tuple[str, Decimal]]:
    from sqlalchemy import delete

    await db.execute(
        delete(IncomeTaxAdjustmentLine).where(IncomeTaxAdjustmentLine.computation_id == doc.id)
    )
    await db.flush()
    pipeline_lines: list[tuple[str, Decimal]] = []
    for i, ln in enumerate(results):
        final = q(ln.final_amount)
        db.add(
            IncomeTaxAdjustmentLine(
                id=uuid.uuid4(),
                company_id=doc.company_id,
                computation_id=doc.id,
                idx=i,
                category_id=ln.category_id,
                provision_id=ln.provision_id,
                rule_id=ln.rule_id,
                section_code=ln.section_code or "",
                stage=ln.stage or "PGBP",
                description=ln.description,
                direction=ln.direction,
                amount=final,
                base_amount=q(ln.base_amount),
                computed_amount=q(ln.computed_amount),
                override_amount=q(ln.override_amount) if ln.override_amount is not None else None,
                final_amount=final,
                status=ln.status,
                explanation=dict(ln.explanation or {}),
                inputs=dict(ln.inputs or {}),
                source_refs=dict(ln.source_refs or {}),
                prior_year_line_id=ln.prior_year_line_id,
                owner=user.id,
                modified_by=user.id,
            )
        )
        if ln.direction in ("Add", "Deduct") and ln.status != "Skipped":
            pipeline_lines.append((ln.direction, final))
    await db.flush()
    return pipeline_lines


async def recompute_adjustments(
    db: AsyncSession,
    doc_id: uuid.UUID,
    user: CurrentUser,
) -> IncomeTaxComputation:
    """Re-run the metadata-driven adjustment engine on a draft computation."""
    doc = await get_computation(db, doc_id, user.company_id)
    require_draft(doc.docstatus)
    settings = await get_income_tax_settings(db, doc.company_id)
    entity = settings.entity_type
    if doc.assessee_mode == "IndividualHeads":
        entity = "Individual"

    engine_inp = AdjustmentEngineInput(
        company_id=doc.company_id,
        assessment_year=doc.assessment_year,
        from_date=doc.from_date,
        to_date=doc.to_date,
        assessee_mode=doc.assessee_mode,
        entity_type=entity,
        filing_regime=settings.filing_regime,
        book_profit=doc.book_profit,
        salary_income=doc.salary_income,
        chapter_via_deduction=doc.chapter_via_deduction,
        standard_deduction=doc.standard_deduction,
        existing_lines=_existing_adj_dicts(doc),
        force_seed=True,
    )
    adj_result = await run_adjustment_engine(db, engine_inp)
    adj_lines = await _persist_adjustment_results(db, doc, adj_result.lines, user)

    if doc.assessee_mode == "IndividualHeads":
        via = sum(
            (
                q(ln.final_amount)
                for ln in adj_result.lines
                if ln.stage == "ChapterVIA" and ln.direction == "Deduct"
            ),
            ZERO,
        )
        if via > ZERO:
            doc.chapter_via_deduction = via

    result = await _pack_from_doc(db, doc, adjustments=adj_lines, settings=settings)
    _apply_result(doc, result, adjustment_audit=adjustment_snapshot(adj_result))
    await log_audit(
        db,
        doctype="Income Tax Computation",
        document_id=doc.id,
        action="UPDATE",
        user_id=user.id,
        company_id=doc.company_id,
        data_after={"recompute_adjustments": True},
    )
    await db.commit()
    return await get_computation(db, doc.id, doc.company_id)


async def _resolve_rules_for_doc(
    db: AsyncSession,
    doc: IncomeTaxComputation,
    settings,
):
    entity = settings.entity_type
    if doc.assessee_mode == "IndividualHeads":
        entity = "Individual"
    policy, rules = await masters.resolve_tax_rules(
        db,
        company_id=doc.company_id,
        assessment_year=doc.assessment_year,
        settings=settings,
        entity_type=entity,
        as_of=doc.to_date,
        policy_id=doc.policy_id,
    )
    if rules is None:
        # Fallback: empty rules → zero tax (explicit missing policy)
        rules = ResolvedTaxRules(
            policy_id=None,
            computation_method="FlatRate",
            ordinary_method="FlatRate",
            filing_regime=settings.filing_regime,
            entity_type=entity,
        )
    return policy, rules


def _special_inputs_from_doc(
    doc: IncomeTaxComputation,
) -> list[SpecialIncomeInput]:
    # Fresh inserts may not have the selectin collection loaded; never trigger
    # sync lazy IO under AsyncSession (MissingGreenlet).
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(doc)
    if "special_income_lines" in insp.unloaded:
        return []
    return [
        SpecialIncomeInput(
            income_category_code=ln.income_category_code,
            amount=ln.amount,
            rate_percent=ln.rate_percent,
            description=ln.description,
            special_rate_id=ln.special_rate_id,
        )
        for ln in doc.special_income_lines
    ]


async def _pack_from_doc(
    db: AsyncSession,
    doc: IncomeTaxComputation,
    *,
    adjustments: list[tuple[str, Decimal]],
    settings,
) -> TaxEngineResult:
    _, rules = await _resolve_rules_for_doc(db, doc, settings)
    if doc.assessee_mode == "IndividualHeads":
        base = individual_heads_base(
            salary_income=doc.salary_income,
            house_property_income=doc.house_property_income,
            other_sources_income=doc.other_sources_income,
            capital_gains_income=doc.capital_gains_income,
            chapter_via_deduction=doc.chapter_via_deduction,
            standard_deduction=doc.standard_deduction,
        )
        credits_tds = q(doc.tds_credit) + q(doc.salary_tds)
        return run_pipeline(
            TaxEngineInput(
                book_profit=base,
                adjustments=[],
                tds_credit=credits_tds,
                tcs_credit=q(doc.tcs_credit),
                advance_tax_paid=doc.advance_tax_paid,
                special_income=_special_inputs_from_doc(doc),
                special_income_in_book=False,
                as_of=doc.to_date,
            ),
            rules,
        )
    return run_pipeline(
        TaxEngineInput(
            book_profit=doc.book_profit,
            adjustments=adjustments,
            tds_credit=doc.tds_credit,
            tcs_credit=q(doc.tcs_credit),
            advance_tax_paid=doc.advance_tax_paid,
            special_income=_special_inputs_from_doc(doc),
            as_of=doc.to_date,
        ),
        rules,
    )


async def _replace_adjustments(
    db: AsyncSession,
    doc: IncomeTaxComputation,
    lines: list[IncomeTaxAdjustmentLineIn],
    user: CurrentUser,
) -> list[tuple[str, Decimal]]:
    from sqlalchemy import delete

    await db.execute(
        delete(IncomeTaxAdjustmentLine).where(IncomeTaxAdjustmentLine.computation_id == doc.id)
    )
    await db.flush()
    result: list[tuple[str, Decimal]] = []
    for i, line in enumerate(lines):
        direction = line.direction
        description = line.description
        section_code = line.section_code or ""
        stage = line.stage or "PGBP"
        provision_id = line.provision_id
        category_id = line.category_id

        if category_id is not None:
            cat = await db.get(TaxAdjustmentCategory, category_id)
            if cat is None or cat.company_id != doc.company_id or cat.disabled:
                raise NotFoundError("Tax adjustment category not found")
            direction = cat.direction
            description = (line.description or "").strip() or cat.category_name
            if not section_code:
                section_code = cat.category_code
            if provision_id is None:
                provision_id = cat.provision_id

        if provision_id is not None:
            prov = await db.get(TaxAdjustmentProvision, provision_id)
            if prov is None or prov.company_id != doc.company_id or prov.disabled:
                raise NotFoundError("Tax adjustment provision not found")
            section_code = section_code or prov.section_code
            stage = prov.stage
            if prov.default_effect in ("Add", "Deduct"):
                direction = prov.default_effect
            description = (description or "").strip() or prov.title

        final = q(line.final_amount) if line.final_amount is not None else q(line.amount)
        if line.override_amount is not None:
            final = q(line.override_amount)
            status = "Overridden"
        else:
            status = line.status or "Manual"
        computed = q(line.computed_amount) if line.computed_amount else final
        db.add(
            IncomeTaxAdjustmentLine(
                id=uuid.uuid4(),
                company_id=doc.company_id,
                computation_id=doc.id,
                idx=i,
                category_id=category_id,
                provision_id=provision_id,
                rule_id=line.rule_id,
                section_code=section_code,
                stage=stage,
                description=description,
                direction=direction,
                amount=final,
                base_amount=q(line.base_amount) if line.base_amount else final,
                computed_amount=computed,
                override_amount=q(line.override_amount) if line.override_amount is not None else None,
                final_amount=final,
                status=status,
                explanation=dict(line.explanation or {}),
                inputs=dict(line.inputs or {}),
                source_refs=dict(line.source_refs or {}),
                prior_year_line_id=line.prior_year_line_id,
                owner=user.id,
                modified_by=user.id,
            )
        )
        result.append((direction, final))
    await db.flush()
    return result


async def _replace_special_income(
    db: AsyncSession,
    doc: IncomeTaxComputation,
    lines: list[IncomeTaxSpecialIncomeLineIn],
    user: CurrentUser,
    *,
    filing_regime: str,
) -> None:
    from sqlalchemy import delete

    await db.execute(
        delete(IncomeTaxSpecialIncomeLine).where(
            IncomeTaxSpecialIncomeLine.computation_id == doc.id
        )
    )
    await db.flush()
    for i, line in enumerate(lines):
        rate_pct = q(line.rate_percent)
        code = line.income_category_code
        special_rate_id = line.special_rate_id
        if special_rate_id is not None:
            sr = await db.get(SpecialIncomeTaxRate, special_rate_id)
            if sr is None or sr.company_id != doc.company_id or sr.disabled:
                raise NotFoundError("Special income tax rate not found")
            rate_pct = q(sr.rate_percent)
            code = sr.income_category_code
        elif code:
            sr = await lookup_special_rate(
                db,
                company_id=doc.company_id,
                assessment_year=doc.assessment_year,
                filing_regime=filing_regime,
                income_category_code=code,
                as_of=doc.to_date,
            )
            if sr is not None:
                special_rate_id = sr.id
                rate_pct = q(sr.rate_percent)
        amount = q(line.amount)
        tax_amt = q(amount * (rate_pct / Decimal("100"))) if amount > ZERO else ZERO
        db.add(
            IncomeTaxSpecialIncomeLine(
                id=uuid.uuid4(),
                company_id=doc.company_id,
                computation_id=doc.id,
                idx=i,
                special_rate_id=special_rate_id,
                income_category_code=code,
                amount=amount,
                rate_percent=rate_pct,
                tax_amount=tax_amt,
                description=line.description,
                owner=user.id,
                modified_by=user.id,
            )
        )
    await db.flush()
    # Refresh relationship
    await db.refresh(doc, attribute_names=["special_income_lines"])


# --- CRUD --------------------------------------------------------------------------


async def create_computation(
    db: AsyncSession, payload: IncomeTaxComputationCreate, user: CurrentUser
) -> IncomeTaxComputation:
    company = await get_company(db, user.company_id)
    if payload.from_date > payload.to_date:
        raise ValidationError("from_date must be on or before to_date", field="from_date")

    settings = await get_income_tax_settings(db, company.id)
    mode = payload.assessee_mode or default_assessee_mode(settings.entity_type)
    # NOTE: per-AY uniqueness temporarily relaxed so multiple test worksheets can coexist.

    entity = "Individual" if mode == "IndividualHeads" else settings.entity_type
    policy, _rules = await masters.resolve_tax_rules(
        db,
        company_id=company.id,
        assessment_year=payload.assessment_year,
        settings=settings,
        entity_type=entity,
        as_of=payload.to_date,
        policy_id=payload.policy_id,
    )

    book_profit = ZERO
    tds_credit = ZERO
    seed_source = "Manual"
    if mode == "EntityBooks":
        if payload.seed_from_books:
            book_profit, tds_credit = await _seed_books(
                db, company.id, payload.from_date, payload.to_date
            )
            seed_source = "Books"
        else:
            book_profit = q(payload.book_profit or ZERO)
            tds_credit = q(payload.tds_credit or ZERO)

    name = await get_next_name(db, _SERIES, company.id, on_date=payload.to_date)
    doc = IncomeTaxComputation(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        assessment_year=payload.assessment_year,
        from_date=payload.from_date,
        to_date=payload.to_date,
        rate_table_id=payload.rate_table_id
        or (policy.rate_table_id if policy else None),
        policy_id=policy.id if policy else payload.policy_id,
        assessee_mode=mode,
        book_profit=book_profit,
        tds_credit=tds_credit if mode == "EntityBooks" else q(payload.tds_credit or ZERO),
        tcs_credit=q(payload.tcs_credit),
        salary_income=q(payload.salary_income),
        house_property_income=q(payload.house_property_income),
        other_sources_income=q(payload.other_sources_income),
        capital_gains_income=q(payload.capital_gains_income),
        chapter_via_deduction=q(payload.chapter_via_deduction),
        standard_deduction=q(payload.standard_deduction),
        salary_tds=q(payload.salary_tds or payload.tax_deducted),
        employer_name=payload.employer_name,
        employer_tan=payload.employer_tan,
        employer_address=payload.employer_address,
        employee_name=payload.employee_name,
        employee_pan=payload.employee_pan,
        gross_salary=q(payload.gross_salary),
        exemptions_total=q(payload.exemptions_total),
        taxable_salary=q(payload.taxable_salary),
        tax_deducted=q(payload.tax_deducted),
        seed_source=seed_source,
        employee_id=payload.employee_id,
        salary_slip_ids=[],
        advance_tax_paid=q(payload.advance_tax_paid),
        status="Draft",
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    if mode == "IndividualHeads" and doc.salary_income == ZERO and doc.taxable_salary:
        doc.salary_income = doc.taxable_salary
    if mode == "IndividualHeads" and doc.salary_tds == ZERO and doc.tax_deducted:
        doc.salary_tds = doc.tax_deducted

    db.add(doc)
    await db.flush()
    adj_audit: list[dict] | None = None
    if payload.adjustments:
        adj_lines = await _replace_adjustments(db, doc, payload.adjustments, user)
    else:
        # Auto-seed from adjustment rule pack (EntityBooks / IndividualHeads VIA)
        engine_inp = AdjustmentEngineInput(
            company_id=doc.company_id,
            assessment_year=doc.assessment_year,
            from_date=doc.from_date,
            to_date=doc.to_date,
            assessee_mode=mode,
            entity_type=entity,
            filing_regime=settings.filing_regime,
            book_profit=doc.book_profit,
            salary_income=doc.salary_income,
            chapter_via_deduction=doc.chapter_via_deduction,
            standard_deduction=doc.standard_deduction,
            existing_lines=[],
            force_seed=True,
        )
        adj_result = await run_adjustment_engine(db, engine_inp)
        adj_lines = await _persist_adjustment_results(db, doc, adj_result.lines, user)
        adj_audit = adjustment_snapshot(adj_result)
        if mode == "IndividualHeads":
            via = sum(
                (
                    q(ln.final_amount)
                    for ln in adj_result.lines
                    if ln.stage == "ChapterVIA" and ln.direction == "Deduct"
                ),
                ZERO,
            )
            if via > ZERO:
                doc.chapter_via_deduction = via
    if payload.special_income:
        await _replace_special_income(
            db, doc, payload.special_income, user, filing_regime=settings.filing_regime
        )
    result = await _pack_from_doc(db, doc, adjustments=adj_lines, settings=settings)
    _apply_result(doc, result, adjustment_audit=adj_audit)
    await log_audit(
        db,
        doctype="Income Tax Computation",
        document_id=doc.id,
        action="INSERT",
        user_id=user.id,
        company_id=company.id,
    )
    await db.commit()
    return await get_computation(db, doc.id, company.id)


async def get_computation(
    db: AsyncSession, doc_id: uuid.UUID, company_id: uuid.UUID | None
) -> IncomeTaxComputation:
    doc = await db.scalar(
        select(IncomeTaxComputation)
        .options(
            selectinload(IncomeTaxComputation.adjustments),
            selectinload(IncomeTaxComputation.special_income_lines),
        )
        .where(
            IncomeTaxComputation.id == doc_id,
            IncomeTaxComputation.company_id == company_id,
        )
    )
    if doc is None:
        raise NotFoundError("Income Tax Computation not found")
    return doc


async def list_computations(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> tuple[list[IncomeTaxComputation], int]:
    stmt = (
        select(IncomeTaxComputation)
        .where(IncomeTaxComputation.company_id == company_id)
        .order_by(IncomeTaxComputation.assessment_year.desc(), IncomeTaxComputation.creation.desc())
    )
    if status:
        stmt = stmt.where(IncomeTaxComputation.status == status)
    return await paginate(db, stmt, page, page_size)


async def update_computation(
    db: AsyncSession,
    doc_id: uuid.UUID,
    payload: IncomeTaxComputationUpdate,
    user: CurrentUser,
) -> IncomeTaxComputation:
    doc = await get_computation(db, doc_id, user.company_id)
    require_draft(doc.docstatus)
    settings = await get_income_tax_settings(db, doc.company_id)

    if payload.reseeds_from_books and doc.assessee_mode == "EntityBooks":
        book_profit, tds_credit = await _seed_books(
            db, doc.company_id, doc.from_date, doc.to_date
        )
        doc.book_profit = book_profit
        doc.tds_credit = tds_credit
        doc.seed_source = "Books"
    if payload.book_profit is not None:
        doc.book_profit = q(payload.book_profit)
    if payload.tds_credit is not None:
        doc.tds_credit = q(payload.tds_credit)
    if payload.tcs_credit is not None:
        doc.tcs_credit = q(payload.tcs_credit)
    if payload.advance_tax_paid is not None:
        doc.advance_tax_paid = q(payload.advance_tax_paid)
    if payload.remarks is not None:
        doc.remarks = payload.remarks
    if payload.policy_id is not None:
        doc.policy_id = payload.policy_id
    for field in (
        "salary_income",
        "house_property_income",
        "other_sources_income",
        "capital_gains_income",
        "chapter_via_deduction",
        "standard_deduction",
        "salary_tds",
        "gross_salary",
        "exemptions_total",
        "taxable_salary",
        "tax_deducted",
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(doc, field, q(val))
    for field in (
        "employer_name",
        "employer_tan",
        "employer_address",
        "employee_name",
        "employee_pan",
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(doc, field, val)
    if payload.employee_id is not None:
        doc.employee_id = payload.employee_id

    if payload.rate_table_id is not None:
        await _rate_table(db, payload.rate_table_id, doc.company_id)
        doc.rate_table_id = payload.rate_table_id
    elif payload.resolve_rate_from_settings:
        resolved = await masters.resolve_rate_table(
            db,
            company_id=doc.company_id,
            assessment_year=doc.assessment_year,
            settings=settings,
        )
        doc.rate_table_id = resolved.id if resolved else None
        policy, _ = await masters.resolve_tax_rules(
            db,
            company_id=doc.company_id,
            assessment_year=doc.assessment_year,
            settings=settings,
            as_of=doc.to_date,
        )
        doc.policy_id = policy.id if policy else None

    if payload.adjustments is not None:
        adj_lines = await _replace_adjustments(db, doc, payload.adjustments, user)
    else:
        adj_lines = [(ln.direction, ln.amount) for ln in doc.adjustments]

    if payload.special_income is not None:
        await _replace_special_income(
            db, doc, payload.special_income, user, filing_regime=settings.filing_regime
        )

    result = await _pack_from_doc(db, doc, adjustments=adj_lines, settings=settings)
    _apply_result(doc, result)
    doc.modified_by = user.id
    await log_audit(
        db,
        doctype="Income Tax Computation",
        document_id=doc.id,
        action="UPDATE",
        user_id=user.id,
        company_id=doc.company_id,
    )
    await db.commit()
    return await get_computation(db, doc.id, doc.company_id)


async def submit_computation(
    db: AsyncSession, doc_id: uuid.UUID, user: CurrentUser
) -> IncomeTaxComputation:
    doc = await get_computation(db, doc_id, user.company_id)
    require_draft(doc.docstatus)
    settings = await get_income_tax_settings(db, doc.company_id)
    adj_lines = [(ln.direction, ln.amount) for ln in doc.adjustments]
    result = await _pack_from_doc(db, doc, adjustments=adj_lines, settings=settings)
    _apply_result(doc, result)
    doc.docstatus = DOCSTATUS_SUBMITTED
    doc.status = "Submitted"
    doc.modified_by = user.id
    await log_audit(
        db,
        doctype="Income Tax Computation",
        document_id=doc.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=doc.company_id,
    )
    await db.commit()
    return await get_computation(db, doc.id, doc.company_id)


async def cancel_computation(
    db: AsyncSession, doc_id: uuid.UUID, user: CurrentUser
) -> IncomeTaxComputation:
    doc = await get_computation(db, doc_id, user.company_id)
    require_submitted(doc.docstatus)
    doc.docstatus = DOCSTATUS_CANCELLED
    doc.status = "Cancelled"
    doc.modified_by = user.id
    await log_audit(
        db,
        doctype="Income Tax Computation",
        document_id=doc.id,
        action="CANCEL",
        user_id=user.id,
        company_id=doc.company_id,
    )
    await db.commit()
    return await get_computation(db, doc.id, doc.company_id)
