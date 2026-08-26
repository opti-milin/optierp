"""Folding a workbook into the pipeline's IR.

The assertions here are accounting ones, because that is where a spreadsheet
importer goes wrong quietly: a debit written as a credit, a document rebuilt a
rupee light, an opening balance booked twice because two sheets carried it.

The real 500-transaction sample workbooks live in ``docs/migration/``. The tests
at the bottom read them if they are present — that is the only way to know the
built-in profiles still fit a genuine export rather than a fixture written to
agree with them.
"""

import io
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.migration.sources import builders as b
from app.services.migration.sources import detect, normalise, templates, workbook
from app.services.migration.sources.builtin import (
    BUILTIN_BY_KEY,
    CUSTOM,
    OPTIERP_TEMPLATE,
    TALLY_FLAT,
    TALLY_WORKBOOK,
    ZOHO_BOOKS,
)
from app.services.migration.sources.ir import as_list, parse_amount
from app.services.migration.sources.profiles import KINDS, SourceProfile
from tests.unit.test_migration_workbook import build

SAMPLES = Path(__file__).resolve().parents[3] / "docs" / "migration"
ZERO = Decimal("0")


def parse(raw: bytes, profile: SourceProfile):
    return normalise.normalise(workbook.read(raw), profile)


def ledger_rows(record) -> list[tuple[str, Decimal]]:
    """``[(ledger, signed amount)]`` — negative is a debit, as Tally writes it."""
    return [
        (node["LEDGERNAME"], parse_amount(node["AMOUNT"]))
        for node in as_list(record["data"].get("ALLLEDGERENTRIES.LIST"))
    ]


def balance(record) -> Decimal:
    return sum((amount for _, amount in ledger_rows(record)), ZERO)


# --- the profile model holds together -------------------------------------------------


def test_every_builtin_profile_round_trips_through_its_serialised_form():
    # A saved profile is stored as JSON and rebuilt on load. If that lost
    # anything, a company's own mapping would drift from the built-in it copied.
    for profile in BUILTIN_BY_KEY.values():
        rebuilt = SourceProfile.from_dict(profile.to_dict())
        assert rebuilt.to_dict() == profile.to_dict(), profile.key


def test_every_profile_only_uses_shapes_the_normaliser_implements():
    for profile in (*BUILTIN_BY_KEY.values(), CUSTOM):
        for spec in profile.sheets:
            assert spec.kind in KINDS, f"{profile.key}/{spec.sheet}"
            if spec.kind != "reference":
                assert hasattr(normalise.Normaliser, f"_shape_{spec.kind}"), spec.kind
            for child in spec.children:
                assert child.role in KINDS[spec.kind].child_roles


def test_a_profile_claiming_an_unknown_shape_is_refused_on_load():
    from app.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        SourceProfile.from_dict({"sheets": [{"sheet": "S", "kind": "telepathy"}]})


def test_every_entity_the_runner_knows_can_actually_be_auto_mapped():
    # Three lists have to agree, and nothing enforced it: the catalogue's
    # entities, the run order, and the allow-list of doctypes a mapping row may
    # name. Adding an entity without the third one made `automap` return a 422
    # naming every *other* doctype — a confusing way to learn about a two-line
    # omission.
    from app.models.migration import MAPPING_TARGETS
    from app.services.migration.catalogue import ENTITIES
    from app.services.migration.mapping import ENTITY_TARGETS
    from app.services.migration.runner import ENTITY_ORDER

    assert not set(ENTITY_TARGETS.values()) - set(MAPPING_TARGETS)
    assert not {e.key for e in ENTITIES} - set(ENTITY_ORDER)
    assert not set(ENTITY_ORDER) - {e.key for e in ENTITIES}


def test_a_reference_sheet_says_why_it_is_not_imported():
    # "Unsupported" is not an answer. Every deliberately-skipped sheet carries a
    # sentence a tester can act on.
    for profile in BUILTIN_BY_KEY.values():
        for spec in profile.sheets:
            if spec.kind == "reference":
                assert spec.reason.strip(), f"{profile.key}/{spec.sheet}"


# --- sign convention ------------------------------------------------------------------


def test_a_debit_is_a_negative_amount_and_a_credit_a_positive_one():
    debit = b.ledger_entry("Cash", debit=100)
    credit = b.ledger_entry("Sales", credit=100)
    assert debit["AMOUNT"] == "-100"
    assert credit["AMOUNT"] == "100"
    assert debit["ISDEEMEDPOSITIVE"] == "Yes"


@pytest.mark.parametrize(
    "marker,amount,expected",
    [
        ("Dr", Decimal("100"), True),
        ("debit", Decimal("100"), True),
        ("Cr", Decimal("100"), False),
        ("credit", Decimal("-100"), False),  # an explicit marker beats the sign
        (None, Decimal("-100"), True),
        (None, Decimal("100"), False),
    ],
)
def test_which_side_a_line_sits_on_reads_the_marker_before_the_sign(marker, amount, expected):
    assert normalise._is_debit(marker, amount=amount) is expected


def test_a_rebuilt_posting_that_will_not_balance_is_refused_not_nudged():
    posting = b.Posting().debit("Cash", 100).credit("Sales", 90)
    assert not posting.balanced
    assert posting.difference == Decimal("10")


