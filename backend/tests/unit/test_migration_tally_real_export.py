"""Parser tests driven by real TallyPrime export shapes.

Every other parser test in this suite uses hand-written XML, and every defect
found while testing Module 12 against a customer's genuine export had passed
those first — the fixtures were simply too tidy to contain the bug.

So these run against ``tests/fixtures/migration/``, whose shapes come from real
exports, and they assert the specific things that were silently wrong:

* the company name survives the ``<COMPANY>`` trailer (it used to be erased);
* reserved groups keep their real parent (they used to parent themselves);
* opening balances are lifted out (they used to be ignored entirely);
* a party's address is found in ``LEDMAILINGDETAILS.LIST`` (it used to be lost);
* an item line's own ledger is seen by the dry run (it used to be missed);
* a cancelled voucher is recognised as cancelled.

The fixtures are stored UTF-8 so they stay diffable, and encoded to UTF-16LE
here — which is what Tally actually writes, and a decoding path worth covering.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.migration.sources import tally_xml as parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "migration"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def masters():
    return parser.parse_xml(_load("masters.xml"))


@pytest.fixture(scope="module")
def transactions():
    return parser.parse_xml(_load("transactions.xml"))


# --- the file itself -------------------------------------------------------------------


def test_the_fixtures_are_present_and_look_like_tally():
    """A guard on the fixtures themselves: if someone 'tidies' them into
    hand-written XML, these tests quietly stop testing anything."""
    masters_xml = _load("masters.xml")
    assert 'RESERVEDNAME="' in masters_xml
    assert "LEDMAILINGDETAILS.LIST" in masters_xml
    assert "<OPENINGBALANCE>" in masters_xml
    assert "REMOTECMPINFO.LIST" in masters_xml
    assert "ACCOUNTINGALLOCATIONS.LIST" in _load("transactions.xml")


def test_a_utf16_export_decodes_the_way_tally_writes_it():
    """Tally writes UTF-16LE with a BOM; the fixture is UTF-8 for readability."""
    raw = b"\xff\xfe" + _load("masters.xml").encode("utf-16-le")
    parsed, source, _text = parser.parse(raw, file_name="masters.xml")
    assert source == "XML"
    assert parsed.company_name == "OptiReach Import Test"


# --- company identity ------------------------------------------------------------------


def test_the_company_trailer_does_not_erase_the_company(masters):
    """The <COMPANY> block used to overwrite the name resolved from the header."""
    assert masters.company_name == "OptiReach Import Test"
    assert masters.company_guid == "6c5c39d3-8d04-455d-85f9-f53464f81e71"


# --- chart of accounts -----------------------------------------------------------------


def test_reserved_groups_keep_their_real_parent(masters):
    """RESERVEDNAME is the group's own canonical name, never its parent."""
    by_name = {g["name"]: g for g in masters.records["group"]}
    assert by_name["Reserves & Surplus"]["parent"] == "Capital Account"
    assert by_name["Sundry Creditors"]["parent"] == "Current Liabilities"
    # A top-level reserved group has no parent at all.
    assert by_name["Capital Account"]["parent"] is None
    # Nothing may be its own parent — the failure mode that flattened the tree.
    assert not [g for g in masters.records["group"] if g["parent"] == g["name"]]


# --- opening balances ------------------------------------------------------------------


def test_opening_balances_are_lifted_out_of_the_masters(masters):
    """These were declared in the catalogue and documented, but never emitted."""
    openings = {o["name"]: o for o in masters.records.get("opening_ledger", [])}
    assert openings, "no opening_ledger records were produced at all"
    assert openings["TechSource Distributors"]["opening_balance"].strip() == "32000.00"
    # Negative is a debit in Tally — a debtor's opening, not a creditor's.
    assert openings["ABC Retail Pvt Ltd"]["opening_balance"].strip() == "-50000.00"


def test_a_zero_opening_balance_is_not_emitted(masters):
    """Tally writes 0.00 on most masters; staging those is noise, not data."""
    openings = {o["name"] for o in masters.records.get("opening_ledger", [])}
    ledgers = {led["name"] for led in masters.records["ledger"]}
    assert openings < ledgers, "every ledger produced an opening — zeros leaked through"


def test_opening_stock_carries_its_quantity_and_value(masters):
    (opening,) = masters.records["opening_stock"]
    assert opening["name"] == "Laptop Computer"
    assert opening["opening_balance"].strip() == "4 Nos"
    assert Decimal(opening["opening_value"].strip()) > 0


# --- party details ---------------------------------------------------------------------


def test_a_party_address_is_found_in_the_nested_mailing_block(masters):
    """TallyPrime nests address/pincode/state inside LEDMAILINGDETAILS.LIST."""
    from app.services.migration.importers.masters import _ledger_address

    ledger = next(
        led for led in masters.records["ledger"]
        if led["name"] == "TechSource Distributors"
    )
    lines, state, pincode = _ledger_address(ledger["data"])
    assert lines, "the party imported with no address at all"
    assert state == "Gujarat"
    assert pincode == "450100"


# --- vouchers --------------------------------------------------------------------------


def test_a_cancelled_voucher_is_recognised(transactions):
    cancelled = [
        v for v in transactions.records["voucher_purchase"] if v["is_cancelled"]
    ]
    assert cancelled, "the cancelled voucher was not flagged"


def test_the_dry_run_sees_the_ledger_nested_under_an_item_line(transactions):
    """An item line's own expense ledger sits inside ACCOUNTINGALLOCATIONS.LIST.
    Missing it did not fail the voucher — it silently booked to the default
    account instead of the one Tally named."""
    import types

    from app.services.migration.runner import _referenced_names

    voucher = next(
        v for v in transactions.records["voucher_purchase"] if not v["is_cancelled"]
    )
    names = _referenced_names(types.SimpleNamespace(raw=voucher))
    ledgers = {name for name, kind in names if kind == "ledger"}
    assert "Purchase - GST 18%" in ledgers
    assert "IGST 18%" in ledgers
    assert ("Laptop Computer", "stock_item") in names


def test_tallys_change_tracking_keys_are_captured(transactions):
    """ALTERID/VCHKEY are what make incremental sync and amend-detection possible."""
    voucher = next(
        v for v in transactions.records["voucher_purchase"] if not v["is_cancelled"]
    )
    assert voucher["alter_id"] is not None
    assert voucher["vch_key"]
