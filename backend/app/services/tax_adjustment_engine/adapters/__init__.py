"""ERP fact adapters for the tax adjustment engine (read-only)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.base import DOCSTATUS_SUBMITTED
from app.models.compliance import IncomeTaxComputation, TaxDepreciationBlock
from app.services.tax_adjustment_engine.context import AdjustmentFactBag, ZERO, q


async def gather_facts(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    from_date: date,
    to_date: date,
    book_profit: Decimal = ZERO,
    schedule_inputs: dict[str, Decimal] | None = None,
    extras: dict | None = None,
) -> AdjustmentFactBag:
    facts = AdjustmentFactBag(
        book_profit=q(book_profit),
        schedule_inputs={k: q(v) for k, v in (schedule_inputs or {}).items()},
        extras=dict(extras or {}),
    )
    facts.books_depreciation = await books_depreciation(db, company_id, from_date, to_date)
    facts.tax_depreciation = await tax_depreciation(db, company_id, assessment_year)
    facts.tds_gap_resident_expense = await tds_gap_estimate(
        db, company_id, from_date, to_date, resident=True
    )
    facts.tds_gap_non_resident_expense = await tds_gap_estimate(
        db, company_id, from_date, to_date, resident=False
    )
    facts.cash_over_threshold = await cash_over_threshold(db, company_id, from_date, to_date)
    facts.unpaid_43b = await unpaid_43b_estimate(db, company_id, to_date)
    prior = await prior_year_adjustment_lines(db, company_id, assessment_year)
    facts.prior_year_lines = prior
    facts.prior_year_43b_reversals = sum(
        (
            q(ln.get("final_amount") or ln.get("amount") or 0)
            for ln in prior
            if ln.get("section_code") == "43B" and ln.get("direction") == "Add"
        ),
        ZERO,
    )
    return facts


async def books_depreciation(
    db: AsyncSession,
    company_id: uuid.UUID,
    from_date: date,
    to_date: date,
) -> Decimal:
    """Sum posted asset depreciation in the period via Asset → schedule join."""
    from app.models.assets import Asset, AssetDepreciationSchedule

    rows = (
        await db.execute(
            select(AssetDepreciationSchedule.depreciation_amount)
            .join(Asset, Asset.id == AssetDepreciationSchedule.asset_id)
            .where(
                Asset.company_id == company_id,
                AssetDepreciationSchedule.schedule_date >= from_date,
                AssetDepreciationSchedule.schedule_date <= to_date,
                AssetDepreciationSchedule.posted.is_(True),
            )
        )
    ).scalars().all()
    return q(sum((r or ZERO) for r in rows))


async def tax_depreciation(
    db: AsyncSession, company_id: uuid.UUID, assessment_year: str
) -> Decimal:
    rows = (
        await db.execute(
            select(TaxDepreciationBlock.depreciation_amount).where(
                TaxDepreciationBlock.company_id == company_id,
                TaxDepreciationBlock.assessment_year == assessment_year,
                TaxDepreciationBlock.disabled.is_(False),
            )
        )
    ).scalars().all()
    return q(sum((r or ZERO) for r in rows))


async def tds_gap_estimate(
    db: AsyncSession,
    company_id: uuid.UUID,
    from_date: date,
    to_date: date,
    *,
    resident: bool,
) -> Decimal:
    """Without a full TDS-gap ledger, return ZERO so methods surface NeedsInput.

    Hook point for Phase B when PI tax-withholding details support gap detection.
    """
    _ = db, company_id, from_date, to_date, resident
    return ZERO


async def cash_over_threshold(
    db: AsyncSession,
    company_id: uuid.UUID,
    from_date: date,
    to_date: date,
    threshold: Decimal = Decimal("10000"),
) -> Decimal:
    """Sum submitted Payment Entries whose mode name contains 'cash' and exceed threshold."""
    from app.models.accounts import ModeOfPayment, PaymentEntry

    rows = (
        await db.execute(
            select(PaymentEntry.paid_amount, ModeOfPayment.mode_name)
            .outerjoin(ModeOfPayment, ModeOfPayment.id == PaymentEntry.mode_of_payment_id)
            .where(
                PaymentEntry.company_id == company_id,
                PaymentEntry.docstatus == DOCSTATUS_SUBMITTED,
                PaymentEntry.posting_date >= from_date,
                PaymentEntry.posting_date <= to_date,
            )
        )
    ).all()
    total = ZERO
    for paid, mode_name in rows:
        mode_s = (mode_name or "").lower()
        if "cash" in mode_s and q(paid) > q(threshold):
            total += q(paid)
    return total


async def unpaid_43b_estimate(
    db: AsyncSession, company_id: uuid.UUID, as_of: date
) -> Decimal:
    """Liability account mapping not configured → ZERO (NeedsInput on 43B rule)."""
    _ = db, company_id, as_of
    return ZERO


def _prev_assessment_year(ay: str) -> str | None:
    try:
        start = int(ay.split("-")[0])
        return f"{start - 1}-{str(start)[2:]}"
    except (ValueError, IndexError):
        return None


async def prior_year_adjustment_lines(
    db: AsyncSession, company_id: uuid.UUID, assessment_year: str
) -> list[dict]:
    prev = _prev_assessment_year(assessment_year)
    if not prev:
        return []
    doc = await db.scalar(
        select(IncomeTaxComputation)
        .options(selectinload(IncomeTaxComputation.adjustments))
        .where(
            IncomeTaxComputation.company_id == company_id,
            IncomeTaxComputation.assessment_year == prev,
            IncomeTaxComputation.assessee_mode == "EntityBooks",
            IncomeTaxComputation.docstatus == DOCSTATUS_SUBMITTED,
        )
        .limit(1)
    )
    if doc is None:
        return []
    out: list[dict] = []
    for ln in doc.adjustments:
        out.append(
            {
                "id": str(ln.id),
                "section_code": ln.section_code or "",
                "direction": ln.direction,
                "amount": str(ln.amount),
                "final_amount": str(getattr(ln, "final_amount", None) or ln.amount),
                "stage": getattr(ln, "stage", "PGBP"),
            }
        )
    return out
