"""Integration: GSTR-2B reconciliation — India Compliance Phase 6.1.

Booked purchase invoices vs an uploaded portal GSTR-2B JSON → matched / mismatch /
only-in-books (ITC at risk) / only-in-2B.
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
        db.add(HsnCode(hsn_code=HSN, description="Widget", gst_rate=18, gst_treatment="Taxable"))
        await db.commit()


async def _gst_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "Recon Co", "abbr": "RECON", "default_currency": "INR",
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


async def _pi(client, h, item, supplier_id, bill_no, rate):
    r = await client.post(
        f"{API}/purchase-invoices",
        json={"supplier_id": supplier_id, "posting_date": "2026-07-05", "bill_no": bill_no,
              "items": [{"item_id": item["id"], "item_name": "Widget", "qty": 1, "rate": str(rate)}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert (await client.post(f"{API}/purchase-invoices/{inv['id']}/submit", headers=h)).status_code == 200
    return inv


async def test_gstr2b_reconcile_buckets(ctx):
    client, _c, base_headers = ctx
    _co, h = await _gst_company(client, base_headers)
    await _seed_hsn()
    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=h)).json()
    item = (await client.post(
        f"{API}/items",
        json={"item_code": "WIDGET", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=h,
    )).json()
    sup_a = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "Alpha Traders", "tax_id": "27AAAAA1111A1Z5"}, headers=h
    )).json()
    sup_b = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "Beta Supplies", "tax_id": "27BBBBB2222B1Z5"}, headers=h
    )).json()

    await _pi(client, h, item, sup_a["id"], "INV-100", 10000)   # will MATCH
    await _pi(client, h, item, sup_a["id"], "INV-200", 5000)    # ONLY IN BOOKS (not in 2B)
    await _pi(client, h, item, sup_b["id"], "BILL-300", 20000)  # MISMATCH (2B under-reports)

    gstr2b = {
        "data": {"docdata": {"b2b": [
            {"ctin": "27AAAAA1111A1Z5", "inv": [
                {"inum": "INV-100", "dt": "05-07-2026", "val": 11800,
                 "itms": [{"num": 1, "txval": 10000, "rt": 18, "cgst": 900, "sgst": 900}]},
                {"inum": "INV-999", "dt": "06-07-2026", "val": 8260,   # ONLY IN 2B
                 "itms": [{"num": 1, "txval": 7000, "rt": 18, "cgst": 630, "sgst": 630}]},
            ]},
            {"ctin": "27BBBBB2222B1Z5", "inv": [
                {"inum": "BILL-300", "dt": "07-07-2026", "val": 23000,  # tax 3000 vs books 3600
                 "itms": [{"num": 1, "txval": 20000, "rt": 18, "cgst": 1500, "sgst": 1500}]},
            ]},
        ]}}
    }

    r = await client.post(
        f"{API}/gst-returns/gstr-2b/reconcile",
        json={"from_date": "2026-07-01", "to_date": "2026-07-31", "gstr2b": gstr2b},
        headers=h,
    )
    assert r.status_code == 200, r.text
    rep = r.json()
    s = rep["summary"]
    assert s["books_count"] == 3 and s["portal_count"] == 3
    assert s["matched"] == 1 and s["mismatch"] == 1
    assert s["only_in_books"] == 1 and s["only_in_2b"] == 1
    assert float(s["books_itc"]) == 6300      # 1800 + 900 + 3600
    assert float(s["portal_itc"]) == 6060      # 1800 + 1260 + 3000
    assert float(s["matched_itc"]) == 1800     # INV-100 reconciles
    assert float(s["at_risk_itc"]) == 900      # INV-200 booked but not in 2B

    by = {(row["invoice_no"]): row for row in rep["rows"]}
    assert by["INV-100"]["status"] == "Matched"
    assert by["BILL-300"]["status"] == "Mismatch"
    assert float(by["BILL-300"]["tax_diff"]) == -600   # portal 3000 − books 3600
    assert by["INV-200"]["status"] == "Only in Books"
    assert by["INV-999"]["status"] == "Only in 2B"
    assert float(by["INV-999"]["portal_tax"]) == 1260
