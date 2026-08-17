"""Tally parser + catalogue unit tests — no database.

These cover the three things that break on real customer files: encoding,
Tally's inverted debit/credit sign, and quantities/rates with the unit glued on.
"""

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.services.tally import parser
from app.services.tally.catalogue import (
    ENTITY_BY_KEY,
    classify_group,
    classify_voucher,
    normalise_unit,
)
from app.services.tally.importers.vouchers import (
    _inventory_rows,
    _ledger_rows,
    _split_qty,
    _split_rate,
)
from app.services.tally.mapping import best_match, Candidate, normalise_name, similarity

SAMPLE = """<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY><IMPORTDATA>
  <REQUESTDESC><STATICVARIABLES>
    <SVCURRENTCOMPANY>Acme Traders &amp; Co</SVCURRENTCOMPANY>
    <SVFROMDATE>20250401</SVFROMDATE><SVTODATE>20260331</SVTODATE>
  </STATICVARIABLES></REQUESTDESC>
  <REQUESTDATA>
   <TALLYMESSAGE><GROUP NAME="North Debtors"><GUID>g-1</GUID>
     <PARENT>Sundry Debtors</PARENT></GROUP></TALLYMESSAGE>
   <TALLYMESSAGE><LEDGER NAME="Bharat Steel Pvt Ltd"><GUID>l-1</GUID>
     <PARENT>North Debtors</PARENT><PARTYGSTIN>27AAACB2894G1ZX</PARTYGSTIN>
   </LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><STOCKITEM NAME="Hex Bolt M12"><GUID>si-1</GUID>
     <PARENT>Fasteners</PARENT><BASEUNITS>Nos</BASEUNITS>
     <GSTHSNCODE>73181500</GSTHSNCODE></STOCKITEM></TALLYMESSAGE>
   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Sales"><GUID>v-1</GUID><DATE>20250415</DATE>
     <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>INV-001</VOUCHERNUMBER>
     <PARTYLEDGERNAME>Bharat Steel Pvt Ltd</PARTYLEDGERNAME>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Bharat Steel Pvt Ltd</LEDGERNAME>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-11800.00</AMOUNT>
       <BILLALLOCATIONS.LIST><NAME>INV-001</NAME><BILLTYPE>New Ref</BILLTYPE>
         <AMOUNT>-11800.00</AMOUNT></BILLALLOCATIONS.LIST>
     </ALLLEDGERENTRIES.LIST>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales @ 18%</LEDGERNAME>
       <AMOUNT>10000.00</AMOUNT></ALLLEDGERENTRIES.LIST>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Output IGST</LEDGERNAME>
       <AMOUNT>1800.00</AMOUNT></ALLLEDGERENTRIES.LIST>
     <ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Hex Bolt M12</STOCKITEMNAME>
       <RATE>100.00/Nos</RATE><AMOUNT>10000.00</AMOUNT><BILLEDQTY>100 Nos</BILLEDQTY>
       <BATCHALLOCATIONS.LIST><GODOWNNAME>Main Store</GODOWNNAME>
         <BATCHNAME>Primary Batch</BATCHNAME></BATCHALLOCATIONS.LIST>
     </ALLINVENTORYENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
   <TALLYMESSAGE><VOUCHER VCHTYPE="Payroll"><GUID>v-9</GUID><DATE>20250430</DATE>
     <VOUCHERTYPENAME>Payroll</VOUCHERTYPENAME></VOUCHER></TALLYMESSAGE>
  </REQUESTDATA>
 </IMPORTDATA></BODY>
</ENVELOPE>"""


def _parse():
    parsed, source, _text = parser.parse(SAMPLE.encode("utf-8"), file_name="masters.xml")
    return parsed, source


# --- encoding + envelope --------------------------------------------------------------


def test_utf16_export_is_decoded():
    """Tally often exports UTF-16LE with a BOM; the parser must not choke."""
    parsed, source, _text = parser.parse(SAMPLE.encode("utf-16"), file_name="masters.xml")
    assert source == "XML"
    assert parsed.company_name == "Acme Traders & Co"


