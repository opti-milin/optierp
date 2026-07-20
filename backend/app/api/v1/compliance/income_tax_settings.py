"""Income Tax Settings endpoints — per-company entity ITR policy (Phase 0)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.compliance import IncomeTaxSettings
from app.services import income_tax_settings as service

router = APIRouter(prefix="/income-tax-settings", tags=["compliance: income tax settings"])


@router.get(
    "",
    response_model=IncomeTaxSettings,
    summary="Get this company's income-tax settings",
    description="Per-company entity type / filing regime. PAN and TAN are derived from the Company.",
)
async def get_income_tax_settings(
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> IncomeTaxSettings:
    return await service.get_income_tax_settings(db, current_user.company_id)


@router.put(
    "",
    response_model=IncomeTaxSettings,
    summary="Save this company's income-tax settings",
)
async def put_income_tax_settings(
    payload: IncomeTaxSettings,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> IncomeTaxSettings:
    return await service.save_income_tax_settings(db, payload, current_user)
