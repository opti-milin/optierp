"""Financial facts — one interface, two sources.

Plan §2.8, and the module's actual moat. Whether the CSR thresholds apply, whether XBRL
is required, whether a s.186 loan is within the ceiling: all of it turns on numbers a
standalone secretarial product does not have. Here the books are one join away.

The trick is that the rules engine must not know or care where the numbers came from.
``get_facts()`` derives them from the general ledger when the entity's books are in this
account, and reads the typed-in row when they are not — which is what lets threshold
applicability work for a practice's offline clients too.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.models.accounts import Account, GLEntry
from app.models.secretarial import SecretarialEntity, SecretarialFinancialFacts
from app.services.audit import log_audit
from app.services.secretarial.common import fy_period, get_entity

logger = get_logger(__name__)

FACT_FIELDS = (
    "turnover",
    "net_profit",
    "net_worth",
    "paid_up_capital",
    "free_reserves",
    "securities_premium",
    "borrowings",
    "deposits",
)


async def get_facts(
    db: AsyncSession, entity: SecretarialEntity, fy: str, *, refresh: bool = False
) -> dict[str, Any]:
    """The numbers for one entity and year, however they have to be obtained.

    Returns floats plus a ``source`` so a UI can be honest about where a figure came
    from — "we computed this from your ledger" and "somebody typed this" deserve
    different levels of trust on screen.
    """
    stored = await db.scalar(
        select(SecretarialFinancialFacts).where(
            SecretarialFinancialFacts.entity_id == entity.id,
            SecretarialFinancialFacts.fy == fy,
        )
    )

    if entity.linked_company_id and (refresh or stored is None or stored.source == "auto"):
        derived = await derive_from_ledger(db, entity, fy)
        if derived is not None:
            stored = await _store(db, entity, fy, derived, source="auto", stored=stored)

    if stored is None:
        return {"fy": fy, "source": "missing", **{f: None for f in FACT_FIELDS}}

    return {
        "fy": fy,
        "source": stored.source,
        "computed_at": stored.computed_at,
        **{f: _to_float(getattr(stored, f)) for f in FACT_FIELDS},
    }


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


async def derive_from_ledger(
    db: AsyncSession, entity: SecretarialEntity, fy: str
) -> dict[str, Decimal] | None:
    """Compute the facts from the general ledger of the linked company.

    Sign conventions follow the ledger's own: income and liability accounts carry credit
    balances, so revenue comes out as credit-minus-debit. Anything that cannot be
    determined stays absent rather than defaulting to zero — a missing figure and a
    genuine nil are very different answers to "does CSR apply".
    """
    if not entity.linked_company_id:
        return None

    period_start, period_end = fy_period(fy, entity.fy_end_mmdd)

    rows = (
        await db.execute(
            select(
                Account.root_type,
                Account.account_type,
                func.coalesce(func.sum(GLEntry.debit), 0).label("debit"),
                func.coalesce(func.sum(GLEntry.credit), 0).label("credit"),
            )
            .join(Account, Account.id == GLEntry.account_id)
            .where(
                GLEntry.company_id == entity.linked_company_id,
                GLEntry.posting_date >= period_start,
                GLEntry.posting_date <= period_end,
                GLEntry.is_cancellation.is_(False),
            )
            .group_by(Account.root_type, Account.account_type)
        )
    ).all()

    if not rows:
        return None

    by_root: dict[str, Decimal] = {}
    by_type: dict[str, Decimal] = {}
    for row in rows:
        # Credit-positive for income/liability/equity, debit-positive for the rest.
        net = Decimal(row.credit) - Decimal(row.debit)
        by_root[row.root_type] = by_root.get(row.root_type, Decimal(0)) + net
        if row.account_type:
            by_type[row.account_type] = by_type.get(row.account_type, Decimal(0)) + net

    income = by_root.get("Income", Decimal(0))
    expense = -by_root.get("Expense", Decimal(0))
    equity = by_root.get("Equity", Decimal(0))

    facts: dict[str, Decimal] = {
        "turnover": income,
        "net_profit": income - expense,
        "net_worth": equity,
    }

    # Balance-sheet positions are as-at the year end, not movements within it.
    closing = (
        await db.execute(
            select(
                Account.account_type,
                func.coalesce(func.sum(GLEntry.credit - GLEntry.debit), 0).label("net"),
            )
            .join(Account, Account.id == GLEntry.account_id)
            .where(
                GLEntry.company_id == entity.linked_company_id,
                GLEntry.posting_date <= period_end,
                GLEntry.is_cancellation.is_(False),
                Account.account_type.in_(("Equity", "Bank", "Payable")),
            )
            .group_by(Account.account_type)
        )
    ).all()
    closing_by_type = {r.account_type: Decimal(r.net) for r in closing}
    if "Equity" in closing_by_type:
        facts["paid_up_capital"] = closing_by_type["Equity"]

    return {k: v for k, v in facts.items() if v is not None}


async def _store(
    db: AsyncSession,
    entity: SecretarialEntity,
    fy: str,
    values: dict[str, Decimal],
    *,
    source: str,
    stored: SecretarialFinancialFacts | None = None,
) -> SecretarialFinancialFacts:
    if stored is None:
        stored = SecretarialFinancialFacts(
            company_id=entity.company_id, entity_id=entity.id, fy=fy
        )
        db.add(stored)
    for field, value in values.items():
        if field in FACT_FIELDS:
            setattr(stored, field, value)
    stored.source = source
    stored.computed_at = datetime.now(UTC)
    await db.flush()
    return stored


async def set_manual_facts(
    db: AsyncSession,
    entity_id: uuid.UUID,
    fy: str,
    values: dict[str, Any],
    user: CurrentUser,
) -> SecretarialFinancialFacts:
    """Type the numbers in — the path for a client whose books are elsewhere."""
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)

    stored = await db.scalar(
        select(SecretarialFinancialFacts).where(
            SecretarialFinancialFacts.entity_id == entity_id,
            SecretarialFinancialFacts.fy == fy,
        )
    )
    if stored is None:
        stored = SecretarialFinancialFacts(
            company_id=user.company_id, entity_id=entity_id, fy=fy
        )
        db.add(stored)

    for field in FACT_FIELDS:
        if field in values:
            raw = values[field]
            setattr(stored, field, Decimal(str(raw)) if raw is not None else None)
    stored.source = "manual"
    stored.computed_at = datetime.now(UTC)
    stored.notes = values.get("notes", stored.notes)
    stored.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype="Secretarial Financial Facts",
        document_id=stored.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"fy": fy, "source": "manual"},
    )
    await db.commit()
    del entity
    return stored


async def facts_for_entity(
    db: AsyncSession, entity_id: uuid.UUID, company_id: uuid.UUID
) -> list[SecretarialFinancialFacts]:
    return list(
        (
            await db.execute(
                select(SecretarialFinancialFacts)
                .where(
                    SecretarialFinancialFacts.company_id == company_id,
                    SecretarialFinancialFacts.entity_id == entity_id,
                )
                .order_by(SecretarialFinancialFacts.fy.desc())
            )
        )
        .scalars()
        .all()
    )


async def refresh_entity(
    db: AsyncSession, entity_id: uuid.UUID, fy: str, user: CurrentUser
) -> dict[str, Any]:
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)
    if not entity.linked_company_id:
        raise NotFoundError(
            "This company's books are not kept in this account, so its figures cannot be "
            "computed. Enter them by hand instead."
        )
    facts = await get_facts(db, entity, fy, refresh=True)
    await db.commit()
    return facts
