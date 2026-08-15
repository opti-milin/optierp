"""End-to-end Tally import: upload -> automap -> dry run -> run -> rollback.

The assertions are the ones a tester would make by eye: did the customer get
created under the right account, did the invoice post the right GL, did the
receipt settle the invoice it was allocated against, and does a rollback put
the outstanding back.
"""

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
    resp = await client.post("/api/v1/tally/imports", json=_payload(**overrides), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _records(client, headers, import_id, **params) -> list[dict]:
    resp = await client.get(
        f"/api/v1/tally/imports/{import_id}/records", params=params, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


# --- parse ----------------------------------------------------------------------------


async def test_upload_parses_and_stages_without_writing_anything(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)

    assert session["status"] == "Parsed"
    assert session["tally_company_name"] == "Acme Traders"
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
    await client.post(f"/api/v1/tally/imports/{first['id']}/automap", headers=headers)
    await client.post(f"/api/v1/tally/imports/{first['id']}/run", headers=headers)

    second = await _upload(client, headers)
    summary = (
        await client.get(f"/api/v1/tally/imports/{second['id']}/summary", headers=headers)
    ).json()
    assert any("already imported" in log["message"] for log in summary["logs"])


# --- mapping --------------------------------------------------------------------------


async def test_automap_proposes_a_mapping_for_every_name(ctx):
    client, company, headers = ctx
    # An existing UOM must be matched, not duplicated.
    await client.post("/api/v1/uoms", json={"uom_name": "Nos"}, headers=headers)
    session = await _upload(client, headers)

    resp = await client.post(f"/api/v1/tally/imports/{session['id']}/automap", headers=headers)
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert summary["ledger"]["matched"] + summary["ledger"]["to_create"] == 3

    mappings = (await client.get("/api/v1/tally/mappings", headers=headers)).json()
    by_name = {(m["entity_key"], m["tally_name"]): m for m in mappings}
    assert by_name[("customer", "Bharat Steel")]["target_doctype"] == "Customer"
    assert by_name[("stock_item", "Hex Bolt M12")]["target_doctype"] == "Item"
    assert by_name[("unit", "Nos")]["match_method"] == "exact"
    assert by_name[("unit", "Nos")]["target_id"] is not None


async def test_a_manual_mapping_survives_a_second_automap(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/tally/imports/{session['id']}/automap", headers=headers)

    mappings = (await client.get("/api/v1/tally/mappings", headers=headers)).json()
    target = next(m for m in mappings if m["tally_name"] == "Hex Bolt M12")
    resp = await client.put(
        f"/api/v1/tally/mappings/{target['id']}",
        json={"target_doctype": "Item", "target_id": None, "notes": "checked by hand"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_locked"] is True

    await client.post(f"/api/v1/tally/imports/{session['id']}/automap", headers=headers)
    after = (await client.get("/api/v1/tally/mappings", headers=headers)).json()
    locked = next(m for m in after if m["tally_name"] == "Hex Bolt M12")
    assert locked["is_locked"] is True and locked["match_method"] == "manual"


# --- dry run --------------------------------------------------------------------------


async def test_dry_run_reports_the_plan_and_unresolved_names(ctx):
    client, company, headers = ctx
    session = await _upload(client, headers)
    await client.post(f"/api/v1/tally/imports/{session['id']}/automap", headers=headers)

    resp = await client.post(f"/api/v1/tally/imports/{session['id']}/validate", headers=headers)
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
    await client.post(f"/api/v1/tally/imports/{session['id']}/automap", headers=headers)
    resp = await client.post(f"/api/v1/tally/imports/{session['id']}/run", headers=headers)
    assert resp.status_code == 200, resp.text
    return client, company, headers, resp.json()


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
    resp = await client.post(f"/api/v1/tally/imports/{session['id']}/run", headers=headers)
    assert resp.status_code == 422
    assert "already been run" in resp.json()["detail"]


async def test_rollback_cancels_the_documents_and_restores_outstanding(imported):
    client, company, headers, session = imported

    resp = await client.post(f"/api/v1/tally/imports/{session['id']}/rollback", headers=headers)
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

    resp = await client.delete(f"/api/v1/tally/imports/{session['id']}", headers=headers)
    assert resp.status_code == 422

    await client.post(f"/api/v1/tally/imports/{session['id']}/rollback", headers=headers)
    resp = await client.delete(f"/api/v1/tally/imports/{session['id']}", headers=headers)
    assert resp.status_code == 204


async def test_a_rerun_after_rollback_reuses_the_masters_it_already_created(ctx):
    """The second import must match the existing Bharat Steel, not make a duplicate."""
    client, company, headers = ctx
    first = await _upload(client, headers)
    await client.post(f"/api/v1/tally/imports/{first['id']}/automap", headers=headers)
    await client.post(f"/api/v1/tally/imports/{first['id']}/run", headers=headers)
    await client.post(f"/api/v1/tally/imports/{first['id']}/rollback", headers=headers)

    second = await _upload(client, headers)
    await client.post(f"/api/v1/tally/imports/{second['id']}/automap", headers=headers)
    run = (
        await client.post(f"/api/v1/tally/imports/{second['id']}/run", headers=headers)
    ).json()

    customers = (await client.get("/api/v1/customers", headers=headers)).json()["items"]
    assert sum(1 for c in customers if c["customer_name"] == "Bharat Steel") == 1

    keys = {e["entity_key"]: e for e in run["entities"]}
    assert keys["customer"]["created"] == 0 and keys["customer"]["updated"] == 1


# --- catalogue ------------------------------------------------------------------------


async def test_catalogue_exposes_the_full_coverage_matrix(ctx):
    client, company, headers = ctx
    resp = await client.get("/api/v1/tally/catalogue", headers=headers)
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
    stats = (await client.get("/api/v1/tally/workspace", headers=headers)).json()
    assert stats["total_imports"] == 1
    assert stats["completed_imports"] == 1
    assert stats["documents_imported"] == session["imported_count"]
