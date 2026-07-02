"""Integration: GST returns (GSTR-1 / GSTR-3B) from submitted invoices — Phase 2.

Builds a Maharashtra (27) company, submits a spread of intra/inter, B2B/B2C sales
(plus a purchase for ITC), then checks the returns bucket everything correctly and
that the totals tie to the invoices.
"""

import pytest

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"
HSN = "84182100"  # a Taxable 18% good


async def _seed_hsn() -> None:
    from app.core.database import async_session_factory
    from app.models.core import HsnCode

    async with async_session_factory() as db:
        db.add(HsnCode(hsn_code=HSN, description="Refrigerator", gst_rate=18, gst_treatment="Taxable"))
        await db.commit()


async def _gst_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "Returns Co", "abbr": "RETCO", "default_currency": "INR",
              "country_code": "IN", "tax_id": "27AAEPM1234C1Z5"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    company = r.json()
    r = await client.post(f"{API}/auth/switch-company", json={"company_id": company["id"]}, headers=headers)
    gh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    duties = await coa_account(client, company, gh, "Duties and Taxes")
    for name in ("Output CGST", "Output SGST", "Output IGST", "Input GST"):
        rr = await client.post(
            f"{API}/accounts",
            json={"account_name": name, "parent_account_id": duties["id"], "account_type": "Tax"},
            headers=gh,
        )
        assert rr.status_code == 201, rr.text
    return company, gh


async def _warehouse(client, headers):
    return (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=headers)).json()


async def _item(client, headers, wh):
    r = await client.post(
        f"{API}/items",
        json={"item_code": "FRIDGE", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _customer(client, headers, name, gstin=None):
    body = {"customer_name": name}
    if gstin:
        body["tax_id"] = gstin
    return (await client.post(f"{API}/customers", json=body, headers=headers)).json()


async def _submit_si(client, headers, cust_id, item, rate, *, place_of_supply=None):
    body = {"customer_id": cust_id, "posting_date": "2026-07-10",
            "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": str(rate)}]}
    if place_of_supply:
        body["place_of_supply"] = place_of_supply
    r = await client.post(f"{API}/sales-invoices", json=body, headers=headers)
    assert r.status_code == 201, r.text
    inv = r.json()
    s = await client.post(f"{API}/sales-invoices/{inv['id']}/submit", headers=headers)
    assert s.status_code == 200, s.text
    return s.json()


