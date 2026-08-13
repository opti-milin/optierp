"""Production Plan endpoints — MRP-I demand → Work Orders + Material Requests."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.manufacturing import (
    ProductionPlanCreate,
    ProductionPlanCreateResult,
    ProductionPlanListItem,
    ProductionPlanResponse,
    ProductionPlanUpdate,
)
from app.services import production_plan as service

router = APIRouter(prefix="/production-plans", tags=["manufacturing: production plan"])


@router.post(
    "",
    response_model=ProductionPlanResponse,
    status_code=201,
    summary="Create a Production Plan",
)
async def create_production_plan(
    payload: ProductionPlanCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanResponse:
    return ProductionPlanResponse.model_validate(
        await service.create_production_plan(db, payload, current_user)
    )


@router.get(
    "",
    response_model=ListResponse[ProductionPlanListItem],
    summary="List Production Plans",
)
async def list_production_plans(
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    status: str | None = None,
) -> ListResponse[ProductionPlanListItem]:
    items, total = await service.list_production_plans(
        db, current_user.company_id, page, page_size, status=status
    )
    return ListResponse(
        items=[ProductionPlanListItem.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{plan_id}",
    response_model=ProductionPlanResponse,
    summary="Get a Production Plan",
)
async def get_production_plan(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanResponse:
    return ProductionPlanResponse.model_validate(
        await service.get_production_plan(db, plan_id, current_user.company_id)
    )


@router.patch(
    "/{plan_id}",
    response_model=ProductionPlanResponse,
    summary="Update a draft Production Plan",
)
async def update_production_plan(
    plan_id: uuid.UUID,
    payload: ProductionPlanUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanResponse:
    return ProductionPlanResponse.model_validate(
        await service.update_production_plan(db, plan_id, payload, current_user)
    )


@router.post(
    "/{plan_id}/get-items",
    response_model=ProductionPlanResponse,
    summary="Pull FG demand from Sales Orders",
    description="Replaces finished-good rows with pending qty from open Sales Orders "
    "whose delivery date falls in the plan's from_date–to_date range.",
)
async def get_items_from_sales_orders(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanResponse:
    return ProductionPlanResponse.model_validate(
        await service.get_items_from_sales_orders(db, plan_id, current_user)
    )


@router.post(
    "/{plan_id}/get-raw-materials",
    response_model=ProductionPlanResponse,
    summary="Explode BOMs and net raw materials vs stock",
)
async def get_raw_materials(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    only_shortfall: Annotated[bool, Query()] = True,
) -> ProductionPlanResponse:
    return ProductionPlanResponse.model_validate(
        await service.get_raw_materials(
            db, plan_id, current_user, only_shortfall=only_shortfall
        )
    )


@router.post(
    "/{plan_id}/submit",
    response_model=ProductionPlanResponse,
    summary="Submit a Production Plan",
)
async def submit_production_plan(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanResponse:
    return ProductionPlanResponse.model_validate(
        await service.submit_production_plan(db, plan_id, current_user)
    )


@router.post(
    "/{plan_id}/cancel",
    response_model=ProductionPlanResponse,
    summary="Cancel a Production Plan",
)
async def cancel_production_plan(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanResponse:
    return ProductionPlanResponse.model_validate(
        await service.cancel_production_plan(db, plan_id, current_user)
    )


@router.post(
    "/{plan_id}/create-work-orders",
    response_model=ProductionPlanCreateResult,
    summary="Create Work Orders from plan items",
)
async def create_work_orders(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanCreateResult:
    return await service.create_work_orders_from_plan(db, plan_id, current_user)


@router.post(
    "/{plan_id}/create-material-requests",
    response_model=ProductionPlanCreateResult,
    summary="Create Manufacture Material Request for shortfalls",
)
async def create_material_requests(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Production Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ProductionPlanCreateResult:
    return await service.create_material_requests_from_plan(db, plan_id, current_user)
