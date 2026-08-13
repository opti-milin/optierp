"""Phase 7.3–7.5 — light demand forecast + workstation capacity board.

Forecast: simple moving average of historical Sales Order demand (by delivery month)
projected forward. Capacity board: open Work Order operation minutes vs workstation
daily working hours (soft / informational — same model as submit warnings).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.base import DOCSTATUS_SUBMITTED
from app.models.manufacturing import WorkOrder, WorkOrderOperation, Workstation
from app.models.selling import SalesOrder, SalesOrderItem
from app.models.stock import Item

ZERO = Decimal("0")


@dataclass(frozen=True)
class ForecastHistoryRow:
    period: str  # YYYY-MM
    demand_qty: Decimal
    is_forecast: bool = False


@dataclass(frozen=True)
class DemandForecast:
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    lookback_months: int
    horizon_months: int
    method: str
    average_monthly_demand: Decimal
    rows: list[ForecastHistoryRow]
    notes: list[str]


@dataclass(frozen=True)
class CapacityBoardRow:
    workstation_id: uuid.UUID
    workstation_name: str
    working_hours_per_day: Decimal
    capacity_mins_per_day: Decimal
    planned_mins: Decimal
    open_work_orders: int
    utilization_pct: Decimal
    overloaded: bool


@dataclass(frozen=True)
class CapacityBoard:
    as_of: date
    rows: list[CapacityBoardRow]
    notes: list[str]


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, months: int) -> date:
    """Shift a date by ``months`` calendar months (day clamped to 1 for period keys)."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, 1)


def _period_key(d: date) -> str:
    return d.strftime("%Y-%m")


async def demand_forecast(
    db: AsyncSession,
    company_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    lookback_months: int = 6,
    horizon_months: int = 3,
    as_of: date | None = None,
) -> DemandForecast:
    """Moving-average demand forecast from Sales Order delivery qty (submitted orders)."""
    if lookback_months < 1 or lookback_months > 36:
        raise ValidationError("lookback_months must be 1–36", field="lookback_months")
    if horizon_months < 1 or horizon_months > 12:
        raise ValidationError("horizon_months must be 1–12", field="horizon_months")

    item = await db.get(Item, item_id)
    if item is None or item.company_id != company_id:
        raise NotFoundError("Item not found")

    as_of_date = as_of or date.today()
    start = _add_months(_month_start(as_of_date), -(lookback_months - 1))
    end = _add_months(_month_start(as_of_date), 1)  # exclusive upper for history

    stmt = (
        select(SalesOrderItem, SalesOrder)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .where(
            SalesOrder.company_id == company_id,
            SalesOrder.docstatus == DOCSTATUS_SUBMITTED,
            SalesOrderItem.item_id == item_id,
        )
    )
    buckets: dict[str, Decimal] = {}
    for soi, so in (await db.execute(stmt)).all():
        due = soi.delivery_date or so.delivery_date
        if due is None:
            continue
        if due < start or due >= end:
            continue
        qty = soi.stock_qty or soi.qty or ZERO
        key = _period_key(due)
        buckets[key] = buckets.get(key, ZERO) + qty

    history: list[ForecastHistoryRow] = []
    cursor = start
    hist_values: list[Decimal] = []
    while cursor < end:
        key = _period_key(cursor)
        qty = buckets.get(key, ZERO)
        history.append(ForecastHistoryRow(period=key, demand_qty=qty, is_forecast=False))
        hist_values.append(qty)
        cursor = _add_months(cursor, 1)

    nonzero = [v for v in hist_values if v > ZERO]
    if nonzero:
        avg = (sum(nonzero, ZERO) / Decimal(len(nonzero))).quantize(Decimal("0.01"))
        method = f"average of {len(nonzero)} non-zero month(s) in lookback"
    elif hist_values:
        avg = ZERO
        method = "no demand in lookback — forecast 0"
    else:
        avg = ZERO
        method = "no history"

    rows = list(history)
    forecast_start = _add_months(_month_start(as_of_date), 1)
    for i in range(horizon_months):
        period = _add_months(forecast_start, i)
        rows.append(
            ForecastHistoryRow(period=_period_key(period), demand_qty=avg, is_forecast=True)
        )

    notes = [
        f"Lookback {lookback_months} month(s) of submitted Sales Order demand by delivery date.",
        f"Method: {method}.",
        "Soft statistical hint only — not a production plan.",
    ]
    return DemandForecast(
        item_id=item.id,
        item_code=item.item_code,
        item_name=item.item_name,
        lookback_months=lookback_months,
        horizon_months=horizon_months,
        method=method,
        average_monthly_demand=avg,
        rows=rows,
        notes=notes,
    )


async def capacity_board(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    as_of: date | None = None,
) -> CapacityBoard:
    """Soft capacity snapshot: open WO planned mins vs workstation mins/day."""
    as_of_date = as_of or date.today()
    workstations = list(
        (
            await db.execute(
                select(Workstation)
                .where(
                    Workstation.company_id == company_id,
                    Workstation.disabled.is_(False),
                )
                .order_by(Workstation.workstation_name)
            )
        ).scalars()
    )

    rows: list[CapacityBoardRow] = []
    notes: list[str] = [
        "Planned minutes = sum of open Work Order operations on each workstation "
        "(Not Started / In Process / Stopped). Compared to one day of working hours.",
        "Informational only — same soft model as Work Order submit warnings.",
    ]

    for ws in workstations:
        capacity = (ws.working_hours * Decimal("60")).quantize(Decimal("0.01"))
        planned_row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(WorkOrderOperation.time_in_mins), ZERO),
                    func.count(func.distinct(WorkOrderOperation.work_order_id)),
                ).where(
                    WorkOrderOperation.workstation_id == ws.id,
                    WorkOrderOperation.work_order_id.in_(
                        select(WorkOrder.id).where(
                            WorkOrder.company_id == company_id,
                            WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
                            WorkOrder.status.in_(("Not Started", "In Process", "Stopped")),
                        )
                    ),
                )
            )
        ).one()
        planned = Decimal(planned_row[0] or 0).quantize(Decimal("0.01"))
        wo_count = int(planned_row[1] or 0)
        util = (
            ((planned / capacity) * Decimal("100")).quantize(Decimal("0.1"))
            if capacity > ZERO
            else ZERO
        )
        rows.append(
            CapacityBoardRow(
                workstation_id=ws.id,
                workstation_name=ws.workstation_name,
                working_hours_per_day=ws.working_hours,
                capacity_mins_per_day=capacity,
                planned_mins=planned,
                open_work_orders=wo_count,
                utilization_pct=util,
                overloaded=capacity > ZERO and planned > capacity,
            )
        )

    if not rows:
        notes.append("No workstations defined — create them at /m/workstation.")

    return CapacityBoard(as_of=as_of_date, rows=rows, notes=notes)
