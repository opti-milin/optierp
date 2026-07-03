"""Sales Invoice endpoints — Module 02."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.accounts import (
    InvoiceListItem,
    InvoiceTaxPreview,
    SalesInvoiceCreate,
    SalesInvoiceResponse,
)
from app.schemas.common import ListResponse
from app.services import print_service
from app.services import sales_invoice as service

router = APIRouter(prefix="/sales-invoices", tags=["accounts: sales invoices"])


@router.post(
    "",
    response_model=SalesInvoiceResponse,
    status_code=201,
    summary="Create a Sales Invoice (draft)",
    description="Totals and taxes are computed server-side by the taxes_and_totals "
    "engine. Example: `{'customer_id': '...', 'posting_date': '2026-06-12', 'items': "
    "[{'item_name': 'Consulting', 'qty': 10, 'rate': 150}], 'taxes': [{'charge_type': "
    "'On Net Total', 'rate': 18, 'account_head_id': '<GST account>'}]}`",
)
async def create(
    payload: SalesInvoiceCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesInvoiceResponse:
    return SalesInvoiceResponse.model_validate(
        await service.create_sales_invoice(db, payload, current_user)
    )


@router.post(
    "/preview",
    response_model=InvoiceTaxPreview,
    summary="Preview GST + totals for a draft (nothing is saved)",
    description="Computes the taxes and totals the create call would apply — so the "
    "form can show CGST/SGST/IGST and the grand total live while drafting.",
)
async def preview(
    payload: SalesInvoiceCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> InvoiceTaxPreview:
    return await service.preview_sales_invoice(db, payload, current_user)


@router.get(
    "",
    response_model=ListResponse[InvoiceListItem],
    summary="List Sales Invoices",
    description="Paginated, newest first; filter by status (Unpaid/Paid/Overdue/...).",
)
async def list_invoices(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    status: Annotated[
        str | None,
        Query(pattern="^(Draft|Unpaid|Partly Paid|Paid|Overdue|Cancelled|Return)$"),
    ] = None,
    customer_id: uuid.UUID | None = None,
) -> ListResponse[InvoiceListItem]:
    invoices, total = await service.list_sales_invoices(
        db, current_user.company_id, page, page_size, status, customer_id
    )
    return ListResponse(
        items=[InvoiceListItem.model_validate(i) for i in invoices],
        total=total, page=page, page_size=page_size,
    )


@router.get(
    "/{invoice_id}",
    response_model=SalesInvoiceResponse,
    summary="Get a Sales Invoice",
    description="Full invoice with items and tax rows.",
)
async def get_invoice(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesInvoiceResponse:
    return SalesInvoiceResponse.model_validate(
        await service.get_sales_invoice(db, invoice_id, current_user.company_id)
    )


@router.post(
    "/{invoice_id}/submit",
    response_model=SalesInvoiceResponse,
    summary="Submit a Sales Invoice",
    description="Posts GL (Dr receivable / Cr income + taxes), sets the outstanding "
    "amount and status. Credit notes reduce the original invoice's outstanding.",
)
async def submit(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesInvoiceResponse:
    return SalesInvoiceResponse.model_validate(
        await service.submit_sales_invoice(db, invoice_id, current_user)
    )


@router.get(
    "/{invoice_id}/pdf",
    summary="Download or preview the invoice (PDF/HTML)",
    description="Renders the themed print format via the shared print service. "
    "`?format=html` returns HTML for in-app preview; `?format=pdf` (default) returns "
    "the PDF (WeasyPrint; 501 if the engine is not installed).",
)
async def download_pdf(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "print"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    format: Annotated[Literal["pdf", "html"], Query()] = "pdf",
) -> Response:
    content, filename, media_type = await print_service.render_document(
        db, "Sales Invoice", invoice_id, current_user.company_id, format
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post(
    "/{invoice_id}/cancel",
    response_model=SalesInvoiceResponse,
    summary="Cancel a Sales Invoice",
    description="Reverses the GL entries. Blocked while payments are allocated against it.",
)
async def cancel(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SalesInvoiceResponse:
    return SalesInvoiceResponse.model_validate(
        await service.cancel_sales_invoice(db, invoice_id, current_user)
    )