async def _returns(client, headers, path):
    r = await client.get(
        f"{API}/gst-returns/{path}",
        params={"from_date": "2026-07-01", "to_date": "2026-07-31"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _build(client, base_headers):
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    wh = await _warehouse(client, h)
    item = await _item(client, h, wh)
    # 1) B2B intra (27 GSTIN) 10000 → CGST 900 + SGST 900
    mh = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")
    await _submit_si(client, h, mh["id"], item, 10000)
    # 2) B2B inter (29 GSTIN) 20000 → IGST 3600
    ka = await _customer(client, h, "KA Buyer", "29ABCDE1234F1Z5")
    await _submit_si(client, h, ka["id"], item, 20000)
    # 3) B2C-Small intra (walk-in) 5000 → CGST 450 + SGST 450
    walkin = await _customer(client, h, "Walk-in")
    await _submit_si(client, h, walkin["id"], item, 5000)
    # 4) B2C-Large inter (walk-in, POS 29, > ₹1L) 200000 → IGST 36000
    big = await _customer(client, h, "Big Walk-in")
    await _submit_si(client, h, big["id"], item, 200000, place_of_supply="29-Karnataka")
    return h


async def test_gstr1_buckets_and_totals(ctx):
    client, _c, base_headers = ctx
    h = await _build(client, base_headers)

    g1 = await _returns(client, h, "gstr-1")

    # B2B: two registered recipients
    gstins = {b["gstin"] for b in g1["b2b"]}
    assert gstins == {"27ABCDE1234F1Z5", "29ABCDE1234F1Z5"}
    mh = next(b for b in g1["b2b"] if b["gstin"].startswith("27"))
    assert float(mh["taxable_value"]) == 10000
    assert float(mh["invoices"][0]["cgst"]) == 900
    assert float(mh["invoices"][0]["sgst"]) == 900
    assert float(mh["invoices"][0]["rate"]) == 18

    # B2C-Small: intra walk-in
    assert len(g1["b2cs"]) == 1
    b2cs = g1["b2cs"][0]
    assert b2cs["supply_type"] == "INTRA"
    assert float(b2cs["taxable_value"]) == 5000
    assert float(b2cs["cgst"]) == 450 and float(b2cs["sgst"]) == 450

    # B2C-Large: inter walk-in above threshold
    assert len(g1["b2cl"]) == 1
    assert float(g1["b2cl"][0]["taxable_value"]) == 200000
    assert float(g1["b2cl"][0]["igst"]) == 36000

    # HSN summary: one code, qty 4, and the tax ties to the sum of all invoices
    assert len(g1["hsn"]) == 1
    hsn = g1["hsn"][0]
    assert hsn["hsn_code"] == HSN
    assert float(hsn["qty"]) == 4
    assert float(hsn["taxable_value"]) == 235000
    assert float(hsn["cgst"]) == 1350 and float(hsn["sgst"]) == 1350
    assert float(hsn["igst"]) == 39600

    # Totals tie
    t = g1["totals"]
    assert float(t["taxable_value"]) == 235000
    assert float(t["cgst"]) == 1350 and float(t["sgst"]) == 1350
    assert float(t["igst"]) == 39600
    assert t["invoice_count"] == 4

    # Document summary
    assert len(g1["docs"]) == 1
    assert g1["docs"][0]["total_count"] == 4


async def test_gstr1_json_export(ctx):
    client, _c, base_headers = ctx
    h = await _build(client, base_headers)

    j = await _returns(client, h, "gstr-1/json")
    assert j["gstin"] == "27AAEPM1234C1Z5"
    assert j["fp"] == "072026"
    assert {blk["ctin"] for blk in j["b2b"]} == {"27ABCDE1234F1Z5", "29ABCDE1234F1Z5"}
    assert "b2cs" in j and "b2cl" in j and "hsn" in j and "doc_issue" in j
    # HSN JSON rows carry the portal keys
    assert j["hsn"]["data"][0]["hsn_sc"] == HSN


async def test_gstr3b_summary_and_itc(ctx):
    client, _c, base_headers = ctx
    h = await _build(client, base_headers)

    # add a purchase (intra, supplier 27 GSTIN) 8000 @ 18% → Input GST 1440 → ITC 720+720
    wh_item = (await client.get(f"{API}/items", headers=h)).json()
    item_id = wh_item["items"][0]["id"] if isinstance(wh_item, dict) else wh_item[0]["id"]
    sup = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "Vendor", "tax_id": "27ZZZZZ1234Z1Z5"}, headers=h
    )).json()
    r = await client.post(
        f"{API}/purchase-invoices",
        json={"supplier_id": sup["id"], "posting_date": "2026-07-12", "bill_no": "B-1",
              "items": [{"item_id": item_id, "item_name": "FRIDGE", "qty": 1, "rate": "8000"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    pi = r.json()
    assert (await client.post(f"{API}/purchase-invoices/{pi['id']}/submit", headers=h)).status_code == 200

    g3 = await _returns(client, h, "gstr-3b")

    outward = {row["label"][:3]: row for row in g3["outward"]}
    a = outward["(a)"]
    assert float(a["taxable_value"]) == 235000
    assert float(a["cgst"]) == 1350 and float(a["sgst"]) == 1350 and float(a["igst"]) == 39600

    # 3.2 inter-state to unregistered: the B2C-Large walk-in only (B2B inter is registered)
    assert len(g3["inter_state_unreg"]) == 1
    assert float(g3["inter_state_unreg"][0]["taxable_value"]) == 200000
    assert float(g3["inter_state_unreg"][0]["igst"]) == 36000

    # ITC 4(A)(5): the intra purchase split to CGST 720 + SGST 720
    itc = {row["label"][:6]: row for row in g3["itc"]}
    other = itc["(A)(5)"]
    assert float(other["cgst"]) == 720 and float(other["sgst"]) == 720
