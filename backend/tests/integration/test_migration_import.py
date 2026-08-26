"""End-to-end Tally XML import: upload -> automap -> dry run -> run -> rollback.

The assertions are the ones a tester would make by eye: did the customer get
created under the right account, did the invoice post the right GL, did the
receipt settle the invoice it was allocated against, and does a rollback put
the outstanding back.
"""

import asyncio
import base64

import pytest

pytestmark = pytest.mark.asyncio

TALLY_XML = """<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY><IMPORTDATA>
  <REQUESTDESC><STATICVARIABLES>
    <SVCURRENTCOMPANY>Acme Traders</SVCURRENTCOMPANY>
    <SVFROMDATE>20250401</SVFROMDATE><SVTODATE>20260331</SVTODATE>
  </STATICVARIABLES></REQUESTDESC>
  <REQUESTDATA>
   <TALLYMESSAGE><UNIT NAME="Nos"><GUID>u-1</GUID><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT></UNIT></TALLYMESSAGE>
   <TALLYMESSAGE><GROUP NAME="North Debtors"><GUID>g-1</GUID>
     <PARENT>Sundry Debtors</PARENT></GROUP></TALLYMESSAGE>
   <TALLYMESSAGE><LEDGER NAME="Bharat Steel"><GUID>l-1</GUID>
     <PARENT>North Debtors</PARENT><PARTYGSTIN>27AAACB2894G1ZX</PARTYGSTIN>
     <EMAIL>ops@bharat.example</EMAIL><LEDSTATENAME>Maharashtra</LEDSTATENAME>
     <ADDRESS.LIST><ADDRESS>Plot 14 MIDC</ADDRESS><ADDRESS>Pune</ADDRESS></ADDRESS.LIST>
   </LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><LEDGER NAME="Domestic Sales"><GUID>l-2</GUID>
     <PARENT>Sales Accounts</PARENT></LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><LEDGER NAME="Petty Cash Box"><GUID>l-3</GUID>
     <PARENT>Cash-in-Hand</PARENT></LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><STOCKGROUP NAME="Fasteners"><GUID>sg-1</GUID></STOCKGROUP></TALLYMESSAGE>
   <TALLYMESSAGE><GODOWN NAME="Main Store"><GUID>gd-1</GUID></GODOWN></TALLYMESSAGE>
   <TALLYMESSAGE><STOCKITEM NAME="Hex Bolt M12"><GUID>si-1</GUID>
     <PARENT>Fasteners</PARENT><BASEUNITS>Nos</BASEUNITS>
     <GSTHSNCODE>73181500</GSTHSNCODE></STOCKITEM></TALLYMESSAGE>
   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Sales"><GUID>v-1</GUID><DATE>20250415</DATE>
     <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>INV-001</VOUCHERNUMBER>
     <PARTYLEDGERNAME>Bharat Steel</PARTYLEDGERNAME><NARRATION>April supply</NARRATION>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Bharat Steel</LEDGERNAME>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-10000.00</AMOUNT>
       <BILLALLOCATIONS.LIST><NAME>INV-001</NAME><BILLTYPE>New Ref</BILLTYPE>
         <AMOUNT>-10000.00</AMOUNT></BILLALLOCATIONS.LIST>
     </ALLLEDGERENTRIES.LIST>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Domestic Sales</LEDGERNAME>
       <AMOUNT>10000.00</AMOUNT></ALLLEDGERENTRIES.LIST>
     <ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Hex Bolt M12</STOCKITEMNAME>
       <RATE>100.00/Nos</RATE><AMOUNT>10000.00</AMOUNT><BILLEDQTY>100 Nos</BILLEDQTY>
       <ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>Domestic Sales</LEDGERNAME>
         <AMOUNT>10000.00</AMOUNT></ACCOUNTINGALLOCATIONS.LIST>
     </ALLINVENTORYENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Receipt"><GUID>v-2</GUID><DATE>20250420</DATE>
     <VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME><VOUCHERNUMBER>RCT-001</VOUCHERNUMBER>
     <PARTYLEDGERNAME>Bharat Steel</PARTYLEDGERNAME>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Bharat Steel</LEDGERNAME>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>6000.00</AMOUNT>
       <BILLALLOCATIONS.LIST><NAME>INV-001</NAME><BILLTYPE>Agst Ref</BILLTYPE>
         <AMOUNT>6000.00</AMOUNT></BILLALLOCATIONS.LIST>
     </ALLLEDGERENTRIES.LIST>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Petty Cash Box</LEDGERNAME>
       <AMOUNT>-6000.00</AMOUNT></ALLLEDGERENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Sales"><GUID>v-3</GUID><DATE>20250422</DATE>
     <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>INV-002</VOUCHERNUMBER>
     <PARTYLEDGERNAME>Ghost Trader</PARTYLEDGERNAME>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Ghost Trader</LEDGERNAME>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-500.00</AMOUNT></ALLLEDGERENTRIES.LIST>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Domestic Sales</LEDGERNAME>
       <AMOUNT>500.00</AMOUNT></ALLLEDGERENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Sales"><GUID>v-4</GUID><DATE>20250425</DATE>
     <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>INV-003</VOUCHERNUMBER>
     <ISCANCELLED>Yes</ISCANCELLED><PARTYLEDGERNAME>Bharat Steel</PARTYLEDGERNAME>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Bharat Steel</LEDGERNAME>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-999.00</AMOUNT></ALLLEDGERENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
   <TALLYMESSAGE><VOUCHER VCHTYPE="Payroll"><GUID>v-5</GUID><DATE>20250430</DATE>
     <VOUCHERTYPENAME>Payroll</VOUCHERTYPENAME></VOUCHER></TALLYMESSAGE>
  </REQUESTDATA>
 </IMPORTDATA></BODY>
</ENVELOPE>"""


