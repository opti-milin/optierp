"""Integration: free-text line items on orders (Quotation / Sales Order / Purchase Order).

A hand-typed line (no item_id, a free-text item_name) saves and submits. Orders have no
per-line HSN field, so a free-text order line carries no HSN-derived GST (the invoice is
where GST/HSN matters); GST on an order still comes from the party's tax template.
"""

import pytest

pytestmark = pytest.mark.asyncio

API = "/api/v1"


async def test_sales_order_free_text_line(ctx):
    client, _company, headers = ctx
    cust = (await client.post(f"{API}/customers", json={"customer_name": "Walk-in SO"}, headers=headers)).json()
    r = await client.post(
        f"{API}/sales-orders",
        json={"customer_id": cust["id"], "posting_date": "2026-07-02", "delivery_date": "2026-07-10",
              "items": [{"item_name": "Custom fabrication job", "qty": 1, "rate": "10000"}]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    so = r.json()
    assert so["items"][0]["item_id"] is None
    assert so["items"][0]["item_name"] == "Custom fabrication job"
    assert float(so["net_total"]) == 10000
    # submits cleanly — no stock reservation for a free-text (non-stock) line
    s = await client.post(f"{API}/sales-orders/{so['id']}/submit", headers=headers)
    assert s.status_code == 200, s.text


async def test_purchase_order_free_text_line(ctx):
    client, _company, headers = ctx
    sup = (await client.post(f"{API}/suppliers", json={"supplier_name": "Ad-hoc Vendor"}, headers=headers)).json()
    r = await client.post(
        f"{API}/purchase-orders",
        json={"supplier_id": sup["id"], "posting_date": "2026-07-02", "schedule_date": "2026-07-10",
              "items": [{"item_name": "One-off tooling", "qty": 2, "rate": "5000"}]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["items"][0]["item_id"] is None
    assert po["items"][0]["item_name"] == "One-off tooling"
    assert float(po["net_total"]) == 10000
    s = await client.post(f"{API}/purchase-orders/{po['id']}/submit", headers=headers)
    assert s.status_code == 200, s.text


async def test_order_line_requires_an_item(ctx):
    """A line with neither item_id nor item_name is rejected (422)."""
    client, _company, headers = ctx
    cust = (await client.post(f"{API}/customers", json={"customer_name": "NoItem"}, headers=headers)).json()
    r = await client.post(
        f"{API}/sales-orders",
        json={"customer_id": cust["id"], "posting_date": "2026-07-02", "delivery_date": "2026-07-10",
              "items": [{"qty": 1, "rate": "100"}]},
        headers=headers,
    )
    assert r.status_code == 422, r.text
