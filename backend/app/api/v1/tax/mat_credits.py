"""MAT credit ledger API — Phase 6."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import MatCreditLedgerOut
from app.services.taxation import mat_credit as service

router = APIRouter(prefix="/tax/mat-credits", tags=["tax: mat-credits"])


class MatCreditBalanceOut(BaseModel):
    ay_code: str
    available: str


@router.get(
    "",
    response_model=list[MatCreditLedgerOut],
    summary="List 115JAA MAT credit ledger entries",
)
async def list_mat_credits(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
) -> list[MatCreditLedgerOut]:
    assert current_user.company_id is not None
    rows = await service.list_mat_credits(db, current_user.company_id, ay_code=ay_code)
    return [service.mat_credit_out(r) for r in rows]


@router.get(
    "/balance",
    response_model=MatCreditBalanceOut,
    summary="Available MAT credit balance for an AY",
)
async def mat_credit_balance(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str, Query()],
) -> MatCreditBalanceOut:
    assert current_user.company_id is not None
    bal = await service.available_mat_credit(db, current_user.company_id, current_ay=ay_code)
    return MatCreditBalanceOut(ay_code=ay_code, available=format(bal, "f"))
