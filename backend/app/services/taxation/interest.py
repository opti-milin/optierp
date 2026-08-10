"""Interest 234A/B/C + advance-tax calendar — Phase 7 services."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models import statutory as stat
from app.models.base import DOCSTATUS_SUBMITTED
from app.models.tax_computation import TaxComputation
from app.models.tax_credits import TaxChallan
from app.services.taxation.catalogue import accessors as cat
from app.services.taxation.kernel.interest_234 import (
    AdvanceInstalment,
    ChallanPayment,
    InstalmentProjection,
    Interest234Input,
    Interest234Result,
    compute_interest_234,
    project_advance_tax,
)
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation import credits as credit_service


async def list_interest_rules(
    db: AsyncSession, *, ay_code: str
) -> list[stat.InterestRule]:
    fav = await cat.get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    result = await db.scalars(
        select(stat.InterestRule)
        .where(stat.InterestRule.finance_act_version_id == fav.id)
        .order_by(stat.InterestRule.code)
    )
    return list(result.all())


def _fy_year_for_ay(ay_code: str) -> int:
    """FY start calendar year for AY '2025-26' → 2024."""
    try:
        return int(ay_code.split("-")[0]) - 1
    except (ValueError, IndexError) as exc:
        raise ValidationError(
            "ay_code must look like '2025-26'", field="ay_code", code="invalid_ay"
        ) from exc


def resolve_instalment_dates(
    rules: list[stat.DueDateRule], *, ay_code: str
) -> list[AdvanceInstalment]:
    """Map statutory AdvanceTax due_date_rules onto calendar dates for the FY."""
    fy_start_year = _fy_year_for_ay(ay_code)
    out: list[AdvanceInstalment] = []
    for rule in rules:
        if rule.rule_kind != "AdvanceTax":
            continue
        if rule.due_month is None or rule.due_day is None:
            continue
        month = int(rule.due_month)
        day = int(rule.due_day)
        year = fy_start_year if month >= 4 else fy_start_year + 1
        pct = money(rule.percent_of_tax or ZERO)
        out.append(
            AdvanceInstalment(
                seq=int(rule.seq),
                due_date=date(year, month, day),
                cumulative_percent=pct,
                code=rule.code,
                label=rule.label or rule.code,
            )
        )
    out.sort(key=lambda i: i.seq)
    return out


def resolve_itr_due_date(
    rules: list[stat.DueDateRule],
    *,
    ay_code: str,
    assessee_class_code: str,
    audit_applicable: bool,
    override: date | None = None,
) -> date | None:
    if override is not None:
        return override
    # ITR due dates fall in the AY calendar year (e.g. 31 Oct 2025 for AY 2025-26).
    try:
        ay_year = int(ay_code.split("-")[0])
    except (ValueError, IndexError):
        return None
    candidates = [
        r
        for r in rules
        if r.rule_kind == "ItrDue"
        and (r.assessee_class_code is None or r.assessee_class_code == assessee_class_code)
        and r.due_month is not None
        and r.due_day is not None
    ]
    for rule in candidates:
        params = rule.params or {}
        audit_flag = params.get("audit")
        if audit_flag is not None and bool(audit_flag) != audit_applicable:
            continue
        return date(ay_year, int(rule.due_month), int(rule.due_day))
    # Fallback: first ItrDue for class ignoring audit param
    for rule in candidates:
        return date(ay_year, int(rule.due_month), int(rule.due_day))
    return None


async def load_advance_challans(
    db: AsyncSession, company_id: uuid.UUID, ay_code: str
) -> list[ChallanPayment]:
    rows = list(
        (
            await db.scalars(
                select(TaxChallan).where(
                    TaxChallan.company_id == company_id,
                    TaxChallan.ay_code == ay_code,
                    TaxChallan.docstatus == DOCSTATUS_SUBMITTED,
                )
            )
        ).all()
    )
    return [
        ChallanPayment(
            deposit_date=r.deposit_date,
            amount=money(r.amount),
            challan_type=r.challan_type,
        )
        for r in rows
    ]


def _rate_map(rules: list[stat.InterestRule]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for r in rules:
        out[r.section_code] = money(r.rate_percent_per_month)
        out[r.code] = money(r.rate_percent_per_month)
    return out


async def compute_for_computation(
    db: AsyncSession,
    doc: TaxComputation,
    *,
    assessed_tax: Decimal,
    as_of_date: date | None = None,
) -> Interest234Result:
    """Compute 234A/B/C for a tax computation using submitted challans + catalogue rules."""
    due_rules = await cat.list_due_date_rules(db, ay_code=doc.ay_code)
    interest_rules = await list_interest_rules(db, ay_code=doc.ay_code)
    rates = _rate_map(interest_rules)
    instalments = resolve_instalment_dates(due_rules, ay_code=doc.ay_code)
    itr_due = resolve_itr_due_date(
        due_rules,
        ay_code=doc.ay_code,
        assessee_class_code=doc.assessee_class_code,
        audit_applicable=bool(doc.audit_applicable),
        override=doc.itr_due_date_override,
    )
    payments = await load_advance_challans(db, doc.company_id, doc.ay_code)
    # TDS/TCS kinds only for interest base (advance tax comes from challans).
    tds_only = await _tds_tcs_total(db, doc.company_id, doc.ay_code, doc.id)

    ay = await db.scalar(select(stat.AssessmentYear).where(stat.AssessmentYear.code == doc.ay_code))
    ay_start = ay.ay_start if ay else None

    return compute_interest_234(
        Interest234Input(
            assessed_tax=assessed_tax,
            tds_tcs_credit=tds_only,
            advance_payments=tuple(payments),
            instalments=tuple(instalments),
            itr_due_date=itr_due,
            return_filed_date=doc.return_filed_date,
            as_of_date=as_of_date or date.today(),
            rate_234a_percent=rates.get("234A", Decimal("1")),
            rate_234b_percent=rates.get("234B", Decimal("1")),
            rate_234c_percent=rates.get("234C", Decimal("1")),
            ay_start=ay_start,
        )
    )


async def _tds_tcs_total(
    db: AsyncSession,
    company_id: uuid.UUID,
    ay_code: str,
    computation_id: uuid.UUID,
) -> Decimal:
    rows = await credit_service.list_credits(
        db, company_id, ay_code=ay_code, computation_id=computation_id
    )
    # Also include company-wide TDS/TCS for the AY not linked to a computation.
    extra = await credit_service.list_credits(db, company_id, ay_code=ay_code)
    seen: set[uuid.UUID] = set()
    total = ZERO
    for row in list(rows) + list(extra):
        if row.id in seen:
            continue
        seen.add(row.id)
        if row.credit_kind not in ("TDS", "TCS", "SalaryTDS"):
            continue
        total = q(total + money(row.amount_claimed))
    return total


async def advance_tax_calendar(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    ay_code: str,
    estimated_tax: Decimal | None = None,
    computation_id: uuid.UUID | None = None,
    as_of: date | None = None,
) -> tuple[list[InstalmentProjection], Decimal]:
    """Projection of instalment requirements vs submitted AdvanceTax challans."""
    due_rules = await cat.list_due_date_rules(db, ay_code=ay_code)
    instalments = resolve_instalment_dates(due_rules, ay_code=ay_code)
    payments = await load_advance_challans(db, company_id, ay_code)

    tax = estimated_tax
    tds = ZERO
    if tax is None and computation_id is not None:
        doc = await db.get(TaxComputation, computation_id)
        if doc is not None and doc.company_id == company_id:
            # Use latest run net tax if available
            from app.models.tax_computation import TaxComputationRun
            from sqlalchemy.orm import selectinload

            run = None
            if doc.current_run_id:
                run = await db.scalar(
                    select(TaxComputationRun)
                    .where(TaxComputationRun.id == doc.current_run_id)
                    .options(selectinload(TaxComputationRun.result))
                )
            if run and run.result:
                tax = money(run.result.total_tax)
            tds = await _tds_tcs_total(db, company_id, ay_code, doc.id)
    if tax is None:
        tax = ZERO

    projections = project_advance_tax(
        estimated_tax=tax,
        tds_tcs_credit=tds,
        payments=tuple(payments),
        instalments=tuple(instalments),
        as_of=as_of,
    )
    return list(projections), q(money(tax) - tds)
