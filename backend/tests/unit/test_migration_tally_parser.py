"""Tally parser + catalogue unit tests — no database.

These cover the three things that break on real customer files: encoding,
Tally's inverted debit/credit sign, and quantities/rates with the unit glued on.
"""

import re
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.services.migration.sources import tally_xml as parser
from app.services.migration.catalogue import (
    ENTITY_BY_KEY,
    classify_group,
    classify_voucher,
    normalise_unit,
)
from app.services.migration.importers.vouchers import (
    _inventory_rows,
    _ledger_rows,
    _split_qty,
    _split_rate,
)
from app.services.migration.mapping import best_match, Candidate, normalise_name, similarity

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


# --- company identity, as TallyPrime actually writes it -------------------------------
#
# The fixture above is hand-written and stops at the header. A real TallyPrime
# export *closes* with a <COMPANY> trailer in which NAME is the company GUID and
# the display name is REMOTECMPNAME. Reading that with "NAME means the name"
# rules yields None for both — and assigning that None erased the company name
# the header had already resolved. These tests are shaped like the real file.

TALLYPRIME = """<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY><IMPORTDATA>
  <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME><STATICVARIABLES>
    <SVCURRENTCOMPANY>OptiReach Import Test</SVCURRENTCOMPANY>
  </STATICVARIABLES></REQUESTDESC>
  <REQUESTDATA>
   <TALLYMESSAGE xmlns:UDF="TallyUDF">
    <VOUCHER REMOTEID="6c5c39d3-8d04-455d-85f9-f53464f81e71-00000002"
             VCHKEY="6c5c39d3-8d04-455d-85f9-f53464f81e71-0000b420:00000010"
             VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Invoice Voucher View">
     <GUID>6c5c39d3-8d04-455d-85f9-f53464f81e71-00000002</GUID>
     <DATE>20260401</DATE><VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
     <VOUCHERNUMBER>2</VOUCHERNUMBER><REFERENCE>PUR-002</REFERENCE>
     <PARTYLEDGERNAME>TechSource Distributors</PARTYLEDGERNAME>
     <PLACEOFSUPPLY>Maharashtra</PLACEOFSUPPLY><STATENAME>Gujarat</STATENAME>
     <ALTERID> 2</ALTERID><MASTERID> 2</MASTERID>
     <LEDGERENTRIES.LIST><LEDGERNAME>TechSource Distributors</LEDGERNAME>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>118000.00</AMOUNT>
     </LEDGERENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
   <TALLYMESSAGE xmlns:UDF="TallyUDF">
    <COMPANY>
     <REMOTECMPINFO.LIST MERGE="Yes">
      <NAME>6c5c39d3-8d04-455d-85f9-f53464f81e71</NAME>
      <REMOTECMPNAME>OptiReach Import Test</REMOTECMPNAME>
      <REMOTECMPSTATE>Maharashtra</REMOTECMPSTATE>
     </REMOTECMPINFO.LIST>
    </COMPANY>
   </TALLYMESSAGE>
  </REQUESTDATA>
 </IMPORTDATA></BODY>
</ENVELOPE>"""


def test_company_trailer_does_not_erase_the_company_name():
    """Regression: the <COMPANY> trailer used to overwrite the name with None."""
    parsed = parser.parse_xml(TALLYPRIME)
    assert parsed.company_name == "OptiReach Import Test"


def test_company_trailer_yields_the_company_guid():
    """REMOTECMPINFO.LIST/NAME is the company UUID, not a display name."""
    parsed = parser.parse_xml(TALLYPRIME)
    assert parsed.company_guid == "6c5c39d3-8d04-455d-85f9-f53464f81e71"


def test_company_guid_falls_back_to_the_record_guid_prefix():
    """Day Book exports carry no <COMPANY>; every record GUID still prefixes it."""
    without_trailer = re.sub(
        r"<TALLYMESSAGE[^>]*>\s*<COMPANY>.*?</COMPANY>\s*</TALLYMESSAGE>",
        "",
        TALLYPRIME,
        flags=re.S,
    )
    parsed = parser.parse_xml(without_trailer)
    assert parsed.company_guid == "6c5c39d3-8d04-455d-85f9-f53464f81e71"
    assert parsed.company_name == "OptiReach Import Test"


