"""Manufacturing report services — read-only.

* **Production Register** — every Work Order's planned-vs-produced position + cost.
* **BOM where-used** — which BOMs consume a given item.
* **BOM stock report** — can I build N of a finished good from current stock?
* **BOM Explorer** — flatten a nested BOM to leaf (and stocked sub-assembly) materials.
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
    BOMExplorerReport,
    BOMExplorerRow,
    BOMStockReport,
    BOMStockReportRow,
    BOMWhereUsedRow,
    MaterialShortageRow,
    ProductionAnalyticsRow,
    ProductionRegisterRow,
    WorkOrderSummaryRow,
)
from app.services.bom_explosion import explode_bom, explode_scrap
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
    completed / cancelled) vs on-hand stock."""
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
    """For a target number of finished units, each required component's need vs on-hand.

    Uses the same explosion as Work Order create (phantoms flatten; stocked sub-assemblies
    stay as one line).
    """
    bom = await db.scalar(
        select(BOM)
        .options(selectinload(BOM.items), selectinload(BOM.scrap_items))
        .where(BOM.id == bom_id, BOM.company_id == company_id)
    )
    if bom is None:
        raise NotFoundError("BOM not found")
    exploded = await explode_bom(db, bom, for_qty, flatten_all=False)
    rows: list[BOMStockReportRow] = []
    buildable = None
    for comp in exploded:
        available = await item_available_qty(db, comp.item_id, comp.source_warehouse_id)
        shortfall = max(ZERO, comp.stock_qty - available)
        rows.append(
            BOMStockReportRow(
                item_id=comp.item_id,
                item_code=comp.item_code,
                item_name=comp.item_name,
                required_qty=comp.stock_qty,
                available_qty=available,
                shortfall_qty=shortfall,
            )
        )
        per_unit = (comp.stock_qty / for_qty) if for_qty else ZERO
        if per_unit > ZERO:
            can = available / per_unit
            buildable = can if buildable is None else min(buildable, can)
    return BOMStockReport(
        bom_id=bom.id,
        bom_name=bom.name,
        for_qty=for_qty,
        buildable_qty=(buildable or ZERO).quantize(Decimal("0.000001")),
        rows=rows,
    )


async def bom_explorer(
    db: AsyncSession,
    bom_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    for_qty: Decimal,
    flatten_all: bool = True,
) -> BOMExplorerReport:
    """Flatten a nested BOM for ``for_qty`` finished units.

    Default ``flatten_all=True`` walks every nested BOM to leaf raw materials (Explorer).
    Pass ``flatten_all=False`` to mirror Work Order explosion (phantoms only).
    """
    bom = await db.scalar(
        select(BOM)
        .options(selectinload(BOM.items), selectinload(BOM.scrap_items))
        .where(BOM.id == bom_id, BOM.company_id == company_id)
    )
    if bom is None:
        raise NotFoundError("BOM not found")

    exploded = await explode_bom(db, bom, for_qty, flatten_all=flatten_all)
    scrap = await explode_scrap(db, bom, for_qty)
    scale = (for_qty / bom.quantity) if bom.quantity else ZERO

    rows = [
        BOMExplorerRow(
            item_id=c.item_id,
            item_code=c.item_code,
            item_name=c.item_name,
            stock_qty=c.stock_qty,
            rate=c.rate,
            amount=(c.stock_qty * c.rate).quantize(Decimal("0.000001")),
            level=c.level,
            source_warehouse_id=c.source_warehouse_id,
            is_leaf=True,
        )
        for c in exploded
    ]
    scrap_rows = [
        BOMExplorerRow(
            item_id=s.item_id,
            item_code=s.item_code,
            item_name=s.item_name,
            stock_qty=s.stock_qty,
            rate=s.rate,
            amount=(s.stock_qty * s.rate).quantize(Decimal("0.000001")),
            level=0,
            source_warehouse_id=s.stock_warehouse_id,
            is_leaf=True,
        )
        for s in scrap
    ]
    raw = sum((r.amount for r in rows), ZERO)
    scrap_cost = sum((r.amount for r in scrap_rows), ZERO)
    op = (bom.operating_cost * scale).quantize(Decimal("0.000001"))
    return BOMExplorerReport(
        bom_id=bom.id,
        bom_name=bom.name,
        production_item_code=bom.production_item_code,
        production_item_name=bom.production_item_name,
        for_qty=for_qty,
        flatten_all=flatten_all,
        raw_material_cost=raw,
        scrap_cost=scrap_cost,
        operating_cost=op,
        total_cost=max(ZERO, raw + op - scrap_cost),
        rows=rows,
        scrap_rows=scrap_rows,
    )


async def work_order_summary(
    db: AsyncSession, company_id: uuid.UUID
) -> list[WorkOrderSummaryRow]:
    """Roll up open/completed Work Orders by status (Work Order Summary equivalent)."""
    stmt = (
        select(WorkOrder)
        .options(selectinload(WorkOrder.bom))
        .where(WorkOrder.company_id == company_id, WorkOrder.docstatus != DOCSTATUS_CANCELLED)
    )
    buckets: dict[str, dict] = {}
    for wo in (await db.execute(stmt)).scalars():
        b = buckets.setdefault(
            wo.status,
            {
                "count": 0,
                "total_qty": ZERO,
                "total_produced_qty": ZERO,
                "total_pending_qty": ZERO,
                "total_estimated_cost": ZERO,
            },
        )
        cost_per_unit = wo.bom.cost_per_unit if wo.bom else ZERO
        b["count"] += 1
        b["total_qty"] += wo.qty
        b["total_produced_qty"] += wo.produced_qty
        b["total_pending_qty"] += max(ZERO, wo.qty - wo.produced_qty)
        b["total_estimated_cost"] += (cost_per_unit * wo.qty).quantize(Decimal("0.01"))
    return [
        WorkOrderSummaryRow(status=status, **vals)
        for status, vals in sorted(buckets.items(), key=lambda x: x[0])
    ]


async def production_analytics(
    db: AsyncSession, company_id: uuid.UUID
) -> list[ProductionAnalyticsRow]:
    """Monthly completed production (qty + estimated cost) from Work Orders."""
    stmt = (
        select(WorkOrder)
        .options(selectinload(WorkOrder.bom))
        .where(
            WorkOrder.company_id == company_id,
            WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
            WorkOrder.status == "Completed",
            WorkOrder.actual_end_date.is_not(None),
        )
        .order_by(WorkOrder.actual_end_date.asc())
    )
    buckets: dict[str, dict] = {}
    for wo in (await db.execute(stmt)).scalars():
        if wo.actual_end_date is None:
            continue
        period = wo.actual_end_date.strftime("%Y-%m")
        b = buckets.setdefault(
            period,
            {"work_orders_completed": 0, "qty_produced": ZERO, "estimated_cost": ZERO},
        )
        cost_per_unit = wo.bom.cost_per_unit if wo.bom else ZERO
        b["work_orders_completed"] += 1
        b["qty_produced"] += wo.produced_qty
        b["estimated_cost"] += (cost_per_unit * wo.produced_qty).quantize(Decimal("0.01"))
    return [
        ProductionAnalyticsRow(period=period, **vals)
        for period, vals in sorted(buckets.items())
    ]
