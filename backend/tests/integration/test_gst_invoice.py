"""Integration: GST invoice completeness (India compliance Phase 1).

Covers HSN/SAC on items, HSN snapshot onto invoice lines, place-of-supply
defaulting (intra/inter-state), and reverse-charge flagging.
"""

import pytest

pytestmark = pytest.mark.asyncio

API = "/api/v1"


async def _warehouse(client, headers, name="Main Store"):
    r = await client.post(f"{API}/warehouses", json={"warehouse_name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _item(client, headers, code, warehouse_id, **extra):
    payload = {
        "item_code": code,
        "standard_rate": "500",
        "valuation_rate": "300",
        "default_warehouse_id": warehouse_id,
        **extra,
    }
    r = await client.post(f"{API}/items", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _customer(client, headers, name, **extra):
    r = await client.post(f"{API}/customers", json={"customer_name": name, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _gst_company(client, headers, name, abbr, tax_id):
    """Create + switch into a GST-registered company; returns its headers."""
    r = await client.post(
        f"{API}/companies",
        json={"company_name": name, "abbr": abbr, "default_currency": "INR",
              "country_code": "IN", "tax_id": tax_id},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    company = r.json()
    r = await client.post(
        f"{API}/auth/switch-company", json={"company_id": company["id"]}, headers=headers
    )
    return company, {"Authorization": f"Bearer {r.json()['access_token']}"}


# ----------------------------------------------------------------------------
# Item-level GST master data
# ----------------------------------------------------------------------------

async def test_item_carries_hsn_and_gst_treatment(ctx):
    client, _company, headers = ctx
    wh = await _warehouse(client, headers)

    it = await _item(client, headers, "FRIDGE-1", wh["id"],
                     hsn_sac_code="84182100", gst_treatment="Taxable")
    assert it["hsn_sac_code"] == "84182100"
    assert it["gst_treatment"] == "Taxable"

    # round-trips on read
    got = (await client.get(f"{API}/items/{it['id']}", headers=headers)).json()
    assert got["hsn_sac_code"] == "84182100"
    assert got["gst_treatment"] == "Taxable"


async def test_item_gst_treatment_defaults_to_taxable(ctx):
    client, _company, headers = ctx
    wh = await _warehouse(client, headers)
    it = await _item(client, headers, "PLAIN-1", wh["id"])
    assert it["gst_treatment"] == "Taxable"
    assert it["hsn_sac_code"] is None


async def test_item_rejects_bad_gst_treatment(ctx):
    client, _company, headers = ctx
    wh = await _warehouse(client, headers)
    r = await client.post(
        f"{API}/items",
        json={"item_code": "BAD-1", "standard_rate": "1", "valuation_rate": "1",
              "default_warehouse_id": wh["id"], "gst_treatment": "Bogus"},
        headers=headers,
    )
    assert r.status_code == 422, r.text


# ----------------------------------------------------------------------------
# Sales invoice — HSN snapshot + place of supply + reverse charge
# ----------------------------------------------------------------------------

async def test_sales_invoice_snapshots_hsn_from_item_master(ctx):
    client, _company, headers = ctx
    wh = await _warehouse(client, headers)
    it = await _item(client, headers, "FRIDGE-2", wh["id"], hsn_sac_code="84182100")
    cust = await _customer(client, headers, "Acme Buyer")

    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "items": [{"item_id": it["id"], "item_name": "Fridge", "qty": 1, "rate": "1000"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["items"][0]["hsn_sac_code"] == "84182100"


async def test_sales_invoice_line_hsn_override_wins(ctx):
    client, _company, headers = ctx
    wh = await _warehouse(client, headers)
    it = await _item(client, headers, "FRIDGE-3", wh["id"], hsn_sac_code="84182100")
    cust = await _customer(client, headers, "Override Buyer")

    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "items": [{"item_id": it["id"], "item_name": "Fridge", "qty": 1, "rate": "1000",
                       "hsn_sac_code": "99999999"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["items"][0]["hsn_sac_code"] == "99999999"


async def test_sales_invoice_place_of_supply_from_customer_gstin(ctx):
    """POS on a sale = the customer's state (Karnataka, code 29)."""
    client, _company, headers = ctx
    cust = await _customer(client, headers, "Karnataka Buyer", tax_id="29ABCDE1234F1Z5")

    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "items": [{"item_name": "Widget", "qty": 1, "rate": "1000"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["place_of_supply"] == "29-Karnataka"


async def test_sales_invoice_place_of_supply_explicit_override(ctx):
    client, _company, headers = ctx
    cust = await _customer(client, headers, "POS Buyer", tax_id="29ABCDE1234F1Z5")

    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "place_of_supply": "07-Delhi",
            "items": [{"item_name": "Widget", "qty": 1, "rate": "1000"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["place_of_supply"] == "07-Delhi"


async def test_sales_invoice_rejects_overlong_hsn_override(ctx):
    """A >8-char HSN override is a clean 422, not a DB truncation 500."""
    client, _company, headers = ctx
    cust = await _customer(client, headers, "Long HSN Buyer")
    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "items": [{"item_name": "Widget", "qty": 1, "rate": "1000",
                       "hsn_sac_code": "123456789"}],
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text


async def test_sales_invoice_rejects_overlong_place_of_supply(ctx):
    """A >64-char POS override is a clean 422, not a DB truncation 500."""
    client, _company, headers = ctx
    cust = await _customer(client, headers, "Long POS Buyer")
    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "place_of_supply": "X" * 65,
            "items": [{"item_name": "Widget", "qty": 1, "rate": "1000"}],
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text


async def test_sales_invoice_place_of_supply_dnhdd_longest_label(ctx):
    """The longest GST label (code 26, 43 chars) must persist (column width regression guard)."""
    client, _company, headers = ctx
    cust = await _customer(client, headers, "DNHDD Buyer", tax_id="26ABCDE1234F1Z5")
    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "items": [{"item_name": "Widget", "qty": 1, "rate": "1000"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["place_of_supply"] == "26-Dadra and Nagar Haveli and Daman and Diu"


async def test_sales_invoice_reverse_charge_flag(ctx):
    client, _company, headers = ctx
    cust = await _customer(client, headers, "RCM Buyer")

    r = await client.post(
        f"{API}/sales-invoices",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-10",
            "is_reverse_charge": True,
            "items": [{"item_name": "Widget", "qty": 1, "rate": "1000"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["is_reverse_charge"] is True

    # default is False
    r2 = await client.post(
        f"{API}/sales-invoices",
        json={"customer_id": cust["id"], "posting_date": "2026-06-10",
              "items": [{"item_name": "Widget", "qty": 1, "rate": "1000"}]},
        headers=headers,
    )
    assert r2.json()["is_reverse_charge"] is False


# ----------------------------------------------------------------------------
# Purchase invoice — POS defaults to the buyer (our company) state
# ----------------------------------------------------------------------------

async def test_purchase_invoice_place_of_supply_from_company_gstin(ctx):
    client, _company, headers = ctx
    # buyer company registered in Maharashtra (code 27)
    _co, gst_headers = await _gst_company(client, headers, "Buyer Co", "BUYCO", "27ABCDE1234F1Z5")

    sup = (await client.post(
        f"{API}/suppliers", json={"supplier_name": "Vendor Ltd"}, headers=gst_headers
    )).json()

    r = await client.post(
        f"{API}/purchase-invoices",
        json={
            "supplier_id": sup["id"],
            "posting_date": "2026-06-10",
            "bill_no": "V-001",
            "items": [{"item_name": "Raw", "qty": 1, "rate": "1000"}],
        },
        headers=gst_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["place_of_supply"] == "27-Maharashtra"
