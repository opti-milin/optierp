"""Tax depreciation register API — Phase 6."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import TaxDepreciationRegisterOut, TaxDepreciationSyncRequest
from app.services.taxation import depreciation as service

router = APIRouter(prefix="/tax/depreciation", tags=["tax: depreciation"])


@router.get(
    "",
    response_model=list[TaxDepreciationRegisterOut],
    summary="List tax depreciation registers",
    description="Per-AY IT Act block WDV registers for the current company.",
)
async def list_registers(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
) -> list[TaxDepreciationRegisterOut]:
    assert current_user.company_id is not None
    rows = await service.list_registers(db, current_user.company_id, ay_code=ay_code)
    return [service.register_out(r) for r in rows]


@router.post(
    "/sync",
    response_model=list[TaxDepreciationRegisterOut],
    summary="Rebuild depreciation registers from assets",
    description=(
        "Syncs submitted assets whose category has tax_block_code into block registers. "
        "Applies the <180-day half-rate rule for FY additions."
    ),
)
async def sync_registers(
    payload: TaxDepreciationSyncRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[TaxDepreciationRegisterOut]:
    rows = await service.sync_from_assets(db, payload, current_user)
    return [service.register_out(r) for r in rows]
