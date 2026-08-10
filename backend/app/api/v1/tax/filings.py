"""Tax filings API — generate ITR-6, acknowledge, chain revised/belated/updated."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    TaxComputationOut,
    TaxFilingAckIn,
    TaxFilingChainIn,
    TaxFilingGenerateIn,
    TaxFilingOut,
)
from app.services.taxation import computations as comp_service
from app.services.taxation import filings as service

router = APIRouter(prefix="/tax/filings", tags=["tax: filings"])


@router.get(
    "",
    response_model=list[TaxFilingOut],
    summary="List tax filings",
)
async def list_filings(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
    computation_id: Annotated[UUID | None, Query()] = None,
) -> list[TaxFilingOut]:
    assert current_user.company_id is not None
    rows = await service.list_filings(
        db,
        current_user.company_id,
        ay_code=ay_code,
        computation_id=computation_id,
    )
    return [service.filing_out(r, include_payload=False) for r in rows]


@router.get(
    "/{filing_id}",
    response_model=TaxFilingOut,
    summary="Get tax filing with CBDT payload",
)
async def get_filing(
    filing_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxFilingOut:
    assert current_user.company_id is not None
    row = await service.get_filing(db, current_user.company_id, filing_id)
    return service.filing_out(row)


@router.post(
    "/generate",
    response_model=TaxFilingOut,
    summary="Generate ITR-6 JSON from a computation via statutory field map",
    description=(
        "Builds CBDT-shaped JSON from statutory.itr_field_map, persists sha256 hash. "
        "Computation must be submitted unless allow_draft=true."
    ),
)
async def generate_filing(
    payload: TaxFilingGenerateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxFilingOut:
    row = await service.generate_filing(db, payload, current_user)
    return service.filing_out(row)


@router.post(
    "/{filing_id}/acknowledge",
    response_model=TaxFilingOut,
    summary="Record portal acknowledgement (ack_no + filed_on)",
)
async def acknowledge_filing(
    filing_id: UUID,
    payload: TaxFilingAckIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxFilingOut:
    row = await service.acknowledge_filing(db, filing_id, payload, current_user)
    return service.filing_out(row)


@router.post(
    "/{filing_id}/efile-sandbox",
    response_model=TaxFilingOut,
    summary="Sandbox e-file — deterministic ack without portal HTTPS",
)
async def efile_sandbox(
    filing_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxFilingOut:
    row = await service.efile_sandbox(db, filing_id, current_user)
    return service.filing_out(row)


@router.post(
    "/chain",
    response_model=TaxComputationOut,
    summary="Create Revised / Belated / Updated computation from a submitted original",
)
async def chain_return(
    payload: TaxFilingChainIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxComputationOut:
    doc = await service.chain_return(db, payload, current_user)
    return comp_service.computation_out(doc)
