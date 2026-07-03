"""Integration: HSN/SAC lookup — auto-fetch HSN + GST rate from a product name."""

import pytest

pytestmark = pytest.mark.asyncio

API = "/api/v1"

# A small representative slice of the HSN master (the full set is seeded in prod
# via scripts/seed.py; create_all leaves the table empty here).
_ROWS = [
    ("84180000", "Refrigerators, freezers and other refrigerating equipment", 28, "Taxable", 84, "V"),
    ("85171200", "Telephone for cellular networks or for other wireless networks", 18, "Taxable", 85, "IV"),
    ("10060000", "Rice", 0, "Nil-Rated", 10, "I"),
    ("25010010", "Common salt, by whatever name it is known", 0, "Nil-Rated", 25, "I"),
]


async def _seed_rows():
    from app.core.database import async_session_factory
    from app.models.core import HsnCode

    async with async_session_factory() as db:
        for code, desc, rate, treat, chap, sch in _ROWS:
            db.add(HsnCode(hsn_code=code, description=desc, gst_rate=rate,
                           gst_treatment=treat, chapter=chap, schedule=sch))
        await db.commit()


async def test_full_text_search_by_product_name(ctx):
    client, _company, headers = ctx
    await _seed_rows()

    r = await client.get(f"{API}/hsn-codes", params={"search": "refrigerator"}, headers=headers)
    assert r.status_code == 200, r.text
    hits = r.json()
    assert hits and hits[0]["hsn_code"] == "84180000"
    assert float(hits[0]["gst_rate"]) == 28
    assert hits[0]["gst_treatment"] == "Taxable"


async def test_trade_name_alias_boosts_correct_heading(ctx):
    """A user typing a trade name the official description never uses still lands
    on the right code (alias layer)."""
    client, _company, headers = ctx
    await _seed_rows()

    # "mobile" never appears in "Telephone for cellular networks" — alias bridges it.
    hits = (await client.get(f"{API}/hsn-codes", params={"search": "mobile phone"}, headers=headers)).json()
    assert hits and hits[0]["hsn_code"] == "85171200"


async def test_search_by_code_prefix(ctx):
    client, _company, headers = ctx
    await _seed_rows()

    hits = (await client.get(f"{API}/hsn-codes", params={"search": "8517"}, headers=headers)).json()
    assert [h["hsn_code"] for h in hits] == ["85171200"]


async def test_nil_rated_commodity_carries_treatment(ctx):
    client, _company, headers = ctx
    await _seed_rows()

    hits = (await client.get(f"{API}/hsn-codes", params={"search": "rice"}, headers=headers)).json()
    rice = next(h for h in hits if h["hsn_code"] == "10060000")
    assert float(rice["gst_rate"]) == 0
    assert rice["gst_treatment"] == "Nil-Rated"


async def test_no_match_returns_empty_list(ctx):
    client, _company, headers = ctx
    await _seed_rows()

    r = await client.get(f"{API}/hsn-codes", params={"search": "zzznotacommodity"}, headers=headers)
    assert r.status_code == 200
    assert r.json() == []


async def test_query_too_short_is_rejected(ctx):
    client, _company, headers = ctx
    r = await client.get(f"{API}/hsn-codes", params={"search": "a"}, headers=headers)
    assert r.status_code == 422, r.text
