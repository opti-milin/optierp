"""Manufacturing report services (Phase 4) — read-only.

* **Production Register** — every Work Order's planned-vs-produced position + cost.
* **BOM where-used** — which BOMs consume a given item.
* **BOM stock report** — can I build N of a finished good from current stock?
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.manufacturing import BOM, BOMItem, WorkOrder
from app.models.stock import Warehouse
from app.schemas.manufacturing import (
    BOMStockReport,
    BOMStockReportRow,
    BOMWhereUsedRow,
    MaterialShortageRow,
    ProductionRegisterRow,
)
from app.services.manufacturing_common import item_available_qty

ZERO = Decimal("0")


async def production_register(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    status: str | None = None,
    production_item_id: uuid.UUID | None = None,
) -> list[ProductionRegisterRow]:
    stmt = (
        select(WorkOrder)
        .options(selectinload(WorkOrder.bom))
        .where(WorkOrder.company_id == company_id, WorkOrder.docstatus != DOCSTATUS_CANCELLED)
        .order_by(WorkOrder.creation.desc())
    )
    if status:
        stmt = stmt.where(WorkOrder.status == status)
    if production_item_id is not None:
        stmt = stmt.where(WorkOrder.production_item_id == production_item_id)
    rows: list[ProductionRegisterRow] = []
    for wo in (await db.execute(stmt)).scalars():
        cost_per_unit = wo.bom.cost_per_unit if wo.bom else ZERO
        rows.append(
            ProductionRegisterRow(
                work_order_id=wo.id,
                name=wo.name,
                production_item_code=wo.production_item_code,
                production_item_name=wo.production_item_name,
                bom_name=wo.bom_name,
                status=wo.status,
                qty=wo.qty,
                produced_qty=wo.produced_qty,
                pending_qty=max(ZERO, wo.qty - wo.produced_qty),
                planned_start_date=wo.planned_start_date,
                actual_end_date=wo.actual_end_date,
                estimated_cost=(cost_per_unit * wo.qty).quantize(Decimal("0.01")),
                operating_cost=wo.operating_cost,
            )
        )
    return rows


async def material_shortage(
    db: AsyncSession, company_id: uuid.UUID, *, only_short: bool = False
) -> list[MaterialShortageRow]:
    """Aggregate component demand across ALL open Work Orders (submitted, not stopped /
    completed / cancelled) vs on-hand stock: "given everything I've committed to build,
    what am I short?" Grouped by (item, consume-from warehouse); availability is checked
    at that warehouse (company-wide when none is set)."""
    stmt = (
        select(WorkOrder)
        .options(selectinload(WorkOrder.items))
        .where(
            WorkOrder.company_id == company_id,
            WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
            WorkOrder.status.in_(("Not Started", "In Process")),
        )
        .order_by(WorkOrder.creation)
    )
    pending: dict[tuple, dict] = {}
    for wo in (await db.execute(stmt)).scalars():
        consume_default = (
            wo.wip_warehouse_id
            if (not wo.skip_transfer and wo.wip_warehouse_id)
            else wo.source_warehouse_id
        )
        for wi in wo.items:
            still = wi.required_qty - wi.consumed_qty
            if still <= ZERO:
                continue
            key = (wi.item_id, wi.source_warehouse_id or consume_default)
            bucket = pending.setdefault(
                key,
                {
                    "qty": ZERO,
                    "work_orders": [],
                    "item_code": wi.item_code,
                    "item_name": wi.item_name,
                },
            )
            bucket["qty"] += still
            bucket["work_orders"].append(wo.name)

    warehouse_names: dict[uuid.UUID, str] = {
        w.id: w.warehouse_name
        for w in (
            await db.execute(select(Warehouse).where(Warehouse.company_id == company_id))
        ).scalars()
    }
    rows: list[MaterialShortageRow] = []
    for (item_id, warehouse_id), bucket in pending.items():
        available = await item_available_qty(db, item_id, warehouse_id)
        shortfall = max(ZERO, bucket["qty"] - available)
        if only_short and shortfall <= ZERO:
            continue
        rows.append(
            MaterialShortageRow(
                item_id=item_id,
                item_code=bucket["item_code"],
                item_name=bucket["item_name"],
                warehouse_id=warehouse_id,
                warehouse_name=warehouse_names.get(warehouse_id) if warehouse_id else None,
                pending_qty=bucket["qty"],
                available_qty=available,
                shortfall_qty=shortfall,
                work_orders=bucket["work_orders"],
            )
        )
    rows.sort(key=lambda r: (-(r.shortfall_qty > ZERO), str(r.item_code or "")))
    return rows


async def bom_where_used(
    db: AsyncSession, company_id: uuid.UUID, item_id: uuid.UUID
) -> list[BOMWhereUsedRow]:
    """Active BOMs that consume ``item_id`` as a component."""
    stmt = (
        select(BOM, BOMItem.stock_qty)
        .join(BOMItem, BOMItem.bom_id == BOM.id)
        .options(selectinload(BOM.production_item))
        .where(
            BOM.company_id == company_id,
            BOMItem.item_id == item_id,
            BOM.docstatus != DOCSTATUS_CANCELLED,
        )
        .order_by(BOM.name)
    )
    rows: list[BOMWhereUsedRow] = []
    for bom, stock_qty in (await db.execute(stmt)).all():
        rows.append(
            BOMWhereUsedRow(
                bom_id=bom.id,
                bom_name=bom.name,
                production_item_code=bom.production_item_code,
                production_item_name=bom.production_item_name,
                is_active=bom.is_active,
                is_default=bom.is_default,
                qty_per_batch=stock_qty,
            )
        )
    return rows


async def bom_stock_report(
    db: AsyncSession, bom_id: uuid.UUID, company_id: uuid.UUID, *, for_qty: Decimal
) -> BOMStockReport:
    """For a target number of finished units, each component's need vs on-hand stock, and
    the maximum finished units the current stock can build (the min over components)."""
    bom = await db.scalar(
        select(BOM).options(selectinload(BOM.items)).where(BOM.id == bom_id, BOM.company_id == company_id)
    )
    if bom is None:
        raise NotFoundError("BOM not found")
    scale = (for_qty / bom.quantity) if bom.quantity else ZERO
    rows: list[BOMStockReportRow] = []
    buildable = None
    for comp in bom.items:
        required = (comp.stock_qty * scale).quantize(Decimal("0.000001"))
        available = await item_available_qty(db, comp.item_id, comp.source_warehouse_id)
        shortfall = max(ZERO, required - available)
        rows.append(
            BOMStockReportRow(
                item_id=comp.item_id,
                item_code=comp.item_code,
                item_name=comp.item_name,
                required_qty=required,
                available_qty=available,
                shortfall_qty=shortfall,
            )
        )
        per_batch = comp.stock_qty / bom.quantity if bom.quantity else ZERO
        if per_batch > ZERO:
            can = available / per_batch
            buildable = can if buildable is None else min(buildable, can)
    return BOMStockReport(
        bom_id=bom.id,
        bom_name=bom.name,
        for_qty=for_qty,
        buildable_qty=(buildable or ZERO).quantize(Decimal("0.000001")),
        rows=rows,
    )