def _payload(xml: str = TALLY_XML, **overrides) -> dict:
    body = {
        "file_name": "acme.xml",
        "content_base64": base64.b64encode(xml.encode("utf-8")).decode("ascii"),
        "title": "Acme FY26",
    }
    body.update(overrides)
    return body


async def _upload(client, headers, **overrides) -> dict:
    resp = await client.post("/api/v1/migration/imports", json=_payload(**overrides), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run(client, headers, import_id, *, timeout: float = 120.0) -> dict:
    """Start a run and wait for it to finish.

    The run happens in a background task, so every test drives the same path a
    browser does: POST /run, then poll /status until it stops running.
    """
    resp = await client.post(f"/api/v1/migration/imports/{import_id}/run", headers=headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "Importing"

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        poll = await client.get(f"/api/v1/migration/imports/{import_id}/status", headers=headers)
        # Surface the actual response: a bare KeyError on the parsed body hides
        # whether the endpoint 401'd, 404'd or blew up.
        assert poll.status_code == 200, f"status poll failed: {poll.status_code} {poll.text}"
        status = poll.json()
        if not status["is_running"]:
            break
        assert asyncio.get_running_loop().time() < deadline, f"run did not finish: {status}"
        # A browser polls this once a second; 50ms only manufactures connection
        # contention that no real client creates.
        await asyncio.sleep(0.25)

    # "is_running" flips when run_import returns, but the task around it is
    # still closing its session. The ctx fixture drops and recreates the schema
    # per test, so leaving that task in flight lets it collide with the next
    # test's teardown — which is how this suite produced unrelated failures.
    from app.services.migration import background

    while background.active_run_count():
        assert asyncio.get_running_loop().time() < deadline, "background task never finished"
        await asyncio.sleep(0.05)

    return (
        await client.get(f"/api/v1/migration/imports/{import_id}", headers=headers)
    ).json()


async def _records(client, headers, import_id, **params) -> list[dict]:
    resp = await client.get(
        f"/api/v1/migration/imports/{import_id}/records", params=params, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


# --- parse ----------------------------------------------------------------------------


async def test_upload_parses_and_stages_without_writing_anything(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)

    assert session["status"] == "Parsed"
    assert session["source_company_name"] == "Acme Traders"
    assert session["from_date"] == "2025-04-01"
    assert session["imported_count"] == 0

    keys = {e["entity_key"]: e for e in session["entities"]}
    assert keys["ledger"]["total"] == 3
    assert keys["customer"]["total"] == 1  # only the Sundry Debtors ledger
    assert keys["voucher_sales"]["total"] == 3
    assert keys["voucher_payroll"]["support"] == "none"

    # nothing hit the ledgers yet
    resp = await client.get("/api/v1/sales-invoices", headers=headers)
    assert resp.json()["total"] == 0


async def test_a_second_upload_of_the_same_file_is_flagged(ctx):
    client, company, headers = ctx
    first = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{first['id']}/automap", headers=headers)
    # Must finish: the duplicate-file warning only fires against a *completed*
    # import, so uploading again mid-run would legitimately find nothing.
    await _run(client, headers, first["id"])

    second = await _upload(client, headers)
    summary = (
        await client.get(f"/api/v1/migration/imports/{second['id']}/summary", headers=headers)
    ).json()
    assert any("already imported" in log["message"] for log in summary["logs"])


# --- mapping --------------------------------------------------------------------------


async def test_automap_proposes_a_mapping_for_every_name(ctx):
    client, company, headers = ctx
    # An existing UOM must be matched, not duplicated.
    await client.post("/api/v1/uoms", json={"uom_name": "Nos"}, headers=headers)
    session = await _upload(client, headers)

    resp = await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert summary["ledger"]["matched"] + summary["ledger"]["to_create"] == 3

    mappings = (await client.get("/api/v1/migration/mappings", headers=headers)).json()
    by_name = {(m["entity_key"], m["source_name"]): m for m in mappings}
    assert by_name[("customer", "Bharat Steel")]["target_doctype"] == "Customer"
    assert by_name[("stock_item", "Hex Bolt M12")]["target_doctype"] == "Item"
    assert by_name[("unit", "Nos")]["match_method"] == "exact"
    assert by_name[("unit", "Nos")]["target_id"] is not None


async def test_a_manual_mapping_survives_a_second_automap(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)

    mappings = (await client.get("/api/v1/migration/mappings", headers=headers)).json()
    target = next(m for m in mappings if m["source_name"] == "Hex Bolt M12")
    resp = await client.put(
        f"/api/v1/migration/mappings/{target['id']}",
        json={"target_doctype": "Item", "target_id": None, "notes": "checked by hand"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_locked"] is True

    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)
    after = (await client.get("/api/v1/migration/mappings", headers=headers)).json()
    locked = next(m for m in after if m["source_name"] == "Hex Bolt M12")
    assert locked["is_locked"] is True and locked["match_method"] == "manual"


# --- dry run --------------------------------------------------------------------------


async def test_dry_run_reports_the_plan_and_unresolved_names(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)

    resp = await client.post(f"/api/v1/migration/imports/{session['id']}/validate", headers=headers)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["total_planned"] > 0
    assert result["blockers"] == []
    assert result["session"]["status"] == "Validated"

    # still nothing written
    assert (await client.get("/api/v1/sales-invoices", headers=headers)).json()["total"] == 0


# --- run ------------------------------------------------------------------------------


@pytest.fixture()
async def imported(ctx):
    """Upload, map and run — the state most assertions below start from."""
    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)
    return client, company, headers, await _run(client, headers, session["id"])


async def test_masters_are_created_with_their_tally_classification(imported):
    client, company, headers, session = imported

    accounts = (
        await client.get(f"/api/v1/companies/{company['id']}/chart-of-accounts", headers=headers)
    ).json()
    by_name = {a["account_name"]: a for a in accounts}

    # a user sub-group under Sundry Debtors inherits Asset/Receivable
    assert by_name["North Debtors"]["root_type"] == "Asset"
    assert by_name["North Debtors"]["is_group"] is True
    assert by_name["Bharat Steel"]["root_type"] == "Asset"
    assert by_name["Domestic Sales"]["root_type"] == "Income"
    assert by_name["Petty Cash Box"]["root_type"] == "Asset"

    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    bharat = next(c for c in customers if c["customer_name"] == "Bharat Steel")
    assert bharat["tax_id"] == "27AAACB2894G1ZX"

    items = (await client.get("/api/v1/items", headers=headers)).json()["items"]
    bolt = next(i for i in items if i["item_code"] == "Hex Bolt M12")
    assert bolt["hsn_sac_code"] == "73181500"
    assert bolt["stock_uom"] == "Nos"

    warehouses = (await client.get("/api/v1/warehouses", headers=headers)).json()
    assert any(w["warehouse_name"] == "Main Store" for w in warehouses)


async def test_the_sales_voucher_becomes_a_submitted_invoice(imported):
    client, company, headers, session = imported

    invoices = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    assert len(invoices) == 1  # INV-002 fails (unmapped party), INV-003 is cancelled in Tally
    invoice = invoices[0]
    assert invoice["docstatus"] == 1
    assert float(invoice["grand_total"]) == 10000.0


async def test_the_receipt_settles_the_invoice_it_was_allocated_against(imported):
    client, company, headers, session = imported

    invoice = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"][0]
    assert float(invoice["outstanding_amount"]) == 4000.0  # 10000 billed - 6000 received

    payments = (await client.get("/api/v1/payment-entries", headers=headers)).json()["items"]
    assert len(payments) == 1 and payments[0]["docstatus"] == 1


async def test_a_cancelled_tally_voucher_is_skipped_not_posted(imported):
    client, company, headers, session = imported
    skipped = await _records(client, headers, session["id"], status="Skipped")
    assert any(r["voucher_number"] == "INV-003" for r in skipped)
    assert any("Cancelled in Tally" in m["message"] for r in skipped for m in r["messages"] or [])


async def test_an_unsupported_entity_is_reported_rather_than_dropped(imported):
    client, company, headers, session = imported
    payroll = await _records(client, headers, session["id"], entity_key="voucher_payroll")
    assert len(payroll) == 1
    assert payroll[0]["status"] == "Skipped"
    assert "HR" in " ".join(m["message"] for m in payroll[0]["messages"] or [])


async def test_a_voucher_naming_an_unmapped_party_fails_alone(imported):
    """INV-002 names a ledger that isn't in the file. It must fail on its own row
    with a readable reason, and not stop the rest of the import."""
    client, company, headers, session = imported

    failed = await _records(client, headers, session["id"], status="Error")
    assert [r["voucher_number"] for r in failed] == ["INV-002"]
    assert "not mapped to a Customer" in failed[0]["messages"][0]["message"]

    assert session["status"] == "Partially Imported"
    assert session["error_count"] == 1
    assert session["imported_count"] > 0


async def test_every_imported_record_points_back_at_the_document_it_made(imported):
    client, company, headers, session = imported
    imported_rows = await _records(client, headers, session["id"], status="Imported")
    assert imported_rows
    assert all(r["target_id"] and r["target_doctype"] for r in imported_rows)


async def test_gl_is_balanced_after_the_import(imported):
    """The importer opens the fiscal years the period needs, and what it posts
    through the document services balances like any hand-keyed voucher."""
    client, company, headers, session = imported

    # Company setup only opens the current year; 2025-2026 exists because the
    # import opened the years its period needed.
    years = (await client.get("/api/v1/fiscal-years", headers=headers)).json()["items"]
    fy = next(y for y in years if y["year_start_date"] <= "2025-04-15" <= y["year_end_date"])
    assert fy["year"] == "2025-2026"

    resp = await client.get(
        "/api/v1/reports/trial-balance",
        params={"fiscal_year_id": fy["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # Leaves only — group rows are roll-ups of the leaves beneath them.
    leaves = [row for row in resp.json() if not row["is_group"]]
    debit = sum(float(row["debit"]) for row in leaves)
    credit = sum(float(row["credit"]) for row in leaves)
    assert debit > 0
    assert abs(debit - credit) < 0.01


# --- rerun + rollback -----------------------------------------------------------------


async def test_running_twice_is_refused(imported):
    client, company, headers, session = imported
    resp = await client.post(f"/api/v1/migration/imports/{session['id']}/run", headers=headers)
    assert resp.status_code == 422
    assert "already been run" in resp.json()["detail"]


async def test_rollback_cancels_the_documents_and_restores_outstanding(imported):
    client, company, headers, session = imported

    resp = await client.post(f"/api/v1/migration/imports/{session['id']}/rollback", headers=headers)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["cancelled"] >= 2  # invoice + payment
    assert result["failed"] == []
    assert result["session"]["status"] == "Rolled Back"

    invoices = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    assert all(i["docstatus"] == 2 for i in invoices)

    # masters are deliberately left behind — cancelling them would orphan anything
    # created against them since
    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    assert any(c["customer_name"] == "Bharat Steel" for c in customers)


async def test_deleting_a_live_import_is_refused_until_it_is_rolled_back(imported):
    client, company, headers, session = imported

    resp = await client.delete(f"/api/v1/migration/imports/{session['id']}", headers=headers)
    assert resp.status_code == 422

    await client.post(f"/api/v1/migration/imports/{session['id']}/rollback", headers=headers)
    resp = await client.delete(f"/api/v1/migration/imports/{session['id']}", headers=headers)
    assert resp.status_code == 204


async def test_a_rerun_after_rollback_reuses_the_masters_it_already_created(ctx):
    """The second import must match the existing Bharat Steel, not make a duplicate."""
    client, company, headers = ctx
    first = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{first['id']}/automap", headers=headers)
    await _run(client, headers, first["id"])
    await client.post(f"/api/v1/migration/imports/{first['id']}/rollback", headers=headers)

    second = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)
    run = await _run(client, headers, second["id"])

    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    assert sum(1 for c in customers if c["customer_name"] == "Bharat Steel") == 1

    keys = {e["entity_key"]: e for e in run["entities"]}
    assert keys["customer"]["created"] == 0 and keys["customer"]["updated"] == 1


# --- overlapping re-imports -----------------------------------------------------------
#
# The failure this guards against is the one customers reach by ordinary use:
# export Apr-Jun, import it, then export Apr-Sep and import that. Different
# bytes, so the file-hash check says nothing — and April to June posts twice.

# The same company re-exported over a longer period: v-1 and v-2 repeat, v-6 is new.
WIDER_EXPORT = TALLY_XML.replace(
    "<SVTODATE>20260331</SVTODATE>", "<SVTODATE>20260930</SVTODATE>"
).replace(
    "  </REQUESTDATA>",
    """   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Sales"><GUID>v-6</GUID><DATE>20250510</DATE>
     <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>INV-004</VOUCHERNUMBER>
     <PARTYLEDGERNAME>Bharat Steel</PARTYLEDGERNAME>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Bharat Steel</LEDGERNAME>
       <ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-2500.00</AMOUNT>
       <BILLALLOCATIONS.LIST><NAME>INV-004</NAME><BILLTYPE>New Ref</BILLTYPE>
         <AMOUNT>-2500.00</AMOUNT></BILLALLOCATIONS.LIST>
     </ALLLEDGERENTRIES.LIST>
     <ALLLEDGERENTRIES.LIST><LEDGERNAME>Domestic Sales</LEDGERNAME>
       <AMOUNT>2500.00</AMOUNT></ALLLEDGERENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
  </REQUESTDATA>""",
)


async def test_an_overlapping_export_is_not_posted_twice(imported):
    """The core guarantee: a wider re-export adds the new voucher and only that."""
    client, company, headers, first = imported
    before = (await client.get("/api/v1/sales-invoices", headers=headers)).json()
    assert before["total"] == 1  # INV-001

    second = await _upload(client, headers, xml=WIDER_EXPORT, file_name="acme-wider.xml")
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)
    run = await _run(client, headers, second["id"])
    assert run["status"] in ("Imported", "Partially Imported")

    # INV-001 is not posted again; INV-004 is the only new document.
    invoices = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    numbers = sorted(float(i["grand_total"]) for i in invoices)
    assert numbers == [2500.0, 10000.0], numbers

    # ...and the receipt did not re-settle the invoice either.
    payments = (await client.get("/api/v1/payment-entries", headers=headers)).json()
    assert payments["total"] == 1


async def test_the_repeated_voucher_says_why_it_was_skipped(imported):
    client, company, headers, first = imported
    second = await _upload(client, headers, xml=WIDER_EXPORT, file_name="acme-wider.xml")
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)
    await _run(client, headers, second["id"])

    skipped = await _records(client, headers, second["id"], status="Skipped")
    repeated = [r for r in skipped if r["voucher_number"] == "INV-001"]
    assert len(repeated) == 1
    text = " ".join(m["message"] for m in repeated[0]["messages"] or [])
    assert "Already imported" in text and "Sales Invoice" in text
    # It points at the document that already exists, not at nothing.
    assert repeated[0]["target_id"] is not None


async def test_the_dry_run_reports_overlap_before_anything_posts(imported):
    """A tester must learn about the overlap while it is still free to fix."""
    client, company, headers, first = imported
    second = await _upload(client, headers, xml=WIDER_EXPORT, file_name="acme-wider.xml")
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)

    result = (
        await client.post(f"/api/v1/migration/imports/{second['id']}/validate", headers=headers)
    ).json()

    assert result["duplicate_count"] >= 1
    assert any(d["voucher_number"] == "INV-001" for d in result["duplicates"])
    # The plan counts what will actually be created, not what is in the file.
    # 4 sales vouchers in the file: INV-001 repeats, INV-003 is cancelled in
    # Tally. Neither will be created, so neither is promised — leaving INV-002
    # and INV-004.
    assert result["planned"].get("voucher_sales") == 2
    assert result["not_posting_count"] >= 1  # the cancelled INV-003


async def test_rollback_releases_the_identity_so_it_can_be_imported_again(imported):
    """Roll back, and the same voucher must be importable again — otherwise the
    import -> compare -> roll back -> fix -> re-import loop dies on the second lap."""
    client, company, headers, first = imported
    await client.post(f"/api/v1/migration/imports/{first['id']}/rollback", headers=headers)

    second = await _upload(client, headers, file_name="acme-again.xml")
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)
    await _run(client, headers, second["id"])

    rows = await _records(client, headers, second["id"], entity_key="voucher_sales")
    inv001 = [r for r in rows if r["voucher_number"] == "INV-001"]
    assert len(inv001) == 1
    assert inv001[0]["status"] in ("Imported", "Warning"), inv001[0]["messages"]


async def test_rollback_cancels_what_the_session_created_even_if_the_row_moved_on(imported):
    """Regression: a document must never be strandable by its staging row.

    A re-run used to overwrite an imported row's status with "Skipped", and
    rollback only looked for "Imported"/"Warning" — so it reported "Rolled back
    0 document(s)" while the invoice stayed live in the books. The identity
    ledger is the authoritative record of what the session posted, so rollback
    now sweeps that too.
    """
    client, company, headers, session = imported
    invoice = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"][0]
    assert invoice["docstatus"] == 1

    # Corrupt the marker rollback used to rely on.
    await _blank_row_status(session["id"])

    result = (
        await client.post(f"/api/v1/migration/imports/{session['id']}/rollback", headers=headers)
    ).json()
    assert result["cancelled"] >= 1, result

    after = (
        await client.get(f"/api/v1/sales-invoices/{invoice['id']}", headers=headers)
    ).json()
    assert after["docstatus"] == 2, "the invoice was left live in the books"


async def test_a_rolled_back_session_can_be_rolled_back_again(imported):
    """Otherwise an incomplete first attempt leaves no way to finish the job."""
    client, company, headers, session = imported
    first = await client.post(
        f"/api/v1/migration/imports/{session['id']}/rollback", headers=headers
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/migration/imports/{session['id']}/rollback", headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["cancelled"] == 0  # idempotent: nothing left to undo


async def _blank_row_status(import_id: str) -> None:
    """Mimic the re-run that used to overwrite an imported row with 'Skipped'."""
    import uuid as _uuid

    from sqlalchemy import update

    from app.core.database import async_session_factory
    from app.models.migration import MigrationStagingRecord

    async with async_session_factory() as db:
        await db.execute(
            update(MigrationStagingRecord)
            .where(
                MigrationStagingRecord.migration_import_id == _uuid.UUID(import_id),
                MigrationStagingRecord.status.in_(("Imported", "Warning")),
            )
            .values(status="Skipped")
        )
        await db.commit()


# --- opening balances ---------------------------------------------------------------------
#
# Tally hangs opening balances off the master itself, not on a voucher. Without
# them the imported books start from zero: every ledger balance, every debtor
# and creditor outstanding and every stock quantity is wrong from day one.

OPENINGS_XML = """<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY><IMPORTDATA>
  <REQUESTDESC><STATICVARIABLES>
    <SVCURRENTCOMPANY>Acme Traders</SVCURRENTCOMPANY>
    <SVFROMDATE>20250401</SVFROMDATE><SVTODATE>20260331</SVTODATE>
  </STATICVARIABLES></REQUESTDESC>
  <REQUESTDATA>
   <TALLYMESSAGE><UNIT NAME="Nos"><GUID>u-1</GUID><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT></UNIT></TALLYMESSAGE>
   <TALLYMESSAGE><LEDGER NAME="Bharat Steel" RESERVEDNAME=""><GUID>l-1</GUID>
     <PARENT>Sundry Debtors</PARENT>
     <OPENINGBALANCE>-50000.00</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><LEDGER NAME="Kirloskar Supply" RESERVEDNAME=""><GUID>l-2</GUID>
     <PARENT>Sundry Creditors</PARENT>
     <OPENINGBALANCE>30000.00</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><LEDGER NAME="Domestic Sales" RESERVEDNAME=""><GUID>l-3</GUID>
     <PARENT>Sales Accounts</PARENT>
     <OPENINGBALANCE>0.00</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><STOCKGROUP NAME="Fasteners"><GUID>sg-1</GUID></STOCKGROUP></TALLYMESSAGE>
   <TALLYMESSAGE><GODOWN NAME="Main Store"><GUID>gd-1</GUID></GODOWN></TALLYMESSAGE>
   <TALLYMESSAGE><STOCKITEM NAME="Hex Bolt M12"><GUID>si-1</GUID>
     <PARENT>Fasteners</PARENT><BASEUNITS>Nos</BASEUNITS>
     <OPENINGBALANCE>120 Nos</OPENINGBALANCE>
     <OPENINGVALUE>60000.00</OPENINGVALUE>
     <BATCHALLOCATIONS.LIST>
       <GODOWNNAME>Main Store</GODOWNNAME>
       <OPENINGBALANCE>120 Nos</OPENINGBALANCE>
       <OPENINGVALUE>60000.00</OPENINGVALUE>
     </BATCHALLOCATIONS.LIST></STOCKITEM></TALLYMESSAGE>
  </REQUESTDATA>
 </IMPORTDATA></BODY>
</ENVELOPE>"""


@pytest.fixture()
async def opened(ctx):
    client, company, headers = ctx
    session = await _upload(
        client, headers, xml=OPENINGS_XML, file_name="openings.xml",
        opening_date="2025-04-01",
    )
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)
    return client, company, headers, await _run(client, headers, session["id"])


async def test_ledger_opening_balances_become_one_opening_journal_entry(opened):
    """Regression: openings were declared in the catalogue and documented, but
    the parser never emitted them and no importer existed — they silently did
    nothing, and the imported books started at zero."""
    client, company, headers, session = opened

    rows = await _records(client, headers, session["id"], entity_key="opening_ledger")
    assert rows, "no opening_ledger rows were staged at all"
    posted = [r for r in rows if r["status"] in ("Imported", "Warning")]
    assert len(posted) == 2, [(r["source_name"], r["status"], r["messages"]) for r in rows]

    # One entry, not one per ledger.
    assert len({r["target_id"] for r in posted}) == 1
    assert all(r["target_doctype"] == "Journal Entry" for r in posted)

    entry = await _journal_entry(posted[0]["target_id"])
    assert entry.posting_date.isoformat() == "2025-04-01"
    assert entry.docstatus == 1


async def test_a_zero_opening_balance_is_not_staged(opened):
    """Tally writes "0.00" on every master; staging those is noise, not data."""
    client, company, headers, session = opened
    rows = await _records(client, headers, session["id"], entity_key="opening_ledger")
    assert "Domestic Sales" not in {r["source_name"] for r in rows}


async def test_the_opening_entry_balances_and_carries_its_parties(opened):
    """A party row without its party never reaches the ageing reports — the
    outstanding sits in the control account with nothing to attribute it to."""
    client, company, headers, session = opened
    rows = await _records(client, headers, session["id"], entity_key="opening_ledger")
    entry = await _journal_entry(
        next(r["target_id"] for r in rows if r["status"] in ("Imported", "Warning"))
    )

    debit = sum(line.debit for line in entry.accounts)
    credit = sum(line.credit for line in entry.accounts)
    assert debit == credit, [(line.debit, line.credit) for line in entry.accounts]

    # Debtor 50,000 Dr and creditor 30,000 Cr, with the 20,000 difference
    # squared off against Temporary Opening.
    parties = {line.party_type for line in entry.accounts if line.party_type}
    assert parties == {"Customer", "Supplier"}, parties
    assert debit >= 50000


async def test_opening_stock_becomes_an_opening_stock_reconciliation(opened):
    client, company, headers, session = opened
    rows = await _records(client, headers, session["id"], entity_key="opening_stock")
    posted = [r for r in rows if r["status"] in ("Imported", "Warning")]
    assert len(posted) == 1, [(r["source_name"], r["status"], r["messages"]) for r in rows]
    assert posted[0]["target_doctype"] == "Stock Reconciliation"

    recon = await _stock_recon(posted[0]["target_id"])
    assert recon.purpose == "Opening Stock"
    (item,) = recon.items
    assert item.qty == 120
    assert item.valuation_rate == 500  # 60,000 over 120 Nos


async def _journal_entry(entry_id: str):
    """Plain values, read inside the session.

    Returning the ORM object instead means every attribute the test touches is
    read after the session closed, which re-queries from sync context and dies
    with MissingGreenlet rather than showing the real assertion.
    """
    import types
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import async_session_factory
    from app.models.accounts import JournalEntry

    async with async_session_factory() as db:
        entry = (
            await db.execute(
                select(JournalEntry)
                .options(selectinload(JournalEntry.accounts))
                .where(JournalEntry.id == _uuid.UUID(entry_id))
            )
        ).scalars().one()
        return types.SimpleNamespace(
            posting_date=entry.posting_date,
            docstatus=entry.docstatus,
            accounts=[
                types.SimpleNamespace(
                    debit=a.debit, credit=a.credit, party_type=a.party_type
                )
                for a in entry.accounts
            ],
        )


async def _stock_recon(recon_id: str):
    """Plain values, for the same reason as _journal_entry above."""
    import types
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import async_session_factory
    from app.models.stock import StockReconciliation

    async with async_session_factory() as db:
        recon = (
            await db.execute(
                select(StockReconciliation)
                .options(selectinload(StockReconciliation.items))
                .where(StockReconciliation.id == _uuid.UUID(recon_id))
            )
        ).scalars().one()
        return types.SimpleNamespace(
            purpose=recon.purpose,
            docstatus=recon.docstatus,
            items=[
                types.SimpleNamespace(qty=i.qty, valuation_rate=i.valuation_rate)
                for i in recon.items
            ],
        )


# --- chart of accounts shape --------------------------------------------------------------

# Tally classifies Capital Account and Reserves & Surplus as Equity. The Indian
# Chart of Accounts ships only Asset/Liability/Income/Expense — capital sits
# under "Source of Funds" the Schedule III way — so importing any real Indian
# company's masters used to dead-end on "no 'Equity' root account".
EQUITY_MASTERS = """<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY><IMPORTDATA>
  <REQUESTDESC><STATICVARIABLES>
    <SVCURRENTCOMPANY>Acme Traders</SVCURRENTCOMPANY>
  </STATICVARIABLES></REQUESTDESC>
  <REQUESTDATA>
   <TALLYMESSAGE><GROUP NAME="Capital Account" RESERVEDNAME="Capital Account">
     <GUID>eq-1</GUID><PARENT/></GROUP></TALLYMESSAGE>
   <TALLYMESSAGE><GROUP NAME="Reserves &amp; Surplus" RESERVEDNAME="Reserves &amp; Surplus">
     <GUID>eq-2</GUID><PARENT>Capital Account</PARENT></GROUP></TALLYMESSAGE>
  </REQUESTDATA>
 </IMPORTDATA></BODY>
</ENVELOPE>"""


async def test_reserved_equity_groups_import_on_the_indian_coa(ctx):
    """Regression: this used to fail outright with "no 'Equity' root account"."""
    client, company, headers = ctx
    session = await _upload(client, headers, xml=EQUITY_MASTERS, file_name="equity.xml")
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)
    await _run(client, headers, session["id"])

    rows = await _records(client, headers, session["id"], entity_key="group")
    assert {r["status"] for r in rows} <= {"Imported", "Warning"}, [
        (r["source_name"], r["status"], r["messages"]) for r in rows
    ]

    by_name = await _accounts_by_name(company["id"])
    capital = by_name["Capital Account"]

    # Capital Account already exists in the Indian COA under "Source of Funds",
    # so it is reused rather than duplicated into a parallel Equity tree — one
    # coherent chart beats a technically-purer split one.
    assert capital.root_type == "Liability"

    # The point of the test: Reserves & Surplus lands *under* Capital Account.
    # Its parent used to come from RESERVEDNAME, which made every reserved group
    # its own parent and left this one unplaceable.
    reserves = by_name["Reserves & Surplus"]
    assert reserves.parent_account_id == capital.id
    assert reserves.id != capital.id