def test_rounding_inside_tolerance_lands_in_an_explicit_round_off_row():
    posting = b.Posting().debit("Party", Decimal("118.02")).credit("Sales", Decimal("118.00"))
    assert posting.balanced
    posting.round_off("Round Off")
    assert ("Round Off", Decimal("0.02")) in [
        (r["LEDGERNAME"], parse_amount(r["AMOUNT"])) for r in posting.rows
    ]


def test_a_synthetic_guid_is_stable_across_runs_so_a_re_import_is_recognised():
    first = b.synth_guid("zoho_books", "voucher", "INV-0001", "2025-04-01")
    second = b.synth_guid("zoho_books", "voucher", "inv-0001", "2025-04-01")
    assert first == second  # case and spacing are not identity
    assert first != b.synth_guid("zoho_books", "voucher", "INV-0002", "2025-04-01")
    assert first.startswith("x-")  # visibly not a real Tally GUID


# --- the flat sheet -------------------------------------------------------------------


FLAT_HEADERS = [
    "Voucher_Date", "Voucher_Type", "Voucher_Number", "Party_Ledger_Name",
    "Stock_Item_Name", "Unit", "Quantity", "Rate", "Taxable_Value",
    "CGST_Amount", "SGST_Amount", "IGST_Amount", "Voucher_Total",
    "Debit_Ledger", "Credit_Ledger", "Narration",
]


def flat_book(rows: list[list], ledgers: list[list] | None = None) -> bytes:
    sheets = {"Transactions": [FLAT_HEADERS, *rows]}
    sheets["Ledger_Master"] = ledgers or [
        ["Ledger_Name", "Ledger_Group"],
        ["Acme Traders", "Sundry Debtors"],
        ["Sales Account", "Sales Accounts"],
        ["Cash", "Cash-in-Hand"],
    ]
    return build(sheets)


def test_rows_sharing_a_voucher_number_become_one_voucher_with_several_lines():
    # Importing them as three invoices would treble the document count, the
    # invoice numbers and the customer's balance.
    raw = flat_book([
        ["2025-04-01", "Sales", "SAL/001", "Acme Traders", "Mouse", "Nos", 2, 500, 1000,
         90, 90, None, 1180, "Acme Traders", "Sales Account", "line 1"],
        ["2025-04-01", "Sales", "SAL/001", "Acme Traders", "Keyboard", "Nos", 1, 800, 800,
         72, 72, None, 944, "Acme Traders", "Sales Account", "line 2"],
    ])
    parsed = parse(raw, TALLY_FLAT)
    sales = parsed.records["voucher_sales"]
    assert len(sales) == 1
    assert len(as_list(sales[0]["data"]["ALLINVENTORYENTRIES.LIST"])) == 2
    assert balance(sales[0]) == ZERO


def test_a_sales_row_debits_the_party_and_credits_revenue_and_gst():
    raw = flat_book([
        ["2025-04-01", "Sales", "SAL/001", "Acme Traders", "Mouse", "Nos", 2, 500, 1000,
         90, 90, None, 1180, "Acme Traders", "Sales Account", ""],
    ])
    record = parse(raw, TALLY_FLAT).records["voucher_sales"][0]
    rows = dict(ledger_rows(record))
    assert rows["Acme Traders"] == Decimal("-1180")   # debit
    assert rows["Sales Account"] == Decimal("1000")   # credit
    assert rows["CGST Output"] == Decimal("90")
    assert rows["SGST Output"] == Decimal("90")
    assert "IGST Output" not in rows                  # the unused head drops out


def test_a_purchase_row_credits_the_party_and_debits_input_gst():
    raw = flat_book([
        ["2025-04-01", "Purchase", "PUR/001", "Acme Traders", "Mouse", "Nos", 2, 400, 800,
         None, None, 144, 944, "Purchase Account", "Acme Traders", ""],
    ])
    record = parse(raw, TALLY_FLAT).records["voucher_purchase"][0]
    rows = dict(ledger_rows(record))
    assert rows["Acme Traders"] == Decimal("944")     # credit
    assert rows["Purchase Account"] == Decimal("-800")
    assert rows["IGST Input"] == Decimal("-144")      # input tax is a debit


def test_gst_accounts_the_file_never_defines_are_created_and_reported():
    raw = flat_book([
        ["2025-04-01", "Sales", "SAL/001", "Acme Traders", "Mouse", "Nos", 2, 500, 1000,
         90, 90, None, 1180, "Acme Traders", "Sales Account", ""],
    ])
    parsed = parse(raw, TALLY_FLAT)
    created = {r["name"]: r["parent"] for r in parsed.records["ledger"]}
    assert created["CGST Output"] == "Duties & Taxes"
    assert any("does not name an account for" in w for w in parsed.warnings)


def test_a_voucher_type_with_no_target_is_counted_not_dropped():
    raw = flat_book([
        ["2025-04-01", "Telepathy Voucher", "TP/001", "Acme Traders", None, None, None, None,
         None, None, None, None, 100, "Cash", "Acme Traders", ""],
    ])
    parsed = parse(raw, TALLY_FLAT)
    assert "Voucher type: Telepathy Voucher" in parsed.unknown_tags


