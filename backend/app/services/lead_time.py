"""Phase 7.0 / 9.0 — lead-time rollup + capable-to-promise (CTP) estimate.

Lean Manufacturing Planning USP: given a finished item + qty, estimate how many
calendar days until materials are ready and how long manufacture takes, then return
an earliest promise date. Not a finite-capacity scheduler.

Phase 7: materials wait = ``max(shortfall component catalog lead times)``.
Phase 9: with ``use_open_supply=True`` (default), prefer open PO / MR / WO dates
from ``open_supply.cover_shortfall``; residual qty still uses catalog lead time.
Manufacture is BOM operation minutes (or the item's ``lead_time_days``).
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
    # Phase 9 — supply-aware detail (optional; None when use_open_supply=False)
    supply_ready_date: date | None = None
    supply_source: str | None = None  # e.g. "Purchase Order PO-…" / "catalog lead"


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
    outbound_days: int  # warehouse → customer transit
    total_days: int  # procurement + manufacturing + outbound
    ready_to_dispatch_date: date  # FG ready at warehouse
    earliest_promise_date: date  # customer receipt (= dispatch + outbound)
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
    """Work backwards from a customer receipt date using the same lead-time model as CTP."""

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
    outbound_days: int
    total_days: int
    earliest_promise_date: date
    ready_to_dispatch_date: date
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
    use_open_supply: bool = True,
    shipping_rule_id: uuid.UUID | None = None,
    outbound_days: int | None = None,
) -> LeadTimeEstimate:
    """Estimate procurement + manufacturing + outbound days and earliest customer receipt.

    Optional what-if overrides (not persisted):
    * ``extra_stock`` — pretend additional on-hand qty per component item_id
    * ``lead_time_overrides`` — replace ``Item.lead_time_days`` for listed items
    * ``deduct_reserved`` — use free stock (actual − reserved) for availability
    * ``use_open_supply`` — Phase 9: prefer open PO/MR/WO dates for shortfall wait
    * ``shipping_rule_id`` / ``outbound_days`` — warehouse→customer transit
    """
    if qty <= ZERO:
        raise ValidationError("Quantity must be greater than zero", field="qty")

    from app.services.shipping import resolve_outbound_days

    outbound = await resolve_outbound_days(
        db,
        company_id,
        shipping_rule_id=shipping_rule_id,
        outbound_days_override=outbound_days,
    )

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
    if use_open_supply:
        notes.append("Supply-aware CTP: open PO/MR/WO dates preferred over catalog lead.")
    if outbound:
        notes.append(
            f"Outbound delivery {outbound} day(s) after ready-to-dispatch "
            f"(warehouse → customer)."
        )
    else:
        notes.append("Outbound delivery 0 days — promise = ready-to-dispatch at warehouse.")
    bom = await find_item_bom(db, company_id, item_id)

    components: list[LeadTimeComponentRow] = []
    procurement_days = 0
    operation_mins = ZERO
    manufacturing_days = 0
    bom_name: str | None = None
    # When open WO covers FG, may shorten / replace manufacture wait.
    fg_wo_ready: date | None = None
    qty_after_wo = qty

    def _lead_for(iid: uuid.UUID, default: int) -> int:
        return int(lead_ov[iid]) if iid in lead_ov else default

    def _avail(base: Decimal, iid: uuid.UUID) -> Decimal:
        add = extra.get(iid, ZERO)
        return base + (add if add > ZERO else ZERO)

    async def _component_wait(
        *,
        comp_id: uuid.UUID,
        code: str | None,
        name: str | None,
        required: Decimal,
        available: Decimal,
        catalog_lead: int,
        include_wo: bool,
    ) -> LeadTimeComponentRow:
        nonlocal procurement_days
        shortfall = max(ZERO, required - available)
        supply_ready: date | None = None
        supply_source: str | None = None
        wait = 0
        if shortfall > ZERO:
            if use_open_supply:
                from app.services.open_supply import cover_shortfall

                plan = await cover_shortfall(
                    db,
                    company_id,
                    comp_id,
                    shortfall,
                    as_of=as_of_date,
                    catalog_lead_days=catalog_lead,
                    include_work_orders=include_wo,
                )
                wait = plan.wait_days
                supply_ready = plan.materials_ready_date
                if plan.slices:
                    first = plan.slices[0]
                    supply_source = f"{first.source_type} {first.source_name or ''}".strip()
                elif plan.residual_qty > ZERO:
                    supply_source = "catalog lead"
                notes.extend(plan.notes)
            else:
                wait = catalog_lead
                supply_source = "catalog lead"
                supply_ready = as_of_date + timedelta(days=catalog_lead)
            procurement_days = max(procurement_days, wait)
        drives = shortfall > ZERO and wait > 0
        return LeadTimeComponentRow(
            item_id=comp_id,
            item_code=code,
            item_name=name,
            required_qty=required,
            available_qty=available,
            shortfall_qty=shortfall,
            lead_time_days=wait if use_open_supply else catalog_lead,
            drives_wait=drives,
            supply_ready_date=supply_ready,
            supply_source=supply_source,
        )

    # Phase 9: open WOs for the FG reduce qty that still needs materials+manufacture.
    if use_open_supply and bom is not None:
        from app.services.open_supply import open_work_order_slices

        remaining_fg = qty
        for sl in await open_work_order_slices(db, company_id, item_id):
            if remaining_fg <= ZERO:
                break
            take = min(remaining_fg, sl.qty)
            remaining_fg -= take
            ready = sl.ready_date or as_of_date
            fg_wo_ready = ready if fg_wo_ready is None else max(fg_wo_ready, ready)
            notes.append(
                f"Open WO {sl.source_name}: {take} FG by {sl.ready_date or as_of_date}."
            )
        qty_after_wo = remaining_fg

    if bom is None:
        # Bought / non-BOM item: promise = purchase lead time (stock shortfall only).
        available = _avail(
            await item_available_qty(db, item_id, warehouse_id, deduct_reserved=deduct_reserved),
            item_id,
        )
        lead = _lead_for(item.id, int(item.lead_time_days or 0))
        components.append(
            await _component_wait(
                comp_id=item.id,
                code=item.item_code,
                name=item.item_name,
                required=qty,
                available=available,
                catalog_lead=lead,
                include_wo=False,
            )
        )
        if components[0].shortfall_qty > ZERO:
            notes.append("No BOM — treating as purchased item (supply-aware or catalog lead).")
        else:
            notes.append("No BOM — enough stock on hand; promise is as-of date.")
        manufacturing_days = 0
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

        # Materials / ops only for qty not already covered by open WOs.
        mfg_qty = qty_after_wo if use_open_supply else qty
        if mfg_qty > ZERO:
            exploded = await explode_bom(db, bom, mfg_qty, flatten_all=False)
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
                lead = _lead_for(row.item_id, int(comp.lead_time_days or 0))
                components.append(
                    await _component_wait(
                        comp_id=row.item_id,
                        code=row.item_code or (comp.item_code if comp else None),
                        name=row.item_name or (comp.item_name if comp else None),
                        required=row.stock_qty,
                        available=available,
                        catalog_lead=lead,
                        include_wo=False,
                    )
                )

            scale = mfg_qty / bom.quantity if bom.quantity > ZERO else ZERO
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
        else:
            notes.append("Open Work Orders cover full demand qty — no new manufacture needed.")
            manufacturing_days = 0

        if procurement_days:
            notes.append(
                f"Procurement wait = max among shortfall components "
                f"({procurement_days} day(s))."
            )
        else:
            notes.append("No material shortfall (or shortfall wait is 0).")

    # Ready-to-dispatch = max(materials+mfg from uncovered qty, open WO ready dates).
    uncovered_dispatch = as_of_date + timedelta(days=procurement_days + manufacturing_days)
    if use_open_supply and fg_wo_ready is not None and qty_after_wo < qty:
        if qty_after_wo <= ZERO:
            ready_dispatch = fg_wo_ready
            # Reflect WO-only path in day counts for display
            procurement_days = 0
            manufacturing_days = max(0, (ready_dispatch - as_of_date).days)
        else:
            ready_dispatch = max(uncovered_dispatch, fg_wo_ready)
    else:
        ready_dispatch = uncovered_dispatch

    customer_receipt = ready_dispatch + timedelta(days=outbound)
    total_days = max(0, (customer_receipt - as_of_date).days)

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
        outbound_days=outbound,
        total_days=total_days,
        ready_to_dispatch_date=ready_dispatch,
        earliest_promise_date=customer_receipt,
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
    shipping_rule_id: uuid.UUID | None = None,
    outbound_days: int | None = None,
) -> ReverseSchedule:
    """Backward schedule from customer *receipt* date.

    ``delivery_date`` is when the customer should receive goods. Dispatch must
    happen ``outbound_days`` earlier. Soft calendar estimate — not finite capacity.
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
        shipping_rule_id=shipping_rule_id,
        outbound_days=outbound_days,
    )

    # Customer receipt = delivery_date → dispatch by receipt − outbound
    ready_to_dispatch = delivery_date - timedelta(days=est.outbound_days)
    manufacturing_start = ready_to_dispatch - timedelta(days=est.manufacturing_days)
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
        f"Reverse schedule for customer receipt {delivery_date}: dispatch by "
        f"{ready_to_dispatch}; start manufacture by {manufacturing_start}; "
        f"materials ready by {materials_ready_by}."
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
        outbound_days=est.outbound_days,
        total_days=est.total_days,
        earliest_promise_date=est.earliest_promise_date,
        ready_to_dispatch_date=ready_to_dispatch,
        manufacturing_start_date=manufacturing_start,
        materials_ready_by=materials_ready_by,
        on_time=on_time,
        slack_days=slack,
        operation_mins=est.operation_mins,
        procurement=procurement,
        components=est.components,
        notes=notes,
    )
