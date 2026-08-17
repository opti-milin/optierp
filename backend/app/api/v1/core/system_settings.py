"""System settings endpoints — Module 01."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.core import SystemSettingResponse, SystemSettingUpsert
from app.services import module_flags as module_flags_service
from app.services import settings as settings_service
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["core: settings"])


class ModuleFlags(BaseModel):
    manufacturing: bool = True
    taxation: bool = True
    # Module 13 is opt-in: most tenants keeping books here do not run their own
    # secretarial function, and an unused module in the nav is noise.
    secretarial: bool = False


class ModuleFlagsUpdate(BaseModel):
    manufacturing: bool | None = None
    taxation: bool | None = None
    secretarial: bool | None = None


@router.get(
    "/module-flags",
    response_model=ModuleFlags,
    summary="Get module feature flags",
    description="Per-company toggles that hide optional modules from the UI (default: on).",
)
async def get_module_flags(
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ModuleFlags:
    if current_user.company_id is None:
        return ModuleFlags()
    flags = await module_flags_service.get_module_flags(db, current_user.company_id)
    return ModuleFlags(**flags)


@router.put(
    "/module-flags",
    response_model=ModuleFlags,
    summary="Update module feature flags",
)
async def update_module_flags(
    payload: ModuleFlagsUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ModuleFlags:
    if current_user.company_id is None:
        from app.core.exceptions import ValidationError

        raise ValidationError("An active company is required")
    data = payload.model_dump(exclude_unset=True)
    flags = await module_flags_service.update_module_flags(db, current_user.company_id, data)
    return ModuleFlags(**flags)


@router.put(
    "",
    response_model=SystemSettingResponse,
    summary="Upsert a setting",
    description="Stores a JSON value under a key, instance-wide (company_id null) or "
    "per company. Example: `{'key': 'print_settings', 'value': {'paper_size': 'A4'}}`",
)
async def upsert_setting(
    payload: SystemSettingUpsert,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SystemSettingResponse:
    return SystemSettingResponse.model_validate(
        await settings_service.upsert_setting(db, payload, current_user)
    )


@router.get(
    "/{key}",
    response_model=SystemSettingResponse,
    summary="Get a setting",
    description="Resolves a setting key; a company-scoped value overrides the instance default.",
)
async def get_setting(
    key: str,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    company_id: Annotated[uuid.UUID | None, Query()] = None,
) -> SystemSettingResponse:
    setting = await settings_service.get_setting(db, key, company_id or current_user.company_id)
    return SystemSettingResponse.model_validate(setting)