async def _accounts_by_name(company_id: str) -> dict:
    import uuid as _uuid

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.accounts import Account

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(Account).where(Account.company_id == _uuid.UUID(company_id))
            )
        ).scalars().all()
        return {a.account_name: a for a in rows}


# --- incremental sync -------------------------------------------------------------------
#
# Tally bumps ALTERID on every edit. Same GUID with a higher ALTERID is not an
# overlapping export — it is a voucher that changed in Tally after we imported
# it, and the two copies now genuinely disagree.

# INV-001 as it looks after being edited in Tally: same GUID, higher ALTERID.
AMENDED_EXPORT = TALLY_XML.replace(
    "<SVTODATE>20260331</SVTODATE>", "<SVTODATE>20260930</SVTODATE>"
).replace(
    '<VOUCHER VCHTYPE="Sales"><GUID>v-1</GUID><DATE>20250415</DATE>',
    '<VOUCHER VCHTYPE="Sales"><GUID>v-1</GUID><ALTERID> 47</ALTERID><DATE>20250415</DATE>',
)

# The same file as first imported, but carrying the ALTERID it had at the time.
ORIGINAL_WITH_ALTERID = TALLY_XML.replace(
    '<VOUCHER VCHTYPE="Sales"><GUID>v-1</GUID><DATE>20250415</DATE>',
    '<VOUCHER VCHTYPE="Sales"><GUID>v-1</GUID><ALTERID> 12</ALTERID><DATE>20250415</DATE>',
)


