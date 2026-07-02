"""Integration: E-Invoice + E-Way Bill JSON generators — India Compliance Phase 4.

Each is gated by the per-company GST Settings flag; e-invoice is B2B only.
"""

import pytest

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"
HSN = "84182100"


async def _seed_hsn() -> None:
    from app.core.database import async_session_factory
    from app.models.core import HsnCode

    async with async_session_factory() as db:
        db.add(HsnCode(hsn_code=HSN, description="Refrigerator", gst_rate=18, gst_treatment="Taxable"))
        await db.commit()


async def _gst_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "EDoc Co", "abbr": "EDOC", "default_currency": "INR",
              "country_code": "IN", "tax_id": "27AAEPM1234C1Z5"},
        headers=headers,
    )
    company = r.json()
    r = await client.post(f"{API}/auth/switch-company", json={"company_id": company["id"]}, headers=headers)
    gh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    duties = await coa_account(client, company, gh, "Duties and Taxes")
    for name in ("Output CGST", "Output SGST", "Output IGST", "Input GST"):
        await client.post(
            f"{API}/accounts",
            json={"account_name": name, "parent_account_id": duties["id"], "account_type": "Tax"},
            headers=gh,
        )
    return company, gh


async def _item(client, h):
    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=h)).json()
    return (await client.post(
        f"{API}/items",
        json={"item_code": "FRIDGE", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=h,
    )).json()


async def _submit_b2b(client, h, item, *, name="KA Buyer", gstin="29ABCDE1234F1Z5", rate=10000):
    body = {"customer_name": name}
    if gstin:
        body["tax_id"] = gstin
    cust = (await client.post(f"{API}/customers", json=body, headers=h)).json()
    inv = (await client.post(
        f"{API}/sales-invoices",
        json={"customer_id": cust["id"], "posting_date": "2026-07-10",
              "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": str(rate)}]},
        headers=h,
    )).json()
    assert (await client.post(f"{API}/sales-invoices/{inv['id']}/submit", headers=h)).status_code == 200
    return inv


async def _enable(client, h, **flags):
    r = await client.put(f"{API}/gst-settings", json={**flags}, headers=h)
    assert r.status_code == 200, r.text


async def test_e_invoice_json_b2b(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    await _enable(client, h, e_invoice_applicable=True)
    item = await _item(client, h)
    inv = await _submit_b2b(client, h, item)  # inter-state (29) → IGST 1800

    r = await client.get(f"{API}/e-documents/sales-invoices/{inv['id']}/e-invoice", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["Version"] == "1.1"
    assert j["TranDtls"]["SupTyp"] == "B2B"
    assert j["DocDtls"]["No"] == inv["name"]
    assert j["SellerDtls"]["Gstin"] == "27AAEPM1234C1Z5"
    assert j["BuyerDtls"]["Gstin"] == "29ABCDE1234F1Z5"
    assert j["ItemList"][0]["HsnCd"] == HSN
    assert j["ItemList"][0]["IgstAmt"] == 1800.0
    assert j["ValDtls"]["IgstVal"] == 1800.0
    assert j["ValDtls"]["AssVal"] == 10000.0


async def test_e_invoice_requires_flag_and_gstin(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)

    # flag off → refused
    inv = await _submit_b2b(client, h, item)
    r = await client.get(f"{API}/e-documents/sales-invoices/{inv['id']}/e-invoice", headers=h)
    assert r.status_code == 422

    # flag on but B2C (no GSTIN) → refused (e-invoice is B2B)
    await _enable(client, h, e_invoice_applicable=True)
    b2c = await _submit_b2b(client, h, item, name="Walk-in", gstin=None)
    r = await client.get(f"{API}/e-documents/sales-invoices/{b2c['id']}/e-invoice", headers=h)
    assert r.status_code == 422


async def test_e_invoice_push_falls_back_to_json(ctx):
    """With no GSP configured, /push returns the JSON envelope for manual upload."""
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    await _enable(client, h, e_invoice_applicable=True)
    item = await _item(client, h)
    inv = await _submit_b2b(client, h, item)

    r = await client.post(f"{API}/e-documents/sales-invoices/{inv['id']}/e-invoice/push", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "generated"
    assert j["provider"] == "none"
    assert j["result"] is None
    assert j["payload"]["Version"] == "1.1"
    assert j["payload"]["BuyerDtls"]["Gstin"] == "29ABCDE1234F1Z5"


async def test_e_way_bill_json(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    await _enable(client, h, e_way_bill_applicable=True)
    item = await _item(client, h)
    inv = await _submit_b2b(client, h, item)  # inter-state

    r = await client.get(
        f"{API}/e-documents/sales-invoices/{inv['id']}/e-way-bill",
        params={"vehicle_no": "MH12AB1234", "distance_km": 350, "transport_mode": "1"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["docNo"] == inv["name"]
    assert j["supplyType"] == "O"
    assert j["fromStateCode"] == 27 and j["toStateCode"] == 29
    assert j["igstValue"] == 1800.0
    assert j["vehicleNo"] == "MH12AB1234"
    assert j["transDistance"] == "350"
    assert j["itemList"][0]["igstRate"] == 18.0
