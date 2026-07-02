"""E-documents — E-Invoice (IRN) and E-Way Bill JSON generators — India Compliance Phase 4.

Bespoke **read-only** generators that turn a submitted Sales Invoice into the JSON payload the
government portals expect (NIC e-invoice schema 1.1 for B2B; the e-way-bill bulk-generation
schema). This is the **data layer** — a tenant without GSP credentials still gets an uploadable
JSON. The **live** IRP/NIC push (IRN + signed QR, EWB number, Part-B vehicle updates) is Phase 5,
which sits on top of these builders.

Per-line GST is computed from each line's effective HSN rate (the same source the invoice's
auto-GST used), split IGST (inter-state) vs CGST+SGST (intra-state) by the company↔place-of-supply
states — so the payload validates against the rate rather than depending on stored tax rows.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.gst_states import state_code_of
from app.models.accounts import SalesInvoice
from app.models.core import Company
from app.models.selling import Address
from app.services.accounts_common import _hsn_rates

ZERO = Decimal("0")
Q2 = Decimal("0.01")

# NIC unit-quantity codes (UQC). Anything unmapped falls back to OTH-OTHERS.
_UQC = {
    "nos": "NOS", "no": "NOS", "unit": "NOS", "units": "NOS", "pcs": "PCS", "pc": "PCS",
    "kg": "KGS", "kgs": "KGS", "gram": "GMS", "g": "GMS", "litre": "LTR", "l": "LTR",
    "ltr": "LTR", "mtr": "MTR", "m": "MTR", "box": "BOX", "set": "SET", "pair": "PRS",
    "dozen": "DOZ", "ton": "TON", "sqm": "SQM", "sqf": "SQF", "roll": "ROL", "bag": "BAG",
    "bottle": "BTL", "can": "CAN", "cartons": "CTN", "carton": "CTN",
}


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


def _uqc(uom: str | None) -> str:
    return _UQC.get((uom or "").strip().lower(), "OTH")


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _pin(value: str | None) -> int:
    """A numeric pincode, or 999999 when unknown (portal placeholder for URP)."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return int(digits) if len(digits) == 6 else 999999


