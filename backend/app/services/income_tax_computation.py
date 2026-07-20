"""Income Tax Computation — entity ITR worksheet (Phase 1).

Reads book profit from P&L, applies adjustment lines, computes tax from the
rate table, credits TDS from the purchase register, and stores the pack.
No GL posting — submit only locks the worksheet (docstatus flip).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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
    TaxAdjustmentCategory,
)
from app.schemas.compliance import (
    IncomeTaxAdjustmentLineIn,
    IncomeTaxComputationCreate,
    IncomeTaxComputationUpdate,
)
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.financial_reports.statements import profit_and_loss
from app.services import income_tax_masters as masters
from app.services.income_tax_settings import get_income_tax_settings
from app.services.pagination import paginate
from app.services.tds_returns import tds_26q

ZERO = Decimal("0")
Q2 = Decimal("0.01")
_SERIES = "ITR-COMP-.YYYY.-"


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


@dataclass(frozen=True)
class TaxPack:
    """Pure tax math result — unit-tested without a DB."""

    net_adjustments: Decimal
    taxable_income: Decimal
    tax_amount: Decimal
    surcharge_amount: Decimal
    cess_amount: Decimal
    total_tax: Decimal
    tax_payable: Decimal


def net_adjustments_of(lines: list[tuple[str, Decimal]]) -> Decimal:
    """Sum Add amounts minus Deduct amounts. ``lines`` = (direction, amount)."""
    total = ZERO
    for direction, amount in lines:
        amt = _q(amount)
        if direction == "Add":
            total += amt
        elif direction == "Deduct":
            total -= amt
        else:
            raise ValidationError(f"Invalid adjustment direction '{direction}'", field="direction")
    return _q(total)


def compute_tax_pack(
    *,
    book_profit: Decimal,
    adjustments: list[tuple[str, Decimal]],
    tax_rate: Decimal,
    surcharge_rate: Decimal,
    cess_rate: Decimal,
    tds_credit: Decimal,
    advance_tax_paid: Decimal,
) -> TaxPack:
    """Books → taxable income → tax / surcharge / cess → net payable (or refund).

    Taxable income is floored at zero for tax calculation (loss years → zero tax).
    ``tax_payable`` may be negative when credits exceed liability (refundable).
    """
    net_adj = net_adjustments_of(adjustments)
    taxable = _q(book_profit) + net_adj
    taxable_for_tax = max(taxable, ZERO)
    tax = _q(taxable_for_tax * (_q(tax_rate) / Decimal("100")))
    surcharge = _q(tax * (_q(surcharge_rate) / Decimal("100")))
    cess = _q((tax + surcharge) * (_q(cess_rate) / Decimal("100")))
    total = _q(tax + surcharge + cess)
    payable = _q(total - _q(tds_credit) - _q(advance_tax_paid))
    return TaxPack(
        net_adjustments=net_adj,
        taxable_income=_q(taxable),
        tax_amount=tax,
        surcharge_amount=surcharge,
        cess_amount=cess,
        total_tax=total,
        tax_payable=payable,
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
    book_profit = _q(pl["net_profit"])
    company = await get_company(db, company_id)
    report = await tds_26q(db, company, from_date=from_date, to_date=to_date)
    tds_credit = _q(report.summary.total_tds)
    return book_profit, tds_credit


def _apply_pack(doc: IncomeTaxComputation, pack: TaxPack) -> None:
    doc.net_adjustments = pack.net_adjustments
    doc.taxable_income = pack.taxable_income
    doc.tax_amount = pack.tax_amount
    doc.surcharge_amount = pack.surcharge_amount
    doc.cess_amount = pack.cess_amount
    doc.total_tax = pack.total_tax
    doc.tax_payable = pack.tax_payable


def _pack_from_lines(
    *,
    book_profit: Decimal,
    tds_credit: Decimal,
    advance_tax_paid: Decimal,
    adjustments: list[tuple[str, Decimal]],
    rate: IncomeTaxRateTable | None,
) -> TaxPack:
    return compute_tax_pack(
        book_profit=book_profit,
        adjustments=adjustments,
        tax_rate=rate.tax_rate if rate else ZERO,
        surcharge_rate=rate.surcharge_rate if rate else ZERO,
        cess_rate=rate.cess_rate if rate else ZERO,
        tds_credit=tds_credit,
        advance_tax_paid=advance_tax_paid,
    )


async def _replace_adjustments(
    db: AsyncSession,
    doc: IncomeTaxComputation,
    lines: list[IncomeTaxAdjustmentLineIn],
    user: CurrentUser,
) -> list[tuple[str, Decimal]]:
    """Replace child rows via DELETE + INSERT (no relationship lazy-load)."""
    from sqlalchemy import delete

    await db.execute(
        delete(IncomeTaxAdjustmentLine).where(IncomeTaxAdjustmentLine.computation_id == doc.id)
    )
    await db.flush()
    result: list[tuple[str, Decimal]] = []
    for i, line in enumerate(lines):
        direction = line.direction
        description = line.description
        if line.category_id is not None:
            cat = await db.get(TaxAdjustmentCategory, line.category_id)
            if cat is None or cat.company_id != doc.company_id or cat.disabled:
                raise NotFoundError("Tax adjustment category not found")
            # Category owns the statutory direction; description defaults to category name.
            direction = cat.direction
            description = (line.description or "").strip() or cat.category_name
        amount = _q(line.amount)
        db.add(
            IncomeTaxAdjustmentLine(
                id=uuid.uuid4(),
                company_id=doc.company_id,
                computation_id=doc.id,
                idx=i,
                category_id=line.category_id,
                description=description,
                direction=direction,
                amount=amount,
                owner=user.id,
                modified_by=user.id,
            )
        )
        result.append((direction, amount))
    await db.flush()
    return result


async def _resolve_rate_for_doc(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    rate_table_id: uuid.UUID | None,
) -> IncomeTaxRateTable | None:
    """Use explicit rate_table_id, else match settings entity + regime + AY."""
    if rate_table_id is not None:
        return await _rate_table(db, rate_table_id, company_id)
    settings = await get_income_tax_settings(db, company_id)
    return await masters.resolve_rate_table(
        db,
        company_id=company_id,
        assessment_year=assessment_year,
        settings=settings,
    )


# --- CRUD --------------------------------------------------------------------------


async def create_computation(
    db: AsyncSession, payload: IncomeTaxComputationCreate, user: CurrentUser
) -> IncomeTaxComputation:
    company = await get_company(db, user.company_id)
    if payload.from_date > payload.to_date:
        raise ValidationError("from_date must be on or before to_date", field="from_date")

    existing = await db.scalar(
        select(IncomeTaxComputation.id).where(
            IncomeTaxComputation.company_id == company.id,
            IncomeTaxComputation.assessment_year == payload.assessment_year,
            IncomeTaxComputation.docstatus != DOCSTATUS_CANCELLED,
        )
    )
    if existing is not None:
        raise ValidationError(
            f"A computation for assessment year {payload.assessment_year} already exists",
            field="assessment_year",
        )

    rate = await _resolve_rate_for_doc(
        db,
        company_id=company.id,
        assessment_year=payload.assessment_year,
        rate_table_id=payload.rate_table_id,
    )

    if payload.seed_from_books:
        book_profit, tds_credit = await _seed_books(
            db, company.id, payload.from_date, payload.to_date
        )
    else:
        book_profit = _q(payload.book_profit or ZERO)
        tds_credit = _q(payload.tds_credit or ZERO)

    name = await get_next_name(db, _SERIES, company.id, on_date=payload.to_date)
    doc = IncomeTaxComputation(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        assessment_year=payload.assessment_year,
        from_date=payload.from_date,
        to_date=payload.to_date,
        rate_table_id=rate.id if rate else payload.rate_table_id,
        book_profit=book_profit,
        tds_credit=tds_credit,
        advance_tax_paid=_q(payload.advance_tax_paid),
        status="Draft",
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(doc)
    await db.flush()
    adj_lines = await _replace_adjustments(db, doc, payload.adjustments, user)
    _apply_pack(
        doc,
        _pack_from_lines(
            book_profit=doc.book_profit,
            tds_credit=doc.tds_credit,
            advance_tax_paid=doc.advance_tax_paid,
            adjustments=adj_lines,
            rate=rate,
        ),
    )
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
        .options(selectinload(IncomeTaxComputation.adjustments))
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

    if payload.reseeds_from_books:
        book_profit, tds_credit = await _seed_books(
            db, doc.company_id, doc.from_date, doc.to_date
        )
        doc.book_profit = book_profit
        doc.tds_credit = tds_credit
    if payload.book_profit is not None:
        doc.book_profit = _q(payload.book_profit)
    if payload.tds_credit is not None:
        doc.tds_credit = _q(payload.tds_credit)
    if payload.advance_tax_paid is not None:
        doc.advance_tax_paid = _q(payload.advance_tax_paid)
    if payload.remarks is not None:
        doc.remarks = payload.remarks
    if payload.rate_table_id is not None:
        await _rate_table(db, payload.rate_table_id, doc.company_id)
        doc.rate_table_id = payload.rate_table_id
    elif payload.resolve_rate_from_settings:
        resolved = await _resolve_rate_for_doc(
            db,
            company_id=doc.company_id,
            assessment_year=doc.assessment_year,
            rate_table_id=None,
        )
        doc.rate_table_id = resolved.id if resolved else None
    if payload.adjustments is not None:
        adj_lines = await _replace_adjustments(db, doc, payload.adjustments, user)
    else:
        adj_lines = [(ln.direction, ln.amount) for ln in doc.adjustments]

    rate = await _rate_table(db, doc.rate_table_id, doc.company_id)
    _apply_pack(
        doc,
        _pack_from_lines(
            book_profit=doc.book_profit,
            tds_credit=doc.tds_credit,
            advance_tax_paid=doc.advance_tax_paid,
            adjustments=adj_lines,
            rate=rate,
        ),
    )
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
    rate = await _rate_table(db, doc.rate_table_id, doc.company_id)
    adj_lines = [(ln.direction, ln.amount) for ln in doc.adjustments]
    _apply_pack(
        doc,
        _pack_from_lines(
            book_profit=doc.book_profit,
            tds_credit=doc.tds_credit,
            advance_tax_paid=doc.advance_tax_paid,
            adjustments=adj_lines,
            rate=rate,
        ),
    )
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