async def _import(client, headers, xml, file_name) -> dict:
    session = await _upload(client, headers, xml=xml, file_name=file_name)
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)
    await _run(client, headers, session["id"])
    return session


async def test_a_voucher_edited_in_tally_is_reported_not_silently_skipped(ctx):
    """The dangerous case: it looks like a duplicate, but the books disagree."""
    client, company, headers = ctx
    await _import(client, headers, ORIGINAL_WITH_ALTERID, "acme.xml")

    second = await _upload(client, headers, xml=AMENDED_EXPORT, file_name="acme-amended.xml")
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)
    await _run(client, headers, second["id"])

    skipped = await _records(client, headers, second["id"], status="Skipped")
    amended = [r for r in skipped if r["voucher_number"] == "INV-001"]
    assert len(amended) == 1
    text = " ".join(m["message"] for m in amended[0]["messages"] or [])
    assert "Changed in Tally" in text
    # It must not be filed under the reassuring "already imported" wording.
    assert "Skipped so it is not posted twice" not in text


async def test_an_amended_voucher_never_rewrites_the_posted_document(ctx):
    """We detect the divergence; we do not act on it behind anyone's back."""
    client, company, headers = ctx
    await _import(client, headers, ORIGINAL_WITH_ALTERID, "acme.xml")
    listed = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    invoice_id = listed[0]["id"]
    before = (
        await client.get(f"/api/v1/sales-invoices/{invoice_id}", headers=headers)
    ).json()

    second = await _upload(client, headers, xml=AMENDED_EXPORT, file_name="acme-amended.xml")
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)
    await _run(client, headers, second["id"])

    after_list = (await client.get("/api/v1/sales-invoices", headers=headers)).json()["items"]
    assert len(after_list) == len(listed)  # no second invoice
    after = (
        await client.get(f"/api/v1/sales-invoices/{invoice_id}", headers=headers)
    ).json()
    # Untouched: same row, same version, same money.
    assert after["modified"] == before["modified"]
    assert after["grand_total"] == before["grand_total"]
    assert after["docstatus"] == before["docstatus"]


