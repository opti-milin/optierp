"""Manufacturing workspace endpoints — landing-page stats + the lean settings singleton."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.manufacturing import ManufacturingSettings, PlanningContextOut
from app.services import manufacturing_common, mfg_planning_dashboard, module_workspace as svc
from app.services.stock_common import get_warehouse

router = APIRouter(prefix="/manufacturing", tags=["manufacturing: workspace"])


@router.get(
    "/workspace",
    summary="Manufacturing workspace stats",
    description="Number cards (Active BOMs, Open / Completed Work Orders) and a 12-month "
    "produced-quantity trend for the Manufacturing workspace page.",
)
async def workspace_stats(
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.get_manufacturing_workspace(db, current_user.company_id)


@router.get(
    "/planning/context",
    response_model=PlanningContextOut,
    summary="Resolve Planning Dashboard context",
    description="Turn a Sales Order, Quotation, Production Plan, or Finished Item into "
    "FG lines for CTP / pegging / capacity tools.",
)
async def planning_context(
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    context: Annotated[
        Literal["item", "sales-order", "quotation", "production-plan"],
        Query(),
    ] = "item",
    id: UUID | None = None,
    item_id: UUID | None = None,
    qty: Decimal | None = None,
    delivery_date: date | None = None,
    warehouse_id: UUID | None = None,
) -> PlanningContextOut:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    result = await mfg_planning_dashboard.resolve_planning_context(
        db,
        current_user.company_id,
        context_type=context,
        document_id=id,
        item_id=item_id,
        qty=qty,
        delivery_date=delivery_date,
        warehouse_id=warehouse_id,
    )
    return PlanningContextOut(
        context_type=result.context_type,
        document_id=result.document_id,
        document_name=result.document_name,
        lines=[
            {
                "item_id": ln.item_id,
                "item_code": ln.item_code,
                "item_name": ln.item_name,
                "qty": ln.qty,
                "delivery_date": ln.delivery_date,
                "warehouse_id": ln.warehouse_id,
                "source_label": ln.source_label,
            }
            for ln in result.lines
        ],
    )


@router.get(
    "/settings",
    response_model=ManufacturingSettings,
    summary="Manufacturing settings",
    description="Per-company defaults: source / WIP / finished-goods warehouses and the "
    "over-production allowance applied to Work Order finishes.",
)
async def get_settings(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ManufacturingSettings:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    value = await manufacturing_common.get_manufacturing_settings(db, current_user.company_id)
    return ManufacturingSettings.model_validate(value)


@router.put(
    "/settings",
    response_model=ManufacturingSettings,
    summary="Update manufacturing settings",
)
async def update_settings(
    payload: ManufacturingSettings,
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ManufacturingSettings:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    for wid in (
        payload.default_source_warehouse_id,
        payload.default_wip_warehouse_id,
        payload.default_fg_warehouse_id,
    ):
        if wid is not None:
            await get_warehouse(db, wid, current_user.company_id)
    updates = {
        "default_source_warehouse_id": (
            str(payload.default_source_warehouse_id) if payload.default_source_warehouse_id else None
        ),
        "default_wip_warehouse_id": (
            str(payload.default_wip_warehouse_id) if payload.default_wip_warehouse_id else None
        ),
        "default_fg_warehouse_id": (
            str(payload.default_fg_warehouse_id) if payload.default_fg_warehouse_id else None
        ),
        "over_production_percentage": str(payload.over_production_percentage),
        "capacity_planning_enabled": payload.capacity_planning_enabled,
        "order_fulfillment_mode": payload.order_fulfillment_mode,
    }
    value = await manufacturing_common.update_manufacturing_settings(
        db, current_user.company_id, updates
    )
    return ManufacturingSettings.model_validate(value)
