"""Integration: TDS returns — Form 26Q + Form 16A — India Compliance Phase 6.2.

A purchase invoice that withholds TDS (2% u/s 194C) surfaces in the 26Q deductee×section
summary and the deductee's Form 16A certificate; the PAN is derived from the GSTIN.
"""

import uuid

import pytest

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"


async def _tds_company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "TDS Co", "abbr": "TDSCO", "default_currency": "INR",
              "country_code": "IN", "tax_id": "27AAEPM1234C1Z5"},
        headers=headers,
    )
    company = r.json()
    r = await client.post(f"{API}/auth/switch-company", json={"company_id": company["id"]}, headers=headers)
    gh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    duties = await coa_account(client, company, gh, "Duties and Taxes")
    acct = (await client.post(
        f"{API}/accounts",
        json={"account_name": "TDS Payable", "parent_account_id": duties["id"], "account_type": "Tax"},
        headers=gh,
    )).json()
    return company, gh, acct["id"]


async def _twc(company_id: str, account_id: str, name="TDS 194C - Contractor (Company) 2%", rate=2) -> str:
    from app.core.database import async_session_factory
    from app.models.accounts import TaxWithholdingCategory

    async with async_session_factory() as db:
        twc = TaxWithholdingCategory(
            company_id=uuid.UUID(company_id), category_name=name, kind="TDS",
            rate=rate, account_id=uuid.UUID(account_id),
        )
        db.add(twc)
        await db.flush()
        tid = str(twc.id)
        await db.commit()
    return tid


async def test_26q_and_16a(ctx):
    client, _c, base_headers = ctx
    company, h, tds_acct = await _tds_company(client, base_headers)
    twc_id = await _twc(company["id"], tds_acct)

    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=h)).json()
    # a service item with NO HSN → no GST, so the TDS maths stays clean
    item = (await client.post(
        f"{API}/items",
        json={"item_code": "CONTRACT-WORK", "standard_rate": "0", "valuation_rate": "0",
              "is_stock_item": False, "default_warehouse_id": wh["id"]},
        headers=h,
    )).json()
    sup = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "Contractor Ltd", "tax_id": "27ABCDE1234F1Z5"}, headers=h
    )).json()

    # ₹1,00,000 contract work, TDS 194C @ 2% = ₹2,000 withheld
    r = await client.post(
        f"{API}/purchase-invoices",
        json={"supplier_id": sup["id"], "posting_date": "2026-07-05", "bill_no": "C-1",
              "tax_withholding_category_id": twc_id,
              "items": [{"item_id": item["id"], "item_name": "Contract work", "qty": 1, "rate": "100000"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert float(inv["tax_withholding_amount"]) == 2000
    assert (await client.post(f"{API}/purchase-invoices/{inv['id']}/submit", headers=h)).status_code == 200

    # --- Form 26Q ---
    q = (await client.get(
        f"{API}/tds-returns/26q", params={"from_date": "2026-07-01", "to_date": "2026-09-30"}, headers=h
    )).json()
    assert q["deductor_gstin"] == "27AAEPM1234C1Z5"
    s = q["summary"]
    assert s["deductee_count"] == 1 and s["document_count"] == 1
    assert float(s["total_base"]) == 100000 and float(s["total_tds"]) == 2000
    assert len(q["rows"]) == 1
    row = q["rows"][0]
    assert row["section"] == "194C"
    assert row["pan"] == "ABCDE1234F"       # derived from GSTIN 27[ABCDE1234F]1Z5
    assert row["deductee_name"] == "Contractor Ltd"
    assert float(row["rate"]) == 2
    assert float(row["total_tds"]) == 2000
    assert row["doc_count"] == 1 and row["documents"][0]["voucher"] == inv["name"]

    # --- Form 16A (certificate for the deductee) ---
    cert = (await client.get(
        f"{API}/tds-returns/16a",
        params={"supplier_id": sup["id"], "from_date": "2026-07-01", "to_date": "2026-09-30"},
        headers=h,
    )).json()
    assert cert["deductee_pan"] == "ABCDE1234F"
    assert cert["deductee_name"] == "Contractor Ltd"
    assert float(cert["total_tds"]) == 2000
    assert len(cert["sections"]) == 1 and cert["sections"][0]["section"] == "194C"
