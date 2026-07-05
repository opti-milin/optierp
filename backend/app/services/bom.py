"""BOM service — the recipe for a finished good (bespoke document, no GL).

A BOM lists the components (and a flat operating cost) needed to build ``quantity`` units of
a production item, and snapshots the resulting cost from the components' current valuation.
It never moves stock or posts to the GL — it is the spec a Work Order consumes.

Costing (master §4):
    raw_material_cost = Σ (component.stock_qty × component.rate)
    total_cost        = raw_material_cost + operating_cost
    cost_per_unit     = total_cost / quantity          (derived, not stored)

``rate`` is sourced from each component's current valuation (``resolve_valuation_rate``)
unless the caller overrides it. The snapshot is recomputed on create/update and on demand
("Update Cost").
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.manufacturing import BOM, BOMItem
from app.schemas.manufacturing import BOMCreate, BOMItemIn, BOMUpdate
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.manufacturing_common import MFG_NAMING_SERIES, resolve_valuation_rate
from app.services.pagination import paginate
from app.services.stock_common import (
    get_item,
    get_items,
    get_warehouse,
    require_stock_item,
    resolve_conversion_factor,
)

ZERO = Decimal("0")


# --- helpers -------------------------------------------------------------------------


async def _build_item_rows(
    db: AsyncSession, company_id: uuid.UUID, rows: list[BOMItemIn],
    *, production_item_id: uuid.UUID,
) -> tuple[list[BOMItem], Decimal]:
    """Resolve each component line (uom → factor → stock_qty, rate, amount) and return the
    child rows plus the raw-material cost total. Rejects non-stock components, unknown
    warehouses, and the finished good listed as its own component (recursion — it would
    feed the FG's own valuation into its cost at every Update Cost)."""
    items = await get_items(db, {r.item_id for r in rows}, company_id)
    built: list[BOMItem] = []
    raw_material_cost = ZERO
    for idx, r in enumerate(rows, start=1):
        item = items[r.item_id]
        require_stock_item(item)
        if r.item_id == production_item_id:
            raise ValidationError(
                f"Component row {idx}: '{item.item_code}' is the BOM's own finished good — "
                "a BOM cannot contain itself",
                field="items",
            )
        uom = r.uom or item.stock_uom
        factor = resolve_conversion_factor(item, uom)
        stock_qty = r.qty * factor
        if r.source_warehouse_id is not None:
            await get_warehouse(db, r.source_warehouse_id, company_id)
        rate = r.rate if r.rate is not None else await resolve_valuation_rate(db, item)
        amount = stock_qty * rate
        raw_material_cost += amount
        built.append(
            BOMItem(
                idx=idx,
                item_id=r.item_id,
                qty=r.qty,
                uom=uom,
                conversion_factor=factor,
                stock_qty=stock_qty,
                rate=rate,
                amount=amount,
                source_warehouse_id=r.source_warehouse_id,
            )
        )
    return built, raw_material_cost


async def _clear_other_defaults(
    db: AsyncSession, company_id: uuid.UUID, production_item_id: uuid.UUID, keep_id: uuid.UUID
) -> None:
    """Ensure at most one default BOM per production item — unset the flag on every other."""
    others = (
        await db.execute(
            select(BOM).where(
                BOM.company_id == company_id,
                BOM.production_item_id == production_item_id,
                BOM.id != keep_id,
                BOM.is_default.is_(True),
            )
        )
    ).scalars()
    for other in others:
        other.is_default = False


# --- CRUD ----------------------------------------------------------------------------


async def get_bom(db: AsyncSession, bom_id: uuid.UUID, company_id: uuid.UUID | None) -> BOM:
    bom = await db.scalar(
        select(BOM).options(selectinload(BOM.items)).where(BOM.id == bom_id, BOM.company_id == company_id)
    )
    if bom is None:
        raise NotFoundError("BOM not found")
    return bom


async def list_boms(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int = 1,
    page_size: int = 20,
    production_item_id: uuid.UUID | None = None,
    is_active: bool | None = None,
) -> tuple[list[BOM], int]:
    stmt = (
        select(BOM)
        .options(selectinload(BOM.items))
        .where(BOM.company_id == company_id)
        .order_by(BOM.creation.desc())
    )
    if production_item_id is not None:
        stmt = stmt.where(BOM.production_item_id == production_item_id)
    if is_active is not None:
        stmt = stmt.where(BOM.is_active.is_(is_active))
    return await paginate(db, stmt, page, page_size)


async def create_bom(db: AsyncSession, payload: BOMCreate, user: CurrentUser) -> BOM:
    company = await get_company(db, user.company_id)
    production_item = await get_item(db, payload.production_item_id, company.id)
    require_stock_item(production_item)

    built, raw_material_cost = await _build_item_rows(
        db, company.id, payload.items, production_item_id=production_item.id
    )
    currency = (payload.currency or company.default_currency).upper()

    name = await get_next_name(db, MFG_NAMING_SERIES["BOM"], company.id)
    bom = BOM(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        production_item_id=production_item.id,
        uom=payload.uom or production_item.stock_uom,
        quantity=payload.quantity,
        is_active=True,
        is_default=payload.is_default,
        currency=currency,
        operating_cost=payload.operating_cost,
        raw_material_cost=raw_material_cost,
        total_cost=raw_material_cost + payload.operating_cost,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(bom)
    await db.flush()
    for row in built:
        row.bom_id = bom.id
        db.add(row)
    if bom.is_default:
        await _clear_other_defaults(db, company.id, production_item.id, bom.id)
    await db.flush()
    await log_audit(
        db, doctype="BOM", document_id=bom.id, action="INSERT", user_id=user.id, company_id=company.id
    )
    await db.commit()
    return await get_bom(db, bom.id, company.id)


async def update_bom(db: AsyncSession, bom_id: uuid.UUID, payload: BOMUpdate, user: CurrentUser) -> BOM:
    """Edit a **draft** BOM (fields + full component replace) and recompute the cost."""
    bom = await get_bom(db, bom_id, user.company_id)
    require_draft(bom.docstatus)

    if payload.quantity is not None:
        bom.quantity = payload.quantity
    if payload.uom is not None:
        bom.uom = payload.uom
    if payload.operating_cost is not None:
        bom.operating_cost = payload.operating_cost
    if payload.remarks is not None:
        bom.remarks = payload.remarks
    if payload.is_default is not None:
        bom.is_default = payload.is_default

    if payload.items is not None:
        built, raw_material_cost = await _build_item_rows(
            db, bom.company_id, payload.items, production_item_id=bom.production_item_id
        )
        bom.items.clear()
        await db.flush()
        for row in built:
            row.bom_id = bom.id
            bom.items.append(row)
        bom.raw_material_cost = raw_material_cost
    bom.total_cost = bom.raw_material_cost + bom.operating_cost

    if bom.is_default:
        await _clear_other_defaults(db, bom.company_id, bom.production_item_id, bom.id)
    bom.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="BOM", document_id=bom.id, action="UPDATE", user_id=user.id, company_id=bom.company_id
    )
    await db.commit()
    return await get_bom(db, bom.id, user.company_id)


async def update_bom_cost(db: AsyncSession, bom_id: uuid.UUID, user: CurrentUser) -> BOM:
    """Re-source component rates from current valuation and refresh the cost snapshot.

    Works on draft or submitted BOMs (refreshing a live cost estimate is safe — it moves no
    stock and posts nothing)."""
    bom = await get_bom(db, bom_id, user.company_id)
    if bom.docstatus == DOCSTATUS_CANCELLED:
        raise ValidationError("Cannot update the cost of a cancelled BOM", code="ERR_DOCSTATUS")
    items = await get_items(db, {r.item_id for r in bom.items}, bom.company_id, allow_disabled=True)
    raw_material_cost = ZERO
    for row in bom.items:
        rate = await resolve_valuation_rate(db, items[row.item_id])
        row.rate = rate
        row.amount = row.stock_qty * rate
        raw_material_cost += row.amount
    bom.raw_material_cost = raw_material_cost
    bom.total_cost = raw_material_cost + bom.operating_cost
    bom.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_bom(db, bom.id, user.company_id)


async def submit_bom(db: AsyncSession, bom_id: uuid.UUID, user: CurrentUser) -> BOM:
    bom = await get_bom(db, bom_id, user.company_id)
    require_draft(bom.docstatus)
    if not bom.items:
        raise ValidationError("A BOM needs at least one component", field="items")
    bom.docstatus = DOCSTATUS_SUBMITTED
    bom.is_active = True
    if bom.is_default:
        await _clear_other_defaults(db, bom.company_id, bom.production_item_id, bom.id)
    bom.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="BOM", document_id=bom.id, action="SUBMIT", user_id=user.id, company_id=bom.company_id
    )
    await db.commit()
    return await get_bom(db, bom.id, user.company_id)


