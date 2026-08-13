"""Phase 7.2 — demand → supply pegging timeline (lean).

For a finished item, list open Sales Order demand, covering supply (stock, open
Work Orders, open Manufacture Material Requests), net shortfall, and the CTP
earliest promise for the uncovered qty. Soft planning view — not a finite scheduler.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.base import DOCSTATUS_SUBMITTED
from app.models.manufacturing import WorkOrder
from app.models.selling import SalesOrder, SalesOrderItem
from app.models.stock import Item, MaterialRequest, MaterialRequestItem
from app.services.lead_time import LeadTimeEstimate, estimate_lead_time
from app.services.manufacturing_common import item_available_qty
from app.services.production_plan import OPEN_SO_STATUSES

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")
OPEN_WO_STATUSES = ("Not Started", "In Process")
OPEN_MR_STATUSES = ("Draft", "Pending", "Partially Ordered", "Partially Received", "Ordered", "Submitted")


@dataclass(frozen=True)
class PeggingRow:
    side: str  # demand | supply
    source_type: str
    source_id: uuid.UUID | None
    source_name: str | None
    qty: Decimal
    due_date: date | None
    notes: str | None = None


@dataclass(frozen=True)
class PeggingTimeline:
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    as_of: date
    warehouse_id: uuid.UUID | None
    demand_qty: Decimal
    supply_qty: Decimal
    net_shortfall: Decimal
    earliest_promise_date: date | None
    ctp: LeadTimeEstimate | None
    rows: list[PeggingRow]
    notes: list[str]


async def pegging_timeline(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> PeggingTimeline:
    """Build a demand/supply pegging view for one finished (or any) item."""
    item = await db.get(Item, item_id)
    if item is None or item.company_id != company_id:
        raise NotFoundError("Item not found")

    as_of_date = as_of or date.today()
    rows: list[PeggingRow] = []
    notes: list[str] = []

    # --- demand: open Sales Orders -------------------------------------------------
    demand_qty = ZERO
    stmt = (
        select(SalesOrderItem, SalesOrder)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .where(
            SalesOrder.company_id == company_id,
            SalesOrder.docstatus == DOCSTATUS_SUBMITTED,
            SalesOrder.status.in_(OPEN_SO_STATUSES),
            SalesOrderItem.item_id == item_id,
        )
        .order_by(SalesOrder.delivery_date.asc().nulls_last(), SalesOrder.name)
    )
    for soi, so in (await db.execute(stmt)).all():
        pending = max(ZERO, (soi.stock_qty or soi.qty) - (soi.delivered_qty or ZERO))
        if pending <= QTY_EPS:
            continue
        due = soi.delivery_date or so.delivery_date
        if from_date is not None and due is not None and due < from_date:
            continue
        if to_date is not None and due is not None and due > to_date:
            continue
        demand_qty += pending
        rows.append(
            PeggingRow(
                side="demand",
                source_type="Sales Order",
                source_id=so.id,
                source_name=so.name,
                qty=pending,
                due_date=due,
                notes=f"Customer demand — status {so.status}",
            )
        )

    # --- supply: on-hand stock -----------------------------------------------------
    on_hand = await item_available_qty(db, item_id, warehouse_id)
    supply_qty = ZERO
    if on_hand > QTY_EPS:
        supply_qty += on_hand
        rows.append(
            PeggingRow(
                side="supply",
                source_type="Stock",
                source_id=warehouse_id,
                source_name="On hand" + (" (warehouse)" if warehouse_id else " (company)"),
                qty=on_hand,
                due_date=as_of_date,
                notes="Available stock",
            )
        )

    # --- supply: open Purchase Orders for this item --------------------------------
    from app.services.open_supply import open_purchase_slices

    for sl in await open_purchase_slices(db, company_id, item_id):
        supply_qty += sl.qty
        rows.append(
            PeggingRow(
                side="supply",
                source_type="Purchase Order",
                source_id=sl.source_id,
                source_name=sl.source_name,
                qty=sl.qty,
                due_date=sl.ready_date,
                notes=sl.notes or "Open PO",
            )
        )

    # --- supply: open Work Orders (still to produce) ------------------------------
    wo_stmt = (
        select(WorkOrder)
        .where(
            WorkOrder.company_id == company_id,
            WorkOrder.production_item_id == item_id,
            WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
            WorkOrder.status.in_(OPEN_WO_STATUSES),
        )
        .order_by(WorkOrder.planned_end_date.asc().nulls_last(), WorkOrder.name)
    )
    for wo in (await db.execute(wo_stmt)).scalars():
        pending = max(ZERO, wo.qty - wo.produced_qty)
        if pending <= QTY_EPS:
            continue
        supply_qty += pending
        rows.append(
            PeggingRow(
                side="supply",
                source_type="Work Order",
                source_id=wo.id,
                source_name=wo.name,
                qty=pending,
                due_date=wo.planned_end_date or wo.planned_start_date,
                notes=f"Open WO — status {wo.status}",
            )
        )

    # --- supply: open Manufacture Material Requests for this item -----------------
    mr_stmt = (
        select(MaterialRequestItem, MaterialRequest)
        .join(MaterialRequest, MaterialRequest.id == MaterialRequestItem.material_request_id)
        .where(
            MaterialRequest.company_id == company_id,
            MaterialRequest.docstatus == DOCSTATUS_SUBMITTED,
            MaterialRequest.material_request_type == "Manufacture",
            MaterialRequestItem.item_id == item_id,
        )
        .order_by(MaterialRequest.schedule_date.asc().nulls_last(), MaterialRequest.name)
    )
    for mri, mr in (await db.execute(mr_stmt)).all():
        status = getattr(mr, "status", None) or ""
        if status and status not in OPEN_MR_STATUSES and status != "Submitted":
            # Keep submitted MRs that are still open-ish; skip Cancelled/Received
            if status in ("Cancelled", "Received", "Stopped"):
                continue
        pending = max(ZERO, (mri.stock_qty or mri.qty) - (mri.ordered_qty or ZERO))
        if pending <= QTY_EPS:
            continue
        supply_qty += pending
        rows.append(
            PeggingRow(
                side="supply",
                source_type="Material Request",
                source_id=mr.id,
                source_name=mr.name,
                qty=pending,
                due_date=mri.schedule_date or mr.schedule_date,
                notes="Manufacture MR (still open)",
            )
        )

    net = max(ZERO, demand_qty - supply_qty)
    ctp: LeadTimeEstimate | None = None
    earliest: date | None = None
    if net > QTY_EPS:
        ctp = await estimate_lead_time(
            db, company_id, item_id, net, as_of=as_of_date, warehouse_id=warehouse_id
        )
        earliest = ctp.earliest_promise_date
        notes.append(
            f"Uncovered demand {net} — CTP earliest promise {earliest} "
            f"({ctp.procurement_days}d procurement + {ctp.manufacturing_days}d manufacture)."
        )
    elif demand_qty <= QTY_EPS:
        notes.append("No open Sales Order demand for this item in range.")
    else:
        notes.append("Supply covers demand (stock + open WOs/MRs).")

    # Stable display order: demands by date, then supplies by date
    rows.sort(
        key=lambda r: (
            0 if r.side == "demand" else 1,
            r.due_date or date.max,
            r.source_name or "",
        )
    )

    return PeggingTimeline(
        item_id=item.id,
        item_code=item.item_code,
        item_name=item.item_name,
        as_of=as_of_date,
        warehouse_id=warehouse_id,
        demand_qty=demand_qty,
        supply_qty=supply_qty,
        net_shortfall=net,
        earliest_promise_date=earliest,
        ctp=ctp,
        rows=rows,
        notes=notes,
    )
