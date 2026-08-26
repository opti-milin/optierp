"""End-to-end spreadsheet migration: upload .xlsx -> map -> dry run -> run -> roll back.

The point of these tests is that a workbook reaches the ledgers by exactly the
path a Tally XML export does. So the assertions are the ledger ones — did the
customer get created under Sundry Debtors, did the invoice post a balanced GL
with the GST on the right head, did the receipt settle the invoice it named, does
a rollback put the outstanding back — asserted against a file that never
mentioned Tally.
"""

import asyncio
import base64
import io

import pytest

pytestmark = pytest.mark.asyncio


def _workbook(sheets: dict[str, list[list]]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


#: A Zoho Books backup cut down to two customers, one vendor, one item and the
#: four documents that exercise the interesting paths: an invoice whose double
#: entry has to be rebuilt, a bill with TDS withheld, a receipt that settles the
#: invoice, and a journal that is already double entry.
def zoho_backup() -> bytes:
    return _workbook({
        "Chart_of_Accounts": [
            ["account_id", "account_name", "account_type", "parent_account",
             "opening_balance", "opening_balance_type"],
            ["AC1", "HDFC Bank", "bank", "Cash and Cash Equivalents", 500000, "debit"],
            ["AC2", "Sales", "income", "Income", None, None],
            ["AC3", "Purchases", "expense", "Expense", None, None],
            ["AC4", "Output CGST", "other_current_liability", "Taxes", None, None],
            ["AC5", "Output SGST", "other_current_liability", "Taxes", None, None],
            ["AC6", "Input CGST", "other_current_asset", "Taxes", None, None],
            ["AC7", "Input SGST", "other_current_asset", "Taxes", None, None],
            ["AC8", "TDS Payable", "other_current_liability", "Taxes", None, None],
            ["AC9", "Owner Equity", "equity", "Equity", 500000, "credit"],
        ],
        "Contacts": [
            ["contact_id", "contact_name", "company_name", "contact_type", "gst_treatment",
             "gstin", "payment_terms", "opening_balance", "opening_balance_type"],
            ["CUS1", "Aarav Retail", "Aarav Retail Private Limited", "customer",
             "business_gst", "27AAACA1111A1Z1", "Net 30", None, None],
            ["VEN1", "Metro Wholesale", "Metro Wholesale LLP", "vendor", "business_gst",
             "27MMMMM1111M1Z1", "Net 45", None, None],
        ],
        "Contact_Addresses": [
            ["contact_id", "address_type", "attention", "address", "city", "state",
             "state_code", "zip", "country", "phone"],
            ["CUS1", "billing", "Accounts Payable", "Unit 4, Nehru Estate", "Mumbai",
             "Maharashtra", "27", "400069", "India", "+91-22-26177973"],
            ["VEN1", "billing", "Purchase Desk", "12 MIDC Road", "Pune", "Maharashtra",
             "27", "411018", "India", "+91-20-26177973"],
        ],
        "Contact_Persons": [
            ["contact_person_id", "contact_id", "salutation", "first_name", "last_name",
             "email", "phone", "mobile", "is_primary_contact"],
            ["CP1", "CUS1", "Ms.", "Priya", "Nair", "priya@aarav.example",
             "02278024124", "9350962947", "Yes"],
        ],
        "Payment_Terms": [
            ["payment_term_id", "payment_term_name", "number_of_days"],
            ["PT1", "Net 30", 30],
            ["PT2", "Net 45", 45],
        ],
        "Currencies": [
            ["currency_id", "currency_code", "currency_name", "symbol", "exchange_rate",
             "is_base_currency"],
            ["C1", "INR", "Indian Rupee", "Rs", 1, "Yes"],
            ["C2", "USD", "US Dollar", "$", 83.5, "No"],
        ],
        "Bank_Accounts": [
            ["bank_account_id", "account_name", "bank_name", "account_number", "ifsc",
             "account_type", "currency_code", "opening_balance", "feed_status"],
            ["BA1", "HDFC Bank", "HDFC Bank Ltd", "50200012345678", "HDFC0001234",
             "bank", "INR", 500000, "not_connected"],
        ],
        "Bank_Transactions": [
            ["bank_transaction_id", "bank_account_id", "date", "transaction_type",
             "description", "reference_number", "amount", "debit_credit",
             "matched_status"],
            ["BT1", "BA1", "2025-05-05", "deposit", "Cheque from Aarav Retail",
             "UTR-1", 7080, "credit", "matched"],
            ["BT2", "BA1", "2025-06-01", "bank_fee", "Quarterly charges", "BNK-9",
             250, "debit", "uncategorized"],
        ],
        "Items": [
            ["item_id", "name", "sku", "hsn_or_sac", "unit", "tax_percentage",
             "purchase_rate", "sales_rate", "opening_stock", "opening_stock_value"],
            ["IT1", "Wireless Mouse", "SKU1", "84716060", "Nos", 18, 400, 600, 100, 40000],
        ],
        "Warehouses": [
            ["warehouse_id", "warehouse_name", "city", "status"],
            ["WH1", "Main Warehouse", "Mumbai", "active"],
        ],
        "Invoices": [
            ["invoice_id", "invoice_number", "date", "due_date", "customer_name",
             "reference_number", "sub_total", "tax_total", "total", "notes"],
            ["INV1", "INV-0001", "2025-04-10", "2025-05-10", "Aarav Retail",
             "PO-1", 6000, 1080, 7080, "Ten mice"],
        ],
        "Invoice_Line_Items": [
            ["invoice_id", "item_name", "quantity", "unit", "rate", "taxable_amount",
             "cgst", "sgst", "igst", "line_total", "account_name"],
            ["INV1", "Wireless Mouse", 10, "Nos", 600, 6000, 540, 540, 0, 7080, "Sales"],
        ],
        "Bills": [
            ["bill_id", "bill_number", "date", "due_date", "vendor_name",
             "sub_total", "tax_total", "tds_amount", "total"],
            ["BILL1", "BILL-0001", "2025-04-12", "2025-05-12", "Metro Wholesale",
             4000, 720, 400, 4320],
        ],
        "Bill_Line_Items": [
            ["bill_id", "item_name", "quantity", "unit", "rate", "taxable_amount",
             "cgst", "sgst", "igst", "line_total", "account_name"],
            ["BILL1", "Wireless Mouse", 10, "Nos", 400, 4000, 360, 360, 0, 4720, "Purchases"],
        ],
        "Customer_Payments": [
            ["payment_id", "payment_number", "date", "customer_name", "payment_mode",
             "bank_account", "reference_number", "amount", "notes"],
            ["PAY1", "PAY-0001", "2025-05-05", "Aarav Retail", "Cheque",
             "HDFC Bank", "UTR-1", 7080, "Settled INV-0001"],
        ],
        "Customer_Payment_Applications": [
            ["payment_id", "invoice_id", "invoice_number", "amount_applied"],
            ["PAY1", "INV1", "INV-0001", 7080],
        ],
        "Journals": [
            ["journal_id", "journal_number", "date", "reference_number", "notes", "status"],
            ["JRN1", "JRN-0001", "2025-06-01", "JV-1", "Bank charges", "published"],
        ],
        "Journal_Lines": [
            ["journal_id", "line_id", "account_name", "description", "debit", "credit"],
            ["JRN1", "L1", "Purchases", "Bank charges", 250, 0],
            ["JRN1", "L2", "HDFC Bank", "Bank charges", 0, 250],
        ],
    })


def _payload(raw: bytes, **overrides) -> dict:
    body = {
        "file_name": "zoho-backup.xlsx",
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "title": "Zoho FY26",
        "opening_date": "2025-04-01",
    }
    body.update(overrides)
    return body


async def _upload(client, headers, raw: bytes | None = None, **overrides) -> dict:
    resp = await client.post(
        "/api/v1/migration/imports",
        json=_payload(raw if raw is not None else zoho_backup(), **overrides),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run(client, headers, import_id, *, timeout: float = 180.0) -> dict:
    resp = await client.post(f"/api/v1/migration/imports/{import_id}/run", headers=headers)
    assert resp.status_code == 202, resp.text

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        poll = await client.get(f"/api/v1/migration/imports/{import_id}/status", headers=headers)
        assert poll.status_code == 200, f"{poll.status_code} {poll.text}"
        if not poll.json()["is_running"]:
            break
        assert asyncio.get_running_loop().time() < deadline, poll.json()
        await asyncio.sleep(0.25)

    from app.services.migration import background

    while background.active_run_count():
        assert asyncio.get_running_loop().time() < deadline, "background task never finished"
        await asyncio.sleep(0.05)

    return (await client.get(f"/api/v1/migration/imports/{import_id}", headers=headers)).json()


async def _prepare(client, headers, **overrides) -> dict:
    session = await _upload(client, headers, **overrides)
    resp = await client.post(
        f"/api/v1/migration/imports/{session['id']}/automap", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return session


# --- parse ----------------------------------------------------------------------------


async def test_a_workbook_is_recognised_and_staged_without_writing_anything(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)

    assert session["source_type"] == "XLSX"
    assert session["source_app"] == "Zoho Books"
    assert session["source_profile"] == "zoho_books"
    assert session["status"] == "Parsed"
    assert session["total_records"] > 0

    staged = {e["entity_key"]: e["total"] for e in session["entities"]}
    assert staged["ledger"] >= 11        # 9 accounts + 2 contacts
    assert staged["customer"] == 1
    assert staged["supplier"] == 1
    assert staged["stock_item"] == 1
    assert staged["voucher_sales"] == 1
    assert staged["voucher_purchase"] == 1
    assert staged["voucher_receipt"] == 1
    assert staged["voucher_journal"] == 1

    # Nothing reached the ledgers.
    assert (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"] == []
    assert (await client.get("/api/v1/customers", headers=headers)).json()["items"] == []


async def test_the_mapping_step_shows_every_sheet_with_samples_and_its_rivals(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)

    resp = await client.get(
        f"/api/v1/migration/imports/{session['id']}/mapping", headers=headers
    )
    assert resp.status_code == 200, resp.text
    mapping = resp.json()

    assert mapping["profile"]["key"] == "zoho_books"
    assert any(c["key"] == "zoho_books" for c in mapping["candidates"])

    invoices = next(s for s in mapping["sheets"] if s["name"] == "Invoices")
    assert invoices["assigned"]["kind"] == "document"
    assert invoices["assigned"]["columns"]["total"] == "total"
    assert not invoices["missing"]
    # Sample values are what let a human recognise a column at a glance.
    assert any(c["samples"] for c in invoices["columns"])

    # A line sheet is not "unassigned" — it is read through its parent.
    lines = next(s for s in mapping["sheets"] if s["name"] == "Invoice_Line_Items")
    assert lines["parent"] == {"sheet": "Invoices", "role": "item_lines"}


async def test_a_tally_xml_import_has_no_mapping_step(ctx):
    client, company, headers = ctx
    xml = (
        "<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>"
        "<TALLYMESSAGE><LEDGER NAME='Cash'><PARENT>Cash-in-Hand</PARENT></LEDGER></TALLYMESSAGE>"
        "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
    )
    session = await _upload(
        client, headers, raw=xml.encode("utf-8"), file_name="masters.xml"
    )
    assert session["source_type"] == "XML"

    resp = await client.get(
        f"/api/v1/migration/imports/{session['id']}/mapping", headers=headers
    )
    assert resp.status_code == 422
    assert "not a spreadsheet" in resp.text


# --- run ------------------------------------------------------------------------------


async def _trial_balance(client, headers) -> list[dict]:
    years = (await client.get("/api/v1/fiscal-years", headers=headers)).json()["items"]
    fy = next(y for y in years if y["year_start_date"] <= "2025-04-10" <= y["year_end_date"])
    resp = await client.get(
        "/api/v1/reports/trial-balance", params={"fiscal_year_id": fy["id"]}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    # Leaves only — group rows are roll-ups of the leaves beneath them.
    return [row for row in resp.json() if not row["is_group"]]


async def test_a_zoho_backup_posts_a_balanced_book_through_the_normal_services(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    done = await _run(client, headers, session["id"])

    assert done["status"] in ("Imported", "Partially Imported"), done["error_message"]
    assert done["imported_count"] > 0
    assert done["error_count"] == 0, done["error_message"]

    # The double entry here was *rebuilt* from documents rather than read from
    # the file, so this is the assertion that matters most.
    leaves = await _trial_balance(client, headers)
    debit = sum(float(row["debit"]) for row in leaves)
    credit = sum(float(row["credit"]) for row in leaves)
    assert debit > 0
    assert abs(debit - credit) < 0.01, f"trial balance is out by {debit - credit}"


async def test_a_contact_becomes_a_party_under_the_right_control_account(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    aarav = next(c for c in customers if c["customer_name"] == "Aarav Retail")
    # contact_type "customer" is what put the ledger under Sundry Debtors, which
    # is what created the Customer at all.
    assert aarav["tax_id"] == "27AAACA1111A1Z1"

    suppliers = (await client.get("/api/v1/suppliers", headers=headers)).json()["items"]
    assert any(s["supplier_name"] == "Metro Wholesale" for s in suppliers)

    items = (await client.get("/api/v1/items", headers=headers)).json()["items"]
    mouse = next(i for i in items if i["item_code"] == "Wireless Mouse")
    assert mouse["hsn_sac_code"] == "84716060"


async def test_the_receipt_settles_the_invoice_it_named(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    invoices = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    assert len(invoices) == 1
    invoice = invoices[0]
    assert invoice["docstatus"] == 1
    assert float(invoice["grand_total"]) == pytest.approx(7080, abs=0.5)
    # The payment carried an "Agst Ref" allocation naming INV-0001, which is what
    # turns it into a settlement rather than an unallocated advance.
    assert float(invoice["outstanding_amount"]) == pytest.approx(0, abs=0.5)

    payments = (await client.get("/api/v1/payment-entries", headers=headers)).json()["items"]
    assert len(payments) == 1 and payments[0]["docstatus"] == 1


async def test_gst_lands_on_the_head_the_file_named(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    leaves = await _trial_balance(client, headers)
    net = {
        row["account_name"]: float(row["credit"]) - float(row["debit"])
        for row in leaves
    }
    # Output GST is a credit of 540 each; input GST a debit of 360 each.
    assert net.get("Output CGST", 0) == pytest.approx(540, abs=0.5)
    assert net.get("Input CGST", 0) == pytest.approx(-360, abs=0.5)
    # TDS withheld on the bill is a liability, not a reduction of the expense —
    # this is the row that made every bill with TDS on it fail to balance before.
    assert net.get("TDS Payable", 0) == pytest.approx(400, abs=0.5)


async def test_rolling_back_a_spreadsheet_import_cancels_what_it_created(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    resp = await client.post(
        f"/api/v1/migration/imports/{session['id']}/rollback", headers=headers
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["cancelled"] > 0
    # No failures at all. This fixture books two opening balances, which are one
    # Journal Entry by design — rollback used to try to cancel that entry once
    # per ledger row and report the second attempt as a failure.
    assert result["failed"] == [], result["failed"]
    assert result["session"]["status"] == "Rolled Back"

    # Rollback cancels rather than deletes, so the trial balance must still
    # balance afterwards — the reversing entries are part of the audit trail.
    leaves = await _trial_balance(client, headers)
    debit = sum(float(row["debit"]) for row in leaves)
    credit = sum(float(row["credit"]) for row in leaves)
    assert abs(debit - credit) < 0.01

    invoices = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    assert all(inv["docstatus"] == 2 for inv in invoices), "an invoice survived the rollback"


async def test_the_same_workbook_twice_does_not_post_its_documents_twice(ctx):
    client, company, headers = ctx
    first = await _prepare(client, headers)
    await _run(client, headers, first["id"])

    # A spreadsheet has no GUIDs, so this only works because identity is derived
    # from each document's natural key. Same file, same keys, same identities.
    second = await _prepare(client, headers)
    validate = await client.post(
        f"/api/v1/migration/imports/{second['id']}/validate", headers=headers
    )
    assert validate.status_code == 200, validate.text
    assert validate.json()["duplicates"], "the overlap was not reported before the run"

    done = await _run(client, headers, second["id"])
    assert done["skipped_count"] > 0

    invoices = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    assert len(invoices) == 1, "the second import posted the invoice again"


# --- the mapping wizard ----------------------------------------------------------------


async def test_an_unrecognised_workbook_stages_nothing_until_it_is_mapped(ctx):
    client, company, headers = ctx
    raw = _workbook({
        "MyLedgers": [
            ["Account", "Under", "Opening", "DrCr"],
            ["Cash Box", "Cash-in-Hand", 1000, "Dr"],
            ["Owner Capital", "Capital Account", 1000, "Cr"],
        ],
    })
    session = await _upload(client, headers, raw=raw, file_name="mystery.xlsx")

    assert session["source_profile"] == "custom"
    assert session["total_records"] == 0

    mapping = (
        await client.get(f"/api/v1/migration/imports/{session['id']}/mapping", headers=headers)
    ).json()
    sheet = next(s for s in mapping["sheets"] if s["name"] == "MyLedgers")
    assert sheet["assigned"] is None
    assert [c["label"] for c in sheet["columns"]] == ["Account", "Under", "Opening", "DrCr"]

    # Now say what it is, the way the wizard does.
    definition = {
        "key": "custom",
        "label": "My old system",
        "app": "Custom",
        "sheets": [
            {
                "sheet": "MyLedgers",
                "kind": "ledger_master",
                "entity": "ledger",
                "columns": {
                    "name": ["account"],
                    "parent": ["under"],
                    "opening": ["opening"],
                    "dr_cr": ["drcr"],
                },
                "children": [],
            }
        ],
    }
    resp = await client.put(
        f"/api/v1/migration/imports/{session['id']}/mapping",
        json={"definition": definition},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_records"] == 4  # 2 ledgers + their 2 opening balances

    staged = {e["entity_key"]: e["total"] for e in resp.json()["entities"]}
    assert staged["ledger"] == 2
    assert staged["opening_ledger"] == 2


async def test_a_mapping_can_be_saved_and_is_then_recognised_on_the_next_upload(ctx):
    client, company, headers = ctx
    raw = _workbook({
        "MyLedgers": [
            ["Account", "Under", "Opening", "DrCr"],
            ["Cash Box", "Cash-in-Hand", 1000, "Dr"],
            ["Owner Capital", "Capital Account", 1000, "Cr"],
        ],
    })
    session = await _upload(client, headers, raw=raw, file_name="mystery.xlsx")
    definition = {
        "key": "custom",
        "label": "My old system",
        "app": "Custom",
        "sheets": [
            {
                "sheet": "MyLedgers",
                "kind": "ledger_master",
                "entity": "ledger",
                "columns": {"name": ["account"], "parent": ["under"]},
                "children": [],
            }
        ],
    }
    await client.put(
        f"/api/v1/migration/imports/{session['id']}/mapping",
        json={"definition": definition},
        headers=headers,
    )

    saved = await client.post(
        "/api/v1/migration/sources",
        json={"label": "My old system", "migration_import_id": session["id"]},
        headers=headers,
    )
    assert saved.status_code == 201, saved.text

    listed = await client.get("/api/v1/migration/sources", headers=headers)
    assert listed.status_code == 200
    assert any(p["label"] == "My old system" for p in listed.json()["saved"])

    # The whole point: the next file of this shape needs no mapping step.
    again = await _upload(client, headers, raw=raw, file_name="mystery-2.xlsx")
    assert again["source_profile"] == "my-old-system"
    assert again["total_records"] == 2


async def test_a_mapping_cannot_be_changed_once_the_import_has_run(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    resp = await client.put(
        f"/api/v1/migration/imports/{session['id']}/mapping",
        json={"profile": "tally_flat"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "roll it back" in resp.text.casefold()


# --- the template -----------------------------------------------------------------------


async def test_the_template_downloads_and_a_filled_one_imports_without_mapping(ctx):
    client, company, headers = ctx
    resp = await client.get("/api/v1/migration/template.xlsx", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    from openpyxl import load_workbook

    filled = load_workbook(io.BytesIO(resp.content))

    def fill(sheet_name: str, values: dict) -> None:
        sheet = filled[sheet_name]
        headers_row = [str(c.value or "") for c in sheet[1]]
        row = [None] * len(headers_row)
        for header, value in values.items():
            assert header in headers_row, f"{sheet_name}: {header!r} not in {headers_row}"
            row[headers_row.index(header)] = value
        sheet.append(row)

    fill("Accounts", {"Account Name": "Bright Retail", "Parent Group": "Sundry Debtors"})
    fill("Accounts", {"Account Name": "Sales", "Parent Group": "Sales Accounts"})
    fill("Vouchers", {
        "Voucher Id": "V1", "Voucher Number": "SI-001", "Date": "2025-04-05",
        "Voucher Type": "Sales", "Party": "Bright Retail", "Total": 5000,
    })
    fill("Voucher_Ledgers", {"Voucher Id": "V1", "Account": "Bright Retail", "Debit": 5000})
    fill("Voucher_Ledgers", {"Voucher Id": "V1", "Account": "Sales", "Credit": 5000})

    out = io.BytesIO()
    filled.save(out)

    session = await _upload(
        client, headers, raw=out.getvalue(), file_name="filled-template.xlsx"
    )
    assert session["source_profile"] == "optierp_template"
    assert session["source_app"] == "OptiERP Template"
    staged = {e["entity_key"]: e["total"] for e in session["entities"]}
    assert staged["ledger"] == 2
    assert staged["voucher_sales"] == 1

# --- the masters that had no path from a spreadsheet before -----------------------------


async def test_payment_terms_arrive_as_templates_and_the_party_points_at_one(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    templates = (
        await client.get("/api/v1/registry/payment-terms-template", headers=headers)
    ).json()["items"]
    by_name = {t["template_name"]: t for t in templates}
    assert {"Net 30", "Net 45"} <= set(by_name)

    # The link is the point. A template nobody points at is a row in a table;
    # a Customer whose default terms survived the move is the migration working.
    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    aarav = next(c for c in customers if c["customer_name"] == "Aarav Retail")
    assert aarav["payment_terms_template_id"] == by_name["Net 30"]["id"]

    suppliers = (await client.get("/api/v1/suppliers", headers=headers)).json()["items"]
    metro = next(s for s in suppliers if s["supplier_name"] == "Metro Wholesale")
    assert metro["payment_terms_template_id"] == by_name["Net 45"]["id"]


async def test_a_gst_treatment_becomes_a_tax_category_the_party_is_filed_under(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    categories = (
        await client.get("/api/v1/registry/tax-category", headers=headers)
    ).json()["items"]
    registered = next(c for c in categories if c["title"] == "Registered")

    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    aarav = next(c for c in customers if c["customer_name"] == "Aarav Retail")
    # Zoho says "business_gst"; that means registered, and Tax Category is where
    # a registered party lives here.
    assert aarav["tax_category_id"] == registered["id"]


async def test_an_address_sheet_joined_by_id_lands_on_the_right_party(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    aarav = next(c for c in customers if c["customer_name"] == "Aarav Retail")

    addresses = (await client.get("/api/v1/registry/address", headers=headers)).json()["items"]
    mine = [a for a in addresses if a.get("customer_id") == aarav["id"]]
    assert len(mine) == 1, addresses
    assert mine[0]["address_line1"] == "Unit 4, Nehru Estate"
    assert mine[0]["city"] == "Mumbai"
    assert mine[0]["pincode"] == "400069"
    assert mine[0]["state"] == "Maharashtra"

    # The vendor's address arrived too, and did not collide with the customer's
    # even though address_title is unique per company.
    suppliers = (await client.get("/api/v1/suppliers", headers=headers)).json()["items"]
    metro = next(sup for sup in suppliers if sup["supplier_name"] == "Metro Wholesale")
    assert any(a.get("supplier_id") == metro["id"] for a in addresses)


async def test_a_named_contact_person_arrives_with_their_own_numbers(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    contacts = (await client.get("/api/v1/registry/contact", headers=headers)).json()["items"]
    priya = next(c for c in contacts if c["first_name"] == "Priya")
    assert priya["last_name"] == "Nair"
    assert priya["email_id"] == "priya@aarav.example"
    assert priya["mobile_no"] == "9350962947"
    assert priya["phone"] == "02278024124"

    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    aarav = next(c for c in customers if c["customer_name"] == "Aarav Retail")
    assert priya["customer_id"] == aarav["id"]


async def test_a_bank_account_links_to_the_ledger_of_the_same_name(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    accounts = (
        await client.get("/api/v1/registry/bank-account", headers=headers)
    ).json()["items"]
    hdfc = next(a for a in accounts if a["account_name"] == "HDFC Bank")
    assert hdfc["account_number"] == "50200012345678"
    assert hdfc["iban"] == "HDFC0001234"
    # Without a book side there is nothing to reconcile against, which is the
    # only reason a Bank Account exists separately from the ledger.
    assert hdfc["gl_account_id"], hdfc


async def test_statement_lines_arrive_unreconciled_and_post_nothing(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)

    result = await _run(client, headers, session["id"])
    counters = {e["entity_key"]: e for e in result["entities"]}
    assert counters["bank_transaction"]["created"] == 2

    # The payment that this statement line corresponds to is imported by the same
    # run. If the line posted anything, the bank would be credited twice and the
    # trial balance would still net out — so the assertion that matters is the
    # bank's own closing balance, not that the books balance.
    leaves = {row["account_name"]: row for row in await _trial_balance(client, headers)}
    assert "HDFC Bank" in leaves


async def test_a_currency_the_app_does_not_know_is_reported_never_invented(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    result = await _run(client, headers, session["id"])

    records = (
        await client.get(
            f"/api/v1/migration/imports/{session['id']}/records",
            params={"entity_key": "currency", "limit": 50},
            headers=headers,
        )
    ).json()["items"]
    by_name = {r["source_name"]: r for r in records}
    assert by_name["INR"]["status"] in ("Imported", "Reused", "Warning")
    # USD is not seeded here. Inventing it would give every amount in it a code
    # no report, rate or print format could resolve.
    assert by_name["USD"]["status"] == "Skipped"
    assert "currency list" in str(by_name["USD"]["messages"])
    # A currency it cannot place is a Skipped row, not a failed import.
    assert result["status"] in ("Imported", "Partially Imported")


async def test_masters_created_along_the_way_are_named_on_the_row_that_made_them(ctx):
    client, company, headers = ctx
    session = await _prepare(client, headers)
    await _run(client, headers, session["id"])

    records = (
        await client.get(
            f"/api/v1/migration/imports/{session['id']}/records",
            params={"entity_key": "customer", "limit": 50},
            headers=headers,
        )
    ).json()["items"]
    messages = " ".join(str(r["messages"]) for r in records)
    # Creating a Tax Category the source only implied is defensible. Doing it
    # silently is not — the accountant has to be able to see the short list of
    # what appeared, on the row that caused it.
    assert "Tax category 'Registered' did not exist here and was created" in messages
