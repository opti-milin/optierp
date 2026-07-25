"""Phase 7.0 — lead-time rollup + capable-to-promise (CTP) estimate.

Lean first slice of Manufacturing Planning USP: given a finished item + qty, estimate
how many calendar days until materials are ready and how long manufacture takes, then
return an earliest promise date. Not a finite-capacity scheduler — materials wait is
``max(shortfall component lead times)``, manufacture is BOM operation minutes (or the
item's ``lead_time_days`` when there are no operations).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.models.manufacturing import BOM
from app.models.stock import Item
from app.services.bom_explosion import explode_bom, find_item_bom
from app.services.manufacturing_common import item_available_qty

ZERO = Decimal("0")
DEFAULT_HOURS_PER_DAY = Decimal("8")


@dataclass(frozen=True)
class LeadTimeComponentRow:
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    required_qty: Decimal
    available_qty: Decimal
    shortfall_qty: Decimal
    lead_time_days: int
    drives_wait: bool  # True when this shortfall contributes to procurement_days


@dataclass(frozen=True)
class LeadTimeEstimate:
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    bom_id: uuid.UUID | None
    bom_name: str | None
    qty: Decimal
    as_of: date
    warehouse_id: uuid.UUID | None
    procurement_days: int
    manufacturing_days: int
    total_days: int
    earliest_promise_date: date
    operation_mins: Decimal
    components: list[LeadTimeComponentRow]
    notes: list[str]


@dataclass(frozen=True)
class ProcurementSuggestion:
    """Latest date to raise a purchase for a shortfall component (reverse schedule)."""

    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    shortfall_qty: Decimal
    lead_time_days: int
    latest_order_date: date
    days_until_order: int  # negative ⇒ already late vs as_of


@dataclass(frozen=True)
class ReverseSchedule:
    """Work backwards from a customer delivery date using the same lead-time model as CTP."""

    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    bom_id: uuid.UUID | None
    bom_name: str | None
    qty: Decimal
    as_of: date
    delivery_date: date
    warehouse_id: uuid.UUID | None
    procurement_days: int
    manufacturing_days: int
    total_days: int
    earliest_promise_date: date
    manufacturing_start_date: date
    materials_ready_by: date
    on_time: bool
    slack_days: int  # delivery − earliest_promise (negative ⇒ late)
    operation_mins: Decimal
    procurement: list[ProcurementSuggestion]
    components: list[LeadTimeComponentRow]
    notes: list[str]


def _days_from_operation_mins(mins: Decimal, hours_per_day: Decimal = DEFAULT_HOURS_PER_DAY) -> int:
    """Ceil operation minutes into whole working days (8h default)."""
    if mins <= ZERO:
        return 0
    day_mins = hours_per_day * Decimal("60")
    if day_mins <= ZERO:
        day_mins = DEFAULT_HOURS_PER_DAY * Decimal("60")
    return int(math.ceil(float(mins / day_mins)))


async def estimate_lead_time(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
    qty: Decimal,
    *,
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    extra_stock: dict[uuid.UUID, Decimal] | None = None,
    lead_time_overrides: dict[uuid.UUID, int] | None = None,
    deduct_reserved: bool = False,
) -> LeadTimeEstimate:
    """Estimate procurement + manufacturing days and an earliest promise date.

    Optional what-if overrides (not persisted):
    * ``extra_stock`` — pretend additional on-hand qty per component item_id
    * ``lead_time_overrides`` — replace ``Item.lead_time_days`` for listed items
    * ``deduct_reserved`` — use free stock (actual − reserved) for availability
    """
    if qty <= ZERO:
        raise ValidationError("Quantity must be greater than zero", field="qty")

    extra = extra_stock or {}
    lead_ov = lead_time_overrides or {}

    item = await db.get(Item, item_id)
    if item is None or item.company_id != company_id:
        raise NotFoundError("Item not found")

    as_of_date = as_of or date.today()
    notes: list[str] = []
    if extra or lead_ov:
        notes.append("What-if overrides applied (not saved to masters/stock).")
    if deduct_reserved:
        notes.append("Availability uses free stock (on-hand minus reserved).")
    bom = await find_item_bom(db, company_id, item_id)

    components: list[LeadTimeComponentRow] = []
    procurement_days = 0
    operation_mins = ZERO
    manufacturing_days = 0
    bom_name: str | None = None

    def _lead_for(iid: uuid.UUID, default: int) -> int:
        return int(lead_ov[iid]) if iid in lead_ov else default

    def _avail(base: Decimal, iid: uuid.UUID) -> Decimal:
        add = extra.get(iid, ZERO)
        return base + (add if add > ZERO else ZERO)

    if bom is None:
        # Bought / non-BOM item: promise = purchase lead time (stock shortfall only).
        available = _avail(
            await item_available_qty(db, item_id, warehouse_id, deduct_reserved=deduct_reserved),
            item_id,
        )
        shortfall = max(ZERO, qty - available)
        lead = _lead_for(item.id, int(item.lead_time_days or 0))
        drives = shortfall > ZERO and lead > 0
        if shortfall > ZERO:
            procurement_days = lead
            notes.append("No BOM — treating as purchased item (lead_time_days on shortfall).")
        else:
            notes.append("No BOM — enough stock on hand; promise is as-of date.")
        components.append(
            LeadTimeComponentRow(
                item_id=item.id,
                item_code=item.item_code,
                item_name=item.item_name,
                required_qty=qty,
                available_qty=available,
                shortfall_qty=shortfall,
                lead_time_days=lead,
                drives_wait=drives,
            )
        )
    else:
        # Reload with operations for manufacture-time estimate.
        from sqlalchemy import select

        bom = await db.scalar(
            select(BOM)
            .options(
                selectinload(BOM.items),
                selectinload(BOM.operations),
                selectinload(BOM.scrap_items),
            )
            .where(BOM.id == bom.id)
        )
        assert bom is not None
        bom_name = bom.name

        exploded = await explode_bom(db, bom, qty, flatten_all=False)
        for row in exploded:
            comp = await db.get(Item, row.item_id)
            if comp is None:
                continue
            wh = warehouse_id or row.source_warehouse_id
            available = _avail(
                await item_available_qty(
                    db, row.item_id, wh, deduct_reserved=deduct_reserved
                ),
                row.item_id,
            )
            shortfall = max(ZERO, row.stock_qty - available)
            lead = _lead_for(row.item_id, int(comp.lead_time_days or 0))
            drives = shortfall > ZERO
            if drives:
                procurement_days = max(procurement_days, lead)
            components.append(
                LeadTimeComponentRow(
                    item_id=row.item_id,
                    item_code=row.item_code or (comp.item_code if comp else None),
                    item_name=row.item_name or (comp.item_name if comp else None),
                    required_qty=row.stock_qty,
                    available_qty=available,
                    shortfall_qty=shortfall,
                    lead_time_days=lead,
                    drives_wait=drives and lead > 0,
                )
            )

        # Operation minutes scale with FG qty / BOM batch.
        scale = qty / bom.quantity if bom.quantity > ZERO else ZERO
        for op in bom.operations:
            operation_mins += (op.time_in_mins or ZERO) * scale

        if operation_mins > ZERO:
            manufacturing_days = _days_from_operation_mins(operation_mins)
            notes.append(
                f"Manufacturing days from BOM operations "
                f"({operation_mins} mins → {manufacturing_days} day(s) at 8h/day)."
            )
        else:
            manufacturing_days = _lead_for(item.id, int(item.lead_time_days or 0))
            if manufacturing_days:
                notes.append(
                    "No BOM operations — using finished item lead_time_days for manufacture."
                )
            else:
                notes.append("No BOM operations and item lead_time_days is 0.")

        if procurement_days:
            notes.append(
                f"Procurement wait = max lead time among shortfall components "
                f"({procurement_days} day(s))."
            )
        else:
            notes.append("No material shortfall (or shortfall items have 0 lead time).")

    total_days = procurement_days + manufacturing_days
    return LeadTimeEstimate(
        item_id=item.id,
        item_code=item.item_code,
        item_name=item.item_name,
        bom_id=bom.id if bom else None,
        bom_name=bom_name,
        qty=qty,
        as_of=as_of_date,
        warehouse_id=warehouse_id,
        procurement_days=procurement_days,
        manufacturing_days=manufacturing_days,
        total_days=total_days,
        earliest_promise_date=as_of_date + timedelta(days=total_days),
        operation_mins=operation_mins.quantize(Decimal("0.01")),
        components=components,
        notes=notes,
    )


async def reverse_schedule(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
    qty: Decimal,
    delivery_date: date,
    *,
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    deduct_reserved: bool = False,
) -> ReverseSchedule:
    """Backward schedule from ``delivery_date``: when to order materials and start manufacture.

    Uses the forward CTP lead-time model. Soft calendar estimate — not finite capacity.
    """
    as_of_date = as_of or date.today()
    if delivery_date < as_of_date:
        raise ValidationError(
            "Delivery date cannot be before as-of date",
            field="delivery_date",
        )

    est = await estimate_lead_time(
        db,
        company_id,
        item_id,
        qty,
        as_of=as_of_date,
        warehouse_id=warehouse_id,
        deduct_reserved=deduct_reserved,
    )

    manufacturing_start = delivery_date - timedelta(days=est.manufacturing_days)
    materials_ready_by = manufacturing_start
    slack = (delivery_date - est.earliest_promise_date).days
    on_time = slack >= 0

    procurement: list[ProcurementSuggestion] = []
    for row in est.components:
        if row.shortfall_qty <= ZERO:
            continue
        latest_order = materials_ready_by - timedelta(days=row.lead_time_days)
        procurement.append(
            ProcurementSuggestion(
                item_id=row.item_id,
                item_code=row.item_code,
                item_name=row.item_name,
                shortfall_qty=row.shortfall_qty,
                lead_time_days=row.lead_time_days,
                latest_order_date=latest_order,
                days_until_order=(latest_order - as_of_date).days,
            )
        )
    procurement.sort(key=lambda p: (p.latest_order_date, p.item_code or ""))

    notes = list(est.notes)
    notes.append(
        f"Reverse schedule to deliver {delivery_date}: start manufacture by "
        f"{manufacturing_start}; materials ready by {materials_ready_by}."
    )
    if on_time:
        notes.append(f"On time with {slack} day(s) slack vs earliest promise.")
    else:
        notes.append(
            f"Late by {-slack} day(s) — earliest promise is {est.earliest_promise_date}."
        )

    return ReverseSchedule(
        item_id=est.item_id,
        item_code=est.item_code,
        item_name=est.item_name,
        bom_id=est.bom_id,
        bom_name=est.bom_name,
        qty=est.qty,
        as_of=as_of_date,
        delivery_date=delivery_date,
        warehouse_id=warehouse_id,
        procurement_days=est.procurement_days,
        manufacturing_days=est.manufacturing_days,
        total_days=est.total_days,
        earliest_promise_date=est.earliest_promise_date,
        manufacturing_start_date=manufacturing_start,
        materials_ready_by=materials_ready_by,
        on_time=on_time,
        slack_days=slack,
        operation_mins=est.operation_mins,
        procurement=procurement,
        components=est.components,
        notes=notes,
    )
