"""Tax challan CRUD + GL posting through services/gl.py."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.tax_credits import TaxChallan
from app.schemas.taxation import TaxChallanCreate, TaxChallanOut, TaxChallanUpdate
from app.services import gl
from app.services.accounts_common import require_draft, require_submitted
from app.services.taxation.accounts import resolve_bank_account, resolve_tax_payable_account
from app.services.taxation.kernel.money import money, q

CHALLAN_TYPES = frozenset({"AdvanceTax", "SelfAssessment", "RegularAssessment"})
VOUCHER_TYPE = "Tax Challan"


async def list_challans(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    ay_code: str | None = None,
) -> list[TaxChallan]:
    stmt = (
        select(TaxChallan)
        .where(TaxChallan.company_id == company_id)
        .order_by(TaxChallan.deposit_date.desc(), TaxChallan.creation.desc())
    )
    if ay_code:
        stmt = stmt.where(TaxChallan.ay_code == ay_code)
    return list((await db.scalars(stmt)).all())


async def get_challan(
    db: AsyncSession, company_id: uuid.UUID, challan_id: uuid.UUID
) -> TaxChallan:
    row = await db.scalar(
        select(TaxChallan).where(
            TaxChallan.id == challan_id, TaxChallan.company_id == company_id
        )
    )
    if row is None:
        raise NotFoundError("Tax challan not found")
    return row


async def create_challan(
    db: AsyncSession, payload: TaxChallanCreate, user: CurrentUser
) -> TaxChallan:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    if payload.challan_type not in CHALLAN_TYPES:
        raise ValidationError(
            f"challan_type must be one of {sorted(CHALLAN_TYPES)}",
            field="challan_type",
            code="invalid_challan_type",
        )
    amount = q(money(payload.amount))
    if amount <= 0:
        raise ValidationError("amount must be positive", field="amount")

    bank = await resolve_bank_account(db, user.company_id, payload.bank_account_id)
    payable = await resolve_tax_payable_account(
        db, user.company_id, payload.tax_payable_account_id
    )
    name = await get_next_name(db, "TCH-.YYYY.-", user.company_id, on_date=payload.deposit_date)

    row = TaxChallan(
        company_id=user.company_id,
        name=name,
        ay_code=payload.ay_code,
        challan_type=payload.challan_type,
        bsr_code=payload.bsr_code.strip(),
        challan_serial=payload.challan_serial.strip(),
        cin=(payload.cin or "").strip() or None,
        deposit_date=payload.deposit_date,
        major_head=payload.major_head,
        minor_head=payload.minor_head,
        amount=amount,
        bank_account_id=bank.id,
        tax_payable_account_id=payable.id,
        computation_id=payload.computation_id,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_challan(
    db: AsyncSession,
    challan_id: uuid.UUID,
    payload: TaxChallanUpdate,
    user: CurrentUser,
) -> TaxChallan:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    row = await get_challan(db, user.company_id, challan_id)
    require_draft(row.docstatus)

    if payload.challan_type is not None:
        if payload.challan_type not in CHALLAN_TYPES:
            raise ValidationError(
                f"challan_type must be one of {sorted(CHALLAN_TYPES)}",
                field="challan_type",
            )
        row.challan_type = payload.challan_type
    if payload.bsr_code is not None:
        row.bsr_code = payload.bsr_code.strip()
    if payload.challan_serial is not None:
        row.challan_serial = payload.challan_serial.strip()
    if payload.cin is not None:
        row.cin = payload.cin.strip() or None
    if payload.deposit_date is not None:
        row.deposit_date = payload.deposit_date
    if payload.major_head is not None:
        row.major_head = payload.major_head
    if payload.minor_head is not None:
        row.minor_head = payload.minor_head
    if payload.amount is not None:
        amount = q(money(payload.amount))
        if amount <= 0:
            raise ValidationError("amount must be positive", field="amount")
        row.amount = amount
    if payload.bank_account_id is not None:
        bank = await resolve_bank_account(db, user.company_id, payload.bank_account_id)
        row.bank_account_id = bank.id
    if payload.tax_payable_account_id is not None:
        payable = await resolve_tax_payable_account(
            db, user.company_id, payload.tax_payable_account_id
        )
        row.tax_payable_account_id = payable.id
    if payload.computation_id is not None:
        row.computation_id = payload.computation_id
    if payload.remarks is not None:
        row.remarks = payload.remarks

    row.modified_by = user.id
    await db.commit()
    await db.refresh(row)
    return row


async def submit_challan(
    db: AsyncSession, challan_id: uuid.UUID, user: CurrentUser
) -> TaxChallan:
    """Post Dr Tax Payable / Cr Bank (cash tax payment) via append-only GL."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    row = await get_challan(db, user.company_id, challan_id)
    require_draft(row.docstatus)
    amount = q(money(row.amount))

    await gl.make_gl_entries(
        db,
        company_id=row.company_id,
        voucher_type=VOUCHER_TYPE,
        voucher_id=row.id,
        voucher_no=row.name,
        posting_date=row.deposit_date,
        rows=[
            gl.GLRow(
                account_id=row.tax_payable_account_id,
                debit=amount,
                remarks=f"{row.challan_type} BSR {row.bsr_code}/{row.challan_serial}",
            ),
            gl.GLRow(
                account_id=row.bank_account_id,
                credit=amount,
                remarks=f"Challan CIN {row.cin or '-'}",
            ),
        ],
        user_id=user.id,
        remarks=row.remarks or f"Tax challan {row.name}",
    )
    row.docstatus = DOCSTATUS_SUBMITTED
    row.modified_by = user.id
    await db.commit()
    await db.refresh(row)
    return row


async def cancel_challan(
    db: AsyncSession, challan_id: uuid.UUID, user: CurrentUser
) -> TaxChallan:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    row = await get_challan(db, user.company_id, challan_id)
    require_submitted(row.docstatus)
    await gl.make_reverse_gl_entries(
        db, voucher_type=VOUCHER_TYPE, voucher_id=row.id, user_id=user.id
    )
    row.docstatus = DOCSTATUS_CANCELLED
    row.modified_by = user.id
    await db.commit()
    await db.refresh(row)
    return row


def challan_out(row: TaxChallan) -> TaxChallanOut:
    return TaxChallanOut.model_validate(row)
