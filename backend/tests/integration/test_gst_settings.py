"""Integration: per-company GST Settings (India compliance Phase 0)."""

import pytest

pytestmark = pytest.mark.asyncio

API = "/api/v1"


async def test_gst_settings_defaults_save_and_reload(ctx):
    client, company, headers = ctx

    # defaults for a company with no GSTIN
    g = (await client.get(f"{API}/gst-settings", headers=headers)).json()
    assert g["registration_type"] == "Regular"
    assert g["filing_cadence"] == "Monthly"
    assert g["e_invoice_applicable"] is False
    assert g["e_way_bill_applicable"] is False
    assert g["is_sez"] is False
    assert g["gstin"] is None  # Acme India was created without a GSTIN
    assert g["gst_state"] is None

    # save policy
    r = await client.put(
        f"{API}/gst-settings",
        json={
            "registration_type": "Composition",
            "filing_cadence": "QRMP",
            "e_invoice_applicable": True,
            "e_way_bill_applicable": True,
            "is_sez": True,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["registration_type"] == "Composition"
    assert saved["filing_cadence"] == "QRMP"
    assert saved["e_invoice_applicable"] is True

    # reload persists
    g2 = (await client.get(f"{API}/gst-settings", headers=headers)).json()
    assert g2["registration_type"] == "Composition"
    assert g2["filing_cadence"] == "QRMP"
    assert g2["e_way_bill_applicable"] is True
    assert g2["is_sez"] is True


async def test_gst_settings_rejects_bad_values(ctx):
    client, company, headers = ctx
    r = await client.put(
        f"{API}/gst-settings",
        json={"registration_type": "Bogus", "filing_cadence": "Monthly"},
        headers=headers,
    )
    assert r.status_code == 422, r.text


async def test_gstin_and_state_derived_from_company(ctx):
    """GSTIN + place-of-supply state come from the Company (single source of truth)."""
    client, _company, headers = ctx
    # a second company that HAS a GSTIN (Maharashtra, state code 27)
    resp = await client.post(
        f"{API}/companies",
        json={"company_name": "GST Co", "abbr": "GSTCO", "default_currency": "INR",
              "country_code": "IN", "tax_id": "27ABCDE1234F1Z5"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    gst_co = resp.json()
    resp = await client.post(
        f"{API}/auth/switch-company", json={"company_id": gst_co["id"]}, headers=headers
    )
    gst_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    g = (await client.get(f"{API}/gst-settings", headers=gst_headers)).json()
    assert g["gstin"] == "27ABCDE1234F1Z5"
    assert g["gst_state"] == "27-Maharashtra"


async def test_gst_settings_isolated_between_tenants(ctx):
    """One company's GST policy must never bleed into another's (no global fallback)."""
    client, _company, headers = ctx
    # company A (the ctx company) sets a non-default policy
    await client.put(
        f"{API}/gst-settings",
        json={"registration_type": "Composition", "filing_cadence": "QRMP",
              "e_invoice_applicable": True, "e_way_bill_applicable": True, "is_sez": True},
        headers=headers,
    )

    # a brand-new company B must still see defaults, not company A's settings
    resp = await client.post(
        f"{API}/companies",
        json={"company_name": "Other Co", "abbr": "OTHCO", "default_currency": "INR", "country_code": "IN"},
        headers=headers,
    )
    resp = await client.post(
        f"{API}/auth/switch-company", json={"company_id": resp.json()["id"]}, headers=headers
    )
    b_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    b = (await client.get(f"{API}/gst-settings", headers=b_headers)).json()
    assert b["registration_type"] == "Regular"
    assert b["filing_cadence"] == "Monthly"
    assert b["e_invoice_applicable"] is False
    assert b["is_sez"] is False