def test_uninformative_company_block_is_counted_not_swallowed():
    """A <COMPANY> that carries neither name nor GUID must not vanish silently."""
    empty = (
        "<ENVELOPE><BODY><REQUESTDATA><TALLYMESSAGE><COMPANY>"
        "<REMOTECMPINFO.LIST MERGE='Yes'/>"
        "</COMPANY></TALLYMESSAGE></REQUESTDATA></BODY></ENVELOPE>"
    )
    parsed = parser.parse_xml(empty)
    assert parsed.unknown_tags.get("COMPANY") == 1


def test_tally_change_tracking_keys_are_captured():
    """ALTERID/VCHKEY are what make an incremental import possible at all."""
    parsed = parser.parse_xml(TALLYPRIME)
    voucher = parsed.records["voucher_purchase"][0]
    assert voucher["alter_id"] == 2  # Tally pads these (" 2") — must parse as int
    assert voucher["master_id"] == 2
    assert voucher["vch_key"].endswith(":00000010")
    assert voucher["remote_id"].endswith("-00000002")


def test_a_higher_alter_id_is_an_amendment_not_a_duplicate():
    """Same voucher, edited in Tally since import — the copies now disagree."""
    from app.services.migration.context import (
        REPEAT_AMENDED,
        REPEAT_UNCHANGED,
        ImportedIdentity,
        classify_repeat,
    )

    def _record(alter_id):
        return SimpleNamespace(raw={"alter_id": alter_id})

    def _prior(alter_id):
        return ImportedIdentity(
            source_guid="g", entity_key="voucher_sales", target_doctype="Sales Invoice",
            target_id=uuid.uuid4(), target_name="SINV-1", voucher_number="INV-001",
            alter_id=alter_id, import_id=uuid.uuid4(), import_name="TALLY-IMP-0001",
        )

    assert classify_repeat(_record(47), _prior(12)) == REPEAT_AMENDED
    assert classify_repeat(_record(12), _prior(12)) == REPEAT_UNCHANGED
    # An older ALTERID means a stale export, not an edit — do not raise a conflict.
    assert classify_repeat(_record(5), _prior(12)) == REPEAT_UNCHANGED
    # CSV exports carry no ALTERID; absence must not invent a conflict.
    assert classify_repeat(_record(None), _prior(12)) == REPEAT_UNCHANGED
    assert classify_repeat(_record(47), _prior(None)) == REPEAT_UNCHANGED


# --- real TallyPrime field shapes ------------------------------------------------------


def _ledger_data(xml):
    (led,) = parser.parse_xml(xml).records["ledger"]
    return led["data"]


def test_a_party_address_is_read_from_tallyprimes_nested_block():
    """Regression: TallyPrime nests address, pincode and state inside
    LEDMAILINGDETAILS.LIST. Reading only the flat Tally.ERP 9 shape meant every
    party from a real export arrived with no address at all."""
    from app.services.migration.importers.masters import _ledger_address

    xml = """<ENVELOPE><BODY><REQUESTDATA><TALLYMESSAGE>
     <LEDGER NAME="TechSource Distributors" RESERVEDNAME="">
      <GUID>l-1</GUID><PARENT>Sundry Creditors</PARENT>
      <LEDMAILINGDETAILS.LIST>
        <ADDRESS.LIST TYPE="String"><ADDRESS>45 Industrial Estate</ADDRESS></ADDRESS.LIST>
        <APPLICABLEFROM>20260401</APPLICABLEFROM>
        <PINCODE>450100</PINCODE><STATE>Gujarat</STATE>
      </LEDMAILINGDETAILS.LIST>
     </LEDGER></TALLYMESSAGE></REQUESTDATA></BODY></ENVELOPE>"""

    lines, state, pincode = _ledger_address(_ledger_data(xml))
    assert lines == ["45 Industrial Estate"]
    assert state == "Gujarat"
    assert pincode == "450100"