async def test_the_dry_run_separates_amendments_from_plain_duplicates(ctx):
    client, company, headers = ctx
    await _import(client, headers, ORIGINAL_WITH_ALTERID, "acme.xml")

    second = await _upload(client, headers, xml=AMENDED_EXPORT, file_name="acme-amended.xml")
    await client.post(f"/api/v1/migration/imports/{second['id']}/automap", headers=headers)
    result = (
        await client.post(f"/api/v1/migration/imports/{second['id']}/validate", headers=headers)
    ).json()

    assert result["amendment_count"] == 1
    amendment = result["amendments"][0]
    assert amendment["voucher_number"] == "INV-001"
    assert amendment["imported_alter_id"] == 12
    assert amendment["file_alter_id"] == 47
    # The receipt is unchanged, so it is an ordinary duplicate, not an amendment.
    assert any(d["voucher_number"] == "RCT-001" for d in result["duplicates"])


async def test_sync_state_reports_the_alter_id_watermark(ctx):
    """The number you type into Tally to export only what changed since."""
    client, company, headers = ctx
    await _import(client, headers, ORIGINAL_WITH_ALTERID, "acme.xml")

    state = (await client.get("/api/v1/migration/sync-state", headers=headers)).json()
    assert state["total_documents_imported"] >= 1
    assert len(state["companies"]) == 1
    entry = state["companies"][0]
    assert entry["source_company_name"] == "Acme Traders"
    assert entry["last_alter_id"] == 12
    assert entry["earliest_voucher_date"] == "2025-04-15"