# --- documents whose double entry has to be rebuilt -----------------------------------


def zoho_book(bill_extra: dict | None = None) -> bytes:
    extra = bill_extra or {}
    return build({
        "Chart_of_Accounts": [
            ["account_id", "account_name", "account_type", "parent_account", "opening_balance",
             "opening_balance_type"],
            ["AC1", "Purchases", "expense", "Expense", None, None],
            ["AC2", "Input CGST", "other_current_asset", "Taxes", None, None],
            ["AC3", "Input SGST", "other_current_asset", "Taxes", None, None],
            ["AC4", "TDS Payable", "other_current_liability", "Taxes", None, None],
        ],
        "Contacts": [
            ["contact_id", "contact_name", "contact_type", "opening_balance", "opening_balance_type"],
            ["V1", "Nova Distribution", "vendor", 5000, "credit"],
        ],
        "Bills": [
            ["bill_id", "bill_number", "date", "due_date", "vendor_name", "sub_total",
             "tax_total", "tds_amount", "total"],
            ["B1", "BILL-001", "2025-06-30", "2025-07-30", "Nova Distribution",
             extra.get("sub_total", 1000), extra.get("tax_total", 180),
             extra.get("tds", 100), extra.get("total", 1080)],
        ],
        "Bill_Line_Items": [
            ["bill_id", "item_name", "quantity", "unit", "rate", "taxable_amount",
             "cgst", "sgst", "igst", "line_total", "account_name"],
            ["B1", "Mouse", 2, "Nos", 500, 1000, 90, 90, 0, 1180, "Purchases"],
        ],
    })


def test_a_zoho_account_type_becomes_the_tally_group_that_classifies_it():
    parsed = parse(zoho_book(), ZOHO_BOOKS)
    groups = {r["name"]: r["parent"] for r in parsed.records["ledger"]}
    # Filed under "Taxes" in Zoho, which types them as ordinary current assets —
    # reading only the type would keep GST out of Duties & Taxes and out of the
    # GST returns.
    assert groups["Input CGST"] == "Duties & Taxes"
    assert groups["Purchases"] == "Indirect Expenses"
    # contact_type decides the party group, which is what creates the Supplier.
    assert groups["Nova Distribution"] == "Sundry Creditors"


def test_tds_withheld_is_posted_so_the_bill_balances():
    # total = sub_total + tax - tds. Without the TDS row every bill carrying it
    # is out by exactly the amount withheld.
    record = parse(zoho_book(), ZOHO_BOOKS).records["voucher_purchase"][0]
    rows = dict(ledger_rows(record))
    assert rows["Nova Distribution"] == Decimal("1080")
    assert rows["Purchases"] == Decimal("-1000")
    assert rows["Input CGST"] == Decimal("-90")
    assert rows["TDS Payable"] == Decimal("100")
    assert balance(record) == ZERO
    assert not record.get("_messages")


def test_a_document_whose_totals_disagree_is_staged_as_an_error_carrying_the_gap():
    parsed = parse(zoho_book({"total": 9999}), ZOHO_BOOKS)
    record = parsed.records["voucher_purchase"][0]
    assert record["_messages"][0]["level"] == "error"
    assert "out by" in record["_messages"][0]["message"]


def test_an_invoice_carries_a_bill_reference_so_a_payment_can_settle_it():
    record = parse(zoho_book(), ZOHO_BOOKS).records["voucher_purchase"][0]
    party = next(
        node for node in as_list(record["data"]["ALLLEDGERENTRIES.LIST"])
        if node.get("ISPARTYLEDGER") == "Yes"
    )
    assert party["BILLALLOCATIONS.LIST"][0]["NAME"] == "BILL-001"
    # 30 days between date and due date, which is what drives the due date here.
    assert party["BILLALLOCATIONS.LIST"][0]["BILLCREDITPERIOD"] == "30 Days"


def test_an_opening_balance_on_a_contact_is_not_lost_to_a_dedicated_sheet():
    # Zoho's Opening_Balances sheet covers its chart of accounts but not its
    # contacts. Trusting only the dedicated sheet dropped every party balance.
    book = build({
        "Chart_of_Accounts": [
            ["account_id", "account_name", "account_type", "opening_balance", "opening_balance_type"],
            ["AC1", "Cash", "cash", 5000, "debit"],
        ],
        "Contacts": [
            ["contact_id", "contact_name", "contact_type", "opening_balance", "opening_balance_type"],
            ["C1", "Acme Traders", "customer", 8000, "debit"],
        ],
        "Opening_Balances": [
            ["account_id", "account_name", "opening_balance", "opening_balance_type", "migration_date"],
            ["AC1", "Cash", 7500, "debit", "2025-04-01"],
        ],
    })
    openings = {r["name"]: r["opening_balance"] for r in parse(book, ZOHO_BOOKS).records["opening_ledger"]}
    assert openings["Acme Traders"] == "-8000"        # from the contact, debit
    assert openings["Cash"] == "-7500"                # the dedicated sheet wins per name


