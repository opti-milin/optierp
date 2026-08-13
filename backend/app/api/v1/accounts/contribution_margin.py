"""Contribution Margin setup endpoints — templates, apply, unclassified, settings."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.accounts import (
    CmApplyTemplateRequest,
    CmApplyTemplateResult,
    CmSettings,
    CmTemplateInfo,
    CmUnclassifiedAccount,
)
from app.services import cm_classification as service

router = APIRouter(prefix="/contribution-margin", tags=["accounts: contribution margin"])


def _company(current_user: CurrentUser) -> uuid.UUID:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return current_user.company_id


@router.get(
    "/templates",
    response_model=list[CmTemplateInfo],
    summary="List industry Contribution Margin templates",
)
async def list_templates(
    current_user: Annotated[CurrentUser, Depends(require_permission("Account", "read"))],
) -> list[CmTemplateInfo]:
    _ = current_user
    return service.list_cm_templates()


@router.post(
    "/apply-template",
    response_model=CmApplyTemplateResult,
    summary="Apply a CM industry template to P&L leaf accounts",
    description="Sets Account.cm_class from heuristic rules. With overwrite=false, "
    "accounts that already have a cm_class are left unchanged.",
)
async def apply_template(
    payload: CmApplyTemplateRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Account", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmApplyTemplateResult:
    return await service.apply_cm_template(
        db,
        _company(current_user),
        payload.template_id,
        current_user,
        overwrite=payload.overwrite,
    )


@router.get(
    "/unclassified",
    response_model=list[CmUnclassifiedAccount],
    summary="List P&L leaf accounts without a cm_class",
)
async def unclassified(
    current_user: Annotated[CurrentUser, Depends(require_permission("Account", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[CmUnclassifiedAccount]:
    return await service.get_unclassified_pnl_accounts(db, _company(current_user))


@router.get(
    "/settings",
    response_model=CmSettings,
    summary="Get Contribution Margin settings for this company",
)
async def get_settings(
    current_user: Annotated[CurrentUser, Depends(require_permission("Account", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmSettings:
    return await service.get_cm_settings(db, current_user.company_id)


@router.put(
    "/settings",
    response_model=CmSettings,
    summary="Save Contribution Margin settings",
)
async def put_settings(
    payload: CmSettings,
    current_user: Annotated[CurrentUser, Depends(require_permission("Account", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmSettings:
    return await service.save_cm_settings(db, payload, current_user)