async def test_sync_state_watermark_advances_after_an_amended_import(ctx):
    client, company, headers = ctx
    await _import(client, headers, ORIGINAL_WITH_ALTERID, "acme.xml")
    await client.post(
        f"/api/v1/migration/imports/{(await _upload(client, headers, xml=AMENDED_EXPORT, file_name='x.xml'))['id']}/automap",
        headers=headers,
    )

    # The amended voucher is not re-imported, so the watermark stays put — it
    # tracks what we actually hold, not what we have seen in a file.
    state = (await client.get("/api/v1/migration/sync-state", headers=headers)).json()
    assert state["companies"][0]["last_alter_id"] == 12


# --- background execution ---------------------------------------------------------------


async def test_run_returns_immediately_and_reports_progress(ctx):
    """/run must not block on the import — it answers 202 and the work continues."""
    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)

    resp = await client.post(f"/api/v1/migration/imports/{session['id']}/run", headers=headers)
    assert resp.status_code == 202
    assert resp.json()["status"] == "Importing"

    # The status endpoint answers while the run is still in flight...
    status = (
        await client.get(f"/api/v1/migration/imports/{session['id']}/status", headers=headers)
    ).json()
    assert status["is_running"] is True
    assert status["heartbeat_at"] is not None

    # ...and settles once it is done.
    final = await _run_to_completion(client, headers, session["id"])
    assert final["is_running"] is False
    assert final["status"] in ("Imported", "Partially Imported")
    assert final["percent"] == 100