def test_a_stock_adjustment_moves_stock_rather_than_setting_it():
    # Physical Stock *sets* the quantity; a Stock Journal *moves* it. Importing a
    # "+3 damaged" delta as a count would set the item to 3 instead of adding 3.
    book = build({
        "Inventory_Adjustments": [
            ["adjustment_id", "date", "reason", "reference_number", "warehouse_id",
             "item_name", "quantity_adjusted", "value_adjusted"],
            ["IA1", "2025-06-16", "Damage", "IA-1", "WH1", "Mouse", -3, -1020],
            ["IA2", "2025-06-17", "Count", "IA-2", "WH1", "Keyboard", 5, 4000],
        ],
    })
    records = parse(book, ZOHO_BOOKS).records["voucher_stock_journal"]
    assert "SOURCEALLINVENTORYENTRIES.LIST" in records[0]["data"]      # issue
    assert "DESTINATIONALLINVENTORYENTRIES.LIST" in records[1]["data"]  # receipt


def test_a_direct_expense_becomes_a_journal_not_a_payment_against_the_vendor():
    # A Payment Entry against the vendor would create an unallocated advance
    # against a payable that never existed.
    book = build({
        "Expenses": [
            ["expense_id", "date", "expense_account", "paid_through", "vendor_name",
             "reference_number", "amount", "tax_amount", "total"],
            ["E1", "2026-02-18", "Office Supplies", "ICICI Bank", "Orbit Components",
             "EXP-1", 1000, 180, 1180],
        ],
    })
    parsed = parse(book, ZOHO_BOOKS)
    assert "voucher_payment" not in parsed.records
    record = parsed.records["voucher_journal"][0]
    rows = dict(ledger_rows(record))
    assert rows["Office Supplies"] == Decimal("-1000")
    assert rows["ICICI Bank"] == Decimal("1180")
    assert "Orbit Components" in record["narration"]


# --- detection ------------------------------------------------------------------------


def test_an_unrecognised_workbook_gets_the_custom_profile_not_a_wrong_guess():
    book = workbook.read(build({
        "Sheet1": [["Widget", "Colour", "Weight"], ["A", "red", 1], ["B", "blue", 2]],
    }))
    found = detect.detect(book)
    assert found.profile.key == CUSTOM.key
    assert found.confidence == 0


def test_a_saved_company_profile_is_ranked_alongside_the_builtins():
    saved = SourceProfile.from_dict(
        {**TALLY_FLAT.to_dict(), "key": "our-busy-export", "label": "Busy"}, saved=True
    )
    book = workbook.read(flat_book([
        ["2025-04-01", "Sales", "S1", "Acme Traders", None, None, None, None, 100,
         None, None, None, 100, "Acme Traders", "Sales Account", ""],
    ]))
    keys = [d.profile.key for d in detect.rank(book, extra=(saved,))]
    assert "our-busy-export" in keys


# --- the template ---------------------------------------------------------------------


def test_the_generated_template_is_recognised_as_itself_and_imports_empty():
    book = workbook.read(templates.build())
    found = detect.detect(book)
    assert found.profile.key == OPTIERP_TEMPLATE.key
    assert found.confidence == 100
    assert normalise.normalise(book, found.profile).total == 0


def test_every_template_heading_maps_back_to_the_field_it_came_from():
    # The template's headings are title-cased aliases. If folding one did not
    # return the alias the profile matches on, a filled template would import
    # with columns silently unmapped.
    book = workbook.read(templates.build())
    for spec in OPTIERP_TEMPLATE.sheets:
        if spec.kind == "reference":
            continue
        sheet = book.by_name(spec.sheet)
        assert sheet is not None, spec.sheet
        for field, aliases in spec.columns.items():
            assert aliases[0] in sheet.headers, f"{spec.sheet}.{field}"


def test_a_filled_template_imports_without_a_mapping_step():
    import io as _io

    from openpyxl import load_workbook

    filled = load_workbook(_io.BytesIO(templates.build()))

    def fill(sheet_name: str, values: dict[str, object]) -> None:
        """Write by heading, the way a person filling the template does."""
        sheet = filled[sheet_name]
        headers = [str(c.value or "") for c in sheet[1]]
        row = [None] * len(headers)
        for header, value in values.items():
            assert header in headers, f"{sheet_name} has no column {header!r}: {headers}"
            row[headers.index(header)] = value
        sheet.append(row)

    fill("Accounts", {"Account Name": "Acme Traders", "Parent Group": "Sundry Debtors"})
    fill("Vouchers", {
        "Voucher Id": "V1", "Voucher Number": "SAL/001", "Date": "2025-04-01",
        "Voucher Type": "Sales", "Party": "Acme Traders", "Total": 1180,
    })
    fill("Voucher_Ledgers", {"Voucher Id": "V1", "Account": "Acme Traders", "Debit": 1180})
    fill("Voucher_Ledgers", {"Voucher Id": "V1", "Account": "Sales", "Credit": 1180})

    out = _io.BytesIO()
    filled.save(out)

    book = workbook.read(out.getvalue())
    parsed = normalise.normalise(book, detect.detect(book).profile)
    assert len(parsed.records["ledger"]) == 1
    record = parsed.records["voucher_sales"][0]
    assert dict(ledger_rows(record)) == {
        "Acme Traders": Decimal("-1180"),
        "Sales": Decimal("1180"),
    }


