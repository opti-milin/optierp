"""Resolve leaf GL accounts for tax challans and current-tax provision."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.accounts import Account
from app.models.core import Company

_LIABILITY_NAMES = (
    "Income Tax Payable",
    "Provision for Taxation",
    "Provision for Income Tax",
    "Income Tax",
)
_EXPENSE_NAMES = (
    "Income Tax Expense",
    "Income Tax",
    "Current Tax Expense",
    "Provision for Taxation",
)


async def _leaf_by_name(
    db: AsyncSession, company_id: uuid.UUID, names: tuple[str, ...]
) -> Account | None:
    for name in names:
        row = await db.scalar(
            select(Account).where(
                Account.company_id == company_id,
                Account.account_name == name,
                Account.is_group.is_(False),
                Account.disabled.is_(False),
            )
        )
        if row is not None:
            return row
    return None


async def require_account(
    db: AsyncSession, company_id: uuid.UUID, account_id: uuid.UUID, *, field: str
) -> Account:
    row = await db.get(Account, account_id)
    if row is None or row.company_id != company_id:
        raise ValidationError("Account not found for this company", field=field, code="account_missing")
    if row.is_group or row.disabled:
        raise ValidationError("Account must be an active leaf", field=field, code="account_invalid")
    return row


async def resolve_bank_account(
    db: AsyncSession,
    company_id: uuid.UUID,
    account_id: uuid.UUID | None,
) -> Account:
    if account_id is not None:
        return await require_account(db, company_id, account_id, field="bank_account_id")
    company = await db.get(Company, company_id)
    if company and company.default_bank_account_id:
        return await require_account(
            db, company_id, company.default_bank_account_id, field="bank_account_id"
        )
    raise ValidationError(
        "bank_account_id required (no company default bank)",
        field="bank_account_id",
        code="bank_required",
    )


async def resolve_tax_payable_account(
    db: AsyncSession,
    company_id: uuid.UUID,
    account_id: uuid.UUID | None,
) -> Account:
    if account_id is not None:
        return await require_account(db, company_id, account_id, field="tax_payable_account_id")
    found = await _leaf_by_name(db, company_id, _LIABILITY_NAMES)
    if found is not None:
        return found
    # Any Tax-typed liability leaf under Duties and Taxes
    row = await db.scalar(
        select(Account).where(
            Account.company_id == company_id,
            Account.account_type == "Tax",
            Account.root_type == "Liability",
            Account.is_group.is_(False),
            Account.disabled.is_(False),
        )
    )
    if row is not None:
        return row
    raise ValidationError(
        "tax_payable_account_id required — create 'Income Tax Payable' or pass an account",
        field="tax_payable_account_id",
        code="tax_payable_required",
    )


async def resolve_tax_expense_account(
    db: AsyncSession,
    company_id: uuid.UUID,
    account_id: uuid.UUID | None,
) -> Account:
    if account_id is not None:
        return await require_account(db, company_id, account_id, field="provision_expense_account_id")
    found = await _leaf_by_name(db, company_id, _EXPENSE_NAMES)
    if found is not None:
        return found
    company = await db.get(Company, company_id)
    if company and company.default_expense_account_id:
        return await require_account(
            db, company_id, company.default_expense_account_id, field="provision_expense_account_id"
        )
    raise ValidationError(
        "provision_expense_account_id required — create 'Income Tax Expense' or pass an account",
        field="provision_expense_account_id",
        code="tax_expense_required",
    )