async def _load(db: AsyncSession, company: Company, invoice_id: uuid.UUID) -> SalesInvoice:
    inv = (
        await db.execute(
            select(SalesInvoice)
            .options(selectinload(SalesInvoice.items))
            .where(SalesInvoice.id == invoice_id, SalesInvoice.company_id == company.id)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise NotFoundError("Sales Invoice not found")
    if inv.docstatus != 1:
        raise ValidationError("E-documents can only be generated for a submitted invoice")
    return inv


async def _company_address(db: AsyncSession, company: Company) -> Address | None:
    return (
        await db.execute(
            select(Address)
            .where(Address.company_id == company.id, Address.is_company_address.is_(True),
                   Address.disabled.is_(False))
            .order_by(Address.address_type)
        )
    ).scalars().first()


async def _line_gst(db: AsyncSession, company: Company, inv: SalesInvoice) -> tuple[list[dict], bool]:
    """Per-line taxable + IGST/CGST/SGST from each line's HSN rate. Returns (lines, inter)."""
    company_code = state_code_of(company.tax_id)
    cust_gstin = inv.customer.tax_id if inv.customer else None
    pos_code = None
    if inv.place_of_supply and inv.place_of_supply[:2].isdigit():
        pos_code = inv.place_of_supply[:2]
    elif cust_gstin:
        pos_code = state_code_of(cust_gstin)
    inter = bool(company_code and pos_code and company_code != pos_code)

    hsn_rate = await _hsn_rates(db, {li.hsn_sac_code for li in inv.items if li.hsn_sac_code})
    lines = []
    for i, li in enumerate(inv.items, start=1):
        rate = hsn_rate.get(li.hsn_sac_code or "", ZERO)
        taxable = Decimal(li.base_net_amount or 0)
        igst = taxable * rate / 100 if inter else ZERO
        cgst = ZERO if inter else taxable * rate / 200
        sgst = ZERO if inter else taxable * rate / 200
        lines.append(
            {
                "sl": i,
                "item": li,
                "rate": rate,
                "taxable": taxable,
                "igst": igst,
                "cgst": cgst,
                "sgst": sgst,
                "total": taxable + igst + cgst + sgst,
            }
        )
    return lines, inter


async def e_invoice_json(db: AsyncSession, company: Company, invoice_id: uuid.UUID) -> dict:
    """NIC e-invoice schema 1.1 payload (B2B). Requires a registered recipient (GSTIN)."""
    inv = await _load(db, company, invoice_id)
    cust = inv.customer
    cust_gstin = (cust.tax_id or "").strip() if cust else ""
    if len(cust_gstin) != 15:
        raise ValidationError("E-invoice applies to B2B supplies — the customer needs a GSTIN")

    seller_addr = await _company_address(db, company)
    buyer_addr = await db.get(Address, inv.customer_address_id) if inv.customer_address_id else None
    ship_addr = await db.get(Address, inv.shipping_address_id) if inv.shipping_address_id else None

    lines, _inter = await _line_gst(db, company, inv)
    company_code = state_code_of(company.tax_id) or "99"
    pos_code = (inv.place_of_supply[:2] if inv.place_of_supply and inv.place_of_supply[:2].isdigit()
                else state_code_of(cust_gstin) or company_code)

    item_list = []
    ass_val = igst_val = cgst_val = sgst_val = ZERO
    for ln in lines:
        li = ln["item"]
        item_list.append(
            {
                "SlNo": str(ln["sl"]),
                "PrdDesc": li.item_name,
                "IsServc": "N",
                "HsnCd": li.hsn_sac_code,
                "Qty": float(li.qty or 0),
                "Unit": _uqc(li.uom),
                "UnitPrice": float(_q(li.rate)),
                "TotAmt": float(_q(ln["taxable"])),
                "AssAmt": float(_q(ln["taxable"])),
                "GstRt": float(ln["rate"]),
                "IgstAmt": float(_q(ln["igst"])),
                "CgstAmt": float(_q(ln["cgst"])),
                "SgstAmt": float(_q(ln["sgst"])),
                "TotItemVal": float(_q(ln["total"])),
            }
        )
        ass_val += ln["taxable"]
        igst_val += ln["igst"]
        cgst_val += ln["cgst"]
        sgst_val += ln["sgst"]

    return {
        "Version": "1.1",
        "TranDtls": {
            "TaxSch": "GST",
            "SupTyp": "B2B",
            "RegRev": "Y" if inv.is_reverse_charge else "N",
            "IgstOnIntra": "N",
        },
        "DocDtls": {"Typ": "CRN" if inv.is_return else "INV", "No": inv.name, "Dt": _fmt(inv.posting_date)},
        "SellerDtls": {
            "Gstin": company.tax_id,
            "LglNm": company.company_name,
            "Addr1": seller_addr.address_line1 if seller_addr else company.company_name,
            "Loc": (seller_addr.city if seller_addr else None) or "NA",
            "Pin": _pin(seller_addr.pincode if seller_addr else None),
            "Stcd": company_code,
        },
        "BuyerDtls": {
            "Gstin": cust_gstin,
            "LglNm": cust.customer_name if cust else "",
            "Pos": pos_code,
            "Addr1": (buyer_addr.address_line1 if buyer_addr else None) or "NA",
            "Loc": (buyer_addr.city if buyer_addr else None) or "NA",
            "Pin": _pin(buyer_addr.pincode if buyer_addr else None),
            "Stcd": state_code_of(cust_gstin) or pos_code,
        },
        "DispDtls": _ship_block(ship_addr) if ship_addr else None,
        "ItemList": item_list,
        "ValDtls": {
            "AssVal": float(_q(ass_val)),
            "IgstVal": float(_q(igst_val)),
            "CgstVal": float(_q(cgst_val)),
            "SgstVal": float(_q(sgst_val)),
            "TotInvVal": float(_q(inv.base_grand_total or 0)),
        },
    }


def _ship_block(addr: Address) -> dict:
    return {
        "Nm": addr.address_title,
        "Addr1": addr.address_line1,
        "Loc": addr.city or "NA",
        "Pin": _pin(addr.pincode),
        "Stcd": addr.state or "",
    }


async def e_way_bill_json(
    db: AsyncSession,
    company: Company,
    invoice_id: uuid.UUID,
    *,
    transport_mode: str = "1",  # 1=Road 2=Rail 3=Air 4=Ship
    vehicle_no: str | None = None,
    transporter_id: str | None = None,
    transporter_name: str | None = None,
    trans_doc_no: str | None = None,
    distance_km: int = 0,
) -> dict:
    """E-way-bill JSON. Transporter/vehicle (Part-B) are optional inputs — not on the invoice."""
    inv = await _load(db, company, invoice_id)
    cust = inv.customer
    cust_gstin = (cust.tax_id or "").strip() if cust else ""
    seller_addr = await _company_address(db, company)
    buyer_addr = await db.get(Address, inv.customer_address_id) if inv.customer_address_id else None
    ship_addr = (
        await db.get(Address, inv.shipping_address_id) if inv.shipping_address_id else None
    ) or buyer_addr

    lines, inter = await _line_gst(db, company, inv)
    company_code = int(state_code_of(company.tax_id) or 99)
    pos_code = int(
        (inv.place_of_supply[:2] if inv.place_of_supply and inv.place_of_supply[:2].isdigit()
         else state_code_of(cust_gstin)) or company_code
    )

    item_list = []
    total_taxable = igst_v = cgst_v = sgst_v = ZERO
    for ln in lines:
        li = ln["item"]
        half = ln["rate"] / 2
        item_list.append(
            {
                "productName": li.item_name,
                "hsnCode": int(li.hsn_sac_code) if (li.hsn_sac_code or "").isdigit() else 0,
                "quantity": float(li.qty or 0),
                "qtyUnit": _uqc(li.uom),
                "taxableAmount": float(_q(ln["taxable"])),
                "sgstRate": float(ZERO if inter else half),
                "cgstRate": float(ZERO if inter else half),
                "igstRate": float(ln["rate"] if inter else ZERO),
                "cessRate": 0,
            }
        )
        total_taxable += ln["taxable"]
        igst_v += ln["igst"]
        cgst_v += ln["cgst"]
        sgst_v += ln["sgst"]

    payload = {
        "supplyType": "O",  # outward
        "subSupplyType": "1",  # supply
        "docType": "CRN" if inv.is_return else "INV",
        "docNo": inv.name,
        "docDate": _fmt(inv.posting_date),
        "fromGstin": company.tax_id or "URP",
        "fromTrdName": company.company_name,
        "fromAddr1": seller_addr.address_line1 if seller_addr else company.company_name,
        "fromPlace": (seller_addr.city if seller_addr else None) or "NA",
        "fromPincode": _pin(seller_addr.pincode if seller_addr else None),
        "fromStateCode": company_code,
        "actFromStateCode": company_code,
        "toGstin": cust_gstin if len(cust_gstin) == 15 else "URP",
        "toTrdName": cust.customer_name if cust else "",
        "toAddr1": (ship_addr.address_line1 if ship_addr else None) or "NA",
        "toPlace": (ship_addr.city if ship_addr else None) or "NA",
        "toPincode": _pin(ship_addr.pincode if ship_addr else None),
        "toStateCode": pos_code,
        "actToStateCode": pos_code,
        "transactionType": 1,
        "totalValue": float(_q(total_taxable)),
        "cgstValue": float(_q(cgst_v)),
        "sgstValue": float(_q(sgst_v)),
        "igstValue": float(_q(igst_v)),
        "cessValue": 0,
        "totInvValue": float(_q(inv.base_grand_total or 0)),
        "transMode": transport_mode,
        "transDistance": str(distance_km),
        "itemList": item_list,
    }
    # Part-B / transporter details are optional (added when moving the goods).
    if transporter_id:
        payload["transporterId"] = transporter_id
    if transporter_name:
        payload["transporterName"] = transporter_name
    if trans_doc_no:
        payload["transDocNo"] = trans_doc_no
    if vehicle_no:
        payload["vehicleNo"] = vehicle_no
        payload["vehicleType"] = "R"
    return payload
