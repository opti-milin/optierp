"""Quotation endpoints — Module 05."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.accounts import InvoiceTaxPreview
from app.schemas.buying import OrderListItem
from app.schemas.common import ListResponse
from app.schemas.selling import QuotationCreate, QuotationResponse
from app.services import quotation as service

router = APIRouter(prefix="/quotations", tags=["selling: quotations"])


@router.post("/preview", response_model=InvoiceTaxPreview,
             summary="Preview GST + totals for a draft (nothing is saved)")
async def preview(
    payload: QuotationCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Quotation", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> InvoiceTaxPreview:
    return await service.preview_quotation(db, payload, current_user)


@router.post("", response_model=QuotationResponse, status_code=201,
             summary="Create a Quotation (draft)",
             description="Rates default from selling price lists / standard rate; taxes "
                         "resolve from the customer's tax category.")
async def create(
    payload: QuotationCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Quotation", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QuotationResponse:
    return QuotationResponse.model_validate(await service.create_quotation(db, payload, current_user))


@router.get("", response_model=ListResponse[OrderListItem],
            summary="List Quotations", description="Paginated; filter by status and customer.")
async def list_quotations(
    current_user: Annotated[CurrentUser, Depends(require_permission("Quotation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    status: Annotated[str | None, Query(pattern="^(Draft|Open|Ordered|Cancelled|Expired)$")] = None,
    customer_id: uuid.UUID | None = None,
) -> ListResponse[OrderListItem]:
    quotations, total = await service.list_quotations(
        db, current_user.company_id, page, page_size, status, customer_id
    )
    return ListResponse(
        items=[OrderListItem.model_validate(q) for q in quotations],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{quotation_id}", response_model=QuotationResponse,
            summary="Get a Quotation", description="Full quotation with items and taxes.")
async def get_quotation(
    quotation_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Quotation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QuotationResponse:
    return QuotationResponse.model_validate(
        await service.get_quotation(db, quotation_id, current_user.company_id)
    )


@router.post("/{quotation_id}/submit", response_model=QuotationResponse,
             summary="Submit a Quotation",
             description="Status becomes Open; convert to a Sales Order from there.")
async def submit(
    quotation_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Quotation", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QuotationResponse:
    return QuotationResponse.model_validate(
        await service.submit_quotation(db, quotation_id, current_user)
    )


@router.post("/{quotation_id}/cancel", response_model=QuotationResponse,
             summary="Cancel a Quotation", description="No stock or GL effect.")
async def cancel(
    quotation_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Quotation", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QuotationResponse:
    return QuotationResponse.model_validate(
        await service.cancel_quotation(db, quotation_id, current_user)
    )