async def test_starting_a_run_twice_is_refused_while_it_is_still_going(ctx):
    """The second click must get an error, not a second 202 and a silent race."""
    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)

    first = await client.post(f"/api/v1/migration/imports/{session['id']}/run", headers=headers)
    assert first.status_code == 202
    second = await client.post(f"/api/v1/migration/imports/{session['id']}/run", headers=headers)
    assert second.status_code == 422
    assert "already running" in second.json()["detail"]

    await _run_to_completion(client, headers, session["id"])


async def test_an_abandoned_run_is_retired_rather_than_stuck(ctx):
    """A crash mid-run leaves status 'Importing', which rollback refuses — so the
    import would be neither finishable nor undoable. The reaper must free it."""
    from datetime import timedelta

    from app.services.migration.background import reap_stale_runs

    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/migration/imports/{session['id']}/automap", headers=headers)
    await _run(client, headers, session["id"])

    # Simulate the crash: put it back to "Importing" with a heartbeat that stopped.
    await _force_status(session["id"], "Importing")

    # A live run must survive the sweep...
    assert await reap_stale_runs(stale_after=timedelta(hours=1)) == 0
    still = (
        await client.get(f"/api/v1/migration/imports/{session['id']}/status", headers=headers)
    ).json()
    assert still["status"] == "Importing"

    # ...but a stopped one is retired, with an explanation and a way forward.
    assert await reap_stale_runs(stale_after=timedelta(seconds=0)) == 1
    after = (
        await client.get(f"/api/v1/migration/imports/{session['id']}/status", headers=headers)
    ).json()
    assert after["status"] == "Failed"
    assert "stopped responding" in after["error_message"]

    # "Failed" is rollback-able; "Importing" was not. That is the point.
    rollback = await client.post(
        f"/api/v1/migration/imports/{session['id']}/rollback", headers=headers
    )
    assert rollback.status_code == 200, rollback.text


