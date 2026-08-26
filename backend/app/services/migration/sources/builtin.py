"""The workbook shapes we ship knowing about.

Each profile below was written against a real export and is exercised by a
fixture cut from it, so "we support Zoho Books" means a specific file shape that
a test actually imports — not a claim.

Adding a source is adding a profile here. If the new shape needs something the
:data:`~.profiles.KINDS` table cannot express, that is a signal to extend the
normaliser deliberately rather than to bolt a special case onto one importer.
"""

from __future__ import annotations

from app.services.migration.sources.profiles import ChildSpec, SheetSpec, SourceProfile

# --------------------------------------------------------------------------------------
# Shared vocabulary
# --------------------------------------------------------------------------------------

#: Tally's reserved groups, as spelled in :data:`...catalogue.PRIMARY_GROUPS`. A
#: non-Tally source's own account vocabulary is translated into these, because
#: that is what decides root type, account type, and whether a ledger is also a
#: Customer or Supplier. Getting a party group wrong does not just mislabel an
#: account — it means no Customer record, so every invoice against it fails.
DEBTORS = "Sundry Debtors"
CREDITORS = "Sundry Creditors"
DUTIES = "Duties & Taxes"


def _c(**columns: str | tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Column map shorthand: ``_c(name="ledger name", parent=("group", "parent"))``."""
    return {
        key: (value,) if isinstance(value, str) else tuple(value)
        for key, value in columns.items()
    }


def _ref(sheet: str, reason: str) -> SheetSpec:
    """A sheet we read, count and report — but deliberately do not import."""
    return SheetSpec(sheet=sheet, kind="reference", reason=reason)


_NO_HR = (
    "Missing module: HR. There is no Employee, Salary Structure or Payroll Entry "
    "to receive these, so the rows are staged and counted rather than posted. The "
    "net payroll journal can still come across by mapping the voucher sheet as a "
    "Journal."
)
_SUMMARY = (
    "This is a report the source computed, not source data. OptiERP recomputes it "
    "from the vouchers being imported, so bringing it across would create a second "
    "answer that could disagree with the ledger."
)
_NO_LINES = (
    "This export ships order headers — party, number, date and totals — with no "
    "line-item sheet beside them. An order is what was ordered, so importing the "
    "totals alone would create a document nobody can fulfil or match a receipt "
    "against. Nothing is missing in OptiERP: Quotation, Sales Order and Purchase "
    "Order all exist and import fully. If your export does include the lines, map "
    "this sheet as an order and point it at that line sheet."
)
_ORTHOGONAL = (
    "Missing concept: a second, independent classification axis. OptiERP has one "
    "tree here, and folding two dimensions into it would make both wrong. The "
    "value is kept on the staging row so nothing is lost."
)


# --------------------------------------------------------------------------------------
# 1. Tally — full relational workbook dump
# --------------------------------------------------------------------------------------

TALLY_WORKBOOK = SourceProfile(
    key="tally_workbook",
    label="Tally — full workbook dump",
    app="Tally",
    signature=("Voucher_Headers", "Accounting_Lines", "Ledger_Master"),
    defaults={
        "output_cgst": "CGST Output",
        "output_sgst": "SGST Output",
        "output_igst": "IGST Output",
        "input_cgst": "CGST Input",
        "input_sgst": "SGST Input",
        "input_igst": "IGST Input",
        "round_off": "Round Off",
    },
    notes=(
        "The relational shape: masters on their own sheets, and each voucher a "
        "header row joined to its accounting, inventory, bill and bank lines. "
        "Closest to a Tally XML export — the double entry is carried across as "
        "the source wrote it, never rebuilt."
    ),
    sheets=(
        # --- masters ------------------------------------------------------------------
        SheetSpec(
            sheet="Groups", kind="group_master", entity="group",
            columns=_c(name="group name", parent="parent group", nature="nature"),
        ),
        SheetSpec(
            sheet="Ledger_Master", kind="ledger_master", entity="ledger",
            columns=_c(
                name="ledger name", key="ledger id", parent="group name",
                opening="opening balance", dr_cr="dr cr", gstin="gstin",
                state="state", state_code="state code", pan="pan",
                credit_days="credit days", bank_account_no="bank account no",
                ifsc="ifsc",
            ),
        ),
        SheetSpec(
            sheet="Units", kind="simple_master", entity="unit",
            columns=_c(
                name="symbol", description="formal name", decimals="decimal places",
            ),
        ),
        SheetSpec(
            sheet="Stock_Groups", kind="simple_master", entity="stock_group",
            columns=_c(name="stock group name", parent="parent group"),
        ),
        SheetSpec(
            sheet="Stock_Items", kind="item_master", entity="stock_item",
            columns=_c(
                name="item name", parent="stock group", category="category",
                hsn="hsn sac", uom="base unit", gst_rate="gst rate",
                standard_rate="standard selling rate", purchase_rate="standard cost",
                opening_qty="opening qty", opening_value="opening value",
            ),
        ),
        SheetSpec(
            sheet="Godowns", kind="simple_master", entity="godown",
            columns=_c(name="godown name", parent="parent godown"),
        ),
        SheetSpec(
            sheet="Cost_Centres", kind="simple_master", entity="cost_centre",
            columns=_c(name="cost centre name", parent="parent centre"),
        ),
        SheetSpec(
            sheet="Opening_Balances", kind="opening_ledger", entity="opening_ledger",
            columns=_c(
                name="ledger name", opening="opening balance", dr_cr="dr cr",
                date="as of date",
            ),
        ),
        # --- vouchers -----------------------------------------------------------------
        SheetSpec(
            sheet="Voucher_Headers",
            kind="voucher_relational",
            key="voucher id",
            columns=_c(
                key="voucher id", number="voucher number", date="voucher date",
                voucher_type="voucher type", party="party ledger",
                party_gstin="party gstin", reference="reference number",
                place_of_supply="place of supply", cost_centre="cost centre",
                godown="godown", narration="narration", currency="currency",
                exchange_rate="exchange rate", sub_total="gross amount",
                tax_total="tax amount", total="net amount", status="status",
                is_cancelled="is cancelled", is_optional="is optional",
            ),
            children=(
                ChildSpec(
                    sheet="Accounting_Lines", role="ledger_lines", key="voucher id",
                    columns=_c(
                        key="voucher id", ledger="ledger name", dr_cr="dr cr",
                        amount="amount", cost_centre="cost centre",
                        bill_ref="bill ref", due_date="due date", narration="narration",
                    ),
                ),
                ChildSpec(
                    sheet="Inventory_Lines", role="item_lines", key="voucher id",
                    columns=_c(
                        key="voucher id", item="item name", godown="godown",
                        batch="batch no", expiry="expiry date", qty="quantity",
                        uom="unit", rate="rate", discount_pct="discount pct",
                        taxable="taxable value", cgst="cgst", sgst="sgst",
                        igst="igst", amount="line total",
                    ),
                ),
                ChildSpec(
                    sheet="Bill_Allocations", role="bill_allocations", key="voucher id",
                    columns=_c(
                        key="voucher id", party="party ledger", bill_ref="bill ref",
                        alloc_type="allocation type", amount="amount", due_date="due date",
                    ),
                ),
                ChildSpec(
                    sheet="Bank_Allocations", role="bank_allocations", key="voucher id",
                    columns=_c(
                        key="voucher id", bank="bank ledger", instrument_no="instrument no",
                        instrument_date="instrument date", txn_type="transaction type",
                        bank_date="bank date", amount="amount", reference="bank reference",
                    ),
                ),
            ),
        ),
        # --- read, counted, not imported ----------------------------------------------
        _ref("Company", "Deliberate, not a gap: the company already exists — you "
             "created it, with its own GSTIN, PAN and fiscal year, before starting "
             "this import. Overwriting that from a file would silently repoint "
             "every document already in the books."),
        _ref("Stock_Categories", _ORTHOGONAL + " Tally categories cut across the item "
             "group tree; the category is written to Item.brand when the item has none."),
        _ref("Cost_Categories", _ORTHOGONAL + " Tally cost categories cut across the "
             "Cost Center tree, and a cost centre belongs to exactly one of them. "
             "The category is readable on the staging row; it is not written onto "
             "the Cost Center, which has no column for it."),
        SheetSpec(
            sheet="Tax_Masters", kind="tax_master", entity="tax_template",
            columns=_c(
                name="tax name", rate="rate", tax_type="tax type",
                input_account="input ledger", output_account="output ledger",
            ),
        ),
        _ref("Voucher_Types", "Nothing to import: a user-defined type like 'Cash "
             "Sales' is imported as whatever reserved type it is built on, which "
             "this sheet is only used to look up. The mapping is editable before "
             "the run."),
        _ref("Employees", _NO_HR),
        _ref("Pay_Heads", _NO_HR),
        _ref("Payroll_Lines", _NO_HR),
        _ref("Attendance_Lines", _NO_HR),
        SheetSpec(
            sheet="Budgets", kind="budget_master", entity="budget",
            columns=_c(
                name="budget name", account="ledger or group", amount="budget amount",
                from_date="period from", to_date="period to",
            ),
        ),
        _ref("Scenarios", "Missing concept: provisional 'what-if' books layered "
             "over the real ones. OptiERP has one set of books, so a scenario has "
             "nowhere to live without changing what the ledger means."),
        SheetSpec(
            sheet="Fixed_Assets", kind="asset_master", entity="asset",
            columns=_c(
                name="asset name", code="asset id", category="asset group",
                purchase_date="purchase date", cost="original cost",
                accumulated="accumulated depreciation", wdv="wdv opening",
                method="depreciation method", rate="rate pct",
            ),
        ),
        _ref("Orders", "Redundant, not missing: this sheet is an index of order "
             "numbers with no item lines. The orders themselves arrive in full "
             "from Voucher_Headers, where their inventory lines are."),
        _ref("Batch_Movement", "Redundant, not missing: every movement here is "
             "already an inventory line on a voucher being imported, and the "
             "Stock Ledger is rebuilt from those."),
        _ref("Bank_Reconciliation", "This is Tally's reconciliation *state* for "
             "vouchers this same file imports, not a bank statement. The bank "
             "dates arrive on the vouchers themselves through Bank_Allocations; "
             "an actual statement export maps to the bank-statement shape."),
        _ref("GST_Summary", _SUMMARY),
        _ref("Outstanding_Bills", "Redundant, not missing: ageing is derived from "
             "the invoices and their bill references, both of which are imported. "
             "Importing a second copy would let the two disagree."),
        _ref("README", "Documentation sheet: prose for a human, no records."),
    ),
)


# --------------------------------------------------------------------------------------
# 2. Tally — flat one-row-per-voucher sheet
# --------------------------------------------------------------------------------------

TALLY_FLAT = SourceProfile(
    key="tally_flat",
    label="Tally — flat transaction sheet",
    app="Tally",
    signature=("Transactions",),
    notes=(
        "One row per voucher line, with the two sides named in Debit_Ledger and "
        "Credit_Ledger. Rows sharing a voucher number are folded back into one "
        "document, so a three-item invoice split over three rows imports as one "
        "invoice with three lines rather than three invoices."
    ),
    defaults={
        # Tally's own spelling, so these line up with the ledgers a full Tally
        # export defines. A flat sheet names no GST ledgers at all — it carries
        # the amounts in columns — so these accounts are created on the way in.
        "output_cgst": "CGST Output",
        "output_sgst": "SGST Output",
        "output_igst": "IGST Output",
        "input_cgst": "CGST Input",
        "input_sgst": "SGST Input",
        "input_igst": "IGST Input",
        "sales": "Sales Account",
        "purchases": "Purchase Account",
        "round_off": "Round Off",
    },
    sheets=(
        SheetSpec(
            sheet="Transactions",
            kind="voucher_flat",
            key="voucher number",
            columns=_c(
                key="voucher number", number="voucher number", date="voucher date",
                voucher_type="voucher type", party="party ledger name",
                party_gstin="party gstin", reference="reference number",
                place_of_supply="party state", narration="narration",
                cost_centre="cost centre", sub_total="taxable value",
                cgst="cgst amount", sgst="sgst amount", igst="igst amount",
                total="voucher total", debit_ledger="debit ledger",
                credit_ledger="credit ledger", bank_ledger="bank cash ledger",
                item="stock item name", qty="quantity", uom="unit", rate="rate",
                hsn="hsn sac",
            ),
        ),
        SheetSpec(
            sheet="Ledger_Master", kind="ledger_master", entity="ledger",
            columns=_c(
                name="ledger name", parent="ledger group", gstin="gstin",
                state="state", opening="opening balance", dr_cr="balance type",
            ),
        ),
        SheetSpec(
            sheet="Stock_Items", kind="item_master", entity="stock_item",
            columns=_c(
                name="stock item name", code="item code", hsn="hsn sac", uom="unit",
                gst_rate="gst rate", standard_rate="standard rate",
            ),
        ),
        _ref("Migration_Notes", "Documentation sheet: prose for a human, no records."),
    ),
)


# --------------------------------------------------------------------------------------
# 3. Zoho Books — full backup export
# --------------------------------------------------------------------------------------

#: Zoho types an account by its balance-sheet role. Tally types it by which
#: reserved group it sits under, and that choice decides far more here (root
#: type, whether GST is claimable, whether a ledger is also a party), so the
#: translation has to be explicit rather than guessed from the name.
ZOHO_ACCOUNT_TYPES = {
    "cash": "Cash-in-Hand",
    "bank": "Bank Accounts",
    "accounts_receivable": DEBTORS,
    "accounts_payable": CREDITORS,
    "stock": "Stock-in-Hand",
    "other_current_asset": "Current Assets",
    "other_asset": "Current Assets",
    "fixed_asset": "Fixed Assets",
    "other_current_liability": "Current Liabilities",
    "other_liability": "Current Liabilities",
    "long_term_liability": "Loans (Liability)",
    "credit_card": "Current Liabilities",
    "equity": "Capital Account",
    "income": "Sales Accounts",
    "other_income": "Indirect Incomes",
    "cost_of_goods_sold": "Purchase Accounts",
    "expense": "Indirect Expenses",
    "other_expense": "Indirect Expenses",
}

ZOHO_BOOKS = SourceProfile(
    key="zoho_books",
    label="Zoho Books — full backup",
    app="Zoho Books",
    signature=("Chart_of_Accounts", "Invoices", "Invoice_Line_Items"),
    account_types=ZOHO_ACCOUNT_TYPES,
    # Zoho files its GST accounts under a parent called "Taxes" while typing them
    # as ordinary current assets and liabilities. Reading only the type would
    # import them as plain balance-sheet accounts, and every invoice's GST would
    # then post outside Duties & Taxes — right total, wrong GST returns.
    account_parents={"taxes": DUTIES, "duties and taxes": DUTIES},
    party_types={"customer": DEBTORS, "vendor": CREDITORS, "supplier": CREDITORS},
    defaults={
        "output_cgst": "Output CGST",
        "output_sgst": "Output SGST",
        "output_igst": "Output IGST",
        "input_cgst": "Input CGST",
        "input_sgst": "Input SGST",
        "input_igst": "Input IGST",
        "sales": "Sales",
        "purchases": "Purchases",
        "discount": "Discount Allowed",
        "tds": "TDS Payable",
        "round_off": "Round Off",
    },
    notes=(
        "Zoho ships documents rather than journals — an invoice knows its "
        "customer, its lines and its tax, but not its double entry. Those "
        "postings are rebuilt from the document's own totals and checked to "
        "balance before the row is staged, so nothing reaches the ledgers on a "
        "guess."
    ),
    sheets=(
        # --- masters ------------------------------------------------------------------
        SheetSpec(
            sheet="Chart_of_Accounts", kind="ledger_master", entity="ledger",
            columns=_c(
                name="account name", parent="parent account", account_type="account type",
                opening="opening balance", dr_cr="opening balance type",
                currency="currency code", is_active="is active",
            ),
        ),
        SheetSpec(
            sheet="Contacts", kind="ledger_master", entity="ledger",
            columns=_c(
                name="contact name", key="contact id", party_type="contact type",
                company_name="company name", gstin="gstin", pan="pan no",
                tax_category="gst treatment", payment_terms="payment terms",
                opening="opening balance", dr_cr="opening balance type",
                currency="currency code", is_active="is active",
            ),
        ),
        SheetSpec(
            sheet="Contact_Addresses", kind="party_address", entity="address",
            columns=_c(
                party="contact id", address_type="address type",
                attention="attention", line1="address", city="city", state="state",
                state_code="state code", pincode="zip", country="country",
                phone="phone",
            ),
        ),
        SheetSpec(
            sheet="Contact_Persons", kind="party_contact", entity="contact",
            columns=_c(
                party="contact id", salutation="salutation", first_name="first name",
                last_name="last name", email="email", phone="phone", mobile="mobile",
                is_primary="is primary contact",
            ),
        ),
        SheetSpec(
            sheet="Payment_Terms", kind="payment_terms", entity="payment_terms",
            columns=_c(name="payment term name", credit_days="number of days"),
        ),
        SheetSpec(
            sheet="Taxes", kind="tax_master", entity="tax_template",
            columns=_c(
                name="tax name", rate="tax percentage", tax_type="tax specific type",
                is_default="is default tax",
            ),
        ),
        SheetSpec(
            sheet="Currencies", kind="currency_master", entity="currency",
            columns=_c(
                code="currency code", name="currency name", symbol="symbol",
                rate="exchange rate", is_base="is base currency",
            ),
        ),
        SheetSpec(
            sheet="Price_Lists", kind="price_list_master", entity="price_list",
            columns=_c(
                name="name", applies_to="type", description="description",
            ),
        ),
        SheetSpec(
            sheet="Bank_Accounts", kind="bank_account", entity="bank_account",
            columns=_c(
                name="account name", key="bank account id", bank="bank name",
                account_no="account number", ifsc="ifsc", currency="currency code",
                opening="opening balance",
            ),
        ),
        SheetSpec(
            sheet="Items", kind="item_master", entity="stock_item",
            columns=_c(
                name="name", code="sku", hsn="hsn or sac", uom="unit",
                gst_rate="tax percentage", standard_rate="sales rate",
                purchase_rate="purchase rate", opening_qty="opening stock",
                opening_value="opening stock value", is_active="status",
                income_account="sales account", expense_account="purchase account",
            ),
        ),
        SheetSpec(
            sheet="Warehouses", kind="simple_master", entity="godown",
            columns=_c(name="warehouse name", description="address"),
        ),
        SheetSpec(
            sheet="Opening_Balances", kind="opening_ledger", entity="opening_ledger",
            columns=_c(
                name="account name", opening="opening balance",
                dr_cr="opening balance type", date="migration date",
            ),
        ),
        # --- documents ----------------------------------------------------------------
        SheetSpec(
            sheet="Invoices", kind="document", key="invoice id",
            constants={"voucher_type": "Sales"},
            columns=_c(
                key="invoice id", number="invoice number", date="date",
                due_date="due date", party="customer name", reference="reference number",
                place_of_supply="place of supply", currency="currency code",
                exchange_rate="exchange rate", sub_total="sub total",
                tax_total="tax total", discount="discount total",
                round_off="adjustment", total="total", status="status",
                narration="notes",
            ),
            children=(
                ChildSpec(
                    sheet="Invoice_Line_Items", role="item_lines", key="invoice id",
                    columns=_c(
                        key="invoice id", item="item name", description="description",
                        qty="quantity", uom="unit", rate="rate",
                        discount_pct="discount pct", taxable="taxable amount",
                        cgst="cgst", sgst="sgst", igst="igst", amount="line total",
                        account="account name",
                    ),
                ),
            ),
        ),
        SheetSpec(
            sheet="Bills", kind="document", key="bill id",
            constants={"voucher_type": "Purchase"},
            columns=_c(
                key="bill id", number="bill number", date="date", due_date="due date",
                party="vendor name", reference="reference number",
                place_of_supply="place of supply", currency="currency code",
                sub_total="sub total", tax_total="tax total", tds="tds amount",
                total="total", status="status", narration="notes",
            ),
            children=(
                ChildSpec(
                    sheet="Bill_Line_Items", role="item_lines", key="bill id",
                    columns=_c(
                        key="bill id", item="item name", qty="quantity", uom="unit",
                        rate="rate", taxable="taxable amount", cgst="cgst",
                        sgst="sgst", igst="igst", amount="line total",
                        account="account name",
                    ),
                ),
            ),
        ),
        SheetSpec(
            sheet="Credit_Notes", kind="document", key="creditnote id",
            constants={"voucher_type": "Credit Note"},
            columns=_c(
                key="creditnote id", number="creditnote number", date="date",
                party="customer name", reference="reference number",
                sub_total="sub total", tax_total="tax total", total="total",
                status="status", narration="reason",
            ),
        ),
        SheetSpec(
            sheet="Vendor_Credits", kind="document", key="vendor credit id",
            constants={"voucher_type": "Debit Note"},
            columns=_c(
                key="vendor credit id", number="vendor credit number", date="date",
                party="vendor name", reference="reference number",
                sub_total="sub total", tax_total="tax total", total="total",
                status="status", narration="reason",
            ),
        ),
        # --- payments -----------------------------------------------------------------
        SheetSpec(
            sheet="Customer_Payments", kind="payment", key="payment id",
            constants={"voucher_type": "Receipt"},
            columns=_c(
                key="payment id", number="payment number", date="date",
                party="customer name", bank=("bank account", "paid through"),
                mode="payment mode", reference="reference number", amount="amount",
                unused="unused amount", narration="notes",
            ),
            children=(
                ChildSpec(
                    sheet="Customer_Payment_Applications", role="bill_allocations",
                    key="payment id",
                    constants={"alloc_type": "Agst Ref"},
                    columns=_c(
                        key="payment id", bill_ref="invoice number",
                        amount="amount applied",
                    ),
                ),
            ),
        ),
        SheetSpec(
            sheet="Vendor_Payments", kind="payment", key="vendor payment id",
            constants={"voucher_type": "Payment"},
            columns=_c(
                key="vendor payment id", number="payment number", date="date",
                party="vendor name", bank=("paid through", "bank account"),
                mode="payment mode", reference="reference number", amount="amount",
                narration="notes",
            ),
            children=(
                ChildSpec(
                    sheet="Vendor_Payment_Applications", role="bill_allocations",
                    key="vendor payment id",
                    constants={"alloc_type": "Agst Ref"},
                    columns=_c(
                        key="vendor payment id", bill_ref="bill number",
                        amount="amount applied",
                    ),
                ),
            ),
        ),
        # --- journals, transfers, expenses, stock -------------------------------------
        SheetSpec(
            sheet="Journals", kind="journal", key="journal id",
            columns=_c(
                key="journal id", number="journal number", date="date",
                reference="reference number", narration="notes", status="status",
            ),
            children=(
                ChildSpec(
                    sheet="Journal_Lines", role="ledger_lines", key="journal id",
                    columns=_c(
                        key="journal id", ledger="account name", debit="debit",
                        credit="credit", narration="description",
                    ),
                ),
            ),
        ),
        SheetSpec(
            sheet="Bank_Transfers", kind="transfer",
            columns=_c(
                number="reference number", date="date", from_account="from account",
                to_account="to account", amount="amount", narration="description",
            ),
        ),
        SheetSpec(
            sheet="Expenses", kind="expense",
            columns=_c(
                number="reference number", date="date", account="expense account",
                bank="paid through", party="vendor name", amount="amount",
                tax_total="tax amount", total="total", narration="notes",
            ),
        ),
        SheetSpec(
            sheet="Bank_Transactions", kind="bank_transaction", entity="bank_transaction",
            columns=_c(
                bank="bank account id", number="bank transaction id", date="date",
                amount="amount", dr_cr="debit credit", txn_type="transaction type",
                description="description", reference="reference number",
                status="matched status",
            ),
        ),
        SheetSpec(
            sheet="Inventory_Adjustments", kind="stock_adjustment",
            columns=_c(
                number="reference number", date="date", item="item name",
                qty="quantity adjusted", value="value adjusted", godown="warehouse id",
                account="account name", narration="reason",
            ),
        ),
        # --- read, counted, not imported ----------------------------------------------
        _ref("Organization", "Deliberate, not a gap: the company already exists — you "
             "created it, with its own GSTIN, PAN and fiscal year, before starting "
             "this import. Overwriting that from a file would silently repoint "
             "every document already in the books."),
        _ref("Projects", "Missing module: Projects. There is no Project to attach "
             "costs, budgets or time to, so the project id on invoices and "
             "expenses is kept on the staging row and dropped from the document."),
        _ref("Project_Tasks", "Missing module: Projects. A task has no parent to "
             "hang off until there is a Project."),
        _ref("Time_Entries", "Missing module: Timesheets. Billable hours have "
             "nowhere to be recorded and nothing to bill from, so importing them "
             "would produce rows nothing can read."),
        _ref("Users", "Deliberate, not a gap: Users exist in OptiERP, but a login "
             "is an access decision, not accounting data. Importing one would "
             "create an account nobody chose to grant, with roles guessed from "
             "another system's vocabulary. Invite them from Settings instead."),
        _ref("Reporting_Tags", _ORTHOGONAL + " Zoho tags cut across the Cost Center "
             "tree, and a transaction can carry several of them at once."),
        _ref("Quotes", _NO_LINES),
        _ref("Sales_Orders", _NO_LINES),
        _ref("Purchase_Orders", _NO_LINES),
        _ref("Retainer_Invoices", "Missing concept: an invoice for an advance held "
             "against no specific bill. The money itself is not lost — the "
             "matching customer payment imports as a Payment Entry with its "
             "unallocated amount intact, which is what the ledger cares about."),
        _ref("Recurring_Invoices", "Missing concept: a profile that *generates* "
             "future documents. This is a rule, not a transaction, and the "
             "invoices it has already raised are in the Invoices sheet and do "
             "import. Recreate the schedule under Subscriptions after the move."),
        _ref("Recurring_Expenses", "Missing concept: a profile that generates "
             "future documents. The expenses it has already raised are in the "
             "Expenses sheet and do import."),
        _ref("Audit_Trail", "Deliberate, not a gap: OptiERP writes its own audit "
             "log, and every row it holds is one it saw happen. Importing another "
             "system's history would mean recording changes made by users who do "
             "not exist here, at times nothing in these books can corroborate — "
             "which is precisely what an audit trail must never contain. Keep the "
             "Zoho export as your record of the period before the move."),
        _ref("Transaction_Counts", _SUMMARY),
        _ref("README", "Documentation sheet: prose for a human, no records."),
    ),
)


# --------------------------------------------------------------------------------------
# 4. The OptiERP template
# --------------------------------------------------------------------------------------

#: Sheet the generated template stamps with its version, so a filled template is
#: recognised outright instead of scored against the other profiles.
TEMPLATE_MARKER_SHEET = "_OptiERP"

OPTIERP_TEMPLATE = SourceProfile(
    key="optierp_template",
    label="OptiERP import template",
    app="OptiERP Template",
    signature=(TEMPLATE_MARKER_SHEET,),
    defaults={
        "output_cgst": "Output CGST",
        "output_sgst": "Output SGST",
        "output_igst": "Output IGST",
        "input_cgst": "Input CGST",
        "input_sgst": "Input SGST",
        "input_igst": "Input IGST",
        "round_off": "Round Off",
    },
    notes=(
        "The shape OptiERP asks for when nobody has profiled your application. "
        "Download it, paste your data in, upload it back — no mapping step."
    ),
    sheets=(
        SheetSpec(
            sheet="Account_Groups", kind="group_master", entity="group",
            columns=_c(
                name="group name", parent="parent group",
                nature="nature",
            ),
        ),
        SheetSpec(
            sheet="Accounts", kind="ledger_master", entity="ledger",
            columns=_c(
                name="account name", key="party id", parent="parent group",
                opening="opening balance", dr_cr="dr cr", gstin="gstin", pan="pan",
                state="state", state_code="state code", email="email", phone="phone",
                address="address", city="city", pincode="pincode",
                credit_days="credit days", credit_limit="credit limit",
                currency="currency", party_type="party type",
                company_name="registered name", entity_kind="company or individual",
                payment_terms="payment terms", tax_category="tax category",
                party_group="party group", territory="territory",
                bank_account_no="bank account no", ifsc="ifsc", notes="notes",
                is_active="active",
            ),
        ),
        SheetSpec(
            sheet="Payment_Terms", kind="payment_terms", entity="payment_terms",
            columns=_c(
                name="term name", credit_days="days", portion="portion pct",
                description="description",
            ),
        ),
        SheetSpec(
            sheet="Addresses", kind="party_address", entity="address",
            columns=_c(
                party="party", title="address title", address_type="address type",
                attention="attention", line1="address line 1", line2="address line 2",
                city="city", state="state", pincode="pincode", country="country",
                phone="phone",
            ),
        ),
        SheetSpec(
            sheet="Contacts", kind="party_contact", entity="contact",
            columns=_c(
                party="party", salutation="salutation", first_name="first name",
                last_name="last name", email="email", phone="phone", mobile="mobile",
                designation="designation", is_primary="primary",
            ),
        ),
        SheetSpec(
            sheet="Bank_Accounts", kind="bank_account", entity="bank_account",
            columns=_c(
                name="account name", key="bank id", bank="bank name",
                account_no="account number", ifsc="ifsc", ledger="ledger account",
                currency="currency", opening="opening balance",
            ),
        ),
        SheetSpec(
            sheet="Bank_Statement", kind="bank_transaction", entity="bank_transaction",
            columns=_c(
                bank="bank account", date="date", amount="amount",
                dr_cr="deposit or withdrawal", reference="reference number",
                description="description", number="statement line id",
            ),
        ),
        SheetSpec(
            sheet="Tax_Rates", kind="tax_master", entity="tax_template",
            columns=_c(
                name="tax name", rate="rate pct", applies_to="sales or purchase",
                tax_type="tax type", account="tax account",
            ),
        ),
        SheetSpec(
            sheet="Currencies", kind="currency_master", entity="currency",
            columns=_c(code="iso code", name="currency name", rate="exchange rate"),
        ),
        SheetSpec(
            sheet="Price_Lists", kind="price_list_master", entity="price_list",
            columns=_c(
                name="price list name", applies_to="selling or buying",
                currency="currency", description="description",
            ),
        ),
        SheetSpec(
            sheet="Budgets", kind="budget_master", entity="budget",
            columns=_c(
                name="budget name", account="account", amount="budget amount",
                from_date="period from", to_date="period to",
                cost_centre="cost centre",
            ),
        ),
        SheetSpec(
            sheet="Fixed_Assets", kind="asset_master", entity="asset",
            columns=_c(
                name="asset name", code="asset code", category="asset category",
                purchase_date="purchase date", cost="original cost",
                accumulated="accumulated depreciation", method="depreciation method",
                rate="depreciation rate pct", life="useful life years",
                location="location", custodian="custodian",
            ),
        ),
        SheetSpec(
            sheet="Items", kind="item_master", entity="stock_item",
            columns=_c(
                name="item name", code="item code", parent="item group", hsn="hsn sac",
                uom="unit", gst_rate="gst rate", standard_rate="selling rate",
                purchase_rate="purchase rate", description="description",
                income_account="income account", expense_account="expense account",
                opening_qty="opening quantity", opening_value="opening value",
            ),
        ),
        SheetSpec(
            sheet="Warehouses", kind="simple_master", entity="godown",
            columns=_c(name="warehouse name", parent="parent warehouse"),
        ),
        SheetSpec(
            sheet="Cost_Centres", kind="simple_master", entity="cost_centre",
            columns=_c(name="cost centre name", parent="parent cost centre"),
        ),
        SheetSpec(
            sheet="Opening_Balances", kind="opening_ledger", entity="opening_ledger",
            columns=_c(name="account name", opening="opening balance", dr_cr="dr cr"),
        ),
        SheetSpec(
            sheet="Opening_Stock", kind="opening_stock", entity="opening_stock",
            columns=_c(
                name="item name", qty="quantity", value="value", uom="unit",
                godown="warehouse",
            ),
        ),
        SheetSpec(
            sheet="Vouchers",
            kind="voucher_relational",
            key="voucher id",
            columns=_c(
                key="voucher id", number="voucher number", date="date",
                voucher_type="voucher type", party="party", reference="reference",
                place_of_supply="place of supply", narration="narration",
                cost_centre="cost centre", godown="warehouse", total="total",
                is_cancelled="cancelled", is_optional="provisional",
            ),
            children=(
                ChildSpec(
                    sheet="Voucher_Ledgers", role="ledger_lines", key="voucher id",
                    columns=_c(
                        key="voucher id", ledger="account", debit="debit",
                        credit="credit", cost_centre="cost centre",
                        bill_ref="bill reference", due_date="due date",
                    ),
                ),
                ChildSpec(
                    sheet="Voucher_Items", role="item_lines", key="voucher id",
                    columns=_c(
                        key="voucher id", item="item name", qty="quantity", uom="unit",
                        rate="rate", taxable="taxable value", cgst="cgst", sgst="sgst",
                        igst="igst", amount="line total", account="income expense account",
                        godown="warehouse", batch="batch",
                    ),
                ),
            ),
        ),
        SheetSpec(
            sheet="Orders",
            kind="order",
            key="order id",
            columns=_c(
                key="order id", number="order number", date="date",
                voucher_type="order type", party="party", due_date="expected date",
                reference="reference", narration="notes", total="total",
                status="status",
            ),
            children=(
                ChildSpec(
                    sheet="Order_Items", role="item_lines", key="order id",
                    columns=_c(
                        key="order id", item="item name", qty="quantity", uom="unit",
                        rate="rate", amount="line total", godown="warehouse",
                    ),
                ),
            ),
        ),
        _ref(TEMPLATE_MARKER_SHEET, "Template version marker."),
        _ref("README", "Documentation sheet: prose for a human, no records."),
    ),
)


BUILTIN_PROFILES: tuple[SourceProfile, ...] = (
    OPTIERP_TEMPLATE,
    TALLY_WORKBOOK,
    TALLY_FLAT,
    ZOHO_BOOKS,
)

BUILTIN_BY_KEY: dict[str, SourceProfile] = {p.key: p for p in BUILTIN_PROFILES}

#: What an unrecognised workbook starts from: nothing mapped, everything for the
#: tester to assign. Deliberately empty rather than a guess — a half-right
#: default mapping is harder to correct than a blank one.
CUSTOM = SourceProfile(
    key="custom",
    label="Custom mapping",
    app="Custom",
    notes="No built-in profile matched this workbook. Assign each sheet and column "
          "in the mapping step, then save it as a profile to reuse next time.",
    defaults=dict(OPTIERP_TEMPLATE.defaults),
)
