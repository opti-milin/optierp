"""Tax challan documents — submit posts GL via services/gl.py."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import TaxChallanCreate, TaxChallanOut, TaxChallanUpdate
from app.services.taxation import challans as service

router = APIRouter(prefix="/tax/challans", tags=["tax: challans"])


@router.get(
    "",
    response_model=list[TaxChallanOut],
    summary="List tax challans",
)
async def list_challans(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
) -> list[TaxChallanOut]:
    assert current_user.company_id is not None
    rows = await service.list_challans(db, current_user.company_id, ay_code=ay_code)
    return [service.challan_out(r) for r in rows]


@router.post(
    "",
    response_model=TaxChallanOut,
    summary="Create a draft tax challan",
)
async def create_challan(
    payload: TaxChallanCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxChallanOut:
    row = await service.create_challan(db, payload, current_user)
    return service.challan_out(row)


@router.get(
    "/{challan_id}",
    response_model=TaxChallanOut,
    summary="Get a tax challan",
)
async def get_challan(
    challan_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxChallanOut:
    assert current_user.company_id is not None
    row = await service.get_challan(db, current_user.company_id, challan_id)
    return service.challan_out(row)


@router.put(
    "/{challan_id}",
    response_model=TaxChallanOut,
    summary="Update a draft tax challan",
)
async def update_challan(
    challan_id: UUID,
    payload: TaxChallanUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxChallanOut:
    row = await service.update_challan(db, challan_id, payload, current_user)
    return service.challan_out(row)


@router.post(
    "/{challan_id}/submit",
    response_model=TaxChallanOut,
    summary="Submit challan and post GL (Dr payable / Cr bank)",
)
async def submit_challan(
    challan_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxChallanOut:
    row = await service.submit_challan(db, challan_id, current_user)
    return service.challan_out(row)


@router.post(
    "/{challan_id}/cancel",
    response_model=TaxChallanOut,
    summary="Cancel challan and reverse GL entries",
)
async def cancel_challan(
    challan_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxChallanOut:
    row = await service.cancel_challan(db, challan_id, current_user)
    return service.challan_out(row)