async def cancel_bom(db: AsyncSession, bom_id: uuid.UUID, user: CurrentUser) -> BOM:
    """Cancel a BOM. Blocked while a submitted Work Order still references it."""
    from app.models.manufacturing import WorkOrder  # local import avoids a cycle

    bom = await get_bom(db, bom_id, user.company_id)
    require_submitted(bom.docstatus)
    in_use = await db.scalar(
        select(WorkOrder.id).where(
            WorkOrder.bom_id == bom.id, WorkOrder.docstatus == DOCSTATUS_SUBMITTED
        ).limit(1)
    )
    if in_use is not None:
        raise ValidationError(
            "Cannot cancel: a submitted Work Order still uses this BOM", code="ERR_DOCSTATUS"
        )
    bom.docstatus = DOCSTATUS_CANCELLED
    bom.is_active = False
    bom.is_default = False
    bom.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="BOM", document_id=bom.id, action="CANCEL", user_id=user.id, company_id=bom.company_id
    )
    await db.commit()
    return await get_bom(db, bom.id, user.company_id)


async def set_active(db: AsyncSession, bom_id: uuid.UUID, active: bool, user: CurrentUser) -> BOM:
    """Activate / deactivate a submitted BOM (toggles usability by Work Orders)."""
    bom = await get_bom(db, bom_id, user.company_id)
    require_submitted(bom.docstatus)
    bom.is_active = active
    if not active:
        bom.is_default = False
    bom.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_bom(db, bom.id, user.company_id)