def test_the_current_mailing_block_wins_over_an_old_one():
    """Tally keeps a history of mailing details; the first is often years stale."""
    from app.services.migration.importers.masters import _ledger_address

    xml = """<ENVELOPE><BODY><REQUESTDATA><TALLYMESSAGE>
     <LEDGER NAME="Movers Ltd" RESERVEDNAME=""><GUID>l-2</GUID>
      <LEDMAILINGDETAILS.LIST>
        <ADDRESS.LIST TYPE="String"><ADDRESS>Old Road 1</ADDRESS></ADDRESS.LIST>
        <APPLICABLEFROM>20200401</APPLICABLEFROM><PINCODE>111111</PINCODE>
      </LEDMAILINGDETAILS.LIST>
      <LEDMAILINGDETAILS.LIST>
        <ADDRESS.LIST TYPE="String"><ADDRESS>New Road 9</ADDRESS></ADDRESS.LIST>
        <APPLICABLEFROM>20260401</APPLICABLEFROM><PINCODE>999999</PINCODE>
      </LEDMAILINGDETAILS.LIST>
     </LEDGER></TALLYMESSAGE></REQUESTDATA></BODY></ENVELOPE>"""

    lines, _state, pincode = _ledger_address(_ledger_data(xml))
    assert lines == ["New Road 9"]
    assert pincode == "999999"


def test_the_flat_tally_erp9_address_shape_still_works():
    """The older layout must keep working — plenty of installs still export it."""
    from app.services.migration.importers.masters import _ledger_address

    xml = """<ENVELOPE><BODY><REQUESTDATA><TALLYMESSAGE>
     <LEDGER NAME="Bharat Steel" RESERVEDNAME=""><GUID>l-3</GUID>
      <ADDRESS.LIST><ADDRESS>Plot 14 MIDC</ADDRESS><ADDRESS>Pune</ADDRESS></ADDRESS.LIST>
      <LEDSTATENAME>Maharashtra</LEDSTATENAME><PINCODE>411001</PINCODE>
     </LEDGER></TALLYMESSAGE></REQUESTDATA></BODY></ENVELOPE>"""

    lines, state, pincode = _ledger_address(_ledger_data(xml))
    assert lines == ["Plot 14 MIDC", "Pune"]
    assert state == "Maharashtra"
    assert pincode == "411001"


def test_an_invoice_due_date_comes_from_tallys_credit_period():
    """An imported invoice with no due date never ages and never shows overdue."""
    from datetime import date

    from app.services.migration.importers.vouchers import LedgerRow, _due_date

    party = LedgerRow(
        ledger="TechSource Distributors",
        debit=Decimal("0"),
        credit=Decimal("118000"),
        is_party=True,
        bills=[{"ref": "PUR-002", "type": "New Ref", "credit_period": "30 Days"}],
    )
    assert _due_date(date(2026, 4, 1), [party]) == date(2026, 5, 1)

    weeks = LedgerRow(
        ledger="X", debit=Decimal("0"), credit=Decimal("1"), is_party=True,
        bills=[{"ref": "A", "type": "New Ref", "credit_period": "2 Weeks"}],
    )
    assert _due_date(date(2026, 4, 1), [weeks]) == date(2026, 4, 15)

    # No credit term is not an error — the invoice simply has no due date.
    bare = LedgerRow(
        ledger="Y", debit=Decimal("0"), credit=Decimal("1"), is_party=True,
        bills=[{"ref": "B", "type": "New Ref", "credit_period": None}],
    )
    assert _due_date(date(2026, 4, 1), [bare]) is None


# --- dry-run honesty -------------------------------------------------------------------


def _voucher_row(xml):
    import types

    (record,) = list(parser.parse_xml(xml).records.values())[0]
    return types.SimpleNamespace(raw=record)


