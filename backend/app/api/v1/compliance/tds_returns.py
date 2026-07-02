"""TDS return endpoints — Form 26Q + Form 16A (India compliance Phase 6.2).

Read-only quarterly TDS summaries computed from submitted Purchase Invoices that withheld tax.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.compliance import Form16A, Tds26qReport
from app.services import tds_returns
from app.services.accounts_common import get_company

router = APIRouter(prefix="/tds-returns", tags=["compliance: tds returns"])


def _company(current_user: CurrentUser) -> uuid.UUID:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return current_user.company_id


@router.get(
    "/26q",
    response_model=Tds26qReport,
    summary="Form 26Q (TDS on non-salary payments)",
    description="Quarterly TDS summary grouped by deductee × section, from submitted Purchase "
    "Invoices that withheld tax. Deductee PAN is derived from the supplier GSTIN.",
)
async def get_26q(
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> Tds26qReport:
    company = await get_company(db, _company(current_user))
    return await tds_returns.tds_26q(db, company, from_date=from_date, to_date=to_date)


@router.get(
    "/16a",
    response_model=Form16A,
    summary="Form 16A (TDS certificate for a deductee)",
    description="The TDS certificate for one supplier (deductee) over the period — section-wise "
    "amounts + tax deducted.",
)
async def get_16a(
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    supplier_id: uuid.UUID,
    from_date: date,
    to_date: date,
) -> Form16A:
    company = await get_company(db, _company(current_user))
    return await tds_returns.form_16a(
        db, company, supplier_id=supplier_id, from_date=from_date, to_date=to_date
    )
