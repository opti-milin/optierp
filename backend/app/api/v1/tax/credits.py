"""Tax credit entries (append-only) and persisted 26AS reconciliation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    Tax26asReconOut,
    Tax26asReconRequest,
    TaxCreditEntryCreate,
    TaxCreditEntryOut,
)
from app.services.taxation import credits as credit_service
from app.services.taxation import recon_26as as recon_service

router = APIRouter(prefix="/tax/credits", tags=["tax: credits"])


@router.get(
    "",
    response_model=list[TaxCreditEntryOut],
    summary="List tax credit entries",
)
async def list_credits(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
    computation_id: Annotated[UUID | None, Query()] = None,
) -> list[TaxCreditEntryOut]:
    assert current_user.company_id is not None
    rows = await credit_service.list_credits(
        db, current_user.company_id, ay_code=ay_code, computation_id=computation_id
    )
    return [credit_service.credit_out(r) for r in rows]


@router.post(
    "",
    response_model=TaxCreditEntryOut,
    summary="Append a tax credit entry (never deletes history)",
)
async def create_credit(
    payload: TaxCreditEntryCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxCreditEntryOut:
    row = await credit_service.create_credit(db, payload, current_user)
    return credit_service.credit_out(row)


@router.post(
    "/{credit_id}/void",
    response_model=TaxCreditEntryOut,
    summary="Void a credit entry (docstatus=2 — no DELETE)",
)
async def void_credit(
    credit_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxCreditEntryOut:
    row = await credit_service.void_credit(db, credit_id, current_user)
    return credit_service.credit_out(row)


@router.post(
    "/reconcile-26as",
    response_model=Tax26asReconOut,
    summary="Reconcile Form 26AS / AIS against book credits (persisted)",
)
async def reconcile_26as(
    payload: Tax26asReconRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Tax26asReconOut:
    run = await recon_service.reconcile_26as(
        db,
        user=current_user,
        ay_code=payload.ay_code,
        form26as=payload.form26as,
        computation_id=payload.computation_id,
    )
    return recon_service.recon_out(run)
