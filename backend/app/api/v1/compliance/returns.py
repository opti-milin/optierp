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
from app.schemas.compliance import (
    Cmp08Report,
    Gstr1Report,
    Gstr2bReconReport,
    Gstr2bReconRequest,
    Gstr3bReport,
    Gstr4Report,
    IffReport,
)
from app.services import gst_returns, gst_settings, gstr2b_recon
from app.services.accounts_common import get_company

router = APIRouter(prefix="/gst-returns", tags=["compliance: gst returns"])


def _company(current_user: CurrentUser) -> uuid.UUID:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return current_user.company_id


async def _reject_if_composition(db: AsyncSession, company_id: uuid.UUID) -> None:
    """GSTR-1 / GSTR-3B are for Regular dealers only — a composition dealer files
    CMP-08 (quarterly) and GSTR-4 (annual) instead."""
    if await gst_settings.is_composition(db, company_id):
        raise ValidationError(
            "This company is registered under the composition scheme — file CMP-08 and "
            "GSTR-4 instead of GSTR-1 / GSTR-3B.",
            code="ERR_COMPOSITION",
        )


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
    await _reject_if_composition(db, company.id)
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
    await _reject_if_composition(db, company.id)
    report = await gst_returns.gstr1(db, company, from_date=from_date, to_date=to_date)
    return gst_returns.gstr1_json(report)


@router.get(
    "/iff",
    response_model=IffReport,
    summary="IFF (QRMP — monthly B2B upload)",
    description="Invoice Furnishing Facility — the monthly B2B / B2C-Large / credit-note upload "
    "for the first two months of a quarter under the QRMP scheme (a subset of GSTR-1).",
)
async def get_iff(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> IffReport:
    company = await get_company(db, _company(current_user))
    await _reject_if_composition(db, company.id)
    return await gst_returns.iff(db, company, from_date=from_date, to_date=to_date)


@router.get(
    "/iff/json",
    summary="IFF portal JSON",
    description="The IFF furnished sections serialised to the GST portal's offline-tool JSON schema.",
)
async def get_iff_json(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> dict:
    company = await get_company(db, _company(current_user))
    await _reject_if_composition(db, company.id)
    report = await gst_returns.iff(db, company, from_date=from_date, to_date=to_date)
    return gst_returns.iff_json(report)


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
    await _reject_if_composition(db, company.id)
    return await gst_returns.gstr3b(db, company, from_date=from_date, to_date=to_date)


@router.get(
    "/cmp-08",
    response_model=Cmp08Report,
    summary="CMP-08 (composition — quarterly)",
    description="Quarterly statement-cum-challan of self-assessed composition tax — composite "
    "levy on outward turnover plus tax on inward reverse-charge supplies. Composition dealers only.",
)
async def get_cmp08(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> Cmp08Report:
    company = await get_company(db, _company(current_user))
    return await gst_returns.cmp08(db, company, from_date=from_date, to_date=to_date)


@router.get(
    "/gstr-4",
    response_model=Gstr4Report,
    summary="GSTR-4 (composition — annual)",
    description="The composition dealer's annual return — outward turnover + composite tax, "
    "inward reverse-charge supplies, and the per-quarter (CMP-08) breakdown.",
)
async def get_gstr4(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> Gstr4Report:
    company = await get_company(db, _company(current_user))
    return await gst_returns.gstr4(db, company, from_date=from_date, to_date=to_date)


@router.post(
    "/gstr-2b/reconcile",
    response_model=Gstr2bReconReport,
    summary="GSTR-2B reconciliation",
    description="Reconcile the purchase register in a period against an uploaded portal GSTR-2B "
    "JSON — matches supplier invoices and flags mismatches + ITC at risk (booked but not yet in 2B).",
)
async def reconcile_gstr2b(
    payload: Gstr2bReconRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Gstr2bReconReport:
    company = await get_company(db, _company(current_user))
    return await gstr2b_recon.reconcile_gstr2b(
        db, company, from_date=payload.from_date, to_date=payload.to_date, gstr2b=payload.gstr2b
    )
