"""Manufacturing report endpoints — read-only."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.manufacturing import (
    BOMExplorerReport,
    BOMStockReport,
    BOMWhereUsedRow,
    CapacityBoardOut,
    CapacityBoardRowOut,
    DemandForecastOut,
    ForecastHistoryRowOut,
    LeadTimeComponentRowOut,
    LeadTimeEstimateOut,
    MaterialShortageRow,
    PeggingRowOut,
    PeggingTimelineOut,
    ProcurementSuggestionOut,
    ProductionAnalyticsRow,
    ProductionRegisterRow,
    ReverseScheduleOut,
    WhatIfCtpIn,
    WorkOrderSummaryRow,
)
from app.services import lead_time as lead_time_svc
from app.services import manufacturing_reports as svc
from app.services import mfg_planning as planning_svc
from app.services import pegging as pegging_svc
from app.services.lead_time import LeadTimeEstimate, ReverseSchedule

router = APIRouter(prefix="/manufacturing-reports", tags=["manufacturing: reports"])


def _lead_time_out(est: LeadTimeEstimate) -> LeadTimeEstimateOut:
    return LeadTimeEstimateOut(
        item_id=est.item_id,
        item_code=est.item_code,
        item_name=est.item_name,
        bom_id=est.bom_id,
        bom_name=est.bom_name,
        qty=est.qty,
        as_of=est.as_of,
        warehouse_id=est.warehouse_id,
        procurement_days=est.procurement_days,
        manufacturing_days=est.manufacturing_days,
        outbound_days=est.outbound_days,
        total_days=est.total_days,
        ready_to_dispatch_date=est.ready_to_dispatch_date,
        earliest_promise_date=est.earliest_promise_date,
        operation_mins=est.operation_mins,
        components=[LeadTimeComponentRowOut(**c.__dict__) for c in est.components],
        notes=list(est.notes),
    )


def _reverse_out(plan: ReverseSchedule) -> ReverseScheduleOut:
    return ReverseScheduleOut(
        item_id=plan.item_id,
        item_code=plan.item_code,
        item_name=plan.item_name,
        bom_id=plan.bom_id,
        bom_name=plan.bom_name,
        qty=plan.qty,
        as_of=plan.as_of,
        delivery_date=plan.delivery_date,
        warehouse_id=plan.warehouse_id,
        procurement_days=plan.procurement_days,
        manufacturing_days=plan.manufacturing_days,
        outbound_days=plan.outbound_days,
        total_days=plan.total_days,
        earliest_promise_date=plan.earliest_promise_date,
        ready_to_dispatch_date=plan.ready_to_dispatch_date,
        manufacturing_start_date=plan.manufacturing_start_date,
        materials_ready_by=plan.materials_ready_by,
        on_time=plan.on_time,
        slack_days=plan.slack_days,
        operation_mins=plan.operation_mins,
        procurement=[ProcurementSuggestionOut(**p.__dict__) for p in plan.procurement],
        components=[LeadTimeComponentRowOut(**c.__dict__) for c in plan.components],
        notes=list(plan.notes),
    )


@router.get(
    "/production-register",
    response_model=list[ProductionRegisterRow],
    summary="Production Register",
    description="Every Work Order's planned-vs-produced position and estimated cost.",
)
async def production_register(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    status: str | None = None,
    production_item_id: uuid.UUID | None = None,
) -> list[ProductionRegisterRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.production_register(
        db, current_user.company_id, status=status, production_item_id=production_item_id
    )


@router.get(
    "/material-shortage",
    response_model=list[MaterialShortageRow],
    summary="Material Shortage (all open Work Orders)",
    description="Aggregate still-to-consume component demand across every open Work Order "
    "vs on-hand stock — the components to buy before production stalls.",
)
async def material_shortage(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    only_short: bool = False,
) -> list[MaterialShortageRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.material_shortage(db, current_user.company_id, only_short=only_short)


@router.get(
    "/bom-where-used",
    response_model=list[BOMWhereUsedRow],
    summary="BOM Where-Used",
    description="Which BOMs consume a given item as a component.",
)
async def bom_where_used(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[BOMWhereUsedRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.bom_where_used(db, current_user.company_id, item_id)


@router.get(
    "/bom-stock",
    response_model=BOMStockReport,
    summary="BOM Stock report (can I build N?)",
    description="For a target finished quantity, each component's need vs on-hand stock and "
    "the maximum finished units the current stock supports. Phantoms are exploded.",
)
async def bom_stock(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    for_qty: Annotated[Decimal, Query(gt=0)] = Decimal("1"),
) -> BOMStockReport:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.bom_stock_report(db, bom_id, current_user.company_id, for_qty=for_qty)


@router.get(
    "/bom-explorer",
    response_model=BOMExplorerReport,
    summary="BOM Explorer (flatten nested BOM)",
    description="Flatten a multi-level BOM to its leaf-level raw materials (default) or to "
    "the same shape a Work Order would explode (phantoms only).",
)
async def bom_explorer(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    for_qty: Annotated[Decimal, Query(gt=0)] = Decimal("1"),
    flatten_all: bool = True,
) -> BOMExplorerReport:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.bom_explorer(
        db, bom_id, current_user.company_id, for_qty=for_qty, flatten_all=flatten_all
    )


@router.get(
    "/work-order-summary",
    response_model=list[WorkOrderSummaryRow],
    summary="Work Order Summary",
    description="Roll-up of Work Orders by status (count, qty, produced, cost).",
)
async def work_order_summary(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[WorkOrderSummaryRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.work_order_summary(db, current_user.company_id)


@router.get(
    "/production-analytics",
    response_model=list[ProductionAnalyticsRow],
    summary="Production Analytics",
    description="Monthly completed production totals (qty + estimated cost).",
)
async def production_analytics(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ProductionAnalyticsRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.production_analytics(db, current_user.company_id)


@router.get(
    "/capable-to-promise",
    response_model=LeadTimeEstimateOut,
    summary="Capable-to-promise (lead-time estimate)",
    description="Phase 7.0 / 9.0: estimate procurement + manufacturing + outbound transit "
    "for an item/qty. earliest_promise_date is customer receipt "
    "(ready_to_dispatch + outbound_days). Prefers open PO/MR/WO dates when present. "
    "Soft estimate — not finite capacity.",
)
async def capable_to_promise(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    qty: Annotated[Decimal, Query(gt=0)] = Decimal("1"),
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    outbound_days: Annotated[int | None, Query(ge=0, le=365)] = None,
    shipping_rule_id: uuid.UUID | None = None,
) -> LeadTimeEstimateOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    est = await lead_time_svc.estimate_lead_time(
        db,
        current_user.company_id,
        item_id,
        qty,
        as_of=as_of,
        warehouse_id=warehouse_id,
        outbound_days=outbound_days,
        shipping_rule_id=shipping_rule_id,
    )
    return _lead_time_out(est)


@router.get(
    "/reverse-schedule",
    response_model=ReverseScheduleOut,
    summary="Reverse schedule from delivery date",
    description="Phase 7.1 USP: work backwards from a customer *receipt* date — when to "
    "dispatch (minus outbound transit), start manufacture, when materials must be ready, "
    "and latest purchase order dates per shortfall. Soft calendar estimate — not finite capacity.",
)
async def reverse_schedule(
    item_id: uuid.UUID,
    delivery_date: date,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    qty: Annotated[Decimal, Query(gt=0)] = Decimal("1"),
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    outbound_days: Annotated[int | None, Query(ge=0, le=365)] = None,
    shipping_rule_id: uuid.UUID | None = None,
) -> ReverseScheduleOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    plan = await lead_time_svc.reverse_schedule(
        db,
        current_user.company_id,
        item_id,
        qty,
        delivery_date,
        as_of=as_of,
        warehouse_id=warehouse_id,
        outbound_days=outbound_days,
        shipping_rule_id=shipping_rule_id,
    )
    return _reverse_out(plan)


@router.get(
    "/pegging",
    response_model=PeggingTimelineOut,
    summary="Demand → supply pegging timeline",
    description="Phase 7.2 / 9.0: for one item, list open Sales Order demand vs stock / "
    "open Work Orders / Purchase Orders / Manufacture MRs, net shortfall, and "
    "supply-aware CTP for uncovered qty.",
)
async def pegging(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> PeggingTimelineOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    timeline = await pegging_svc.pegging_timeline(
        db,
        current_user.company_id,
        item_id,
        as_of=as_of,
        warehouse_id=warehouse_id,
        from_date=from_date,
        to_date=to_date,
    )
    return PeggingTimelineOut(
        item_id=timeline.item_id,
        item_code=timeline.item_code,
        item_name=timeline.item_name,
        as_of=timeline.as_of,
        warehouse_id=timeline.warehouse_id,
        demand_qty=timeline.demand_qty,
        supply_qty=timeline.supply_qty,
        net_shortfall=timeline.net_shortfall,
        earliest_promise_date=timeline.earliest_promise_date,
        ctp=_lead_time_out(timeline.ctp) if timeline.ctp is not None else None,
        rows=[PeggingRowOut(**r.__dict__) for r in timeline.rows],
        notes=list(timeline.notes),
    )


@router.get(
    "/demand-forecast",
    response_model=DemandForecastOut,
    summary="Light demand forecast (moving average)",
    description="Phase 7.3 USP: average monthly Sales Order demand over a lookback window "
    "and project the same average forward for a horizon. Soft hint — not a production plan.",
)
async def demand_forecast(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    lookback_months: Annotated[int, Query(ge=1, le=36)] = 6,
    horizon_months: Annotated[int, Query(ge=1, le=12)] = 3,
    as_of: date | None = None,
) -> DemandForecastOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    fc = await planning_svc.demand_forecast(
        db,
        current_user.company_id,
        item_id,
        lookback_months=lookback_months,
        horizon_months=horizon_months,
        as_of=as_of,
    )
    return DemandForecastOut(
        item_id=fc.item_id,
        item_code=fc.item_code,
        item_name=fc.item_name,
        lookback_months=fc.lookback_months,
        horizon_months=fc.horizon_months,
        method=fc.method,
        average_monthly_demand=fc.average_monthly_demand,
        rows=[ForecastHistoryRowOut(**r.__dict__) for r in fc.rows],
        notes=list(fc.notes),
    )


@router.post(
    "/what-if-ctp",
    response_model=LeadTimeEstimateOut,
    summary="What-if capable-to-promise",
    description="Phase 7.4 USP: run CTP with temporary extra stock and/or lead-time "
    "overrides. Nothing is persisted to masters or stock.",
)
async def what_if_ctp(
    body: WhatIfCtpIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> LeadTimeEstimateOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    extra = {row.item_id: row.extra_qty for row in body.extra_stock}
    leads = {row.item_id: row.lead_time_days for row in body.lead_time_overrides}
    est = await lead_time_svc.estimate_lead_time(
        db,
        current_user.company_id,
        body.item_id,
        body.qty,
        as_of=body.as_of,
        warehouse_id=body.warehouse_id,
        extra_stock=extra or None,
        lead_time_overrides=leads or None,
    )
    return _lead_time_out(est)


@router.get(
    "/capacity-board",
    response_model=CapacityBoardOut,
    summary="Workstation capacity board (soft)",
    description="Phase 7.5 USP: open Work Order planned minutes vs each workstation's "
    "daily working hours. Informational — same soft model as submit warnings.",
)
async def capacity_board(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date | None = None,
) -> CapacityBoardOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    board = await planning_svc.capacity_board(
        db, current_user.company_id, as_of=as_of
    )
    return CapacityBoardOut(
        as_of=board.as_of,
        rows=[CapacityBoardRowOut(**r.__dict__) for r in board.rows],
        notes=list(board.notes),
    )
