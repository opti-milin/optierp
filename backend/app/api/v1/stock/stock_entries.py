"""Stock Entry endpoints — Module 03."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.stock import StockEntryCreate, StockEntryListItem, StockEntryResponse
from app.services import stock_entry as service

router = APIRouter(prefix="/stock-entries", tags=["stock: stock entries"])


@router.post("", response_model=StockEntryResponse, status_code=201,
             summary="Create a Stock Entry (draft)",
             description="Purposes: Material Receipt / Material Issue / Material Transfer. "
                         "Example: `{'purpose': 'Material Receipt', 'posting_date': '2026-06-12', "
                         "'to_warehouse_id': '...', 'items': [{'item_id': '...', 'qty': 10, "
                         "'basic_rate': 95}]}`")
async def create(
    payload: StockEntryCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Stock Entry", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> StockEntryResponse:
    return StockEntryResponse.model_validate(await service.create_stock_entry(db, payload, current_user))


@router.get("", response_model=ListResponse[StockEntryListItem],
            summary="List Stock Entries", description="Paginated, newest first; filter by purpose.")
async def list_entries(
    current_user: Annotated[CurrentUser, Depends(require_permission("Stock Entry", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    purpose: Annotated[
        str | None,
        Query(
            pattern="^(Material Receipt|Material Issue|Material Transfer|Manufacture|"
            "Material Transfer for Manufacture|Repack)$"
        ),
    ] = None,
) -> ListResponse[StockEntryListItem]:
    entries, total = await service.list_stock_entries(
        db, current_user.company_id, page, page_size, purpose
    )
    return ListResponse(
        items=[StockEntryListItem.model_validate(e) for e in entries],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{entry_id}", response_model=StockEntryResponse,
            summary="Get a Stock Entry", description="Full entry with item rows.")
async def get_entry(
    entry_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Stock Entry", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> StockEntryResponse:
    return StockEntryResponse.model_validate(
        await service.get_stock_entry(db, entry_id, current_user.company_id)
    )


@router.post("/{entry_id}/submit", response_model=StockEntryResponse,
             summary="Submit a Stock Entry",
             description="Posts stock ledger entries (moving average) and, under perpetual "
                         "inventory, the matching GL entries.")
async def submit(
    entry_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Stock Entry", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> StockEntryResponse:
    return StockEntryResponse.model_validate(
        await service.submit_stock_entry(db, entry_id, current_user)
    )


@router.post("/{entry_id}/cancel", response_model=StockEntryResponse,
             summary="Cancel a Stock Entry",
             description="Reverses stock and GL via mirror entries.")
async def cancel(
    entry_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Stock Entry", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> StockEntryResponse:
    return StockEntryResponse.model_validate(
        await service.cancel_stock_entry(db, entry_id, current_user)
    )