# --- against the real sample exports ---------------------------------------------------

REAL = [
    ("Tally_ERP_Complete_Sample_Dump_500_Transactions.xlsx", TALLY_WORKBOOK.key),
    ("Tally_Sample_500_Transactions_OptiERP.xlsx", TALLY_FLAT.key),
    ("Zoho_Books_Complete_Sample_Backup_500_Transactions.xlsx", ZOHO_BOOKS.key),
]


@pytest.mark.parametrize("name,expected", REAL)
def test_the_real_sample_exports_are_detected_and_fold_into_balanced_vouchers(name, expected):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} is not checked out")

    book = workbook.read(path.read_bytes(), file_name=name)
    found = detect.detect(book)
    assert found.profile.key == expected
    assert found.confidence >= detect.MIN_CONFIDENCE
    assert not found.unmatched_sheets, found.unmatched_sheets

    parsed = normalise.normalise(book, found.profile)
    assert parsed.total > 400
    assert parsed.from_date and parsed.to_date

    unbalanced = [
        record
        for key, rows in parsed.records.items()
        if key.startswith("voucher_")
        for record in rows
        if record["data"].get("ALLLEDGERENTRIES.LIST") and balance(record) != ZERO
    ]
    assert not unbalanced, [r["voucher_number"] for r in unbalanced[:5]]

    blocked = [
        r for rows in parsed.records.values() for r in rows
        if any(m.get("level") == "error" for m in r.get("_messages") or [])
    ]
    assert not blocked, [r["voucher_number"] for r in blocked[:5]]


# --- the party columns that had no home before ----------------------------------------


def party_book(extra: dict | None = None) -> bytes:
    """A Zoho-shaped contact list with the columns a Customer actually has."""
    row = {
        "payment_terms": "Net 45", "gst_treatment": "business_gst",
        "company_name": "Aarav Retail Private Limited", "is_active": "Yes",
        **(extra or {}),
    }
    return build({
        "Chart_of_Accounts": [
            ["account_id", "account_name", "account_type", "parent_account"],
            ["AC1", "Sales", "income", "Income"],
        ],
        "Contacts": [
            ["contact_id", "contact_name", "company_name", "contact_type", "gst_treatment",
             "gstin", "payment_terms", "is_active"],
            ["CUS001", "Aarav Retail", row["company_name"], "customer", row["gst_treatment"],
             "27AAACA1111A1Z1", row["payment_terms"], row["is_active"]],
        ],
        "Contact_Addresses": [
            ["contact_id", "address_type", "attention", "address", "city", "state",
             "state_code", "zip", "country", "phone"],
            ["CUS001", "billing", "Accounts Payable", "Unit 4, Nehru Estate", "Mumbai",
             "Maharashtra", "27", "400069", "India", "+91-22-26177973"],
        ],
        "Contact_Persons": [
            ["contact_person_id", "contact_id", "salutation", "first_name", "last_name",
             "email", "phone", "mobile", "is_primary_contact"],
            ["CP001", "CUS001", "Ms.", "Priya", "Nair", "priya@aarav.example",
             "02278024124", "9350962947", "Yes"],
        ],
        "Payment_Terms": [
            ["payment_term_id", "payment_term_name", "number_of_days"],
            ["PT001", "Net 45", 45],
        ],
    })


def test_party_columns_the_customer_form_has_reach_the_staged_record():
    # Every one of these is a real column on Customer. Before the profile
    # vocabulary could name them, no workbook could ever fill them in.
    ledgers = {r["name"]: r["data"] for r in parse(party_book(), ZOHO_BOOKS).records["ledger"]}
    data = ledgers["Aarav Retail"]
    assert data["PAYMENTTERMSNAME"] == "Net 45"
    assert data["TAXCATEGORYNAME"] == "business_gst"
    assert data["LEDGERLEGALNAME"] == "Aarav Retail Private Limited"
    assert data["PARTYGSTIN"] == "27AAACA1111A1Z1"


def test_an_inactive_party_is_carried_across_as_disabled_not_dropped():
    parsed = parse(party_book({"is_active": "No"}), ZOHO_BOOKS)
    party = next(r for r in parsed.records["ledger"] if r["name"] == "Aarav Retail")
    assert party["data"]["ISDELETED"] == "Yes"


def test_an_address_sheet_keyed_on_the_source_id_finds_its_party_by_name():
    # Zoho joins Contact_Addresses to Contacts on `contact_id`, never on the
    # name. Reading the id straight through would hang every address off a party
    # called "CUS001" that no importer could resolve.
    parsed = parse(party_book(), ZOHO_BOOKS)
    address = parsed.records["address"][0]
    assert address["data"]["PARTYNAME"] == "Aarav Retail"
    assert address["data"]["ADDRESS.LIST"]["ADDRESS"] == ["Unit 4, Nehru Estate"]
    assert address["data"]["CITYNAME"] == "Mumbai"
    assert address["data"]["PINCODE"] == "400069"
    assert address["data"]["ADDRESSTYPE"] == "billing"


