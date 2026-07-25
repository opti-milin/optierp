"""BOM service — the recipe for a finished good (bespoke document, no GL).

A BOM lists the components (and a flat operating cost, plus optional scrap) needed to
build ``quantity`` units of a production item, and snapshots the resulting cost from the
components' current valuation / nested BOM rollup. It never moves stock or posts to the GL
— it is the spec a Work Order consumes.

Costing:
    raw_material_cost = Σ (component.stock_qty × component.rate)
    scrap_cost        = Σ (scrap.stock_qty × scrap.rate)
    total_cost        = raw_material_cost + operating_cost − scrap_cost
    cost_per_unit     = total_cost / quantity          (derived, not stored)

``rate`` is sourced from a nested default BOM's cost_per_unit when present, else the
component's live valuation (``resolve_valuation_rate``), unless the caller overrides it.
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
from app.models.manufacturing import BOM, BOMItem, BOMOperation, BOMScrapItem, Operation, Routing, Workstation
from app.schemas.manufacturing import BOMCreate, BOMItemIn, BOMOperationIn, BOMScrapItemIn, BOMUpdate
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.bom_explosion import assert_no_bom_cycle, find_item_bom, resolve_component_rate
from app.services.manufacturing_common import MFG_NAMING_SERIES
from app.services.pagination import paginate
from app.services.stock_common import (
    get_item,
    get_items,
    get_warehouse,
    require_stock_item,
    resolve_conversion_factor,
)

ZERO = Decimal("0")


def _total_cost(raw: Decimal, operating: Decimal, scrap: Decimal) -> Decimal:
    total = raw + operating - scrap
    if total < ZERO:
        raise ValidationError(
            "BOM total cost cannot be negative — scrap recovery exceeds material + operating cost",
            field="scrap_items",
        )
    return total


# --- helpers -------------------------------------------------------------------------


async def _build_item_rows(
    db: AsyncSession,
    company_id: uuid.UUID,
    rows: list[BOMItemIn],
    *,
    production_item_id: uuid.UUID,
    exclude_bom_id: uuid.UUID | None = None,
) -> tuple[list[BOMItem], Decimal]:
    """Resolve each component line and return child rows + raw-material cost total."""
    items = await get_items(db, {r.item_id for r in rows}, company_id)
    await assert_no_bom_cycle(
        db,
        company_id,
        production_item_id=production_item_id,
        component_item_ids={r.item_id for r in rows},
        exclude_bom_id=exclude_bom_id,
    )
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
        rate = await resolve_component_rate(
            db, company_id, r.item_id, override=r.rate
        )
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
                allow_alternative_item=r.allow_alternative_item,
            )
        )
    return built, raw_material_cost


async def _build_scrap_rows(
    db: AsyncSession,
    company_id: uuid.UUID,
    rows: list[BOMScrapItemIn],
    *,
    production_item_id: uuid.UUID,
) -> tuple[list[BOMScrapItem], Decimal]:
    if not rows:
        return [], ZERO
    items = await get_items(db, {r.item_id for r in rows}, company_id)
    built: list[BOMScrapItem] = []
    scrap_cost = ZERO
    for idx, r in enumerate(rows, start=1):
        item = items[r.item_id]
        require_stock_item(item)
        if r.item_id == production_item_id:
            raise ValidationError(
                f"Scrap row {idx}: scrap item cannot be the BOM's own finished good",
                field="scrap_items",
            )
        uom = r.uom or item.stock_uom
        factor = resolve_conversion_factor(item, uom)
        stock_qty = r.qty * factor
        if r.stock_warehouse_id is not None:
            await get_warehouse(db, r.stock_warehouse_id, company_id)
        amount = stock_qty * r.rate
        scrap_cost += amount
        built.append(
            BOMScrapItem(
                idx=idx,
                item_id=r.item_id,
                qty=r.qty,
                uom=uom,
                conversion_factor=factor,
                stock_qty=stock_qty,
                rate=r.rate,
                amount=amount,
                stock_warehouse_id=r.stock_warehouse_id,
            )
        )
    return built, scrap_cost


async def _build_operation_rows(
    db: AsyncSession,
    company_id: uuid.UUID,
    rows: list[BOMOperationIn],
) -> tuple[list[BOMOperation], Decimal]:
    """Resolve BOM operation lines; return rows + Σ (time_in_mins/60 × hour_rate)."""
    if not rows:
        return [], ZERO
    built: list[BOMOperation] = []
    ops_cost = ZERO
    for idx, r in enumerate(rows, start=1):
        op = await db.get(Operation, r.operation_id)
        if op is None or op.company_id != company_id:
            raise ValidationError(
                f"Operation row {idx}: operation not found in this company",
                field="operations",
            )
        if op.disabled:
            raise ValidationError(
                f"Operation row {idx}: '{op.operation_name}' is disabled",
                field="operations",
            )
        hour_rate = r.hour_rate
        if r.workstation_id is not None:
            ws = await db.get(Workstation, r.workstation_id)
            if ws is None or ws.company_id != company_id:
                raise ValidationError(
                    f"Operation row {idx}: workstation not found in this company",
                    field="operations",
                )
            if ws.disabled:
                raise ValidationError(
                    f"Operation row {idx}: workstation '{ws.workstation_name}' is disabled",
                    field="operations",
                )
            if hour_rate is None:
                hour_rate = ws.hour_rate
        if hour_rate is None:
            hour_rate = op.default_hour_rate
        row_cost = (r.time_in_mins / Decimal("60") * hour_rate).quantize(Decimal("0.000001"))
        ops_cost += row_cost
        built.append(
            BOMOperation(
                idx=idx,
                operation_id=r.operation_id,
                workstation_id=r.workstation_id,
                time_in_mins=r.time_in_mins,
                hour_rate=hour_rate,
                operating_cost=row_cost,
                description=r.description,
            )
        )
    return built, ops_cost


async def _operations_from_routing(
    db: AsyncSession, company_id: uuid.UUID, routing_id: uuid.UUID
) -> list[BOMOperationIn]:
    routing = await db.scalar(
        select(Routing)
        .options(selectinload(Routing.operations))
        .where(Routing.id == routing_id, Routing.company_id == company_id)
    )
    if routing is None:
        raise ValidationError("Routing not found", field="routing_id")
    if routing.disabled:
        raise ValidationError("Routing is disabled", field="routing_id")
    return [
        BOMOperationIn(
            operation_id=row.operation_id,
            workstation_id=row.workstation_id,
            time_in_mins=row.time_in_mins,
        )
        for row in sorted(routing.operations, key=lambda r: r.idx)
    ]


async def _clear_other_defaults(
    db: AsyncSession, company_id: uuid.UUID, production_item_id: uuid.UUID, keep_id: uuid.UUID
) -> None:
    """Ensure at most one default BOM per production item — unset the flag on every other
    and point the Item's ``default_bom_id`` at ``keep_id``."""
    from app.models.stock import Item

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
    item = await db.get(Item, production_item_id)
    if item is not None and item.company_id == company_id:
        item.default_bom_id = keep_id


