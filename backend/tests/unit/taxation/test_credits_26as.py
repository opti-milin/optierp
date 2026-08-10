"""Unit tests for Phase 5 credit matching and claimed totals helpers."""

from __future__ import annotations

from decimal import Decimal

from app.services.taxation.recon_26as import _match_key, _payload_hash, _portal_rows


def test_portal_rows_common_shapes() -> None:
    rows = _portal_rows(
        {
            "tds": [
                {"deductor_tan": "ABCD12345E", "section": "194C", "tds": "1000"},
                {"tan": "ZZZZ99999A", "nature": "194J", "amount": 500},
            ]
        }
    )
    assert len(rows) == 2
    assert rows[0]["deductor_tan"] == "ABCD12345E"
    assert rows[0]["amount"] == Decimal("1000.00")
    assert rows[1]["section"] == "194J"


def test_match_key_normalizes() -> None:
    a = _match_key("abcd12345e", "194c", Decimal("1000.00"))
    b = _match_key("ABCD12345E", "194C", Decimal("1000"))
    assert a == b


def test_payload_hash_stable() -> None:
    p = {"tds": [{"tan": "A", "amount": 1}], "meta": {"x": 1}}
    assert _payload_hash(p) == _payload_hash(dict(reversed(list(p.items()))))
    assert len(_payload_hash(p)) == 64


def test_portal_amount_quantize() -> None:
    assert _portal_rows({"credits": [{"amount": "10.1"}]})[0]["amount"] == Decimal("10.10")
