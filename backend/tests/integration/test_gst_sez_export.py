"""Integration: SEZ / Export (zero-rated supplies u/s 16 IGST Act).

Export/SEZ WITHOUT payment (LUT/bond) = zero-rated, no GST. WITH payment = IGST, ALWAYS
inter-state (even an SEZ in the supplier's own state). GSTR-1: exports → Table 6A (exp,
EXPWP/EXPWOP + shipping bill/port); SEZ → B2B tagged inv_typ SEZWP/SEZWOP. GSTR-3B: all fold
into 3.1(b) zero-rated and are excluded from 3.1(a).
"""

import pytest

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"
HSN = "84182100"  # 18% good


async def _seed_hsn() -> None:
    from app.core.database import async_session_factory
    from app.models.core import HsnCode

    async with async_session_factory() as db:
        db.add(HsnCode(hsn_code=HSN, description="Machine", gst_rate=18, gst_treatment="Taxable"))
        await db.commit()


async def _gst_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "Export Co", "abbr": "EXPCO", "default_currency": "INR",
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


async def _item(client, headers):
    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=headers)).json()
    return (await client.post(
        f"{API}/items",
        json={"item_code": "MACHINE", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=headers,
    )).json()


async def _customer(client, headers, name, gstin=None):
    body = {"customer_name": name}
    if gstin:
        body["tax_id"] = gstin
    return (await client.post(f"{API}/customers", json=body, headers=headers)).json()


async def _submit_si(client, headers, cust_id, item, rate, **extra):
    body = {"customer_id": cust_id, "posting_date": "2026-07-10",
            "items": [{"item_id": item["id"], "item_name": "MACHINE", "qty": 1, "rate": str(rate)}], **extra}
    r = await client.post(f"{API}/sales-invoices", json=body, headers=headers)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert (await client.post(f"{API}/sales-invoices/{inv['id']}/submit", headers=headers)).status_code == 200
    return inv


async def _returns(client, headers, path):
    return (await client.get(
        f"{API}/gst-returns/{path}", params={"from_date": "2026-07-01", "to_date": "2026-07-31"}, headers=headers
    )).json()


async def test_export_lut_is_zero_rated_no_gst(ctx):
    """Export WITHOUT payment (LUT) — no GST, in create AND live preview."""
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    buyer = await _customer(client, h, "Foreign Buyer")  # no GSTIN

    pv = (await client.post(
        f"{API}/sales-invoices/preview",
        json={"customer_id": buyer["id"], "posting_date": "2026-07-10", "gst_category": "Export",
              "export_with_payment": False,
              "items": [{"item_id": item["id"], "item_name": "MACHINE", "qty": 1, "rate": "10000"}]},
        headers=h,
    )).json()
    assert float(pv["total_taxes_and_charges"]) == 0
    assert pv["taxes"] == []

    inv = await _submit_si(client, h, buyer["id"], item, 10000, gst_category="Export", export_with_payment=False)
    assert inv["gst_category"] == "Export"
    assert float(inv["grand_total"]) == 10000
    assert inv["taxes"] == []


async def test_export_with_payment_charges_igst_and_table_6a(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    buyer = await _customer(client, h, "Export Buyer")  # no GSTIN

    inv = await _submit_si(
        client, h, buyer["id"], item, 20000, gst_category="Export", export_with_payment=True,
        shipping_bill_no="SB-77", shipping_bill_date="2026-07-12", port_code="INMAA1",
    )
    # IGST charged (18% of 20000 = 3600), never CGST/SGST
    assert float(inv["total_taxes_and_charges"]) == 3600
    descs = {t["description"] for t in inv["taxes"]}
    assert descs == {"IGST"}

    g1 = await _returns(client, h, "gstr-1")
    assert len(g1["exp"]) == 1
    row = g1["exp"][0]
    assert row["export_type"] == "WPAY"
    assert float(row["taxable_value"]) == 20000 and float(row["igst"]) == 3600
    assert row["shipping_bill_no"] == "SB-77" and row["port_code"] == "INMAA1"
    # exports are NOT in B2B/B2C
    assert g1["b2b"] == [] and g1["b2cl"] == [] and g1["b2cs"] == []

    j = await _returns(client, h, "gstr-1/json")
    assert "exp" in j
    assert j["exp"][0]["exp_typ"] == "WPAY"
    assert j["exp"][0]["inv"][0]["sbnum"] == "SB-77"


async def test_same_state_sez_with_payment_forces_igst_and_sezwp(ctx):
    """An SEZ unit in the supplier's OWN state must still be IGST (deemed inter-state)."""
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    sez = await _customer(client, h, "SEZ Unit", "27AAECS1234F1Z5")  # same state (27) as company

    inv = await _submit_si(client, h, sez["id"], item, 30000, gst_category="SEZ", export_with_payment=True)
    # forced IGST despite same-state party (would normally be CGST+SGST)
    assert float(inv["total_taxes_and_charges"]) == 5400
    assert {t["description"] for t in inv["taxes"]} == {"IGST"}

    g1 = await _returns(client, h, "gstr-1")
    # SEZ stays in B2B (registered recipient), NOT in exports
    assert len(g1["b2b"]) == 1 and g1["exp"] == []
    assert g1["b2b"][0]["gstin"] == "27AAECS1234F1Z5"

    j = await _returns(client, h, "gstr-1/json")
    assert j["b2b"][0]["inv"][0]["inv_typ"] == "SEZWP"


async def test_gstr3b_zero_rated_3_1b_excluded_from_3_1a(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)

    lut = await _customer(client, h, "LUT Buyer")
    await _submit_si(client, h, lut["id"], item, 10000, gst_category="Export", export_with_payment=False)
    wpay = await _customer(client, h, "WPAY Buyer")
    await _submit_si(client, h, wpay["id"], item, 20000, gst_category="Export", export_with_payment=True)
    sez = await _customer(client, h, "SEZ Unit", "27AAECS1234F1Z5")
    await _submit_si(client, h, sez["id"], item, 30000, gst_category="SEZ", export_with_payment=True)

    g3 = await _returns(client, h, "gstr-3b")
    outward = {row["label"][:3]: row for row in g3["outward"]}
    # 3.1(b) zero-rated: taxable 60000, IGST = 3600 + 5400 = 9000 (LUT contributes 0 tax)
    b = outward["(b)"]
    assert float(b["taxable_value"]) == 60000
    assert float(b["igst"]) == 9000
    assert float(b["cgst"]) == 0 and float(b["sgst"]) == 0
    # 3.1(a) excludes all zero-rated docs
    a = outward["(a)"]
    assert float(a["taxable_value"]) == 0
    assert float(a["igst"]) == 0
    # net tax payable includes the with-payment IGST (a real, then-refunded, liability)
    assert float(g3["net_tax_payable"]["igst"]) == 9000