def test_bare_ampersand_is_repaired():
    """Tally writes raw '&' in company and ledger names; strict XML would fail."""
    broken = "<ENVELOPE><BODY><TALLYMESSAGE><GROUP NAME='A & B'><GUID>g</GUID></GROUP>" \
             "</TALLYMESSAGE></BODY></ENVELOPE>"
    parsed = parser.parse_xml(broken)
    assert parsed.records["group"][0]["name"] == "A & B"


def test_envelope_header_gives_company_and_period():
    parsed, _ = _parse()
    assert parsed.company_name == "Acme Traders & Co"
    assert parsed.from_date.isoformat() == "2025-04-01"
    assert parsed.to_date.isoformat() == "2026-03-31"


def test_empty_file_is_rejected_with_guidance():
    with pytest.raises(ValidationError):
        parser.parse(b"", file_name="empty.xml")


# --- record bucketing -----------------------------------------------------------------


def test_records_are_bucketed_by_catalogue_entity():
    parsed, _ = _parse()
    assert set(parsed.records) == {"group", "ledger", "stock_item", "voucher_sales", "voucher_payroll"}
    assert parsed.total == 5


def test_unsupported_voucher_is_still_staged_not_dropped():
    """Payroll has no target module — it must be counted, not silently lost."""
    parsed, _ = _parse()
    assert len(parsed.records["voucher_payroll"]) == 1
    assert ENTITY_BY_KEY["voucher_payroll"].support == "none"


def test_voucher_fields_are_lifted_to_the_top_level():
    parsed, _ = _parse()
    voucher = parsed.records["voucher_sales"][0]
    assert voucher["voucher_number"] == "INV-001"
    assert voucher["date"].isoformat() == "2025-04-15"
    assert voucher["party"] == "Bharat Steel Pvt Ltd"


# --- dates, amounts, quantities -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("20250415", "2025-04-15"), ("15-Apr-2025", "2025-04-15"), ("2025-04-15", "2025-04-15")],
)
def test_tally_date_formats(raw, expected):
    assert parser.parse_tally_date(raw).isoformat() == expected


def test_unparseable_date_returns_none_rather_than_guessing():
    assert parser.parse_tally_date("not a date") is None


@pytest.mark.parametrize(
    "raw,expected",
    [("1,234.56", "1234.56"), ("-1234.56", "-1234.56"), ("₹ 1,000", "1000"), ("", "0")],
)
def test_amount_parsing(raw, expected):
    assert parser.parse_amount(raw) == Decimal(expected)


def test_quantity_and_rate_split_off_their_units():
    assert _split_qty("100 Nos") == (Decimal("100"), "Nos")
    assert _split_qty("2.5 Kg") == (Decimal("2.5"), "Kg")
    assert _split_rate("250.00/Nos") == Decimal("250.00")


# --- the sign convention --------------------------------------------------------------


def test_negative_tally_amount_is_a_debit():
    """Tally's ledger entries use negative = debit. Getting this backwards would
    invert every voucher, so it is pinned here."""
    parsed, _ = _parse()
    rows = _ledger_rows(parsed.records["voucher_sales"][0]["data"])
    party = next(r for r in rows if r.is_party)
    assert party.debit == Decimal("11800.00") and party.credit == Decimal("0")
    sales = next(r for r in rows if r.ledger == "Sales @ 18%")
    assert sales.credit == Decimal("10000.00") and sales.debit == Decimal("0")


def test_ledger_rows_balance():
    parsed, _ = _parse()
    rows = _ledger_rows(parsed.records["voucher_sales"][0]["data"])
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows)


def test_bill_allocations_are_captured():
    parsed, _ = _parse()
    rows = _ledger_rows(parsed.records["voucher_sales"][0]["data"])
    party = next(r for r in rows if r.is_party)
    assert party.bills == [
        {"ref": "INV-001", "type": "New Ref", "amount": Decimal("11800.00")}
    ]


def test_inventory_row_carries_godown_and_ignores_primary_batch():
    parsed, _ = _parse()
    row = _inventory_rows(parsed.records["voucher_sales"][0]["data"])[0]
    assert row.item == "Hex Bolt M12"
    assert row.qty == Decimal("100") and row.uom == "Nos"
    assert row.rate == Decimal("100.00")
    assert row.godown == "Main Store"
    assert row.batch is None  # "Primary Batch" is Tally's default, not a real batch


