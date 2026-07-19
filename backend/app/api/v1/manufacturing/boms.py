"""BOM endpoints — the manufacturing recipe (create / cost / submit / activate)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.manufacturing import BOMCreate, BOMListItem, BOMResponse, BOMUpdate
from app.services import bom as service

router = APIRouter(prefix="/boms", tags=["manufacturing: bom"])


@router.post(
    "",
    response_model=BOMResponse,
    status_code=201,
    summary="Create a BOM",
    description="Registers a Bill of Materials (Draft) and snapshots its cost "
    "(raw material from component valuation + flat operating cost).",
)
async def create_bom(
    payload: BOMCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.create_bom(db, payload, current_user))


@router.get("", response_model=ListResponse[BOMListItem], summary="List BOMs")
async def list_boms(
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    production_item_id: uuid.UUID | None = None,
    is_active: bool | None = None,
) -> ListResponse[BOMListItem]:
    items, total = await service.list_boms(
        db, current_user.company_id, page, page_size,
        production_item_id=production_item_id, is_active=is_active,
    )
    return ListResponse(
        items=[BOMListItem.model_validate(b) for b in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{bom_id}", response_model=BOMResponse, summary="Get a BOM")
async def get_bom(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.get_bom(db, bom_id, current_user.company_id))


@router.patch("/{bom_id}", response_model=BOMResponse, summary="Update a draft BOM")
async def update_bom(
    bom_id: uuid.UUID,
    payload: BOMUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.update_bom(db, bom_id, payload, current_user))


@router.post("/{bom_id}/update-cost", response_model=BOMResponse, summary="Refresh BOM cost")
async def update_bom_cost(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.update_bom_cost(db, bom_id, current_user))


@router.post("/{bom_id}/submit", response_model=BOMResponse, summary="Submit a BOM")
async def submit_bom(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.submit_bom(db, bom_id, current_user))


@router.post("/{bom_id}/cancel", response_model=BOMResponse, summary="Cancel a BOM")
async def cancel_bom(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.cancel_bom(db, bom_id, current_user))


@router.post("/{bom_id}/activate", response_model=BOMResponse, summary="Activate a BOM")
async def activate_bom(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.set_active(db, bom_id, True, current_user))


@router.post("/{bom_id}/deactivate", response_model=BOMResponse, summary="Deactivate a BOM")
async def deactivate_bom(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> BOMResponse:
    return BOMResponse.model_validate(await service.set_active(db, bom_id, False, current_user))
