"""Append-only tax credit entries + claimed totals for the pipeline."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.tax_credits import TaxCreditEntry
from app.schemas.taxation import TaxCreditEntryCreate, TaxCreditEntryOut
from app.services.taxation.kernel.money import ZERO, money, q

CREDIT_KINDS = frozenset(
    {"TDS", "TCS", "AdvanceTax", "SelfAssessment", "SalaryTDS", "Other"}
)


async def list_credits(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    ay_code: str | None = None,
    computation_id: uuid.UUID | None = None,
) -> list[TaxCreditEntry]:
    stmt = (
        select(TaxCreditEntry)
        .where(TaxCreditEntry.company_id == company_id, TaxCreditEntry.docstatus != 2)
        .order_by(TaxCreditEntry.creation.desc())
    )
    if ay_code:
        stmt = stmt.where(TaxCreditEntry.ay_code == ay_code)
    if computation_id:
        stmt = stmt.where(TaxCreditEntry.computation_id == computation_id)
    return list((await db.scalars(stmt)).all())


async def get_credit(
    db: AsyncSession, company_id: uuid.UUID, credit_id: uuid.UUID
) -> TaxCreditEntry:
    row = await db.scalar(
        select(TaxCreditEntry).where(
            TaxCreditEntry.id == credit_id, TaxCreditEntry.company_id == company_id
        )
    )
    if row is None:
        raise NotFoundError("Tax credit entry not found")
    return row


async def create_credit(
    db: AsyncSession, payload: TaxCreditEntryCreate, user: CurrentUser
) -> TaxCreditEntry:
    """Insert a credit row — never UPDATE amounts on existing rows for history."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    if payload.credit_kind not in CREDIT_KINDS:
        raise ValidationError(
            f"credit_kind must be one of {sorted(CREDIT_KINDS)}",
            field="credit_kind",
            code="invalid_credit_kind",
        )
    credited = q(money(payload.amount_credited))
    claimed = q(money(payload.amount_claimed if payload.amount_claimed is not None else credited))
    if credited < ZERO or claimed < ZERO:
        raise ValidationError("amounts must be non-negative", field="amount_credited")
    if claimed > credited:
        raise ValidationError(
            "amount_claimed cannot exceed amount_credited",
            field="amount_claimed",
            code="claim_exceeds_credit",
        )

    row = TaxCreditEntry(
        company_id=user.company_id,
        ay_code=payload.ay_code,
        computation_id=payload.computation_id,
        credit_kind=payload.credit_kind,
        deductor_tan=(payload.deductor_tan or "").strip().upper() or None,
        deductor_name=payload.deductor_name,
        section_code=payload.section_code,
        amount_credited=credited,
        amount_claimed=claimed,
        challan_id=payload.challan_id,
        reconciliation_status="Unmatched",
        source_refs=dict(payload.source_refs or {}),
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def void_credit(
    db: AsyncSession, credit_id: uuid.UUID, user: CurrentUser
) -> TaxCreditEntry:
    """Soft-void via docstatus=2 — never DELETE credit rows."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    row = await get_credit(db, user.company_id, credit_id)
    if row.docstatus == 2:
        raise ValidationError("Already voided", code="already_voided")
    row.docstatus = 2
    row.modified_by = user.id
    await db.commit()
    await db.refresh(row)
    return row


async def claimed_credits_total(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    ay_code: str,
    computation_id: uuid.UUID | None = None,
) -> Decimal:
    """Sum amount_claimed for active credits (optionally scoped to a computation)."""
    stmt = select(func.coalesce(func.sum(TaxCreditEntry.amount_claimed), 0)).where(
        TaxCreditEntry.company_id == company_id,
        TaxCreditEntry.ay_code == ay_code,
        TaxCreditEntry.docstatus != 2,
    )
    if computation_id is not None:
        stmt = stmt.where(
            (TaxCreditEntry.computation_id == computation_id)
            | (TaxCreditEntry.computation_id.is_(None))
        )
    total = await db.scalar(stmt)
    return q(money(total or 0))


def credit_out(row: TaxCreditEntry) -> TaxCreditEntryOut:
    return TaxCreditEntryOut.model_validate(row)
