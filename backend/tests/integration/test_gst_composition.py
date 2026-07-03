"""Integration: composition scheme — Bill of Supply + CMP-08 + GSTR-4 — Phase 6.3.

A composition dealer issues a Bill of Supply (NO output GST — the tax engine suppresses it
on the sales side, in create AND live preview) and instead pays a flat composite tax on
turnover, declared quarterly in CMP-08 and annually in GSTR-4. Inward reverse charge still
self-assesses (composition dealers are not exempt from RCM). GSTR-1 / GSTR-3B are rejected.
"""

import pytest

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"
HSN = "84182100"  # Taxable 18% good


async def _seed_hsn() -> None:
    from app.core.database import async_session_factory
    from app.models.core import HsnCode

    async with async_session_factory() as db:
        db.add(HsnCode(hsn_code=HSN, description="Refrigerator", gst_rate=18, gst_treatment="Taxable"))
        await db.commit()


async def _composition_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "Comp Co", "abbr": "COMPCO", "default_currency": "INR",
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
    # register the company under the composition scheme (Trader = 1%)
    s = await client.put(
        f"{API}/gst-settings",
        json={"registration_type": "Composition", "composition_category": "Trader"},
        headers=gh,
    )
    assert s.status_code == 200, s.text
    assert s.json()["registration_type"] == "Composition"
    return company, gh


async def _item(client, headers):
    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=headers)).json()
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


async def _submit_si(client, headers, cust_id, item, rate):
    body = {"customer_id": cust_id, "posting_date": "2026-07-10",
            "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": str(rate)}]}
    r = await client.post(f"{API}/sales-invoices", json=body, headers=headers)
    assert r.status_code == 201, r.text
    inv = r.json()
    s = await client.post(f"{API}/sales-invoices/{inv['id']}/submit", headers=headers)
    assert s.status_code == 200, s.text
    return s.json()


async def test_composition_sale_is_bill_of_supply_no_gst(ctx):
    """A composition dealer's sale carries NO output GST — create AND live preview."""
    client, _c, base_headers = ctx
    _co, h = await _composition_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    cust = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")

    # live preview: no GST added (preview == create)
    pv = (await client.post(
        f"{API}/sales-invoices/preview",
        json={"customer_id": cust["id"], "posting_date": "2026-07-10",
              "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": "10000"}]},
        headers=h,
    )).json()
    assert float(pv["net_total"]) == 10000
    assert float(pv["total_taxes_and_charges"]) == 0
    assert float(pv["grand_total"]) == 10000
    assert pv["taxes"] == []

    # create + submit: still a Bill of Supply
    inv = await _submit_si(client, h, cust["id"], item, 10000)
    assert float(inv["net_total"]) == 10000
    assert float(inv["grand_total"]) == 10000
    assert float(inv["total_taxes_and_charges"]) == 0
    assert inv["taxes"] == []


async def test_composition_rejects_gstr1_and_gstr3b(ctx):
    client, _c, base_headers = ctx
    _co, h = await _composition_company(client, base_headers)
    for path in ("gstr-1", "gstr-3b"):
        r = await client.get(
            f"{API}/gst-returns/{path}",
            params={"from_date": "2026-07-01", "to_date": "2026-07-31"},
            headers=h,
        )
        assert r.status_code == 422, r.text
        assert "composition" in r.text.lower()


async def test_cmp08_composite_levy_and_inward_rcm(ctx):
    client, _c, base_headers = ctx
    _co, h = await _composition_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)

    # two outward sales (Bill of Supply): turnover 10000 + 5000 = 15000
    b2b = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")
    await _submit_si(client, h, b2b["id"], item, 10000)
    walkin = await _customer(client, h, "Walk-in")
    await _submit_si(client, h, walkin["id"], item, 5000)

    # one inward reverse-charge purchase 10000 @ 18% → self-assessed CGST 900 + SGST 900
    sup = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "GTA", "tax_id": "27ZZZZZ1234Z1Z5"}, headers=h
    )).json()
    r = await client.post(
        f"{API}/purchase-invoices",
        json={"supplier_id": sup["id"], "posting_date": "2026-07-12", "bill_no": "RCM-1",
              "is_reverse_charge": True,
              "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": "10000"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert (await client.post(f"{API}/purchase-invoices/{r.json()['id']}/submit", headers=h)).status_code == 200

    cmp08 = (await client.get(
        f"{API}/gst-returns/cmp-08", params={"from_date": "2026-07-01", "to_date": "2026-09-30"}, headers=h
    )).json()
    assert cmp08["filing_period"] == "Q2-2026"
    assert cmp08["composition_category"] == "Trader"
    assert float(cmp08["composition_rate"]) == 1

    rows = {row["label"][:4]: row for row in cmp08["rows"]}
    # 3(1) composite levy on 15000 turnover @ 1% → 75 CGST + 75 SGST
    outward = rows["3(1)"]
    assert float(outward["taxable_value"]) == 15000
    assert float(outward["cgst"]) == 75 and float(outward["sgst"]) == 75
    assert float(outward["igst"]) == 0
    # 3(2) inward reverse charge: 900 + 900
    inward = rows["3(2)"]
    assert float(inward["taxable_value"]) == 10000
    assert float(inward["cgst"]) == 900 and float(inward["sgst"]) == 900
    # 3(3) tax payable = composite + RCM
    payable = rows["3(3)"]
    assert float(payable["cgst"]) == 975 and float(payable["sgst"]) == 975
    assert float(cmp08["total_tax"]) == 1950


async def test_gstr4_annual_summary_and_quarters(ctx):
    client, _c, base_headers = ctx
    _co, h = await _composition_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    cust = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")
    await _submit_si(client, h, cust["id"], item, 10000)  # July → Q2

    g4 = (await client.get(
        f"{API}/gst-returns/gstr-4", params={"from_date": "2026-04-01", "to_date": "2027-03-31"}, headers=h
    )).json()
    assert g4["filing_period"] == "2026-27"
    assert float(g4["composition_rate"]) == 1
    outward = g4["rows"][0]
    assert float(outward["taxable_value"]) == 10000
    assert float(outward["cgst"]) == 50 and float(outward["sgst"]) == 50  # 1% of 10000
    assert float(g4["total_tax"]) == 100
    # per-quarter: all turnover in Q2 (Jul–Sep)
    assert len(g4["quarters"]) == 1
    assert g4["quarters"][0]["label"] == "Q2"
    assert float(g4["quarters"][0]["taxable_value"]) == 10000
