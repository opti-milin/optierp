"""Multi-level BOM explosion — cycle-guarded, phantom-aware.

Used by Work Order create (phantoms explode; stocked sub-assemblies stay as one line),
BOM Explorer (full flatten to leaf raws), BOM Stock report, and recursive cost rollup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ValidationError
from app.models.base import DOCSTATUS_SUBMITTED
from app.models.manufacturing import BOM
from app.models.stock import Item
from app.services.manufacturing_common import resolve_valuation_rate

ZERO = Decimal("0")


@dataclass(frozen=True)
class ExplodedComponent:
    """One leaf (or stocked sub-assembly) line after explosion for a target FG qty."""

    item_id: uuid.UUID
    stock_qty: Decimal
    rate: Decimal
    source_warehouse_id: uuid.UUID | None
    allow_alternative_item: bool
    level: int
    item_code: str | None = None
    item_name: str | None = None


@dataclass(frozen=True)
class ExplodedScrap:
    """Scrap / by-product qty for a target FG qty (scaled from the root BOM only)."""

    item_id: uuid.UUID
    stock_qty: Decimal
    rate: Decimal
    stock_warehouse_id: uuid.UUID | None
    item_code: str | None = None
    item_name: str | None = None


async def find_item_bom(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    exclude_bom_id: uuid.UUID | None = None,
) -> BOM | None:
    """Resolve the submitted/active BOM to use for a production item.

    Prefers ``Item.default_bom_id`` when it is still submitted + active, else the
    item's ``is_default`` BOM, else any submitted active BOM for that item.
    """
    item = await db.get(Item, item_id)
    if item is not None and item.company_id == company_id and item.default_bom_id is not None:
        if exclude_bom_id is None or item.default_bom_id != exclude_bom_id:
            bom = await db.scalar(
                select(BOM)
                .options(selectinload(BOM.items), selectinload(BOM.scrap_items))
                .where(
                    BOM.id == item.default_bom_id,
                    BOM.company_id == company_id,
                    BOM.docstatus == DOCSTATUS_SUBMITTED,
                    BOM.is_active.is_(True),
                )
            )
            if bom is not None:
                return bom

    stmt = (
        select(BOM)
        .options(selectinload(BOM.items), selectinload(BOM.scrap_items))
        .where(
            BOM.company_id == company_id,
            BOM.production_item_id == item_id,
            BOM.docstatus == DOCSTATUS_SUBMITTED,
            BOM.is_active.is_(True),
        )
        .order_by(BOM.is_default.desc(), BOM.creation.desc())
        .limit(1)
    )
    if exclude_bom_id is not None:
        stmt = stmt.where(BOM.id != exclude_bom_id)
    return await db.scalar(stmt)


async def assert_no_bom_cycle(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    production_item_id: uuid.UUID,
    component_item_ids: set[uuid.UUID],
    exclude_bom_id: uuid.UUID | None = None,
) -> None:
    """Reject a BOM whose components (via nested default BOMs) eventually reach the FG.

    Walks only submitted/active child BOMs — draft nesting is ignored for cycle checks
    (a draft cannot yet be used by a Work Order).
    """
    stack = list(component_item_ids)
    seen: set[uuid.UUID] = set()
    while stack:
        item_id = stack.pop()
        if item_id == production_item_id:
            raise ValidationError(
                "Multi-level BOM cycle detected — a component's BOM eventually includes "
                "this finished good",
                field="items",
            )
        if item_id in seen:
            continue
        seen.add(item_id)
        child = await find_item_bom(
            db, company_id, item_id, exclude_bom_id=exclude_bom_id
        )
        if child is None:
            continue
        for row in child.items:
            stack.append(row.item_id)


async def explode_bom(
    db: AsyncSession,
    bom: BOM,
    for_qty: Decimal,
    *,
    flatten_all: bool = False,
    _ancestors: frozenset[uuid.UUID] | None = None,
    _level: int = 0,
) -> list[ExplodedComponent]:
    """Explode ``bom`` for ``for_qty`` finished units into component lines.

    * ``flatten_all=False`` (Work Order / BOM Stock): phantom child BOMs explode through;
      non-phantom sub-assemblies stay as a single stocked line (valued at child BOM cost).
    * ``flatten_all=True`` (BOM Explorer): every nested BOM is walked to leaf raw materials.
    """
    if for_qty <= ZERO:
        return []
    if bom.quantity <= ZERO:
        raise ValidationError("BOM batch quantity must be greater than zero", field="quantity")

    ancestors = _ancestors or frozenset()
    if bom.production_item_id in ancestors:
        raise ValidationError(
            "Multi-level BOM cycle detected while exploding",
            field="bom_id",
        )
    next_ancestors = ancestors | {bom.production_item_id}
    scale = for_qty / bom.quantity

    # Aggregate duplicate leaves (same item + warehouse + alt flag) so WO items stay clean.
    buckets: dict[tuple, ExplodedComponent] = {}

    def _add(row: ExplodedComponent) -> None:
        key = (row.item_id, row.source_warehouse_id, row.allow_alternative_item)
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = row
        else:
            new_qty = existing.stock_qty + row.stock_qty
            buckets[key] = ExplodedComponent(
                item_id=existing.item_id,
                stock_qty=new_qty,
                rate=existing.rate,
                source_warehouse_id=existing.source_warehouse_id,
                allow_alternative_item=existing.allow_alternative_item or row.allow_alternative_item,
                level=min(existing.level, row.level),
                item_code=existing.item_code or row.item_code,
                item_name=existing.item_name or row.item_name,
            )

    for comp in bom.items:
        need = (comp.stock_qty * scale).quantize(Decimal("0.000001"))
        if need <= ZERO:
            continue
        child_bom = await find_item_bom(db, bom.company_id, comp.item_id)
        explode_child = child_bom is not None and (flatten_all or child_bom.is_phantom)
        if explode_child and child_bom is not None:
            nested = await explode_bom(
                db,
                child_bom,
                need,  # child BOM yields `child_bom.quantity` of the sub-item per batch;
                # `need` is stock qty of the sub-item required → treat as for_qty of child FG
                flatten_all=flatten_all,
                _ancestors=next_ancestors,
                _level=_level + 1,
            )
            for n in nested:
                _add(n)
            continue

        rate = comp.rate
        if child_bom is not None and not child_bom.is_phantom:
            # stocked sub-assembly: prefer rolled-up BOM cost when present
            rate = child_bom.cost_per_unit if child_bom.cost_per_unit > ZERO else comp.rate

        _add(
            ExplodedComponent(
                item_id=comp.item_id,
                stock_qty=need,
                rate=rate,
                source_warehouse_id=comp.source_warehouse_id,
                allow_alternative_item=bool(comp.allow_alternative_item),
                level=_level,
                item_code=comp.item_code,
                item_name=comp.item_name,
            )
        )

    return sorted(buckets.values(), key=lambda r: (r.level, str(r.item_code or ""), str(r.item_id)))


async def explode_scrap(
    db: AsyncSession, bom: BOM, for_qty: Decimal
) -> list[ExplodedScrap]:
    """Scale root-BOM scrap rows to ``for_qty`` finished units (scrap is not nested)."""
    if for_qty <= ZERO or bom.quantity <= ZERO:
        return []
    scale = for_qty / bom.quantity
    rows: list[ExplodedScrap] = []
    for scrap in bom.scrap_items:
        qty = (scrap.stock_qty * scale).quantize(Decimal("0.000001"))
        if qty <= ZERO:
            continue
        rows.append(
            ExplodedScrap(
                item_id=scrap.item_id,
                stock_qty=qty,
                rate=scrap.rate,
                stock_warehouse_id=scrap.stock_warehouse_id,
                item_code=scrap.item_code,
                item_name=scrap.item_name,
            )
        )
    return rows


async def resolve_component_rate(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    override: Decimal | None = None,
    visited: frozenset[uuid.UUID] | None = None,
) -> Decimal:
    """Rate for a BOM component: override → child BOM cost_per_unit → live valuation."""
    if override is not None:
        return override
    seen = visited or frozenset()
    if item_id in seen:
        item = await db.get(Item, item_id)
        if item is None:
            return ZERO
        return await resolve_valuation_rate(db, item)
    child = await find_item_bom(db, company_id, item_id)
    if child is not None and child.cost_per_unit > ZERO:
        return child.cost_per_unit
    item = await db.get(Item, item_id)
    if item is None:
        return ZERO
    return await resolve_valuation_rate(db, item)
