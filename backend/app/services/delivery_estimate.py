"""Phase 9.1 — Sales Order delivery-date timeline (forward + reverse).

Builds a stage chain for each SO line: Sourcing (MR) → Purchase Order → Work Order
→ Finish → Delivery Note, using supply-aware CTP for the promise date. Suggests a
new delivery date when the promise slips — never writes SO.delivery_date.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.base import DOCSTATUS_SUBMITTED
from app.models.manufacturing import WorkOrder
from app.models.selling import SalesOrder, SalesOrderItem
from app.models.stock import DeliveryNote, DeliveryNoteItem, Item, MaterialRequest, MaterialRequestItem
from app.services.bom_explosion import find_item_bom
from app.services.lead_time import estimate_lead_time, reverse_schedule
from app.services.manufacturing_common import item_available_qty
from app.services.open_supply import (
    open_material_request_slices,
    open_purchase_slices,
    open_work_order_slices,
)

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")
AT_RISK_SLACK_DAYS = 3  # slack in [0, AT_RISK) ⇒ at_risk


@dataclass(frozen=True)
class DeliveryStage:
    stage: str  # Demand | Stock | Material Request | Purchase Order | Work Order | Delivery Note
    status: str  # pending | in_progress | done | skipped
    source_type: str | None
    source_id: uuid.UUID | None
    source_name: str | None
    qty: Decimal | None
    planned_date: date | None
    actual_date: date | None
    notes: str | None = None


@dataclass
class DeliveryLineEstimate:
    sales_order_item_id: uuid.UUID
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    qty: Decimal
    pending_qty: Decimal
    promised_date: date | None
    earliest_promise_date: date | None
    suggested_delivery_date: date | None
    manufacturing_start_date: date | None
    materials_ready_by: date | None
    on_time: bool | None
    slack_days: int | None
    health: str  # on_time | at_risk | late | unknown
    stages: list[DeliveryStage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SalesOrderDeliveryEstimate:
    sales_order_id: uuid.UUID
    sales_order_name: str | None
    as_of: date
    promised_date: date | None
    earliest_promise_date: date | None
    suggested_delivery_date: date | None
    on_time: bool | None
    slack_days: int | None
    health: str
    lines: list[DeliveryLineEstimate]
    notes: list[str] = field(default_factory=list)


def _health(on_time: bool | None, slack_days: int | None) -> str:
    if on_time is None or slack_days is None:
        return "unknown"
    if not on_time or slack_days < 0:
        return "late"
    if slack_days < AT_RISK_SLACK_DAYS:
        return "at_risk"
    return "on_time"


def _worst_health(a: str, b: str) -> str:
    order = {"late": 3, "at_risk": 2, "unknown": 1, "on_time": 0}
    return a if order.get(a, 0) >= order.get(b, 0) else b


async def _dn_stages_for_so_item(
    db: AsyncSession,
    company_id: uuid.UUID,
    so_item_id: uuid.UUID,
) -> list[DeliveryStage]:
    stmt = (
        select(DeliveryNoteItem, DeliveryNote)
        .join(DeliveryNote, DeliveryNote.id == DeliveryNoteItem.delivery_note_id)
        .where(
            DeliveryNote.company_id == company_id,
            DeliveryNote.docstatus == DOCSTATUS_SUBMITTED,
            DeliveryNoteItem.sales_order_item_id == so_item_id,
        )
        .order_by(DeliveryNote.posting_date.asc())
    )
    stages: list[DeliveryStage] = []
    for dni, dn in (await db.execute(stmt)).all():
        stages.append(
            DeliveryStage(
                stage="Delivery Note",
                status="done",
                source_type="Delivery Note",
                source_id=dn.id,
                source_name=dn.name,
                qty=dni.stock_qty or dni.qty,
                planned_date=dn.posting_date,
                actual_date=dn.posting_date,
                notes=f"Delivered — status {dn.status}",
            )
        )
    return stages


async def estimate_sales_order_delivery(
    db: AsyncSession,
    company_id: uuid.UUID,
    sales_order_id: uuid.UUID,
    *,
    as_of: date | None = None,
) -> SalesOrderDeliveryEstimate:
    """Build supply-aware forward/reverse delivery estimate for one Sales Order."""
    so = await db.get(SalesOrder, sales_order_id)
    if so is None or so.company_id != company_id:
        raise NotFoundError("Sales Order not found")

    as_of_date = as_of or date.today()
    items_stmt = (
        select(SalesOrderItem)
        .where(SalesOrderItem.order_id == so.id)
        .order_by(SalesOrderItem.idx)
    )
    so_items = list((await db.execute(items_stmt)).scalars())

    lines: list[DeliveryLineEstimate] = []
    header_notes: list[str] = [
        "Suggest-only: apply suggested_delivery_date manually — SO dates are never auto-written.",
        "Supply-aware CTP uses open PO / MR / WO dates when present.",
    ]
    order_earliest: date | None = None
    order_health = "on_time"

    for soi in so_items:
        if soi.item_id is None:
            continue
        item = await db.get(Item, soi.item_id)
        if item is None:
            continue
        pending = max(ZERO, (soi.stock_qty or soi.qty) - (soi.delivered_qty or ZERO))
        promised = soi.delivery_date or so.delivery_date
        wh = soi.warehouse_id or so.set_warehouse_id
        stages: list[DeliveryStage] = [
            DeliveryStage(
                stage="Demand",
                status="done" if pending <= QTY_EPS else "in_progress",
                source_type="Sales Order",
                source_id=so.id,
                source_name=so.name,
                qty=pending if pending > QTY_EPS else (soi.stock_qty or soi.qty),
                planned_date=promised,
                actual_date=None,
                notes=f"SO line — status {so.status}",
            )
        ]

        on_hand = await item_available_qty(db, item.id, wh)
        if on_hand > QTY_EPS:
            stages.append(
                DeliveryStage(
                    stage="Stock",
                    status="done",
                    source_type="Stock",
                    source_id=wh,
                    source_name="On hand",
                    qty=on_hand,
                    planned_date=as_of_date,
                    actual_date=as_of_date,
                    notes="Available stock",
                )
            )

        line_notes: list[str] = []
        earliest: date | None = None
        mfg_start: date | None = None
        materials_ready: date | None = None
        on_time: bool | None = None
        slack: int | None = None
        suggested: date | None = None

        if pending <= QTY_EPS:
            dn_stages = await _dn_stages_for_so_item(db, company_id, soi.id)
            stages.extend(dn_stages)
            if not dn_stages:
                stages.append(
                    DeliveryStage(
                        stage="Delivery Note",
                        status="done",
                        source_type=None,
                        source_id=None,
                        source_name=None,
                        qty=ZERO,
                        planned_date=None,
                        actual_date=None,
                        notes="Fully delivered (no DN link found)",
                    )
                )
            line_notes.append("Line fully delivered.")
            health = "on_time"
        else:
            # Linked / open supply stages for the FG (and BOM components' POs/MRs).
            for sl in await open_material_request_slices(db, company_id, item.id):
                stages.append(
                    DeliveryStage(
                        stage="Material Request",
                        status="in_progress",
                        source_type=sl.source_type,
                        source_id=sl.source_id,
                        source_name=sl.source_name,
                        qty=sl.qty,
                        planned_date=sl.ready_date,
                        actual_date=None,
                        notes=sl.notes,
                    )
                )
            for sl in await open_purchase_slices(db, company_id, item.id):
                stages.append(
                    DeliveryStage(
                        stage="Purchase Order",
                        status="in_progress",
                        source_type=sl.source_type,
                        source_id=sl.source_id,
                        source_name=sl.source_name,
                        qty=sl.qty,
                        planned_date=sl.ready_date,
                        actual_date=None,
                        notes=sl.notes,
                    )
                )
            for sl in await open_work_order_slices(db, company_id, item.id):
                stages.append(
                    DeliveryStage(
                        stage="Work Order",
                        status="in_progress",
                        source_type=sl.source_type,
                        source_id=sl.source_id,
                        source_name=sl.source_name,
                        qty=sl.qty,
                        planned_date=sl.ready_date,
                        actual_date=None,
                        notes=sl.notes,
                    )
                )
            # Also surface component POs when FG has a BOM (sourcing chain).
            bom = await find_item_bom(db, company_id, item.id)
            if bom is not None:
                from app.services.bom_explosion import explode_bom

                exploded = await explode_bom(db, bom, pending, flatten_all=False)
                seen_comp: set[uuid.UUID] = set()
                for row in exploded:
                    if row.item_id in seen_comp:
                        continue
                    seen_comp.add(row.item_id)
                    for sl in await open_purchase_slices(db, company_id, row.item_id):
                        stages.append(
                            DeliveryStage(
                                stage="Purchase Order",
                                status="in_progress",
                                source_type=sl.source_type,
                                source_id=sl.source_id,
                                source_name=sl.source_name,
                                qty=sl.qty,
                                planned_date=sl.ready_date,
                                actual_date=None,
                                notes=f"Component {row.item_code or row.item_id}: {sl.notes}",
                            )
                        )
                    for sl in await open_material_request_slices(db, company_id, row.item_id):
                        stages.append(
                            DeliveryStage(
                                stage="Material Request",
                                status="in_progress",
                                source_type=sl.source_type,
                                source_id=sl.source_id,
                                source_name=sl.source_name,
                                qty=sl.qty,
                                planned_date=sl.ready_date,
                                actual_date=None,
                                notes=f"Component {row.item_code or row.item_id}: {sl.notes}",
                            )
                        )

            # WO linked explicitly to this SO
            wo_stmt = (
                select(WorkOrder)
                .where(
                    WorkOrder.company_id == company_id,
                    WorkOrder.sales_order_id == so.id,
                    WorkOrder.production_item_id == item.id,
                    WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
                )
                .order_by(WorkOrder.planned_end_date.asc().nulls_last())
            )
            for wo in (await db.execute(wo_stmt)).scalars():
                if any(s.source_id == wo.id for s in stages):
                    continue
                pending_wo = max(ZERO, wo.qty - wo.produced_qty)
                stages.append(
                    DeliveryStage(
                        stage="Work Order",
                        status="done" if pending_wo <= QTY_EPS else "in_progress",
                        source_type="Work Order",
                        source_id=wo.id,
                        source_name=wo.name,
                        qty=pending_wo if pending_wo > QTY_EPS else wo.qty,
                        planned_date=wo.planned_end_date or wo.planned_start_date,
                        actual_date=None,
                        notes=f"Linked to SO — status {wo.status}",
                    )
                )

            stages.extend(await _dn_stages_for_so_item(db, company_id, soi.id))

            est = await estimate_lead_time(
                db,
                company_id,
                item.id,
                pending,
                as_of=as_of_date,
                warehouse_id=wh,
                use_open_supply=True,
            )
            earliest = est.earliest_promise_date
            suggested = earliest
            line_notes.extend(est.notes)

            stages.append(
                DeliveryStage(
                    stage="Dispatch",
                    status="pending",
                    source_type=None,
                    source_id=None,
                    source_name="Ready to dispatch",
                    qty=pending,
                    planned_date=est.ready_to_dispatch_date,
                    actual_date=None,
                    notes=f"FG ready at warehouse ({est.outbound_days}d outbound after this)",
                )
            )
            stages.append(
                DeliveryStage(
                    stage="Customer receipt",
                    status="pending",
                    source_type=None,
                    source_id=None,
                    source_name="In transit / delivery",
                    qty=pending,
                    planned_date=est.earliest_promise_date,
                    actual_date=None,
                    notes=(
                        f"Outbound transit {est.outbound_days} day(s) "
                        f"(warehouse → customer)"
                        if est.outbound_days
                        else "No outbound transit configured (0 days)"
                    ),
                )
            )

            if promised is not None and promised >= as_of_date:
                rev = await reverse_schedule(
                    db,
                    company_id,
                    item.id,
                    pending,
                    promised,
                    as_of=as_of_date,
                    warehouse_id=wh,
                )
                mfg_start = rev.manufacturing_start_date
                materials_ready = rev.materials_ready_by
                on_time = rev.on_time
                slack = rev.slack_days
                earliest = rev.earliest_promise_date
                suggested = earliest
                line_notes.extend(
                    n for n in rev.notes if n not in line_notes
                )
            elif promised is not None:
                slack = (promised - earliest).days
                on_time = slack >= 0
                line_notes.append("Promised date is before as-of; treated as late.")
            else:
                line_notes.append("No promised delivery date on SO line/header.")

            health = _health(on_time, slack)
            if on_time is False and suggested is not None:
                line_notes.append(
                    f"Suggest delivery date {suggested} (do not auto-write)."
                )

        if earliest is not None:
            order_earliest = (
                earliest if order_earliest is None else max(order_earliest, earliest)
            )
        order_health = _worst_health(order_health, health)

        lines.append(
            DeliveryLineEstimate(
                sales_order_item_id=soi.id,
                item_id=item.id,
                item_code=item.item_code,
                item_name=item.item_name,
                qty=soi.stock_qty or soi.qty,
                pending_qty=pending,
                promised_date=promised,
                earliest_promise_date=earliest,
                suggested_delivery_date=suggested,
                manufacturing_start_date=mfg_start,
                materials_ready_by=materials_ready,
                on_time=on_time,
                slack_days=slack,
                health=health,
                stages=stages,
                notes=line_notes,
            )
        )

    header_promised = so.delivery_date
    header_slack: int | None = None
    header_on_time: bool | None = None
    if order_earliest is not None and header_promised is not None:
        header_slack = (header_promised - order_earliest).days
        header_on_time = header_slack >= 0
        order_health = _worst_health(order_health, _health(header_on_time, header_slack))
    elif lines:
        # Aggregate from lines when header has no date
        for ln in lines:
            if ln.health != "unknown":
                order_health = _worst_health(order_health, ln.health)

    suggested_header = order_earliest
    return SalesOrderDeliveryEstimate(
        sales_order_id=so.id,
        sales_order_name=so.name,
        as_of=as_of_date,
        promised_date=header_promised,
        earliest_promise_date=order_earliest,
        suggested_delivery_date=suggested_header,
        on_time=header_on_time,
        slack_days=header_slack,
        health=order_health if lines else "unknown",
        lines=lines,
        notes=header_notes,
    )
