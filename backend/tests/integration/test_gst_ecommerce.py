"""Integration: e-commerce TCS u/s 52 — GSTR-1 Table 14(a) — seller side.

A seller who supplies THROUGH an e-commerce operator tags the invoice with the operator's
GSTIN. The supply still carries the seller's own GST (it appears in B2B/B2C as usual) AND is
reported, aggregated per operator, in GSTR-1 Table 14(a) — the operator collects TCS and files
GSTR-8. This is additive reporting: the invoice is NOT removed from B2B/B2C.
"""

import pytest

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"
HSN = "84182100"  # Taxable 18% good
ECO = "29AAECS1234F1Z5"  # e-commerce operator GSTIN


async def _seed_hsn() -> None:
    from app.core.database import async_session_factory
    from app.models.core import HsnCode

    async with async_session_factory() as db:
        db.add(HsnCode(hsn_code=HSN, description="Refrigerator", gst_rate=18, gst_treatment="Taxable"))
        await db.commit()


async def _gst_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "ECom Seller", "abbr": "ECOM", "default_currency": "INR",
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
        json={"item_code": "FRIDGE", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=headers,
    )).json()


async def _customer(client, headers, name, gstin=None):
    body = {"customer_name": name}
    if gstin:
        body["tax_id"] = gstin
    return (await client.post(f"{API}/customers", json=body, headers=headers)).json()


async def _submit_si(client, headers, cust_id, item, rate, ecommerce_gstin=None):
    body = {"customer_id": cust_id, "posting_date": "2026-07-10",
            "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": str(rate)}]}
    if ecommerce_gstin:
        body["ecommerce_gstin"] = ecommerce_gstin
    r = await client.post(f"{API}/sales-invoices", json=body, headers=headers)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert (await client.post(f"{API}/sales-invoices/{inv['id']}/submit", headers=headers)).status_code == 200
    return inv


async def _gstr1(client, headers, path="gstr-1"):
    return (await client.get(
        f"{API}/gst-returns/{path}", params={"from_date": "2026-07-01", "to_date": "2026-07-31"}, headers=headers
    )).json()


async def test_ecommerce_supply_still_charges_gst_and_echoes_tag(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    cust = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")

    inv = await _submit_si(client, h, cust["id"], item, 10000, ecommerce_gstin=ECO)
    # the ECO tag is stored, and the seller STILL charges its own GST
    assert inv["ecommerce_gstin"] == ECO
    assert float(inv["total_taxes_and_charges"]) == 1800  # CGST 900 + SGST 900
    assert float(inv["grand_total"]) == 11800


async def test_gstr1_table14_aggregates_by_operator_and_is_additive(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    b2b = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")

    # two supplies through the ECO (10000 + 5000) and one direct (8000, no ECO)
    await _submit_si(client, h, b2b["id"], item, 10000, ecommerce_gstin=ECO)
    walkin = await _customer(client, h, "Walk-in")
    await _submit_si(client, h, walkin["id"], item, 5000, ecommerce_gstin=ECO)
    direct = await _customer(client, h, "Direct Buyer", "27PQRSX1234F1Z5")
    await _submit_si(client, h, direct["id"], item, 8000)

    g1 = await _gstr1(client, h)

    # Table 14(a): one operator, aggregating the two ECO supplies (15000 → 1350 + 1350)
    assert len(g1["eco"]) == 1
    row = g1["eco"][0]
    assert row["ecommerce_gstin"] == ECO
    assert float(row["taxable_value"]) == 15000
    assert float(row["cgst"]) == 1350 and float(row["sgst"]) == 1350
    assert row["invoice_count"] == 2

    # additive: the ECO supplies are STILL reported in the normal sections, not removed —
    # the two registered buyers in B2B (10000 + 8000) and the walk-in in B2CS (5000).
    all_b2b_taxable = sum(float(b["taxable_value"]) for b in g1["b2b"])
    all_b2cs_taxable = sum(float(b["taxable_value"]) for b in g1["b2cs"])
    assert all_b2b_taxable == 18000  # the two registered buyers
    assert all_b2cs_taxable == 5000  # the walk-in
    # totals cover every invoice exactly once (Table 14 does not double-count)
    assert float(g1["totals"]["taxable_value"]) == 23000


async def test_gstr1_json_emits_supeco_clttx(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    cust = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")
    await _submit_si(client, h, cust["id"], item, 10000, ecommerce_gstin=ECO)

    j = await _gstr1(client, h, "gstr-1/json")
    assert "supeco" in j
    clttx = j["supeco"]["clttx"]
    assert len(clttx) == 1
    assert clttx[0]["etin"] == ECO
    assert clttx[0]["suppval"] == 10000
    assert clttx[0]["cgst"] == 900 and clttx[0]["sgst"] == 900
