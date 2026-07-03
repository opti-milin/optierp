"""Integration: GST on advances received — GSTR-1 Table 11 + GSTR-3B 3.1(a) netting.

A Regular dealer booking a SERVICE advance self-assesses inclusive output GST at receipt
(Dr 'GST on Advances' control / Cr Output GST), reports it in GSTR-1 Table 11A and folds it
into GSTR-3B 3.1(a). When the advance is later adjusted to an invoice, the tax reverses
(Table 11B) so the invoice's own output GST is never counted twice — the control account nets
to zero. Goods advances book nothing (Notf. 66/2017).
"""

import uuid

import pytest
from sqlalchemy import func, select

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"
HSN = "84182100"  # 18%


async def _seed_hsn() -> None:
    from app.core.database import async_session_factory
    from app.models.core import HsnCode

    async with async_session_factory() as db:
        db.add(HsnCode(hsn_code=HSN, description="Service", gst_rate=18, gst_treatment="Taxable"))
        await db.commit()


async def _company(client, headers):
    r = await client.post(
        f"{API}/companies",
        json={"company_name": "Adv Co", "abbr": "ADVCO", "default_currency": "INR",
              "country_code": "IN", "tax_id": "27AAEPM1234C1Z5"},
        headers=headers,
    )
    company = r.json()
    r = await client.post(f"{API}/auth/switch-company", json={"company_id": company["id"]}, headers=headers)
    gh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    duties = await coa_account(client, company, gh, "Duties and Taxes")
    accounts = {}
    for name in ("Output CGST", "Output SGST", "Output IGST", "Input GST", "GST on Advances"):
        rr = await client.post(
            f"{API}/accounts",
            json={"account_name": name, "parent_account_id": duties["id"], "account_type": "Tax"},
            headers=gh,
        )
        accounts[name] = rr.json()["id"]
    banks = await coa_account(client, company, gh, "Bank Accounts")
    bank = await client.post(
        f"{API}/accounts",
        json={"account_name": "HDFC", "parent_account_id": banks["id"], "account_type": "Bank"},
        headers=gh,
    )
    accounts["Bank"] = bank.json()["id"]
    return company, gh, accounts


