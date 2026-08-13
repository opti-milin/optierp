"""Phase 9.0 — open supply document dates for supply-aware CTP.

Reads pending qty + expected dates from open Purchase Orders, Material Requests,
and Work Orders so lead-time estimates prefer live schedule dates over catalog
``Item.lead_time_days`` when supply is already in flight.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import DOCSTATUS_SUBMITTED
from app.models.buying import PurchaseOrder, PurchaseOrderItem
from app.models.manufacturing import WorkOrder
from app.models.stock import MaterialRequest, MaterialRequestItem

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")

OPEN_PO_STATUSES = ("To Receive and Bill", "To Receive", "To Bill")
OPEN_WO_STATUSES = ("Not Started", "In Process")
OPEN_MR_STATUSES = (
    "Draft",
    "Pending",
    "Partially Ordered",
    "Partially Received",
    "Ordered",
    "Submitted",
)


@dataclass(frozen=True)
class OpenSupplySlice:
    """One open supply document contributing pending qty toward a shortfall."""

    source_type: str  # Purchase Order | Material Request | Work Order
    source_id: uuid.UUID
    source_name: str | None
    qty: Decimal
    ready_date: date | None
    notes: str | None = None


@dataclass(frozen=True)
class ShortfallCoverPlan:
    """How a component shortfall is covered by open supply + residual catalog lead."""

    shortfall_qty: Decimal
    covered_qty: Decimal
    residual_qty: Decimal
    materials_ready_date: date
    wait_days: int
    catalog_lead_days: int
    slices: list[OpenSupplySlice]
    notes: list[str]


def _ready_or_as_of(ready: date | None, as_of: date) -> date:
    return ready if ready is not None else as_of


async def open_purchase_slices(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
) -> list[OpenSupplySlice]:
    """Pending (unordered received) PO lines for an item, earliest schedule first."""
    stmt = (
        select(PurchaseOrderItem, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(
            PurchaseOrder.company_id == company_id,
            PurchaseOrder.docstatus == DOCSTATUS_SUBMITTED,
            PurchaseOrder.status.in_(OPEN_PO_STATUSES),
            PurchaseOrderItem.item_id == item_id,
        )
        .order_by(PurchaseOrderItem.schedule_date.asc().nulls_last(), PurchaseOrder.name)
    )
    out: list[OpenSupplySlice] = []
    for poi, po in (await db.execute(stmt)).all():
        pending = max(ZERO, (poi.stock_qty or poi.qty) - (poi.received_qty or ZERO))
        if pending <= QTY_EPS:
            continue
        ready = poi.schedule_date or po.schedule_date
        out.append(
            OpenSupplySlice(
                source_type="Purchase Order",
                source_id=po.id,
                source_name=po.name,
                qty=pending,
                ready_date=ready,
                notes=f"Open PO — status {po.status}",
            )
        )
    return out


async def open_material_request_slices(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
) -> list[OpenSupplySlice]:
    """Open MR lines not yet fully ordered (Purchase or Manufacture)."""
    stmt = (
        select(MaterialRequestItem, MaterialRequest)
        .join(MaterialRequest, MaterialRequest.id == MaterialRequestItem.material_request_id)
        .where(
            MaterialRequest.company_id == company_id,
            MaterialRequest.docstatus == DOCSTATUS_SUBMITTED,
            MaterialRequestItem.item_id == item_id,
        )
        .order_by(MaterialRequest.schedule_date.asc().nulls_last(), MaterialRequest.name)
    )
    out: list[OpenSupplySlice] = []
    for mri, mr in (await db.execute(stmt)).all():
        status = getattr(mr, "status", None) or ""
        if status in ("Cancelled", "Received", "Stopped"):
            continue
        if status and status not in OPEN_MR_STATUSES and status != "Submitted":
            continue
        pending = max(ZERO, (mri.stock_qty or mri.qty) - (mri.ordered_qty or ZERO))
        if pending <= QTY_EPS:
            continue
        ready = mri.schedule_date or mr.schedule_date
        mr_type = getattr(mr, "material_request_type", None) or "Purchase"
        out.append(
            OpenSupplySlice(
                source_type="Material Request",
                source_id=mr.id,
                source_name=mr.name,
                qty=pending,
                ready_date=ready,
                notes=f"{mr_type} MR — status {status or 'Submitted'}",
            )
        )
    return out


async def open_work_order_slices(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
) -> list[OpenSupplySlice]:
    """Open WOs still to produce for a finished item."""
    stmt = (
        select(WorkOrder)
        .where(
            WorkOrder.company_id == company_id,
            WorkOrder.production_item_id == item_id,
            WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
            WorkOrder.status.in_(OPEN_WO_STATUSES),
        )
        .order_by(WorkOrder.planned_end_date.asc().nulls_last(), WorkOrder.name)
    )
    out: list[OpenSupplySlice] = []
    for wo in (await db.execute(stmt)).scalars():
        pending = max(ZERO, wo.qty - wo.produced_qty)
        if pending <= QTY_EPS:
            continue
        out.append(
            OpenSupplySlice(
                source_type="Work Order",
                source_id=wo.id,
                source_name=wo.name,
                qty=pending,
                ready_date=wo.planned_end_date or wo.planned_start_date,
                notes=f"Open WO — status {wo.status}",
            )
        )
    return out


async def cover_shortfall(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
    shortfall_qty: Decimal,
    *,
    as_of: date,
    catalog_lead_days: int,
    include_work_orders: bool = False,
) -> ShortfallCoverPlan:
    """Cover ``shortfall_qty`` with open PO (+ optional WO) then residual catalog lead.

    Material Requests are listed as slices for transparency but do **not** reduce
    wait by themselves (no purchase yet) — residual still uses catalog lead unless
    a PO already covers the qty. MR schedule is used only when no PO covers and the
    MR has a schedule_date (treat as planned buy date + 0 extra lead if scheduled).
    """
    notes: list[str] = []
    if shortfall_qty <= QTY_EPS:
        return ShortfallCoverPlan(
            shortfall_qty=ZERO,
            covered_qty=ZERO,
            residual_qty=ZERO,
            materials_ready_date=as_of,
            wait_days=0,
            catalog_lead_days=catalog_lead_days,
            slices=[],
            notes=["No shortfall."],
        )

    slices: list[OpenSupplySlice] = []
    remaining = shortfall_qty
    ready = as_of

    po_slices = await open_purchase_slices(db, company_id, item_id)
    for sl in po_slices:
        if remaining <= QTY_EPS:
            break
        take = min(remaining, sl.qty)
        if take <= QTY_EPS:
            continue
        slices.append(
            OpenSupplySlice(
                source_type=sl.source_type,
                source_id=sl.source_id,
                source_name=sl.source_name,
                qty=take,
                ready_date=sl.ready_date,
                notes=sl.notes,
            )
        )
        ready = max(ready, _ready_or_as_of(sl.ready_date, as_of))
        remaining -= take
        notes.append(
            f"Open PO {sl.source_name}: {take} by {sl.ready_date or as_of}."
        )

    if include_work_orders and remaining > QTY_EPS:
        for sl in await open_work_order_slices(db, company_id, item_id):
            if remaining <= QTY_EPS:
                break
            take = min(remaining, sl.qty)
            if take <= QTY_EPS:
                continue
            slices.append(
                OpenSupplySlice(
                    source_type=sl.source_type,
                    source_id=sl.source_id,
                    source_name=sl.source_name,
                    qty=take,
                    ready_date=sl.ready_date,
                    notes=sl.notes,
                )
            )
            ready = max(ready, _ready_or_as_of(sl.ready_date, as_of))
            remaining -= take
            notes.append(
                f"Open WO {sl.source_name}: {take} by {sl.ready_date or as_of}."
            )

    # Unordered MRs: if still short, use earliest MR schedule as planned buy date
    # when present; otherwise fall through to catalog lead from as_of.
    if remaining > QTY_EPS:
        mr_slices = await open_material_request_slices(db, company_id, item_id)
        used_mr = False
        for sl in mr_slices:
            if remaining <= QTY_EPS:
                break
            if sl.ready_date is None:
                continue
            take = min(remaining, sl.qty)
            if take <= QTY_EPS:
                continue
            slices.append(
                OpenSupplySlice(
                    source_type=sl.source_type,
                    source_id=sl.source_id,
                    source_name=sl.source_name,
                    qty=take,
                    ready_date=sl.ready_date,
                    notes=sl.notes,
                )
            )
            # MR schedule_date is "needed by" — assume order placed in time so
            # material arrives by schedule (lean). Residual beyond MR uses catalog.
            ready = max(ready, sl.ready_date)
            remaining -= take
            used_mr = True
            notes.append(
                f"Open MR {sl.source_name}: plan {take} ready by {sl.ready_date}."
            )
        if not used_mr and mr_slices:
            notes.append(
                "Open Material Request(s) without schedule_date — not used for wait."
            )

    if remaining > QTY_EPS:
        buy_ready = as_of + timedelta(days=max(0, catalog_lead_days))
        ready = max(ready, buy_ready)
        notes.append(
            f"Residual {remaining} uses catalog lead {catalog_lead_days}d "
            f"(ready {buy_ready})."
        )
    elif not notes:
        notes.append("Shortfall covered by open supply documents.")

    wait_days = max(0, (ready - as_of).days)
    covered = shortfall_qty - remaining
    return ShortfallCoverPlan(
        shortfall_qty=shortfall_qty,
        covered_qty=covered,
        residual_qty=max(ZERO, remaining),
        materials_ready_date=ready,
        wait_days=wait_days,
        catalog_lead_days=catalog_lead_days,
        slices=slices,
        notes=notes,
    )
