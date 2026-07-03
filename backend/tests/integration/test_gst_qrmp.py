"""Integration: QRMP cadence — quarterly GSTR-1 + IFF (Invoice Furnishing Facility).

A QRMP filer files GSTR-1 / GSTR-3B QUARTERLY (same endpoints, a quarter-wide range) and
furnishes B2B invoices monthly via IFF for months 1 & 2. IFF is a GSTR-1 subset: B2B,
B2C-Large and credit/debit notes only — NO B2C-Small, HSN or document summary.
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


async def _gst_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "QRMP Co", "abbr": "QRMPCO", "default_currency": "INR",
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
    # opt into QRMP (config only — the same endpoints serve a quarter range)
    s = await client.put(
        f"{API}/gst-settings", json={"registration_type": "Regular", "filing_cadence": "QRMP"}, headers=gh
    )
    assert s.status_code == 200, s.text
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


async def _submit_si(client, headers, cust_id, item, rate, posting_date, place_of_supply=None):
    body = {"customer_id": cust_id, "posting_date": posting_date,
            "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": str(rate)}]}
    if place_of_supply:
        body["place_of_supply"] = place_of_supply
    inv = (await client.post(f"{API}/sales-invoices", json=body, headers=headers)).json()
    assert (await client.post(f"{API}/sales-invoices/{inv['id']}/submit", headers=headers)).status_code == 200
    return inv


async def _build_quarter(client, base_headers):
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    # April: B2B intra (27) 10000 → CGST 900 + SGST 900
    mh = await _customer(client, h, "MH Buyer", "27ABCDE1234F1Z5")
    await _submit_si(client, h, mh["id"], item, 10000, "2026-04-10")
    # May: B2B inter (29) 20000 → IGST 3600
    ka = await _customer(client, h, "KA Buyer", "29ABCDE1234F1Z5")
    await _submit_si(client, h, ka["id"], item, 20000, "2026-05-10", "29-Karnataka")
    # June: B2C-Small intra walk-in 5000 → CGST 450 + SGST 450
    walkin = await _customer(client, h, "Walk-in")
    await _submit_si(client, h, walkin["id"], item, 5000, "2026-06-10")
    return h, item


async def test_quarterly_gstr1_aggregates_and_period_is_last_month(ctx):
    client, _c, base_headers = ctx
    h, _item = await _build_quarter(client, base_headers)

    g1 = (await client.get(
        f"{API}/gst-returns/gstr-1", params={"from_date": "2026-04-01", "to_date": "2026-06-30"}, headers=h
    )).json()
    # filing period = last month of the quarter (portal convention for quarterly GSTR-1)
    assert g1["filing_period"] == "062026"
    assert {b["gstin"] for b in g1["b2b"]} == {"27ABCDE1234F1Z5", "29ABCDE1234F1Z5"}
    assert len(g1["b2cs"]) == 1  # the June walk-in
    assert float(g1["totals"]["taxable_value"]) == 35000
    assert g1["totals"]["invoice_count"] == 3


async def test_iff_is_gstr1_subset_b2b_only(ctx):
    client, _c, base_headers = ctx
    h, _item = await _build_quarter(client, base_headers)

    # IFF for April (month 1): the one B2B invoice, no B2CS
    iff = (await client.get(
        f"{API}/gst-returns/iff", params={"from_date": "2026-04-01", "to_date": "2026-04-30"}, headers=h
    )).json()
    assert iff["filing_period"] == "042026"
    assert len(iff["b2b"]) == 1
    assert iff["b2b"][0]["gstin"] == "27ABCDE1234F1Z5"
    assert float(iff["totals"]["taxable_value"]) == 10000
    assert float(iff["totals"]["cgst"]) == 900 and float(iff["totals"]["sgst"]) == 900
    assert iff["totals"]["invoice_count"] == 1
    # IFF carries no B2CS field at all
    assert "b2cs" not in iff

    # IFF for June furnishes NOTHING (only a B2C-Small sale that month — not IFF-eligible)
    iff_jun = (await client.get(
        f"{API}/gst-returns/iff", params={"from_date": "2026-06-01", "to_date": "2026-06-30"}, headers=h
    )).json()
    assert iff_jun["b2b"] == [] and iff_jun["b2cl"] == []
    assert iff_jun["totals"]["invoice_count"] == 0


async def test_iff_json_omits_b2cs_and_hsn(ctx):
    client, _c, base_headers = ctx
    h, _item = await _build_quarter(client, base_headers)

    j = (await client.get(
        f"{API}/gst-returns/iff/json", params={"from_date": "2026-04-01", "to_date": "2026-04-30"}, headers=h
    )).json()
    assert j["fp"] == "042026"
    assert "b2b" in j
    # IFF furnishes only invoice-wise sections — never B2CS / HSN / doc summary
    assert "b2cs" not in j
    assert "hsn" not in j
    assert "doc_issue" not in j
