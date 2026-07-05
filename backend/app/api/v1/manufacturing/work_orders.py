"""Work Order endpoints — make N of a finished good; Finish drives the Manufacture entry."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.manufacturing import (
    MaterialAvailabilityResponse,
    WorkOrderCreate,
    WorkOrderFinishIn,
    WorkOrderFinishResult,
    WorkOrderListItem,
    WorkOrderResponse,
    WorkOrderTransferIn,
)
from app.schemas.stock import MaterialRequestResponse
from app.services import work_order as service

router = APIRouter(prefix="/work-orders", tags=["manufacturing: work order"])


@router.post(
    "",
    response_model=WorkOrderResponse,
    status_code=201,
    summary="Create a Work Order",
    description="Creates a Work Order (Draft) and explodes the BOM × qty into required items.",
)
async def create_work_order(
    payload: WorkOrderCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderResponse:
    return WorkOrderResponse.model_validate(await service.create_work_order(db, payload, current_user))


@router.get("", response_model=ListResponse[WorkOrderListItem], summary="List Work Orders")
async def list_work_orders(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    status: str | None = None,
) -> ListResponse[WorkOrderListItem]:
    items, total = await service.list_work_orders(db, current_user.company_id, page, page_size, status=status)
    return ListResponse(
        items=[WorkOrderListItem.model_validate(w) for w in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{work_order_id}", response_model=WorkOrderResponse, summary="Get a Work Order")
async def get_work_order(
    work_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderResponse:
    return WorkOrderResponse.model_validate(
        await service.get_work_order(db, work_order_id, current_user.company_id)
    )


@router.get(
    "/{work_order_id}/material-availability",
    response_model=MaterialAvailabilityResponse,
    summary="Material availability",
    description="On-hand vs still-required per component and how many finished units the "
    "current stock supports.",
)
async def material_availability(
    work_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MaterialAvailabilityResponse:
    return await service.material_availability(db, work_order_id, current_user.company_id)


@router.post("/{work_order_id}/submit", response_model=WorkOrderResponse, summary="Submit a Work Order")
async def submit_work_order(
    work_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderResponse:
    return WorkOrderResponse.model_validate(await service.submit_work_order(db, work_order_id, current_user))


@router.post("/{work_order_id}/cancel", response_model=WorkOrderResponse, summary="Cancel a Work Order")
async def cancel_work_order(
    work_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderResponse:
    return WorkOrderResponse.model_validate(await service.cancel_work_order(db, work_order_id, current_user))


@router.post("/{work_order_id}/stop", response_model=WorkOrderResponse, summary="Stop a Work Order")
async def stop_work_order(
    work_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderResponse:
    return WorkOrderResponse.model_validate(
        await service.stop_work_order(db, work_order_id, current_user, stop=True)
    )


@router.post("/{work_order_id}/resume", response_model=WorkOrderResponse, summary="Resume a Work Order")
async def resume_work_order(
    work_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderResponse:
    return WorkOrderResponse.model_validate(
        await service.stop_work_order(db, work_order_id, current_user, stop=False)
    )


@router.post(
    "/{work_order_id}/finish",
    response_model=WorkOrderFinishResult,
    summary="Finish (manufacture) some/all of a Work Order",
    description="Posts a Manufacture Stock Entry — consume the required components and "
    "produce the finished good at input cost (consumed value + operating cost) — and "
    "advances the Work Order's produced qty and status.",
)
async def finish_work_order(
    work_order_id: uuid.UUID,
    payload: WorkOrderFinishIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderFinishResult:
    wo, entry = await service.finish_work_order(db, work_order_id, payload, current_user)
    return WorkOrderFinishResult(
        work_order_id=wo.id, stock_entry_id=entry.id, stock_entry_no=entry.name,
        produced_qty=wo.produced_qty, status=wo.status,
    )


@router.post(
    "/{work_order_id}/transfer",
    response_model=WorkOrderFinishResult,
    summary="Transfer materials to WIP (optional)",
    description="Moves the required materials source → WIP warehouse "
    "(a Material Transfer for Manufacture Stock Entry). Only for Work Orders using WIP.",
)
async def transfer_for_manufacture(
    work_order_id: uuid.UUID,
    payload: WorkOrderTransferIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderFinishResult:
    wo, entry = await service.transfer_for_manufacture(db, work_order_id, payload, current_user)
    return WorkOrderFinishResult(
        work_order_id=wo.id, stock_entry_id=entry.id, stock_entry_no=entry.name,
        produced_qty=wo.produced_qty, status=wo.status,
    )


@router.post(
    "/{work_order_id}/material-request",
    response_model=MaterialRequestResponse,
    status_code=201,
    summary="Raise a Material Request for shortfalls",
    description="Creates a Purchase Material Request for every component short of its "
    "pending requirement (reuses the existing Material Request document).",
)
async def create_shortfall_material_request(
    work_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MaterialRequestResponse:
    return MaterialRequestResponse.model_validate(
        await service.create_shortfall_material_request(db, work_order_id, current_user)
    )
