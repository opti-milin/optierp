"""Order fulfillability + estimated cost-to-serve for Sales Orders and Quotations.

Composes Phase 7 CTP / reverse-schedule with BOM cost rollup so selling can answer:
"Can we deliver by the promised date, and roughly at what manufacturing cost?"
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.stock import Item
from app.services.bom_explosion import find_item_bom
from app.services.lead_time import estimate_lead_time, reverse_schedule
from app.services.manufacturing_common import (
    get_manufacturing_settings,
    resolve_valuation_rate,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass
class FulfillmentShortfall:
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    shortfall_qty: Decimal
    estimated_buy_cost: Decimal


@dataclass
class FulfillmentLineResult:
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    qty: Decimal
    delivery_date: date | None
    warehouse_id: uuid.UUID | None
    bom_id: uuid.UUID | None
    bom_name: str | None
    on_time: bool | None
    earliest_promise_date: date | None
    bom_cost_per_unit: Decimal
    estimated_cost: Decimal
    selling_amount: Decimal
    estimated_margin: Decimal
    estimated_margin_pct: Decimal | None
    shortfalls: list[FulfillmentShortfall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: bool = False


@dataclass
class OrderFulfillmentResult:
    mode: str  # off | warn | block
    can_fulfill_on_time: bool | None
    earliest_promise_date: date | None
    estimated_cost: Decimal
    estimated_selling_amount: Decimal
    estimated_margin: Decimal
    estimated_margin_pct: Decimal | None
    lines: list[FulfillmentLineResult]
    warnings: list[str]
    hard_block_reasons: list[str]
    planning_dashboard_path: str


@dataclass(frozen=True)
class FulfillmentLineIn:
    item_id: uuid.UUID
    qty: Decimal
    selling_amount: Decimal = ZERO
    delivery_date: date | None = None
    warehouse_id: uuid.UUID | None = None


def _margin_pct(margin: Decimal, selling: Decimal) -> Decimal | None:
    if selling <= ZERO:
        return None
    return (margin / selling * HUNDRED).quantize(Decimal("0.01"))


def _dashboard_path(
    *,
    context: str,
    document_id: uuid.UUID | None,
) -> str:
    if document_id is not None and context in ("sales-order", "quotation"):
        return f"/manufacturing-planning?context={context}&id={document_id}"
    return "/manufacturing-planning"


async def check_order_fulfillment(
    db: AsyncSession,
    company_id: uuid.UUID,
    lines: list[FulfillmentLineIn],
    *,
    header_delivery_date: date | None = None,
    as_of: date | None = None,
    document_context: str = "sales-order",
    document_id: uuid.UUID | None = None,
    force: bool = False,
) -> OrderFulfillmentResult:
    """Evaluate manufacturable / stock lines for promise date + BOM cost vs selling.

    When mode is ``off`` and ``force`` is False, returns an empty pass-through result
    (no CTP work). Callers that power the explicit "Check fulfillment" button should
    pass ``force=True``.
    """
    settings = await get_manufacturing_settings(db, company_id)
    mode = str(settings.get("order_fulfillment_mode") or "warn")
    as_of_date = as_of or date.today()
    path = _dashboard_path(context=document_context, document_id=document_id)

    empty = OrderFulfillmentResult(
        mode=mode,
        can_fulfill_on_time=None,
        earliest_promise_date=None,
        estimated_cost=ZERO,
        estimated_selling_amount=ZERO,
        estimated_margin=ZERO,
        estimated_margin_pct=None,
        lines=[],
        warnings=[],
        hard_block_reasons=[],
        planning_dashboard_path=path,
    )
    if mode == "off" and not force:
        return empty
    if not lines:
        empty.warnings.append("No lines to check for fulfillment.")
        return empty

    results: list[FulfillmentLineResult] = []
    warnings: list[str] = []
    hard_blocks: list[str] = []
    any_timed = False
    all_on_time = True
    max_promise: date | None = None
    total_cost = ZERO
    total_selling = ZERO

    for line in lines:
        if line.qty <= ZERO or line.item_id is None:
            continue
        item = await db.get(Item, line.item_id)
        if item is None or item.company_id != company_id:
            continue
        # Skip non-stock / explicitly excluded from manufacturing planning
        if not item.is_stock_item or not getattr(item, "include_item_in_manufacturing", True):
            results.append(
                FulfillmentLineResult(
                    item_id=item.id,
                    item_code=item.item_code,
                    item_name=item.item_name,
                    qty=line.qty,
                    delivery_date=line.delivery_date or header_delivery_date,
                    warehouse_id=line.warehouse_id,
                    bom_id=None,
                    bom_name=None,
                    on_time=None,
                    earliest_promise_date=None,
                    bom_cost_per_unit=ZERO,
                    estimated_cost=ZERO,
                    selling_amount=line.selling_amount,
                    estimated_margin=line.selling_amount,
                    estimated_margin_pct=_margin_pct(line.selling_amount, line.selling_amount),
                    notes=["Skipped — non-stock or not included in manufacturing."],
                    skipped=True,
                )
            )
            total_selling += line.selling_amount
            continue

        delivery = line.delivery_date or header_delivery_date
        bom = await find_item_bom(db, company_id, item.id)
        cost_per_unit = bom.cost_per_unit if bom is not None else ZERO
        if bom is None:
            # Purchased FG: estimate unit cost from valuation
            cost_per_unit = await resolve_valuation_rate(db, item)
        estimated_cost = (cost_per_unit * line.qty).quantize(Decimal("0.01"))
        margin = (line.selling_amount - estimated_cost).quantize(Decimal("0.01"))

        shortfalls: list[FulfillmentShortfall] = []
        notes: list[str] = []
        on_time: bool | None = None
        promise: date | None = None

        try:
            if delivery is not None:
                rev = await reverse_schedule(
                    db,
                    company_id,
                    item.id,
                    line.qty,
                    delivery,
                    as_of=as_of_date,
                    warehouse_id=line.warehouse_id,
                    deduct_reserved=True,
                )
                on_time = rev.on_time
                promise = rev.earliest_promise_date
                notes.extend(rev.notes)
                for row in rev.components:
                    if row.shortfall_qty <= ZERO:
                        continue
                    comp = await db.get(Item, row.item_id)
                    rate = await resolve_valuation_rate(db, comp) if comp else ZERO
                    shortfalls.append(
                        FulfillmentShortfall(
                            item_id=row.item_id,
                            item_code=row.item_code,
                            item_name=row.item_name,
                            shortfall_qty=row.shortfall_qty,
                            estimated_buy_cost=(row.shortfall_qty * rate).quantize(
                                Decimal("0.01")
                            ),
                        )
                    )
            else:
                est = await estimate_lead_time(
                    db,
                    company_id,
                    item.id,
                    line.qty,
                    as_of=as_of_date,
                    warehouse_id=line.warehouse_id,
                    deduct_reserved=True,
                )
                promise = est.earliest_promise_date
                notes.extend(est.notes)
                notes.append("No delivery date — on-time not evaluated.")
                for row in est.components:
                    if row.shortfall_qty <= ZERO:
                        continue
                    comp = await db.get(Item, row.item_id)
                    rate = await resolve_valuation_rate(db, comp) if comp else ZERO
                    shortfalls.append(
                        FulfillmentShortfall(
                            item_id=row.item_id,
                            item_code=row.item_code,
                            item_name=row.item_name,
                            shortfall_qty=row.shortfall_qty,
                            estimated_buy_cost=(row.shortfall_qty * rate).quantize(
                                Decimal("0.01")
                            ),
                        )
                    )
        except ValidationError as exc:
            notes.append(str(exc.detail) if hasattr(exc, "detail") else str(exc))
            label = item.item_code or item.item_name
            hard_blocks.append(f"{label}: {notes[-1]}")
            on_time = False

        if bom is None and item.is_stock_item:
            notes.append("No active BOM — cost from item valuation; CTP as purchased item.")

        label = item.item_code or item.item_name
        if on_time is False and delivery is not None and promise is not None:
            msg = (
                f"{label}: cannot meet delivery {delivery} "
                f"(earliest promise {promise})"
            )
            warnings.append(msg)
            hard_blocks.append(msg)
            all_on_time = False
            any_timed = True
        elif on_time is True:
            any_timed = True
        elif on_time is None and promise is not None:
            warnings.append(f"{label}: earliest promise {promise} (no delivery date set)")

        if promise is not None:
            max_promise = promise if max_promise is None else max(max_promise, promise)

        total_cost += estimated_cost
        total_selling += line.selling_amount

        results.append(
            FulfillmentLineResult(
                item_id=item.id,
                item_code=item.item_code,
                item_name=item.item_name,
                qty=line.qty,
                delivery_date=delivery,
                warehouse_id=line.warehouse_id,
                bom_id=bom.id if bom else None,
                bom_name=bom.name if bom else None,
                on_time=on_time,
                earliest_promise_date=promise,
                bom_cost_per_unit=cost_per_unit.quantize(Decimal("0.01")),
                estimated_cost=estimated_cost,
                selling_amount=line.selling_amount,
                estimated_margin=margin,
                estimated_margin_pct=_margin_pct(margin, line.selling_amount),
                shortfalls=shortfalls,
                notes=notes,
            )
        )

    can_fulfill: bool | None
    if not any_timed:
        can_fulfill = None
    else:
        can_fulfill = all_on_time

    total_margin = (total_selling - total_cost).quantize(Decimal("0.01"))
    return OrderFulfillmentResult(
        mode=mode,
        can_fulfill_on_time=can_fulfill,
        earliest_promise_date=max_promise,
        estimated_cost=total_cost.quantize(Decimal("0.01")),
        estimated_selling_amount=total_selling.quantize(Decimal("0.01")),
        estimated_margin=total_margin,
        estimated_margin_pct=_margin_pct(total_margin, total_selling),
        lines=results,
        warnings=warnings,
        hard_block_reasons=hard_blocks if mode == "block" else [],
        planning_dashboard_path=path,
    )


def raise_if_blocked(result: OrderFulfillmentResult) -> None:
    """Raise ValidationError when mode is block and there are hard-block reasons."""
    if result.mode == "block" and result.hard_block_reasons:
        raise ValidationError(
            "; ".join(result.hard_block_reasons),
            code="FULFILLMENT_BLOCKED",
            field="delivery_date",
        )


def fulfillment_result_to_dict(result: OrderFulfillmentResult) -> dict:
    """Serialize for Pydantic OrderFulfillmentOut.model_validate."""
    return {
        "mode": result.mode,
        "can_fulfill_on_time": result.can_fulfill_on_time,
        "earliest_promise_date": result.earliest_promise_date,
        "estimated_cost": result.estimated_cost,
        "estimated_selling_amount": result.estimated_selling_amount,
        "estimated_margin": result.estimated_margin,
        "estimated_margin_pct": result.estimated_margin_pct,
        "lines": [
            {
                "item_id": ln.item_id,
                "item_code": ln.item_code,
                "item_name": ln.item_name,
                "qty": ln.qty,
                "delivery_date": ln.delivery_date,
                "warehouse_id": ln.warehouse_id,
                "bom_id": ln.bom_id,
                "bom_name": ln.bom_name,
                "on_time": ln.on_time,
                "earliest_promise_date": ln.earliest_promise_date,
                "bom_cost_per_unit": ln.bom_cost_per_unit,
                "estimated_cost": ln.estimated_cost,
                "selling_amount": ln.selling_amount,
                "estimated_margin": ln.estimated_margin,
                "estimated_margin_pct": ln.estimated_margin_pct,
                "shortfalls": [
                    {
                        "item_id": s.item_id,
                        "item_code": s.item_code,
                        "item_name": s.item_name,
                        "shortfall_qty": s.shortfall_qty,
                        "estimated_buy_cost": s.estimated_buy_cost,
                    }
                    for s in ln.shortfalls
                ],
                "notes": ln.notes,
                "skipped": ln.skipped,
            }
            for ln in result.lines
        ],
        "warnings": result.warnings,
        "hard_block_reasons": result.hard_block_reasons,
        "planning_dashboard_path": result.planning_dashboard_path,
    }
