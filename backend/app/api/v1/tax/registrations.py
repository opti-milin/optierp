"""Tenant tax registration, regime elections, and policy overrides."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    TaxPolicyOverrideCreate,
    TaxPolicyOverrideOut,
    TaxRegistrationOut,
    TaxRegistrationUpsert,
    TaxRegimeElectionCreate,
    TaxRegimeElectionOut,
)
from app.services.taxation import registration as service

router = APIRouter(prefix="/tax", tags=["tax: registration"])


@router.get(
    "/registrations",
    response_model=TaxRegistrationOut,
    summary="Get this company's tax registration",
)
async def get_registration(
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxRegistrationOut:
    assert current_user.company_id is not None
    row = await service.get_or_bootstrap_registration(db, current_user.company_id)
    return service.registration_out(row)


@router.put(
    "/registrations",
    response_model=TaxRegistrationOut,
    summary="Create or update this company's tax registration",
)
async def put_registration(
    payload: TaxRegistrationUpsert,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxRegistrationOut:
    row = await service.upsert_registration(db, payload, current_user)
    return service.registration_out(row)


@router.get(
    "/elections",
    response_model=list[TaxRegimeElectionOut],
    summary="List regime elections for this company",
)
async def get_elections(
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[TaxRegimeElectionOut]:
    assert current_user.company_id is not None
    rows = await service.list_elections(db, current_user.company_id)
    return [service.election_out(r) for r in rows]


@router.post(
    "/elections",
    response_model=TaxRegimeElectionOut,
    summary="Create or update a per-AY regime election",
)
async def post_election(
    payload: TaxRegimeElectionCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxRegimeElectionOut:
    row = await service.create_or_update_election(db, payload, current_user)
    return service.election_out(row)


@router.get(
    "/overrides",
    response_model=list[TaxPolicyOverrideOut],
    summary="List tax policy overrides",
)
async def get_overrides(
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
) -> list[TaxPolicyOverrideOut]:
    assert current_user.company_id is not None
    rows = await service.list_overrides(db, current_user.company_id, ay_code=ay_code)
    return [service.override_out(r) for r in rows]


@router.post(
    "/overrides",
    response_model=TaxPolicyOverrideOut,
    summary="Create a tax policy override (audited reason required)",
)
async def post_override(
    payload: TaxPolicyOverrideCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("System Settings", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxPolicyOverrideOut:
    row = await service.create_override(db, payload, current_user)
    return service.override_out(row)
