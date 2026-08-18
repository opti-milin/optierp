"""Workspace statistics for the Secretarial module dashboard."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.secretarial import SecretarialWorkspace
from app.services.secretarial import workspace as service

router = APIRouter(prefix="/secretarial", tags=["secretarial: workspace"])


@router.get(
    "/workspace",
    response_model=SecretarialWorkspace,
    summary="Dashboard cards and the 12-month obligation trend",
    description="`profile` tells the UI which shell to render — a practice lands on the "
    "client roster, a business on its single entity.",
)
async def workspace(
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Secretarial Entity", "read"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SecretarialWorkspace:
    assert current_user.company_id is not None
    data = await service.get_workspace(db, current_user.company_id)
    await db.commit()
    return SecretarialWorkspace.model_validate(data)