def test_a_contact_person_sheet_keeps_the_person_and_their_numbers():
    contact = parse(party_book(), ZOHO_BOOKS).records["contact"][0]
    assert contact["data"]["PARTYNAME"] == "Aarav Retail"
    assert contact["data"]["FIRSTNAME"] == "Priya"
    assert contact["data"]["LASTNAME"] == "Nair"
    assert contact["data"]["EMAIL"] == "priya@aarav.example"
    assert contact["data"]["LEDGERMOBILE"] == "9350962947"
    assert contact["data"]["LEDGERPHONE"] == "02278024124"


def test_payment_terms_become_one_template_carrying_its_credit_period():
    terms = parse(party_book(), ZOHO_BOOKS).records["payment_terms"]
    assert [t["name"] for t in terms] == ["Net 45"]
    rows = terms[0]["data"]["PAYMENTTERM.LIST"]
    assert rows[0]["CREDITDAYS"] == "45"
    # One unstated instalment is the whole invoice, not nothing.
    assert rows[0]["INVOICEPORTION"] == "100"


def test_split_payment_terms_share_a_name_and_become_one_template():
    raw = build({
        "Payment_Terms": [
            ["payment_term_id", "payment_term_name", "number_of_days"],
            ["PT1", "50/50", 0],
            ["PT2", "50/50", 30],
        ],
    })
    terms = parse(raw, ZOHO_BOOKS).records["payment_terms"]
    assert len(terms) == 1
    rows = terms[0]["data"]["PAYMENTTERM.LIST"]
    assert [r["CREDITDAYS"] for r in rows] == ["0", "30"]
    # Two instalments with no stated share are half each, not 0% each.
    assert [r["INVOICEPORTION"] for r in rows] == ["50", "50"]


# --- orders: the lines are the document -----------------------------------------------


def order_book(with_lines: bool) -> bytes:
    sheets = {
        "Chart_of_Accounts": [["account_id", "account_name", "account_type"],
                              ["AC1", "Sales", "income"]],
        "Contacts": [["contact_id", "contact_name", "contact_type"],
                     ["C1", "Evergreen Stores", "customer"]],
        "Sales_Orders": [
            ["salesorder_id", "salesorder_number", "date", "shipment_date",
             "customer_name", "sub_total", "tax_total", "total"],
            ["SO1", "SO-0001", "2025-10-04", "2025-10-08", "Evergreen Stores",
             51791.38, 9322.45, 61113.83],
        ],
    }
    if with_lines:
        sheets["Sales_Order_Items"] = [
            ["salesorder_id", "item_name", "quantity", "unit", "rate", "line_total"],
            ["SO1", "Wireless Mouse", 10, "Nos", 650, 6500],
        ]
    return build(sheets)


def order_profile(with_lines: bool) -> SourceProfile:
    """The Zoho profile with its order sheet mapped, which the wizard can do."""
    from app.services.migration.sources.profiles import ChildSpec, SheetSpec

    children = (
        (ChildSpec(
            sheet="Sales_Order_Items", role="item_lines", key="salesorder id",
            columns={"key": ("salesorder id",), "item": ("item name",),
                     "qty": ("quantity",), "uom": ("unit",), "rate": ("rate",),
                     "amount": ("line total",)},
        ),)
        if with_lines
        else ()
    )
    mapped = SheetSpec(
        sheet="Sales_Orders", kind="order", key="salesorder id",
        constants={"voucher_type": "Sales Order"},
        columns={"key": ("salesorder id",), "number": ("salesorder number",),
                 "date": ("date",), "party": ("customer name",),
                 "sub_total": ("sub total",), "tax_total": ("tax total",),
                 "total": ("total",)},
        children=children,
    )
    return replace(
        ZOHO_BOOKS,
        sheets=tuple(s for s in ZOHO_BOOKS.sheets if s.sheet != "Sales_Orders") + (mapped,),
    )


def test_an_order_with_its_lines_imports_as_a_commitment_with_no_ledger_entries():
    record = parse(order_book(True), order_profile(True)).records["voucher_sales_order"][0]
    assert record["voucher_number"] == "SO-0001"
    assert record["party"] == "Evergreen Stores"
    items = as_list(record["data"]["ALLINVENTORYENTRIES.LIST"])
    assert [n["STOCKITEMNAME"] for n in items] == ["Wireless Mouse"]
    # An order posts nothing. A ledger entry here would put a commitment in the
    # books, which is exactly what an order is not.
    assert "ALLLEDGERENTRIES.LIST" not in record["data"]
    assert not record.get("_messages")


def test_an_order_shipped_without_its_lines_is_blocked_with_the_reason_on_the_row():
    # The header carries a party, a number and a total, and none of that is the
    # order. Staging it blocked is the honest answer: visible, counted, and
    # never a document somebody tries to fulfil.
    record = parse(order_book(False), order_profile(False)).records["voucher_sales_order"][0]
    message = record["_messages"][0]
    assert message["level"] == "error"
    assert "lines" in message["message"]


