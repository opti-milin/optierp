"""GST Settings endpoints — per-company GST configuration (India compliance Phase 0)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.compliance import GstSettings
from app.services import gst_settings as service

router = APIRouter(prefix="/gst-settings", tags=["compliance: gst settings"])


@router.get(
    "",
    response_model=GstSettings,
    summary="Get this company's GST settings",
    description="Per-company GST policy (registration type, filing cadence, e-invoice / e-way-bill "
    "applicability, SEZ). GSTIN + place-of-supply state are derived from the company.",
)
async def get_gst_settings(
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> GstSettings:
    return await service.get_gst_settings(db, current_user.company_id)


@router.put(
    "",
    response_model=GstSettings,
    summary="Save this company's GST settings",
)
async def put_gst_settings(
    payload: GstSettings,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> GstSettings:
    return await service.save_gst_settings(db, payload, current_user)