def test_the_dry_run_checks_ledgers_nested_under_an_item_line():
    """Regression: an item line's own income/expense ledger sits one level down
    in ACCOUNTINGALLOCATIONS.LIST. Missing it did not fail the voucher — the
    line silently fell back to the default account, booking the purchase
    somewhere Tally never put it."""
    from app.services.migration.runner import _referenced_names

    xml = """<ENVELOPE><BODY><REQUESTDATA><TALLYMESSAGE>
     <VOUCHER VCHTYPE="Purchase"><GUID>v-1</GUID><DATE>20260401</DATE>
      <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
      <LEDGERENTRIES.LIST><LEDGERNAME>TechSource Distributors</LEDGERNAME>
        <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>118000</AMOUNT></LEDGERENTRIES.LIST>
      <ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Laptop Computer</STOCKITEMNAME>
        <ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>Purchase - GST 18%</LEDGERNAME>
          <AMOUNT>-100000</AMOUNT></ACCOUNTINGALLOCATIONS.LIST>
      </ALLINVENTORYENTRIES.LIST>
     </VOUCHER></TALLYMESSAGE></REQUESTDATA></BODY></ENVELOPE>"""

    found = _referenced_names(_voucher_row(xml))
    assert ("Purchase - GST 18%", "ledger") in found
    assert ("TechSource Distributors", "ledger") in found
    assert ("Laptop Computer", "stock_item") in found


def test_cancelled_and_optional_vouchers_are_not_counted_as_planned():
    """The dry run must promise only what the run will actually create."""
    from app.services.migration.runner import _will_be_skipped
    import types

    assert _will_be_skipped(types.SimpleNamespace(raw={"is_cancelled": True}))
    assert _will_be_skipped(types.SimpleNamespace(raw={"is_optional": True}))
    assert not _will_be_skipped(types.SimpleNamespace(raw={"is_cancelled": False}))
    assert not _will_be_skipped(types.SimpleNamespace(raw={}))


def test_ampersand_and_the_word_and_are_the_same_name():
    """Tally ships "Reserves & Surplus"; the Indian COA calls it "Reserves and
    Surplus". Stripping "&" as punctuation left them unmatched, and a duplicate
    account got created beside the real one."""
    assert normalise_name("Reserves & Surplus") == normalise_name("Reserves and Surplus")
    assert normalise_name("Duties & Taxes") == normalise_name("Duties and Taxes")
    assert normalise_name("A & B Traders") == normalise_name("A and B Traders")
    # ...without collapsing genuinely different names.
    assert normalise_name("A & B Traders") != normalise_name("A B C Traders")


# --- reserved masters ------------------------------------------------------------------


def test_a_reserved_group_is_not_its_own_parent():
    """Regression: RESERVEDNAME holds the master's own canonical name, not its
    parent. Preferring it made "Bank Accounts" sit under "Bank Accounts" and
    collapsed the imported chart into 28 self-parented roots."""
    xml = """<ENVELOPE><BODY><REQUESTDATA>
     <TALLYMESSAGE><GROUP NAME="Reserves &amp; Surplus" RESERVEDNAME="Reserves &amp; Surplus">
       <GUID>g-1</GUID><PARENT>Capital Account</PARENT></GROUP></TALLYMESSAGE>
     <TALLYMESSAGE><GROUP NAME="Bank Accounts" RESERVEDNAME="Bank Accounts">
       <GUID>g-2</GUID><PARENT>Current Assets</PARENT></GROUP></TALLYMESSAGE>
    </REQUESTDATA></BODY></ENVELOPE>"""
    by_name = {g["name"]: g for g in parser.parse_xml(xml).records["group"]}
    assert by_name["Reserves & Surplus"]["parent"] == "Capital Account"
    assert by_name["Bank Accounts"]["parent"] == "Current Assets"
    # Still recorded — it is what identifies a renamed built-in group.
    assert by_name["Bank Accounts"]["reserved_name"] == "Bank Accounts"


def test_a_top_level_reserved_group_has_no_parent():
    """Tally writes <PARENT/> for its roots; that must read as 'no parent'."""
    xml = (
        '<ENVELOPE><BODY><REQUESTDATA><TALLYMESSAGE>'
        '<GROUP NAME="Capital Account" RESERVEDNAME="Capital Account">'
        "<GUID>g-3</GUID><PARENT/></GROUP></TALLYMESSAGE></REQUESTDATA></BODY></ENVELOPE>"
    )
    (group,) = parser.parse_xml(xml).records["group"]
    assert group["parent"] is None


