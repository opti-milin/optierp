"""Sales Order endpoints — Module 05."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.buying import OrderListItem
from app.schemas.accounts import InvoiceTaxPreview
from app.schemas.common import ListResponse
from app.schemas.selling import (
    DeliveryLineEstimateOut,
    DeliveryStageOut,
    OrderFulfillmentOut,
    OrderFulfillmentPreviewIn,
    SalesOrderCreate,
    SalesOrderDeliveryEstimateOut,
    SalesOrderResponse,
)
from app.services import delivery_estimate, sales_order as service

router = APIRouter(prefix="/sales-orders", tags=["selling: sales orders"])

SO_STATUS_PATTERN = "^(Draft|To Deliver and Bill|To Deliver|To Bill|Completed|Cancelled|Closed)$"


@router.post("/preview", response_model=InvoiceTaxPreview,
             summary="Preview GST + totals for a draft (nothing is saved)")
async def preview(
    payload: SalesOrderCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> InvoiceTaxPreview:
    return await service.preview_sales_order(db, payload, current_user)


@router.post(
    "/check-fulfillment",
    response_model=OrderFulfillmentOut,
    summary="Preview fulfillability + cost for draft lines (nothing saved)",
)
async def check_fulfillment_preview(
    payload: OrderFulfillmentPreviewIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> OrderFulfillmentOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    data = await service.preview_sales_order_fulfillment(
        db,
        current_user.company_id,
        items=payload.items,
        delivery_date=payload.delivery_date,
        set_warehouse_id=payload.set_warehouse_id,
        as_of=payload.as_of,
    )
    return OrderFulfillmentOut.model_validate(data)


@router.post("", response_model=SalesOrderResponse, status_code=201,
             summary="Create a Sales Order (draft)",
             description="Optionally created from a submitted Quotation via quotation_id "
                         "(+ quotation_item_id per row).")
async def create(
    payload: SalesOrderCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesOrderResponse:
    return SalesOrderResponse.model_validate(
        await service.create_sales_order(db, payload, current_user)
    )


@router.get("", response_model=ListResponse[OrderListItem],
            summary="List Sales Orders", description="Paginated; filter by status and customer.")
async def list_orders(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    status: Annotated[str | None, Query(pattern=SO_STATUS_PATTERN)] = None,
    customer_id: uuid.UUID | None = None,
) -> ListResponse[OrderListItem]:
    orders, total = await service.list_sales_orders(
        db, current_user.company_id, page, page_size, status, customer_id
    )
    return ListResponse(
        items=[OrderListItem.model_validate(o) for o in orders],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{so_id}", response_model=SalesOrderResponse,
            summary="Get a Sales Order", description="Full order with items and taxes.")
async def get_order(
    so_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesOrderResponse:
    return SalesOrderResponse.model_validate(
        await service.get_sales_order(db, so_id, current_user.company_id)
    )


@router.post(
    "/{so_id}/check-fulfillment",
    response_model=OrderFulfillmentOut,
    summary="Check fulfillability + manufacturing cost for a Sales Order",
)
async def check_fulfillment(
    so_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> OrderFulfillmentOut:
    data = await service.check_sales_order_fulfillment(db, so_id, current_user.company_id)
    return OrderFulfillmentOut.model_validate(data)


@router.get(
    "/{so_id}/delivery-estimate",
    response_model=SalesOrderDeliveryEstimateOut,
    summary="Live delivery-date timeline for a Sales Order",
    description="Phase 9: supply-aware CTP + stage chain (MR → PO → WO → DN). "
    "Returns suggested_delivery_date but never writes the Sales Order.",
)
async def delivery_estimate_endpoint(
    so_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: Annotated[date | None, Query()] = None,
) -> SalesOrderDeliveryEstimateOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    est = await delivery_estimate.estimate_sales_order_delivery(
        db, current_user.company_id, so_id, as_of=as_of
    )
    return SalesOrderDeliveryEstimateOut(
        sales_order_id=est.sales_order_id,
        sales_order_name=est.sales_order_name,
        as_of=est.as_of,
        promised_date=est.promised_date,
        earliest_promise_date=est.earliest_promise_date,
        suggested_delivery_date=est.suggested_delivery_date,
        on_time=est.on_time,
        slack_days=est.slack_days,
        health=est.health,
        notes=list(est.notes),
        lines=[
            DeliveryLineEstimateOut(
                sales_order_item_id=ln.sales_order_item_id,
                item_id=ln.item_id,
                item_code=ln.item_code,
                item_name=ln.item_name,
                qty=ln.qty,
                pending_qty=ln.pending_qty,
                promised_date=ln.promised_date,
                earliest_promise_date=ln.earliest_promise_date,
                suggested_delivery_date=ln.suggested_delivery_date,
                manufacturing_start_date=ln.manufacturing_start_date,
                materials_ready_by=ln.materials_ready_by,
                on_time=ln.on_time,
                slack_days=ln.slack_days,
                health=ln.health,
                notes=list(ln.notes),
                stages=[
                    DeliveryStageOut(
                        stage=s.stage,
                        status=s.status,
                        source_type=s.source_type,
                        source_id=s.source_id,
                        source_name=s.source_name,
                        qty=s.qty,
                        planned_date=s.planned_date,
                        actual_date=s.actual_date,
                        notes=s.notes,
                    )
                    for s in ln.stages
                ],
            )
            for ln in est.lines
        ],
    )


@router.post("/{so_id}/submit", response_model=SalesOrderResponse,
             summary="Submit a Sales Order",
             description="Reserves stock, credit-limit check, and optional CTP fulfillment "
                         "gate (warn or block per Manufacturing Settings).")
async def submit(
    so_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesOrderResponse:
    so, warnings = await service.submit_sales_order(db, so_id, current_user)
    response = SalesOrderResponse.model_validate(so)
    response.warnings = warnings
    return response


@router.post("/{so_id}/cancel", response_model=SalesOrderResponse,
             summary="Cancel a Sales Order",
             description="Releases reservations. Blocked while deliveries or invoices "
                         "reference it.")
async def cancel(
    so_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Order", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesOrderResponse:
    return SalesOrderResponse.model_validate(
        await service.cancel_sales_order(db, so_id, current_user)
    )