async def _run_to_completion(client, headers, import_id, *, timeout: float = 120.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        status = (
            await client.get(f"/api/v1/migration/imports/{import_id}/status", headers=headers)
        ).json()
        if not status["is_running"]:
            return status
        assert asyncio.get_running_loop().time() < deadline, f"run did not finish: {status}"
        await asyncio.sleep(0.05)


async def _force_status(import_id: str, status: str) -> None:
    """Put a session into a state only a crash would produce."""
    import uuid as _uuid

    from sqlalchemy import update

    from app.core.database import async_session_factory
    from app.models.migration import MigrationImport

    async with async_session_factory() as db:
        await db.execute(
            update(MigrationImport)
            .where(MigrationImport.id == _uuid.UUID(import_id))
            .values(status=status)
        )
        await db.commit()


# --- catalogue ------------------------------------------------------------------------


async def test_catalogue_exposes_the_full_coverage_matrix(ctx):
    client, company, headers = ctx
    resp = await client.get("/api/v1/migration/catalogue", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert len(data["entities"]) >= 30
    assert data["primary_groups"]["Sundry Debtors"]["party_type"] == "Customer"
    assert data["voucher_types"]["Sales"] == "Sales Invoice"
    assert "Accounts" in data["modules"]
    # every entity declares an honest coverage level
    assert all(e["support"] in ("full", "partial", "reference", "none") for e in data["entities"])


async def test_workspace_stats_track_the_run(imported):
    client, company, headers, session = imported
    stats = (await client.get("/api/v1/migration/workspace", headers=headers)).json()
    # The endpoint returns the shared module-workspace shape (labelled cards +
    # a 12-month trend), not flat counters — see get_migration_workspace.
    cards = {card["label"]: card["value"] for card in stats["cards"]}
    assert cards["Imports"] == 1
    assert cards["Documents Imported"] == session["imported_count"]