def test_a_user_created_group_keeps_its_parent():
    """RESERVEDNAME is empty for user groups — the path that always worked."""
    xml = (
        '<ENVELOPE><BODY><REQUESTDATA><TALLYMESSAGE>'
        '<GROUP NAME="North Debtors" RESERVEDNAME="">'
        "<GUID>g-4</GUID><PARENT>Sundry Debtors</PARENT></GROUP>"
        "</TALLYMESSAGE></REQUESTDATA></BODY></ENVELOPE>"
    )
    (group,) = parser.parse_xml(xml).records["group"]
    assert group["parent"] == "Sundry Debtors"
    assert group["reserved_name"] is None


# --- tax direction ---------------------------------------------------------------------
#
# Which side a tax ledger sits on in Tally depends on the voucher: output GST on
# a sale is a credit, input GST on a purchase is a debit. Reading "debit means
# deduct" made every purchase *subtract* its GST — a 1,00,000 bill with 18,000
# IGST imported as 82,000, and the supplier's payable understated to match.


def _tax_rows(kind, *, debit, is_return=False):
    """Run _invoice_taxes over one tax ledger posted on the given side."""
    from app.services.migration.importers.vouchers import LedgerRow, _invoice_taxes

    amount = Decimal("18000")
    rows = [
        LedgerRow(
            ledger="IGST 18%",
            debit=amount if debit else Decimal("0"),
            credit=Decimal("0") if debit else amount,
        )
    ]
    account = uuid.uuid4()
    context = SimpleNamespace(
        book=SimpleNamespace(account=lambda _name: account),
        warn=lambda *a, **k: None,
    )
    return _invoice_taxes(context, rows, kind=kind, is_return=is_return)


def test_purchase_gst_is_added_not_deducted():
    """Regression: input GST is a debit in Tally, and it still adds to the bill."""
    (tax,) = _tax_rows("Purchase", debit=True)
    assert tax.add_deduct_tax == "Add"
    assert tax.tax_amount == Decimal("18000")


def test_sales_gst_is_added():
    """Output GST is a credit — the case that happened to work before."""
    (tax,) = _tax_rows("Sales", debit=False)
    assert tax.add_deduct_tax == "Add"


def test_returns_flip_the_side_but_still_add():
    """A note reverses its voucher, so its tax sits on the other side — and the
    document carries the return as a flag, so the amount still adds."""
    (credit_note,) = _tax_rows("Sales", debit=True, is_return=True)
    assert credit_note.add_deduct_tax == "Add"
    (debit_note,) = _tax_rows("Purchase", debit=False, is_return=True)
    assert debit_note.add_deduct_tax == "Add"


def test_a_tax_on_the_wrong_side_is_a_real_deduction():
    """TDS withheld on a purchase bill is a credit — that one genuinely comes off."""
    (tax,) = _tax_rows("Purchase", debit=False)
    assert tax.add_deduct_tax == "Deduct"
    (on_a_sale,) = _tax_rows("Sales", debit=True)
    assert on_a_sale.add_deduct_tax == "Deduct"


def test_utf16le_tallyprime_export_round_trips():
    """The real file is UTF-16LE with a BOM — decode, then keep the identity."""
    raw = b"\xff\xfe" + TALLYPRIME.encode("utf-16-le")
    parsed, source, _text = parser.parse(raw, file_name="Transactions.xml")
    assert source == "XML"
    assert parsed.company_name == "OptiReach Import Test"
    assert parsed.company_guid == "6c5c39d3-8d04-455d-85f9-f53464f81e71"
    assert parsed.records["voucher_purchase"][0]["party"] == "TechSource Distributors"


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
        {
            "ref": "INV-001",
            "type": "New Ref",
            "amount": Decimal("11800.00"),
            # This fixture carries no BILLCREDITPERIOD, so the invoice gets no
            # due date — see test_an_invoice_due_date_comes_from_tallys_credit_period.
            "credit_period": None,
        }
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
