"""Purchase Order endpoints — Module 04."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.accounts import InvoiceTaxPreview
from app.schemas.buying import OrderListItem, PurchaseOrderCreate, PurchaseOrderResponse
from app.schemas.common import ListResponse
from app.services import purchase_order as service

router = APIRouter(prefix="/purchase-orders", tags=["buying: purchase orders"])

PO_STATUS_PATTERN = "^(Draft|To Receive and Bill|To Receive|To Bill|Completed|Cancelled|Closed)$"


@router.post("/preview", response_model=InvoiceTaxPreview,
             summary="Preview GST + totals for a draft (nothing is saved)")
async def preview(
    payload: PurchaseOrderCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Order", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> InvoiceTaxPreview:
    return await service.preview_purchase_order(db, payload, current_user)


@router.post("", response_model=PurchaseOrderResponse, status_code=201,
             summary="Create a Purchase Order (draft)",
             description="Rates default from buying price lists / last purchase rate; taxes "
                         "resolve from the supplier's tax category like invoices. Example: "
                         "`{'supplier_id': '...', 'posting_date': '2026-06-12', "
                         "'items': [{'item_id': '...', 'qty': 10}]}`")
async def create(
    payload: PurchaseOrderCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Order", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PurchaseOrderResponse:
    return PurchaseOrderResponse.model_validate(
        await service.create_purchase_order(db, payload, current_user)
    )


@router.get("", response_model=ListResponse[OrderListItem],
            summary="List Purchase Orders",
            description="Paginated; filter by status and supplier.")
async def list_orders(
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    status: Annotated[str | None, Query(pattern=PO_STATUS_PATTERN)] = None,
    supplier_id: uuid.UUID | None = None,
) -> ListResponse[OrderListItem]:
    orders, total = await service.list_purchase_orders(
        db, current_user.company_id, page, page_size, status, supplier_id
    )
    return ListResponse(
        items=[OrderListItem.model_validate(o) for o in orders],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{po_id}", response_model=PurchaseOrderResponse,
            summary="Get a Purchase Order", description="Full order with items and taxes.")
async def get_order(
    po_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PurchaseOrderResponse:
    return PurchaseOrderResponse.model_validate(
        await service.get_purchase_order(db, po_id, current_user.company_id)
    )


@router.post("/{po_id}/submit", response_model=PurchaseOrderResponse,
             summary="Submit a Purchase Order",
             description="Accrues ordered qty on bins and linked Material Requests; "
                         "status becomes To Receive and Bill.")
async def submit(
    po_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Order", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PurchaseOrderResponse:
    return PurchaseOrderResponse.model_validate(
        await service.submit_purchase_order(db, po_id, current_user)
    )


@router.post("/{po_id}/cancel", response_model=PurchaseOrderResponse,
             summary="Cancel a Purchase Order",
             description="Blocked while receipts or invoices reference it.")
async def cancel(
    po_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Order", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PurchaseOrderResponse:
    return PurchaseOrderResponse.model_validate(
        await service.cancel_purchase_order(db, po_id, current_user)
    )
