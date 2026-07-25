"""Item Alternative — allowed substitutes for Finish when BOM allows alt."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ValidationError
from app.models.stock import Item, ItemAlternative


async def list_alternatives_for_item(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
) -> list[Item]:
    """Items that may replace ``item_id`` on Finish.

    Two-way links are stored as a second row (B→A), so only ``item_id`` matches
    are considered — no implicit reverse from a one-way A→B row.
    """
    rows = (
        await db.scalars(
            select(ItemAlternative)
            .where(
                ItemAlternative.company_id == company_id,
                ItemAlternative.item_id == item_id,
            )
            .options(selectinload(ItemAlternative.alternative_item))
        )
    ).all()

    out: dict[uuid.UUID, Item] = {}
    for row in rows:
        if row.alternative_item is not None:
            out[row.alternative_item_id] = row.alternative_item
    return sorted(out.values(), key=lambda i: i.item_code or "")


async def assert_valid_substitute(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    planned_item_id: uuid.UUID,
    substitute_item_id: uuid.UUID,
    planned_item_code: str | None = None,
) -> None:
    """Reject a Finish substitute that is not registered as an Item Alternative."""
    allowed = await list_alternatives_for_item(db, company_id, planned_item_id)
    if any(a.id == substitute_item_id for a in allowed):
        return
    label = planned_item_code or str(planned_item_id)
    if not allowed:
        raise ValidationError(
            f"No Item Alternative is configured for '{label}'. "
            "Add one under Stock → Item Alternative, then retry.",
            field="substitute_item_id",
        )
    raise ValidationError(
        f"'{label}' cannot be substituted with that item — "
        "only registered Item Alternatives are allowed",
        field="substitute_item_id",
    )
