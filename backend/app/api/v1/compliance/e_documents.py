"""E-document endpoints — E-Invoice (IRN) & E-Way Bill JSON (India compliance Phase 4).

Read-only JSON generators for a submitted Sales Invoice, each gated by the per-company
GST Settings applicability flag. The live IRP/NIC push (IRN/QR, EWB number) is Phase 5.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.services import e_documents, gsp, gst_settings
from app.services.accounts_common import get_company
from app.services.gsp import GspError

router = APIRouter(prefix="/e-documents", tags=["compliance: e-documents"])


def _company(current_user: CurrentUser) -> uuid.UUID:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return current_user.company_id


@router.get(
    "/sales-invoices/{invoice_id}/e-invoice",
    summary="E-Invoice JSON (Schema 1.1)",
    description="Generate the NIC e-invoice (IRN) JSON for a submitted B2B Sales Invoice. "
    "Requires 'E-Invoice applicable' in GST Settings and a registered (GSTIN) customer.",
)
async def e_invoice(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    company_id = _company(current_user)
    settings = await gst_settings.get_gst_settings(db, company_id)
    if not settings.e_invoice_applicable:
        raise ValidationError("E-Invoice is not enabled for this company (enable it in GST Settings)")
    company = await get_company(db, company_id)
    return await e_documents.e_invoice_json(db, company, invoice_id)


@router.get(
    "/sales-invoices/{invoice_id}/e-way-bill",
    summary="E-Way Bill JSON",
    description="Generate the e-way-bill JSON for a submitted Sales Invoice. Requires 'E-Way Bill "
    "applicable' in GST Settings. Transporter/vehicle (Part-B) fields are optional query params.",
)
async def e_way_bill(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    transport_mode: str = "1",
    vehicle_no: str | None = None,
    transporter_id: str | None = None,
    transporter_name: str | None = None,
    trans_doc_no: str | None = None,
    distance_km: int = 0,
) -> dict:
    company_id = _company(current_user)
    settings = await gst_settings.get_gst_settings(db, company_id)
    if not settings.e_way_bill_applicable:
        raise ValidationError("E-Way Bill is not enabled for this company (enable it in GST Settings)")
    company = await get_company(db, company_id)
    return await e_documents.e_way_bill_json(
        db, company, invoice_id,
        transport_mode=transport_mode, vehicle_no=vehicle_no, transporter_id=transporter_id,
        transporter_name=transporter_name, trans_doc_no=trans_doc_no, distance_km=distance_km,
    )


async def _push(db, company_id, kind: str, payload: dict) -> dict:
    """Push a payload to the tenant's GSP; degrade to JSON-only when none is configured.

    Returns a unified envelope so the caller always gets the JSON (for manual upload) plus
    the live result (IRN / EWB number) when a GSP actually pushed it."""
    settings = await gst_settings.get_gst_settings(db, company_id)
    provider = gsp.get_provider(settings)
    if not provider.configured:
        return {"status": "generated", "provider": provider.name, "payload": payload, "result": None,
                "message": "No GSP configured — download this JSON and upload it on the portal."}
    try:
        result = (
            await provider.push_e_invoice(payload) if kind == "e-invoice"
            else await provider.push_e_way_bill(payload)
        )
    except GspError as exc:
        raise ValidationError(str(exc)) from exc
    return {"status": "pushed", "provider": provider.name, "payload": payload, "result": result,
            "message": f"Pushed via {provider.name}."}


@router.post(
    "/sales-invoices/{invoice_id}/e-invoice/push",
    summary="Push E-Invoice to the IRP (or return JSON)",
    description="Generate the e-invoice JSON and push it to the tenant's configured GSP/IRP to "
    "obtain an IRN + signed QR. With no GSP configured, returns the JSON for manual upload.",
)
async def push_e_invoice(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    company_id = _company(current_user)
    settings = await gst_settings.get_gst_settings(db, company_id)
    if not settings.e_invoice_applicable:
        raise ValidationError("E-Invoice is not enabled for this company (enable it in GST Settings)")
    company = await get_company(db, company_id)
    payload = await e_documents.e_invoice_json(db, company, invoice_id)
    return await _push(db, company_id, "e-invoice", payload)


@router.post(
    "/sales-invoices/{invoice_id}/e-way-bill/push",
    summary="Push E-Way Bill to NIC (or return JSON)",
    description="Generate the e-way-bill JSON and push it to the tenant's configured GSP/NIC to "
    "obtain an EWB number. With no GSP configured, returns the JSON for manual upload.",
)
async def push_e_way_bill(
    invoice_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    transport_mode: str = "1",
    vehicle_no: str | None = None,
    transporter_id: str | None = None,
    transporter_name: str | None = None,
    trans_doc_no: str | None = None,
    distance_km: int = 0,
) -> dict:
    company_id = _company(current_user)
    settings = await gst_settings.get_gst_settings(db, company_id)
    if not settings.e_way_bill_applicable:
        raise ValidationError("E-Way Bill is not enabled for this company (enable it in GST Settings)")
    company = await get_company(db, company_id)
    payload = await e_documents.e_way_bill_json(
        db, company, invoice_id,
        transport_mode=transport_mode, vehicle_no=vehicle_no, transporter_id=transporter_id,
        transporter_name=transporter_name, trans_doc_no=trans_doc_no, distance_km=distance_km,
    )
    return await _push(db, company_id, "e-way-bill", payload)
