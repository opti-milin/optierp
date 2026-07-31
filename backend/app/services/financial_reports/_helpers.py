"""Financial reports — shared helpers (balances, rollup, statement rows, aging bucket)."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import (
    Account,
    GLEntry,
)
from app.schemas.accounts import (
    FinancialStatementRow,
)

ZERO = Decimal("0")
async def _account_map(db: AsyncSession, company_id: uuid.UUID) -> dict[uuid.UUID, Account]:
    accounts = (
        (await db.execute(select(Account).where(Account.company_id == company_id))).scalars().all()
    )
    return {a.id: a for a in accounts}


async def _balances(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    cost_center_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """(debit, credit) sums per leaf account in the window.

    Optional ``cost_center_id`` filters GL rows posted to that cost center
    (lines with a null cost center are excluded when the filter is set).
    """
    stmt: Select = select(
        GLEntry.account_id,
        func.coalesce(func.sum(GLEntry.debit), 0),
        func.coalesce(func.sum(GLEntry.credit), 0),
    ).where(GLEntry.company_id == company_id)
    if from_date:
        stmt = stmt.where(GLEntry.posting_date >= from_date)
    if to_date:
        stmt = stmt.where(GLEntry.posting_date <= to_date)
    if cost_center_id is not None:
        stmt = stmt.where(GLEntry.cost_center_id == cost_center_id)
    rows = (await db.execute(stmt.group_by(GLEntry.account_id))).all()
    return {account_id: (Decimal(d), Decimal(c)) for account_id, d, c in rows}


def _rollup(
    accounts: dict[uuid.UUID, Account], leaf_values: dict[uuid.UUID, Decimal]
) -> dict[uuid.UUID, Decimal]:
    """Roll leaf balances up the tree using path prefixes."""
    totals: dict[uuid.UUID, Decimal] = {a_id: ZERO for a_id in accounts}
    by_path = {a.path: a.id for a in accounts.values()}
    for leaf_id, value in leaf_values.items():
        leaf = accounts.get(leaf_id)
        if leaf is None or value == ZERO:
            continue
        labels = leaf.path.split(".")
        for i in range(1, len(labels) + 1):
            ancestor_id = by_path.get(".".join(labels[:i]))
            if ancestor_id is not None:
                totals[ancestor_id] += value
    return totals


# --- General Ledger ----------------------------------------------------------------


def _statement_rows(
    accounts: dict[uuid.UUID, Account],
    rolled: dict[uuid.UUID, Decimal],
    root_types: tuple[str, ...],
    *,
    credit_positive: bool,
) -> list[FinancialStatementRow]:
    rows: list[FinancialStatementRow] = []
    for account in sorted(accounts.values(), key=lambda a: a.path):
        if account.root_type not in root_types:
            continue
        amount = rolled.get(account.id, ZERO)
        if amount == ZERO:
            continue
        if credit_positive:
            amount = -amount
        rows.append(
            FinancialStatementRow(
                account_id=account.id,
                account_name=account.account_name,
                root_type=account.root_type,
                is_group=account.is_group,
                indent=account.path.count("."),
                amount=amount,
            )
        )
    return rows


def _bucket(age: int, outstanding: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if age <= 30:
        return outstanding, ZERO, ZERO, ZERO
    if age <= 60:
        return ZERO, outstanding, ZERO, ZERO
    if age <= 90:
        return ZERO, ZERO, outstanding, ZERO
    return ZERO, ZERO, ZERO, outstanding