async def _item(client, headers):
    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=headers)).json()
    return (await client.post(
        f"{API}/items",
        json={"item_code": "SVC", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=headers,
    )).json()


async def _customer(client, headers, gstin="27ABCDE1234F1Z5"):
    return (await client.post(f"{API}/customers", json={"customer_name": "Client", "tax_id": gstin}, headers=headers)).json()


async def _advance(client, headers, accounts, cust_id, amount, rate, supply_type, posting="2026-07-05"):
    body = {"posting_date": posting, "payment_type": "Receive", "party_type": "Customer",
            "party_id": cust_id, "paid_to_id": accounts["Bank"], "paid_amount": amount,
            "advance_supply_type": supply_type}
    if rate is not None:
        body["advance_gst_rate"] = rate
    r = await client.post(f"{API}/payment-entries", json=body, headers=headers)
    assert r.status_code == 201, r.text
    pe = r.json()
    s = await client.post(f"{API}/payment-entries/{pe['id']}/submit", headers=headers)
    assert s.status_code == 200, s.text
    return s.json()


async def _returns(client, headers, path):
    return (await client.get(
        f"{API}/gst-returns/{path}", params={"from_date": "2026-07-01", "to_date": "2026-07-31"}, headers=headers
    )).json()


async def _acct_balance(company_id, account_id) -> float:
    """Net debit-minus-credit posted to an account (should be 0 once an advance is fully adjusted)."""
    from app.core.database import async_session_factory
    from app.models.accounts import GLEntry

    async with async_session_factory() as db:
        bal = (await db.execute(
            select(func.coalesce(func.sum(GLEntry.debit - GLEntry.credit), 0))
            .where(GLEntry.company_id == uuid.UUID(company_id), GLEntry.account_id == uuid.UUID(account_id))
        )).scalar()
    return float(bal)


async def test_service_advance_books_inclusive_gst_and_table_11a(ctx):
    client, _c, base_headers = ctx
    co, h, accounts = await _company(client, base_headers)
    await _seed_hsn()
    cust = await _customer(client, h)

    # ₹11,800 service advance @18% inclusive → tax 1800 (CGST 900 + SGST 900)
    pe = await _advance(client, h, accounts, cust["id"], 11800, 18, "Services")
    assert float(pe["advance_gst_amount"]) == 1800
    assert float(pe["advance_gst_outstanding"]) == 1800

    # GL balanced, and the control account holds the 1800 (Dr) until the invoice clears it
    assert await _acct_balance(co["id"], accounts["GST on Advances"]) == 1800

    # GSTR-1 Table 11A: advance received, net taxable 10000, CGST 900 + SGST 900
    g1 = await _returns(client, h, "gstr-1")
    assert len(g1["advances"]) == 1
    a = g1["advances"][0]
    assert a["supply_type"] == "INTRA"
    assert float(a["rate"]) == 18
    assert float(a["gross_advance"]) == 10000
    assert float(a["cgst"]) == 900 and float(a["sgst"]) == 900
    assert g1["advances_adjusted"] == []

    # GSTR-3B 3.1(a) picks up the advance tax (no invoices yet)
    g3 = await _returns(client, h, "gstr-3b")
    a31 = next(r for r in g3["outward"] if r["label"].startswith("(a)"))
    assert float(a31["taxable_value"]) == 10000
    assert float(a31["cgst"]) == 900 and float(a31["sgst"]) == 900


async def test_goods_advance_books_no_gst(ctx):
    client, _c, base_headers = ctx
    _co, h, accounts = await _company(client, base_headers)
    await _seed_hsn()
    cust = await _customer(client, h)

    pe = await _advance(client, h, accounts, cust["id"], 11800, 18, "Goods")
    assert float(pe["advance_gst_amount"]) == 0
    g1 = await _returns(client, h, "gstr-1")
    assert g1["advances"] == []


async def test_advance_adjustment_reverses_and_nets_3b(ctx):
    client, _c, base_headers = ctx
    co, h, accounts = await _company(client, base_headers)
    await _seed_hsn()
    item = await _item(client, h)
    cust = await _customer(client, h)

    # 1) receive the ₹11,800 service advance (tax 1800 booked)
    pe = await _advance(client, h, accounts, cust["id"], 11800, 18, "Services")

    # 2) raise the service invoice: net 10000 + GST 1800 = 11800 (its own output GST)
    inv = (await client.post(
        f"{API}/sales-invoices",
        json={"customer_id": cust["id"], "posting_date": "2026-07-10",
              "items": [{"item_id": item["id"], "item_name": "SVC", "qty": 1, "rate": "10000"}]},
        headers=h,
    )).json()
    assert (await client.post(f"{API}/sales-invoices/{inv['id']}/submit", headers=h)).status_code == 200
    assert float(inv["grand_total"]) == 11800

    # 3) adjust the advance to the invoice
    r = await client.post(
        f"{API}/payment-reconciliation/reconcile",
        json={"party_type": "Customer", "party_id": cust["id"],
              "allocations": [{"payment_entry_id": pe["id"], "invoice_type": "Sales Invoice",
                               "invoice_id": inv["id"], "allocated_amount": 11800}]},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # control account nets to ZERO (advance tax booked at receipt, reversed on adjustment)
    assert await _acct_balance(co["id"], accounts["GST on Advances"]) == 0

    # GSTR-1: Table 11A (received 1800) AND Table 11B (adjusted 1800) both present
    g1 = await _returns(client, h, "gstr-1")
    assert len(g1["advances"]) == 1 and len(g1["advances_adjusted"]) == 1
    assert float(g1["advances"][0]["cgst"]) == 900
    assert float(g1["advances_adjusted"][0]["cgst"]) == 900

    # GSTR-3B 3.1(a) = invoice tax + (11A - 11B) = 1800 + 0 → the invoice's tax ONLY (no double count)
    g3 = await _returns(client, h, "gstr-3b")
    a31 = next(r for r in g3["outward"] if r["label"].startswith("(a)"))
    assert float(a31["taxable_value"]) == 10000
    assert float(a31["cgst"]) == 900 and float(a31["sgst"]) == 900