def test_the_zoho_order_sheets_stay_unimported_and_say_which_sheet_is_missing():
    # This export genuinely has no line sheet, so the built-in profile leaves
    # them alone — but the reason has to name the missing thing, not just refuse.
    reasons = {
        s.sheet: s.reason for s in ZOHO_BOOKS.sheets if s.kind == "reference"
    }
    for sheet in ("Quotes", "Sales_Orders", "Purchase_Orders"):
        assert "line-item sheet" in reasons[sheet]
        # ...and say the target exists, so nobody reads it as a missing feature.
        assert "Quotation, Sales Order and Purchase Order all exist" in reasons[sheet]


# --- setup masters ---------------------------------------------------------------------


def test_a_statement_line_resolves_its_bank_by_the_id_the_account_sheet_defined():
    raw = build({
        "Bank_Accounts": [
            ["bank_account_id", "account_name", "bank_name", "account_number", "ifsc",
             "currency_code", "opening_balance"],
            ["BA001", "HDFC Current", "HDFC Bank", "50200012345678", "HDFC0001234",
             "INR", 900000],
        ],
        "Bank_Transactions": [
            ["bank_transaction_id", "bank_account_id", "date", "transaction_type",
             "description", "reference_number", "amount", "debit_credit",
             "matched_status"],
            ["BT1", "BA001", "2025-10-22", "other", "Statement line", "BNK-433",
             45920.32, "debit", "matched"],
            ["BT2", "BA001", "2025-10-25", "deposit", "Statement line", "BNK-948",
             13378.89, "credit", "uncategorized"],
        ],
    })
    parsed = parse(raw, ZOHO_BOOKS)
    account = parsed.records["bank_account"][0]
    assert account["data"]["BANKACCOUNTNO"] == "50200012345678"
    assert account["data"]["IFSCODE"] == "HDFC0001234"

    lines = {r["data"]["REFERENCENUMBER"]: r["data"] for r in parsed.records["bank_transaction"]}
    assert lines["BNK-433"]["BANKNAME"] == "HDFC Current"
    # A statement is written from the bank's side: "debit" there is money leaving
    # the account, which is a withdrawal here. Getting this backwards would make
    # every reconciliation propose the opposite match.
    assert lines["BNK-433"]["WITHDRAWAL"] == "45920.32"
    assert lines["BNK-433"]["DEPOSIT"] == "0"
    assert lines["BNK-948"]["DEPOSIT"] == "13378.89"
    assert lines["BNK-948"]["WITHDRAWAL"] == "0"


def test_currencies_and_their_rates_come_across_by_iso_code():
    raw = build({
        "Currencies": [
            ["currency_id", "currency_code", "currency_name", "symbol", "exchange_rate",
             "is_base_currency"],
            ["C1", "INR", "Indian Rupee", "Rs", 1, "Yes"],
            ["C2", "USD", "US Dollar", "$", 83.5, "No"],
        ],
    })
    rows = {r["name"]: r["data"] for r in parse(raw, ZOHO_BOOKS).records["currency"]}
    assert set(rows) == {"INR", "USD"}
    assert rows["USD"]["DAILYRATE"] == "83.5"
    assert rows["INR"]["ISBASECURRENCY"] == "Yes"


def test_a_tax_rate_master_becomes_a_named_template_at_that_rate():
    raw = build({
        "Taxes": [
            ["tax_id", "tax_name", "tax_percentage", "tax_type", "tax_specific_type",
             "is_default_tax"],
            ["TX1", "GST18", 18, "tax", "gst", "No"],
        ],
    })
    row = parse(raw, ZOHO_BOOKS).records["tax_template"][0]
    assert row["name"] == "GST18"
    assert row["data"]["RATE"] == "18"


def test_budget_rows_sharing_a_name_become_one_budget_with_a_line_per_account():
    raw = build({
        "Budgets": [
            ["budget_id", "budget_name", "period_from", "period_to", "ledger_or_group",
             "budget_amount"],
            ["B1", "FY26 Opex", "2025-04-01", "2026-03-31", "Rent", 600000],
            ["B2", "FY26 Opex", "2025-04-01", "2026-03-31", "Travel", 240000],
        ],
    })
    budgets = parse(raw, TALLY_WORKBOOK).records["budget"]
    assert len(budgets) == 1
    lines = budgets[0]["data"]["BUDGETALLOCATION.LIST"]
    assert {(r["LEDGERNAME"], r["AMOUNT"]) for r in lines} == {
        ("Rent", "600000"), ("Travel", "240000"),
    }


def test_a_fixed_asset_keeps_its_cost_and_the_depreciation_already_taken():
    raw = build({
        "Fixed_Assets": [
            ["asset_id", "asset_name", "asset_group", "purchase_date", "original_cost",
             "accumulated_depreciation", "wdv_opening", "depreciation_method", "rate_pct"],
            ["FA1", "CNC Lathe", "Plant & Machinery", "2022-07-01", 1800000, 540000,
             1260000, "WDV", 15],
        ],
    })
    row = parse(raw, TALLY_WORKBOOK).records["asset"][0]
    assert row["name"] == "CNC Lathe"
    assert row["data"]["ORIGINALCOST"] == "1800000"
    # Book value is cost minus this. Dropping it would restart depreciation from
    # the full cost and overstate the asset for the rest of its life.
    assert row["data"]["ACCUMULATEDDEP"] == "540000"
    assert row["data"]["ASSETGROUP"] == "Plant & Machinery"


