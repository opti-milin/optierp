"""Manufacturing report endpoints (Phase 4) — read-only."""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.manufacturing import (
    BOMStockReport,
    BOMWhereUsedRow,
    MaterialShortageRow,
    ProductionRegisterRow,
)
from app.services import manufacturing_reports as svc

router = APIRouter(prefix="/manufacturing-reports", tags=["manufacturing: reports"])


@router.get(
    "/production-register",
    response_model=list[ProductionRegisterRow],
    summary="Production Register",
    description="Every Work Order's planned-vs-produced position and estimated cost.",
)
async def production_register(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    status: str | None = None,
    production_item_id: uuid.UUID | None = None,
) -> list[ProductionRegisterRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.production_register(
        db, current_user.company_id, status=status, production_item_id=production_item_id
    )


@router.get(
    "/material-shortage",
    response_model=list[MaterialShortageRow],
    summary="Material Shortage (all open Work Orders)",
    description="Aggregate still-to-consume component demand across every open Work Order "
    "vs on-hand stock — the components to buy before production stalls.",
)
async def material_shortage(
    current_user: Annotated[CurrentUser, Depends(require_permission("Work Order", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    only_short: bool = False,
) -> list[MaterialShortageRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.material_shortage(db, current_user.company_id, only_short=only_short)


@router.get(
    "/bom-where-used",
    response_model=list[BOMWhereUsedRow],
    summary="BOM Where-Used",
    description="Which BOMs consume a given item as a component.",
)
async def bom_where_used(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[BOMWhereUsedRow]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.bom_where_used(db, current_user.company_id, item_id)


@router.get(
    "/bom-stock",
    response_model=BOMStockReport,
    summary="BOM Stock report (can I build N?)",
    description="For a target finished quantity, each component's need vs on-hand stock and "
    "the maximum finished units the current stock supports.",
)
async def bom_stock(
    bom_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("BOM", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    for_qty: Annotated[Decimal, Query(gt=0)] = Decimal("1"),
) -> BOMStockReport:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.bom_stock_report(db, bom_id, current_user.company_id, for_qty=for_qty)
