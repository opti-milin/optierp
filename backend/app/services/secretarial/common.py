"""Shared helpers for the Secretarial module: entity lookup and financial-year maths.

The FY helpers are the fiddly part. An Indian FY label like ``2025-26`` means
1 Apr 2025 → 31 Mar 2026 for the default 31-March year end, but the module supports
other year ends (a foreign-held subsidiary may align with its parent under s.2(41)),
so nothing here hardcodes March.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.secretarial import SecretarialEntity


async def get_entity(
    db: AsyncSession, entity_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialEntity:
    """Fetch an entity, enforcing tenant ownership explicitly as well as via RLS."""
    entity = await db.get(SecretarialEntity, entity_id)
    if entity is None or entity.company_id != company_id:
        raise NotFoundError("Secretarial entity not found")
    return entity


async def list_entity_ids(db: AsyncSession, company_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(SecretarialEntity.id).where(
        SecretarialEntity.company_id == company_id,
        SecretarialEntity.status == "active",
    )
    return list((await db.execute(stmt)).scalars().all())


# --- Financial year -------------------------------------------------------------


def add_months(anchor: date, months: int) -> date:
    """Shift by whole months, clamping the day to the target month's length.

    31 Jan + 1 month is 28/29 Feb, not an error — the statutory phrasing is always
    "within N months", which is read this way.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def parse_fy(fy: str) -> int:
    """``"2025-26"`` → 2025 (the year the financial year starts in)."""
    try:
        start_year = int(fy.split("-")[0])
    except (ValueError, IndexError) as exc:
        raise ValidationError(f"Financial year must look like '2025-26', got {fy!r}", field="fy") from exc
    if not 1900 <= start_year <= 2200:
        raise ValidationError(f"Implausible financial year {fy!r}", field="fy")
    return start_year


def fy_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def fy_period(fy: str, fy_end_mmdd: str = "0331") -> tuple[date, date]:
    """Start and end dates of ``fy`` for an entity with the given year end.

    A year end in Jan–Mar closes in the *following* calendar year (the Indian
    default); an Apr–Dec year end closes in the same year as the label.
    """
    start_year = parse_fy(fy)
    month, day = int(fy_end_mmdd[:2]), int(fy_end_mmdd[2:])
    end_year = start_year + 1 if month <= 3 else start_year
    day = min(day, calendar.monthrange(end_year, month)[1])
    period_end = date(end_year, month, day)
    period_start = add_months(period_end, -12) + timedelta(days=1)
    return period_start, period_end


def current_fy(on: date, fy_end_mmdd: str = "0331") -> str:
    """The financial year that ``on`` falls inside."""
    month, day = int(fy_end_mmdd[:2]), int(fy_end_mmdd[2:])
    this_year_end = date(on.year, month, min(day, calendar.monthrange(on.year, month)[1]))
    # Which calendar year does the FY containing `on` close in?
    end_year = on.year if on <= this_year_end else on.year + 1
    # And the label is keyed to the year it *opened* in.
    return fy_label(end_year - 1 if month <= 3 else end_year)


def agm_due_date(period_end: date, *, is_first_fy: bool = False) -> date:
    """s.96 — AGM within 6 months of FY close, 9 months for the first AGM."""
    return add_months(period_end, 9 if is_first_fy else 6)


def compute_due_date(
    formula: dict | None,
    *,
    period_start: date,
    period_end: date,
    is_first_fy: bool = False,
) -> date | None:
    """Resolve a rule's ``due_formula`` into a concrete date.

    Supported shapes (all optional keys combine, applied in this order):

        {"anchor": "fy_end"|"fy_start"|"agm_due", "offset_days": N}
        {"anchor": "fy_end", "offset_months": N}
        {"anchor": "fy_end", "month": 10, "day": 30}   -> that date in the closing year

    Returning ``None`` (no formula, or an event-based rule with no event yet) means
    "cannot be scheduled", and the caller skips the row rather than inventing a date.
    """
    if not formula:
        return None

    anchor_name = formula.get("anchor", "fy_end")
    if anchor_name == "fy_start":
        anchor = period_start
    elif anchor_name == "agm_due":
        anchor = agm_due_date(period_end, is_first_fy=is_first_fy)
    elif anchor_name == "fy_end":
        anchor = period_end
    else:
        # Event-based rules are instantiated when the event happens, not by the
        # FY sweep.
        return None

    month, day = formula.get("month"), formula.get("day")
    if month and day:
        year = anchor.year
        clamped = min(int(day), calendar.monthrange(year, int(month))[1])
        anchor = date(year, int(month), clamped)

    if formula.get("offset_months"):
        anchor = add_months(anchor, int(formula["offset_months"]))
    if formula.get("offset_days"):
        anchor = anchor + timedelta(days=int(formula["offset_days"]))
    return anchor


def is_applicable(applicability: dict | None, entity: SecretarialEntity) -> bool:
    """Phase-1 applicability: a static filter over entity attributes.

    Phase 4 replaces this with a predicate AST evaluated against ledger-derived
    financial facts. Keeping the column and the call site identical now means that
    upgrade touches one function, not every rule.
    """
    if not applicability:
        return True

    kinds = applicability.get("kinds")
    if kinds and entity.kind not in kinds:
        return False

    classes = applicability.get("classes")
    if classes and entity.entity_class not in classes:
        return False

    excluded = applicability.get("exclude_classes")
    if excluded and entity.entity_class in excluded:
        return False

    listed = applicability.get("listed")
    if listed is not None and bool(entity.is_listed) != bool(listed):
        return False

    return True