async def _sync_item_default_bom(db: AsyncSession, bom: BOM) -> None:
    """Keep Item.default_bom_id aligned when this BOM is (or ceases to be) the default."""
    from app.models.stock import Item

    item = await db.get(Item, bom.production_item_id)
    if item is None or item.company_id != bom.company_id:
        return
    if bom.is_default and bom.docstatus == DOCSTATUS_SUBMITTED and bom.is_active:
        item.default_bom_id = bom.id
    elif item.default_bom_id == bom.id:
        item.default_bom_id = None


# --- CRUD ----------------------------------------------------------------------------


async def get_bom(db: AsyncSession, bom_id: uuid.UUID, company_id: uuid.UUID | None) -> BOM:
    bom = await db.scalar(
        select(BOM)
        .options(
            selectinload(BOM.items),
            selectinload(BOM.scrap_items),
            selectinload(BOM.operations),
        )
        .where(BOM.id == bom_id, BOM.company_id == company_id)
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
        .options(
            selectinload(BOM.items),
            selectinload(BOM.scrap_items),
            selectinload(BOM.operations),
        )
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
    scrap_built, scrap_cost = await _build_scrap_rows(
        db, company.id, payload.scrap_items, production_item_id=production_item.id
    )
    op_inputs = list(payload.operations)
    if payload.routing_id is not None and not op_inputs:
        op_inputs = await _operations_from_routing(db, company.id, payload.routing_id)
    elif payload.routing_id is not None:
        # Validate routing exists even when operations are explicit.
        await _operations_from_routing(db, company.id, payload.routing_id)
    op_built, ops_cost = await _build_operation_rows(db, company.id, op_inputs)
    operating_cost = (payload.operating_cost + ops_cost).quantize(Decimal("0.000001"))
    currency = (payload.currency or company.default_currency).upper()
    total = _total_cost(raw_material_cost, operating_cost, scrap_cost)

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
        is_phantom=payload.is_phantom,
        routing_id=payload.routing_id,
        currency=currency,
        operating_cost=operating_cost,
        raw_material_cost=raw_material_cost,
        scrap_cost=scrap_cost,
        total_cost=total,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(bom)
    await db.flush()
    for row in built:
        row.bom_id = bom.id
        db.add(row)
    for row in scrap_built:
        row.bom_id = bom.id
        db.add(row)
    for row in op_built:
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
    """Edit a **draft** BOM (fields + full component/scrap/ops replace) and recompute the cost."""
    bom = await get_bom(db, bom_id, user.company_id)
    require_draft(bom.docstatus)

    flat_operating = bom.operating_cost - sum((o.operating_cost for o in bom.operations), ZERO)
    if flat_operating < ZERO:
        flat_operating = ZERO

    if payload.quantity is not None:
        bom.quantity = payload.quantity
    if payload.uom is not None:
        bom.uom = payload.uom
    if payload.operating_cost is not None:
        flat_operating = payload.operating_cost
    if payload.remarks is not None:
        bom.remarks = payload.remarks
    if payload.is_default is not None:
        bom.is_default = payload.is_default
    if payload.is_phantom is not None:
        bom.is_phantom = payload.is_phantom
    if "routing_id" in payload.model_fields_set:
        bom.routing_id = payload.routing_id

    if payload.items is not None:
        built, raw_material_cost = await _build_item_rows(
            db,
            bom.company_id,
            payload.items,
            production_item_id=bom.production_item_id,
            exclude_bom_id=bom.id,
        )
        bom.items.clear()
        await db.flush()
        for row in built:
            row.bom_id = bom.id
            bom.items.append(row)
        bom.raw_material_cost = raw_material_cost

    if payload.scrap_items is not None:
        scrap_built, scrap_cost = await _build_scrap_rows(
            db, bom.company_id, payload.scrap_items, production_item_id=bom.production_item_id
        )
        bom.scrap_items.clear()
        await db.flush()
        for row in scrap_built:
            row.bom_id = bom.id
            bom.scrap_items.append(row)
        bom.scrap_cost = scrap_cost

    if payload.operations is not None:
        op_built, ops_cost = await _build_operation_rows(db, bom.company_id, payload.operations)
        bom.operations.clear()
        await db.flush()
        for row in op_built:
            row.bom_id = bom.id
            bom.operations.append(row)
        bom.operating_cost = (flat_operating + ops_cost).quantize(Decimal("0.000001"))
    elif payload.operating_cost is not None:
        ops_cost = sum((o.operating_cost for o in bom.operations), ZERO)
        bom.operating_cost = (flat_operating + ops_cost).quantize(Decimal("0.000001"))

    bom.total_cost = _total_cost(bom.raw_material_cost, bom.operating_cost, bom.scrap_cost)

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
    """Re-source component rates (nested BOM rollup or valuation) and refresh the snapshot.

    Works on draft or submitted BOMs. Child BOMs are refreshed depth-first first so the
    parent's rollup sees current nested costs. Operation hour rates are re-sourced from
    workstation / operation defaults.
    """
    bom = await get_bom(db, bom_id, user.company_id)
    if bom.docstatus == DOCSTATUS_CANCELLED:
        raise ValidationError("Cannot update the cost of a cancelled BOM", code="ERR_DOCSTATUS")

    # Refresh nested child BOMs first (depth-first), skipping cycles via visited set.
    await _refresh_nested_costs(db, bom, user, visited=frozenset({bom.id}))

    raw_material_cost = ZERO
    for row in bom.items:
        rate = await resolve_component_rate(db, bom.company_id, row.item_id)
        row.rate = rate
        row.amount = row.stock_qty * rate
        raw_material_cost += row.amount
    scrap_cost = ZERO
    for row in bom.scrap_items:
        row.amount = row.stock_qty * row.rate
        scrap_cost += row.amount

    flat_operating = bom.operating_cost - sum((o.operating_cost for o in bom.operations), ZERO)
    if flat_operating < ZERO:
        flat_operating = ZERO
    ops_cost = ZERO
    for row in bom.operations:
        hour_rate = row.hour_rate
        if row.workstation_id is not None:
            ws = await db.get(Workstation, row.workstation_id)
            if ws is not None and ws.company_id == bom.company_id:
                hour_rate = ws.hour_rate
        else:
            op = await db.get(Operation, row.operation_id)
            if op is not None and op.company_id == bom.company_id:
                hour_rate = op.default_hour_rate
        row.hour_rate = hour_rate
        row.operating_cost = (row.time_in_mins / Decimal("60") * hour_rate).quantize(
            Decimal("0.000001")
        )
        ops_cost += row.operating_cost

    bom.raw_material_cost = raw_material_cost
    bom.scrap_cost = scrap_cost
    bom.operating_cost = (flat_operating + ops_cost).quantize(Decimal("0.000001"))
    bom.total_cost = _total_cost(raw_material_cost, bom.operating_cost, scrap_cost)
    bom.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_bom(db, bom.id, user.company_id)


async def _refresh_nested_costs(
    db: AsyncSession,
    bom: BOM,
    user: CurrentUser,
    *,
    visited: frozenset[uuid.UUID],
) -> None:
    """Flush-only recursive cost refresh of child BOMs (no commit)."""
    for row in bom.items:
        child = await find_item_bom(db, bom.company_id, row.item_id)
        if child is None or child.id in visited:
            continue
        child = await get_bom(db, child.id, bom.company_id)
        await _refresh_nested_costs(db, child, user, visited=visited | {child.id})
        raw = ZERO
        for crow in child.items:
            rate = await resolve_component_rate(db, child.company_id, crow.item_id)
            crow.rate = rate
            crow.amount = crow.stock_qty * rate
            raw += crow.amount
        scrap = ZERO
        for srow in child.scrap_items:
            srow.amount = srow.stock_qty * srow.rate
            scrap += srow.amount
        child.raw_material_cost = raw
        child.scrap_cost = scrap
        child.total_cost = _total_cost(raw, child.operating_cost, scrap)
        child.modified_by = user.id
        await db.flush()


async def submit_bom(db: AsyncSession, bom_id: uuid.UUID, user: CurrentUser) -> BOM:
    bom = await get_bom(db, bom_id, user.company_id)
    require_draft(bom.docstatus)
    if not bom.items:
        raise ValidationError("A BOM needs at least one component", field="items")
    await assert_no_bom_cycle(
        db,
        bom.company_id,
        production_item_id=bom.production_item_id,
        component_item_ids={r.item_id for r in bom.items},
        exclude_bom_id=bom.id,
    )
    bom.docstatus = DOCSTATUS_SUBMITTED
    bom.is_active = True
    if bom.is_default:
        await _clear_other_defaults(db, bom.company_id, bom.production_item_id, bom.id)
    await _sync_item_default_bom(db, bom)
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
    await _sync_item_default_bom(db, bom)
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
    await _sync_item_default_bom(db, bom)
    bom.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_bom(db, bom.id, user.company_id)
