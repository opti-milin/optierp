"""Integration: reverse charge (RCM) on a purchase invoice — India Compliance Phase 3.

A reverse-charge inward supply self-assesses GST: the supplier is paid the base only,
while the buyer books Dr Input GST (ITC) / Cr Output GST (liability) — net zero on the
payable — and the returns pick up 3.1(d) + ITC 4(A)(3).
"""

import uuid

import pytest
from sqlalchemy import func, select

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
        json={"company_name": "RCM Co", "abbr": "RCMCO", "default_currency": "INR",
              "country_code": "IN", "tax_id": "27AAEPM1234C1Z5"},
        headers=headers,
    )
    company = r.json()
    r = await client.post(f"{API}/auth/switch-company", json={"company_id": company["id"]}, headers=headers)
    gh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    duties = await coa_account(client, company, gh, "Duties and Taxes")
    accounts = {}
    for name in ("Output CGST", "Output SGST", "Output IGST", "Input GST"):
        rr = await client.post(
            f"{API}/accounts",
            json={"account_name": name, "parent_account_id": duties["id"], "account_type": "Tax"},
            headers=gh,
        )
        accounts[name] = rr.json()["id"]
    return company, gh, accounts


async def test_rcm_purchase_posts_itc_and_liability(ctx):
    client, _c, base_headers = ctx
    _co, h, accounts = await _gst_company(client, base_headers)
    await _seed_hsn()

    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=h)).json()
    item = (await client.post(
        f"{API}/items",
        json={"item_code": "FRIDGE", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=h,
    )).json()
    # intra-state supplier (GTA / unregistered) — RCM applies
    sup = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "GTA Transport", "tax_id": "27ZZZZZ1234Z1Z5"}, headers=h
    )).json()

    r = await client.post(
        f"{API}/purchase-invoices",
        json={"supplier_id": sup["id"], "posting_date": "2026-07-12", "bill_no": "RCM-1",
              "is_reverse_charge": True,
              "items": [{"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": "10000"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    # payable is the base only (self-assessed GST nets out)
    assert float(inv["net_total"]) == 10000
    assert float(inv["grand_total"]) == 10000
    assert float(inv["total_taxes_and_charges"]) == 0
    # both sides recorded as tax rows
    descs = {t["description"] for t in inv["taxes"]}
    assert "Input GST (RCM)" in descs
    assert {"Output CGST (RCM)", "Output SGST (RCM)"} <= descs

    # submit → GL posts Dr Input GST 1800 / Cr Output CGST 900 + Cr Output SGST 900
    s = await client.post(f"{API}/purchase-invoices/{inv['id']}/submit", headers=h)
    assert s.status_code == 200, s.text

    from app.core.database import async_session_factory
    from app.models.accounts import GLEntry

    async with async_session_factory() as db:
        vid = uuid.UUID(inv["id"])
        total_debit, total_credit = (
            await db.execute(
                select(func.sum(GLEntry.debit), func.sum(GLEntry.credit)).where(GLEntry.voucher_id == vid)
            )
        ).one()
        assert total_debit == total_credit  # balanced

        def _acc(name):
            return uuid.UUID(accounts[name])

        input_dr = (await db.execute(
            select(func.coalesce(func.sum(GLEntry.debit - GLEntry.credit), 0))
            .where(GLEntry.voucher_id == vid, GLEntry.account_id == _acc("Input GST"))
        )).scalar()
        cgst_cr = (await db.execute(
            select(func.coalesce(func.sum(GLEntry.credit - GLEntry.debit), 0))
            .where(GLEntry.voucher_id == vid, GLEntry.account_id == _acc("Output CGST"))
        )).scalar()
        sgst_cr = (await db.execute(
            select(func.coalesce(func.sum(GLEntry.credit - GLEntry.debit), 0))
            .where(GLEntry.voucher_id == vid, GLEntry.account_id == _acc("Output SGST"))
        )).scalar()
        assert Decimal_eq(input_dr, 1800)   # Dr ITC
        assert Decimal_eq(cgst_cr, 900)      # Cr liability
        assert Decimal_eq(sgst_cr, 900)

    # GSTR-3B: 3.1(d) liability 1800 and ITC 4(A)(3) 1800
    g3 = (await client.get(
        f"{API}/gst-returns/gstr-3b", params={"from_date": "2026-07-01", "to_date": "2026-07-31"}, headers=h
    )).json()
    d_row = next(row for row in g3["outward"] if row["label"].startswith("(d)"))
    assert float(d_row["cgst"]) == 900 and float(d_row["sgst"]) == 900
    assert float(d_row["taxable_value"]) == 10000
    rcm_itc = next(row for row in g3["itc"] if row["label"].startswith("(A)(3)"))
    assert float(rcm_itc["cgst"]) == 900 and float(rcm_itc["sgst"]) == 900


async def test_rcm_preview_nets_payable_to_base(ctx):
    """The draft live-preview of a reverse-charge purchase must show the payable at the
    BASE (self-assessed GST nets out) — preview == create — not base + GST."""
    client, _c, base_headers = ctx
    _co, h, _accounts = await _gst_company(client, base_headers)
    await _seed_hsn()
    wh = (await client.post(f"{API}/warehouses", json={"warehouse_name": "Main"}, headers=h)).json()
    item = (await client.post(
        f"{API}/items",
        json={"item_code": "FRIDGE", "standard_rate": "0", "valuation_rate": "0",
              "default_warehouse_id": wh["id"], "hsn_sac_code": HSN},
        headers=h,
    )).json()
    sup = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "GTA Transport", "tax_id": "27ZZZZZ1234Z1Z5"}, headers=h
    )).json()
    line = {"item_id": item["id"], "item_name": "FRIDGE", "qty": 1, "rate": "10000"}

    # reverse charge ON → payable = base 10000, taxes net to 0, Output rows shown negative
    rc = (await client.post(
        f"{API}/purchase-invoices/preview",
        json={"supplier_id": sup["id"], "posting_date": "2026-07-12", "is_reverse_charge": True,
              "items": [line]},
        headers=h,
    )).json()
    assert float(rc["net_total"]) == 10000
    assert float(rc["total_taxes_and_charges"]) == 0
    assert float(rc["grand_total"]) == 10000
    heads = {t["description"]: float(t["tax_amount"]) for t in rc["taxes"]}
    assert heads["Input GST (RCM)"] == 1800
    assert heads["Output CGST (RCM)"] == -900
    assert heads["Output SGST (RCM)"] == -900

    # reverse charge OFF → the same line adds Input GST to the payable (11800)
    fwd = (await client.post(
        f"{API}/purchase-invoices/preview",
        json={"supplier_id": sup["id"], "posting_date": "2026-07-12", "items": [line]},
        headers=h,
    )).json()
    assert float(fwd["grand_total"]) == 11800
    assert [t["description"] for t in fwd["taxes"]] == ["Input GST"]


def Decimal_eq(a, b) -> bool:
    from decimal import Decimal

    return Decimal(a) == Decimal(b)
