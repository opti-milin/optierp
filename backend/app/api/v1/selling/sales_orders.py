"""Sales Order endpoints — Module 05."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.buying import OrderListItem
from app.schemas.accounts import InvoiceTaxPreview
from app.schemas.common import ListResponse
from app.schemas.selling import SalesOrderCreate, SalesOrderResponse
from app.services import sales_order as service

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


@router.post("/{so_id}/submit", response_model=SalesOrderResponse,
             summary="Submit a Sales Order",
             description="Reserves stock and runs the credit-limit check; breaches return "
                         "as `warnings` (the order still submits, matching ERPNext's "
                         "warning-only default).")
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