def test_tally_credit_terms_and_bank_details_are_no_longer_dropped_on_the_floor():
    # `credit_days` was in the field vocabulary and mapped in the Tally profile
    # from the start, and the shaper never emitted it.
    raw = build({
        "Ledger_Master": [
            ["ledger_id", "ledger_name", "group_name", "credit_days", "bank_account_no",
             "ifsc", "pan"],
            ["L1", "ABC Traders", "Sundry Debtors", 45, "50200012345678", "HDFC0001234",
             "AAACO1234F"],
        ],
    })
    data = parse(raw, TALLY_WORKBOOK).records["ledger"][0]["data"]
    assert data["BILLCREDITPERIOD"] == "45"
    assert data["BANKACCOUNTNO"] == "50200012345678"
    assert data["IFSCODE"] == "HDFC0001234"


def test_a_tally_optional_voucher_is_flagged_so_the_run_leaves_it_out():
    # Tally's "optional" voucher is a provisional entry deliberately outside the
    # books. The XML path has always read ISOPTIONAL and the runner excludes it;
    # the workbook path never set it, so a workbook posted provisional entries as
    # real ones and overstated the ledger.
    raw = build({
        "Ledger_Master": [["ledger_id", "ledger_name", "group_name"],
                          ["L1", "Cash", "Cash-in-Hand"]],
        "Voucher_Headers": [
            ["voucher_id", "voucher_date", "voucher_type", "voucher_number",
             "party_ledger", "net_amount", "is_optional"],
            ["V1", "2025-04-10", "Journal", "JV-1", "Cash", 500, "Yes"],
            ["V2", "2025-04-11", "Journal", "JV-2", "Cash", 500, "No"],
        ],
        "Accounting_Lines": [
            ["voucher_id", "line_no", "ledger_name", "dr_cr", "amount"],
            ["V1", 1, "Cash", "Dr", 500],
            ["V1", 2, "Sales", "Cr", 500],
            ["V2", 1, "Cash", "Dr", 500],
            ["V2", 2, "Sales", "Cr", 500],
        ],
    })
    flags = {
        r["voucher_number"]: r["is_optional"]
        for r in parse(raw, TALLY_WORKBOOK).records["voucher_journal"]
    }
    assert flags == {"JV-1": True, "JV-2": False}


def test_an_items_own_revenue_and_cost_accounts_survive_the_move():
    # Item has income_account_id and expense_account_id and both were left null,
    # so every future invoice for a migrated item fell back to the company
    # default rather than the account the source actually books it to.
    raw = build({
        "Items": [
            ["item_id", "name", "sku", "unit", "sales_account", "purchase_account"],
            ["IT1", "Wireless Mouse", "SKU1", "Nos", "Sales - Hardware", "COGS - Hardware"],
        ],
    })
    data = parse(raw, ZOHO_BOOKS).records["stock_item"][0]["data"]
    assert data["INCOMELEDGER"] == "Sales - Hardware"
    assert data["EXPENSELEDGER"] == "COGS - Hardware"


def test_a_unit_that_cannot_be_fractional_says_so():
    # `import_units` has always read DECIMALPLACES to decide must_be_whole_number;
    # nothing ever wrote it, so every imported unit allowed half a box.
    raw = build({
        "Units": [["unit_id", "symbol", "formal_name", "decimal_places"],
                  ["U1", "Nos", "Numbers", 0],
                  ["U2", "Kg", "Kilograms", 3]],
    })
    units = {r["name"]: r["data"] for r in parse(raw, TALLY_WORKBOOK).records["unit"]}
    assert units["Nos"]["DECIMALPLACES"] == "0"
    assert units["Kg"]["DECIMALPLACES"] == "3"


# --- the template covers what the importer can now do ---------------------------------


def test_every_importable_shape_has_a_sheet_in_the_downloadable_template():
    # The template is the answer for an application nobody has profiled. A shape
    # the importer supports but the template never mentions is unreachable to
    # anyone who is not writing a profile by hand.
    covered = {s.kind for s in OPTIERP_TEMPLATE.sheets}
    covered |= {c.role for s in OPTIERP_TEMPLATE.sheets for c in s.children}
    missing = {
        key for key in KINDS
        if key not in covered
        # The three voucher shapes are alternative spellings of the one the
        # template already ships, and `reference` is the absence of a shape.
        and key not in {"reference", "voucher_flat", "document", "payment", "journal",
                        "transfer", "expense", "stock_adjustment"}
    }
    assert not missing, missing


def test_a_narrowed_template_download_ships_only_what_was_asked_for():
    import openpyxl

    raw = templates.build(("ledger", "customer", "supplier", "address", "contact"))
    names = openpyxl.load_workbook(io.BytesIO(raw)).sheetnames
    assert "Accounts" in names and "Addresses" in names and "Contacts" in names
    # Somebody asking for just the parties should not be handed the voucher
    # sheets, which is what an entity-less sheet used to slip through as.
    assert "Vouchers" not in names and "Fixed_Assets" not in names
