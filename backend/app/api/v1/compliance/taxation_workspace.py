"""Taxation workspace — landing-page stats + module enable toggle surface."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.services import module_flags as module_flags_service
from app.services import module_workspace as svc

router = APIRouter(prefix="/taxation", tags=["taxation: workspace"])


@router.get(
    "/workspace",
    summary="Taxation workspace stats",
    description="Number cards (draft ITR, submitted ITR, GST configured) for the Taxation home.",
)
async def workspace_stats(
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "read"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await svc.get_taxation_workspace(db, current_user.company_id)


@router.get(
    "/module-enabled",
    summary="Whether Taxation module is enabled for this company",
)
async def get_taxation_enabled(
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, bool]:
    if current_user.company_id is None:
        return {"enabled": True}
    flags = await module_flags_service.get_module_flags(db, current_user.company_id)
    return {"enabled": bool(flags.get("taxation", True))}


@router.put(
    "/module-enabled",
    summary="Enable or disable the Taxation module for this company",
)
async def put_taxation_enabled(
    payload: dict[str, bool],
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, bool]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    enabled = bool(payload.get("enabled", True))
    flags = await module_flags_service.update_module_flags(
        db, current_user.company_id, {"taxation": enabled}
    )
    return {"enabled": bool(flags.get("taxation", True))}
