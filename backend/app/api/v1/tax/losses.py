"""Loss carry-forward and set-off API — Phase 6."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    TaxLossCarryForwardCreate,
    TaxLossCarryForwardOut,
    TaxLossSetoffEntryOut,
)
from app.services.taxation import loss_setoff as service

router = APIRouter(prefix="/tax/losses", tags=["tax: losses"])


@router.get(
    "",
    response_model=list[TaxLossCarryForwardOut],
    summary="List loss carry-forward ledger rows",
)
async def list_losses(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
    remaining_only: Annotated[bool, Query()] = False,
) -> list[TaxLossCarryForwardOut]:
    assert current_user.company_id is not None
    rows = await service.list_losses(
        db, current_user.company_id, ay_code=ay_code, remaining_only=remaining_only
    )
    return [service.loss_out(r) for r in rows]


@router.post(
    "",
    response_model=TaxLossCarryForwardOut,
    summary="Create a brought-forward loss row",
)
async def create_loss(
    payload: TaxLossCarryForwardCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxLossCarryForwardOut:
    row = await service.create_loss(db, payload, current_user)
    return service.loss_out(row)


@router.get(
    "/setoffs",
    response_model=list[TaxLossSetoffEntryOut],
    summary="List set-off applications (append-only)",
)
async def list_setoffs(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
) -> list[TaxLossSetoffEntryOut]:
    assert current_user.company_id is not None
    rows = await service.list_setoffs(db, current_user.company_id, ay_code=ay_code)
    return [service.setoff_out(r) for r in rows]