# --- catalogue classification ---------------------------------------------------------


@pytest.mark.parametrize(
    "group,root_type,party_type",
    [
        ("Sundry Debtors", "Asset", "Customer"),
        ("Sundry Creditors", "Liability", "Supplier"),
        ("Bank Accounts", "Asset", None),
        ("Duties & Taxes", "Liability", None),
        ("Sales Accounts", "Income", None),
        ("Indirect Expenses", "Expense", None),
    ],
)
def test_reserved_groups_classify(group, root_type, party_type):
    spec = classify_group(group)
    assert spec.root_type == root_type
    assert spec.party_type == party_type


def test_group_lookup_is_case_insensitive_and_alias_aware():
    assert classify_group("sundry debtors").party_type == "Customer"
    assert classify_group("Retained Earnings").root_type == "Equity"


def test_unknown_group_returns_none_so_ancestry_is_walked():
    assert classify_group("My Custom Group") is None


def test_user_voucher_type_falls_back_to_its_reserved_parent():
    assert classify_voucher("Cash Sales", "Sales").doctype == "Sales Invoice"
    assert classify_voucher("Credit Note").is_return is True
    assert classify_voucher("Receipt").payment_type == "Receive"
    assert classify_voucher("Nonsense") is None


@pytest.mark.parametrize(
    "symbol,expected", [("nos", "Nos"), ("PCS", "Nos"), ("kgs", "Kg"), ("Ltr", "Litre")]
)
def test_unit_aliases(symbol, expected):
    assert normalise_unit(symbol) == expected


def test_unknown_unit_is_kept_verbatim():
    assert normalise_unit("Widget") == "Widget"


# --- name matching --------------------------------------------------------------------


def test_normalise_name_strips_legal_suffixes_and_punctuation():
    assert normalise_name("Bharat Steel Pvt. Ltd.") == "bharat steel"
    assert normalise_name("A.B.C.  Traders") == "a b c traders"


def test_exact_match_beats_fuzzy():
    candidates = [
        Candidate(id=1, name="Bharat Steel", normalised="bharat steel"),
        Candidate(id=2, name="Bharat Steel Pvt Ltd", normalised="bharat steel"),
    ]
    match, method, confidence = best_match("Bharat Steel Pvt Ltd", candidates)
    assert match.id == 2 and method == "exact" and confidence == 95


def test_normalised_match_when_only_the_suffix_differs():
    candidates = [Candidate(id=1, name="Bharat Steel", normalised="bharat steel")]
    match, method, _ = best_match("Bharat Steel Pvt. Ltd.", candidates)
    assert match.id == 1 and method == "normalised"


def test_no_close_candidate_proposes_creating_a_new_record():
    candidates = [Candidate(id=1, name="Zeta Corp", normalised="zeta corp")]
    match, method, _ = best_match("Bharat Steel", candidates)
    assert match is None and method == "created"


def test_similarity_is_symmetric_and_bounded():
    assert similarity("abc", "abc") == 100
    assert 0 <= similarity("abc", "xyz") <= 100


# --- CSV fallback ---------------------------------------------------------------------


def test_csv_daybook_is_parsed_with_a_lossiness_warning():
    csv = (
        "Acme Traders\nDay Book\n"
        "Date,Particulars,Voucher Type,Voucher No,Debit,Credit\n"
        "15-Apr-2025,Bharat Steel,Sales,INV-001,11800,\n"
        "20-Apr-2025,Bharat Steel,Receipt,RCT-001,,11800\n"
    )
    parsed = parser.parse_csv(csv)
    assert parsed.total == 2
    assert set(parsed.records) == {"voucher_sales", "voucher_receipt"}
    assert any("CSV imports carry no GUIDs" in w for w in parsed.warnings)


def test_csv_without_a_recognisable_header_is_rejected():
    with pytest.raises(ValidationError):
        parser.parse_csv("just\nsome\nlines\n")
