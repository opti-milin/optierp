"""GST returns endpoints — GSTR-1 & GSTR-3B (India compliance Phase 2).

Read-only monthly returns computed from submitted invoices, plus the GSTR-1
portal-JSON export for the offline upload tool.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.compliance import Gstr1Report, Gstr3bReport
from app.services import gst_returns
from app.services.accounts_common import get_company

router = APIRouter(prefix="/gst-returns", tags=["compliance: gst returns"])


def _company(current_user: CurrentUser) -> uuid.UUID:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return current_user.company_id


@router.get(
    "/gstr-1",
    response_model=Gstr1Report,
    summary="GSTR-1 (outward supplies)",
    description="Monthly outward-supply return — B2B, B2C-Large, B2C-Small, credit/debit notes, "
    "HSN summary and the document-issued summary — from submitted Sales Invoices.",
)
async def get_gstr1(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> Gstr1Report:
    company = await get_company(db, _company(current_user))
    return await gst_returns.gstr1(db, company, from_date=from_date, to_date=to_date)


@router.get(
    "/gstr-1/json",
    summary="GSTR-1 portal JSON",
    description="The GSTR-1 return serialised to the GST portal's offline-tool JSON schema.",
)
async def get_gstr1_json(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> dict:
    company = await get_company(db, _company(current_user))
    report = await gst_returns.gstr1(db, company, from_date=from_date, to_date=to_date)
    return gst_returns.gstr1_json(report)


@router.get(
    "/gstr-3b",
    response_model=Gstr3bReport,
    summary="GSTR-3B (summary)",
    description="Monthly summary return — §3.1 outward tax liability, §3.2 inter-state supplies to "
    "unregistered persons, and §4 eligible ITC from submitted Purchase Invoices.",
)
async def get_gstr3b(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> Gstr3bReport:
    company = await get_company(db, _company(current_user))
    return await gst_returns.gstr3b(db, company, from_date=from_date, to_date=to_date)
