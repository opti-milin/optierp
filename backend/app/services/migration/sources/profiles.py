"""What a workbook's sheets and columns *mean*.

A profile is the answer to three questions, and nothing else:

1. Which sheet holds which kind of record? (``SheetSpec.sheet`` -> ``kind``)
2. Which column holds which field? (``SheetSpec.columns``)
3. When a header sheet has line sheets, what joins them? (``key`` + ``children``)

Everything structural lives in :data:`KINDS`. A ``kind`` is one of a deliberately
small set of shapes that :mod:`.normalise` knows how to fold into the IR — not an
open-ended scripting hook. That ceiling is the point: the mapping wizard can only
offer what the normaliser can actually build, so a tester is never allowed to
describe a workbook the importer will then fail on. An export whose shape is not
in this list needs reshaping into the OptiERP template, and the wizard says so
instead of half-importing it.

Built-in profiles cover the shapes we have seen. A company that maps an
unrecognised workbook saves its own as a ``MigrationSourceProfile`` row, which
deserialises back into exactly these dataclasses — so a saved profile and a
built-in one are the same thing to the normaliser.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from app.core.exceptions import ValidationError
from app.services.migration.sources.workbook import Sheet, normalise_header

# --------------------------------------------------------------------------------------
# Field vocabulary
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """One thing a shaper can read off a row."""

    name: str
    label: str
    required: bool = False
    #: Hint for the wizard's column picker and the generated template.
    kind: str = "text"  # text | date | amount | qty | bool | choice


def _f(name: str, label: str, *, required: bool = False, kind: str = "text") -> FieldSpec:
    return FieldSpec(name=name, label=label, required=required, kind=kind)


#: Fields shared by every document header, whatever the source calls the document.
_DOC_FIELDS: tuple[FieldSpec, ...] = (
    _f("key", "Document ID (joins to line sheets)"),
    _f("number", "Document number", required=True),
    _f("date", "Date", required=True, kind="date"),
    _f("due_date", "Due date", kind="date"),
    _f("party", "Party name", required=True),
    _f("party_gstin", "Party GSTIN"),
    _f("reference", "Reference / their document no."),
    _f("narration", "Narration / notes"),
    _f("place_of_supply", "Place of supply"),
    _f("cost_centre", "Cost centre"),
    _f("godown", "Warehouse / godown"),
    _f("currency", "Currency"),
    _f("exchange_rate", "Exchange rate", kind="amount"),
    _f("sub_total", "Taxable value", kind="amount"),
    _f("tax_total", "Tax total", kind="amount"),
    _f("cgst", "CGST", kind="amount"),
    _f("sgst", "SGST", kind="amount"),
    _f("igst", "IGST", kind="amount"),
    _f("cess", "Cess", kind="amount"),
    _f("discount", "Discount", kind="amount"),
    _f("tds", "TDS / TCS withheld", kind="amount"),
    _f("round_off", "Round off", kind="amount"),
    _f("total", "Document total", required=True, kind="amount"),
    _f("status", "Status"),
    _f("is_cancelled", "Cancelled?", kind="bool"),
    # Tally's "optional" voucher: entered, visible, and deliberately outside the
    # books until someone confirms it. Importing one as real overstates the ledger.
    _f("is_optional", "Optional / provisional?", kind="bool"),
)

_ITEM_LINE_FIELDS: tuple[FieldSpec, ...] = (
    _f("key", "Document ID", required=True),
    _f("item", "Item name", required=True),
    _f("description", "Description"),
    _f("qty", "Quantity", required=True, kind="qty"),
    _f("uom", "Unit"),
    _f("rate", "Rate", kind="amount"),
    _f("discount_pct", "Discount %", kind="amount"),
    _f("taxable", "Taxable value", kind="amount"),
    _f("cgst", "CGST", kind="amount"),
    _f("sgst", "SGST", kind="amount"),
    _f("igst", "IGST", kind="amount"),
    _f("cess", "Cess", kind="amount"),
    _f("amount", "Line total", kind="amount"),
    _f("account", "Income / expense account"),
    _f("godown", "Warehouse / godown"),
    _f("batch", "Batch"),
    _f("expiry", "Expiry", kind="date"),
)

_LEDGER_LINE_FIELDS: tuple[FieldSpec, ...] = (
    _f("key", "Document ID", required=True),
    _f("ledger", "Ledger / account", required=True),
    _f("dr_cr", "Dr/Cr", kind="choice"),
    _f("amount", "Amount (signed or with Dr/Cr)", kind="amount"),
    _f("debit", "Debit", kind="amount"),
    _f("credit", "Credit", kind="amount"),
    _f("cost_centre", "Cost centre"),
    _f("bill_ref", "Bill reference"),
    _f("due_date", "Due date", kind="date"),
    _f("narration", "Narration"),
)

_BILL_LINE_FIELDS: tuple[FieldSpec, ...] = (
    _f("key", "Document ID", required=True),
    _f("party", "Party"),
    _f("bill_ref", "Bill reference", required=True),
    _f("alloc_type", "New Ref / Agst Ref"),
    _f("amount", "Amount", kind="amount"),
    _f("due_date", "Due date", kind="date"),
)

_BANK_LINE_FIELDS: tuple[FieldSpec, ...] = (
    _f("key", "Document ID", required=True),
    _f("bank", "Bank ledger"),
    _f("instrument_no", "Instrument / cheque no."),
    _f("instrument_date", "Instrument date", kind="date"),
    _f("txn_type", "Transaction type"),
    _f("bank_date", "Bank date", kind="date"),
    _f("amount", "Amount", kind="amount"),
    _f("reference", "Bank reference"),
)


# --------------------------------------------------------------------------------------
# Shapes the normaliser knows how to build
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class KindSpec:
    """One structural shape a sheet may have."""

    key: str
    label: str
    fields: tuple[FieldSpec, ...]
    #: Which child roles this kind accepts, if any.
    child_roles: tuple[str, ...] = ()
    notes: str = ""


#: Child sheet roles, and the field set each one uses.
CHILD_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "item_lines": _ITEM_LINE_FIELDS,
    "ledger_lines": _LEDGER_LINE_FIELDS,
    "bill_allocations": _BILL_LINE_FIELDS,
    "bank_allocations": _BANK_LINE_FIELDS,
}


KINDS: dict[str, KindSpec] = {
    "ledger_master": KindSpec(
        key="ledger_master",
        label="Ledgers / chart of accounts",
        fields=(
            _f("name", "Ledger name", required=True),
            # The source's own id for this party. Only needed when an address or
            # contact sheet joins to it by id rather than by name.
            _f("key", "Source id (joins to address / contact sheets)"),
            _f("parent", "Group / parent account"),
            _f("account_type", "Account type (source's own vocabulary)"),
            _f("party_type", "Customer or vendor"),
            _f("opening", "Opening balance", kind="amount"),
            _f("dr_cr", "Dr/Cr", kind="choice"),
            _f("gstin", "GSTIN"),
            _f("pan", "PAN"),
            _f("state", "State"),
            _f("state_code", "State code"),
            _f("email", "Email"),
            _f("phone", "Phone"),
            _f("address", "Address"),
            _f("city", "City"),
            _f("pincode", "PIN code"),
            _f("credit_days", "Credit days"),
            _f("credit_limit", "Credit limit", kind="amount"),
            _f("currency", "Currency"),
            _f("is_active", "Active?", kind="bool"),
            # Everything below lands on the Customer / Supplier rather than on the
            # account. Named here because the party is built from this same row.
            _f("company_name", "Registered / legal name"),
            _f("entity_kind", "Company or Individual", kind="choice"),
            _f("payment_terms", "Payment terms (template name)"),
            _f("tax_category", "Tax category / GST treatment"),
            _f("party_group", "Customer / supplier group"),
            _f("territory", "Territory"),
            _f("bank_account_no", "Bank account number"),
            _f("ifsc", "IFSC / branch code"),
            _f("notes", "Notes"),
        ),
        notes="Becomes a Chart of Accounts leaf, plus a Customer or Supplier when "
              "the group or party type says so. The party-only columns (payment "
              "terms, tax category, group, territory) are matched to an existing "
              "master by name and created if there is none.",
    ),
    "group_master": KindSpec(
        key="group_master",
        label="Account groups",
        fields=(
            _f("name", "Group name", required=True),
            _f("parent", "Parent group"),
            _f("nature", "Nature (Asset/Liability/Income/Expense)"),
        ),
    ),
    "item_master": KindSpec(
        key="item_master",
        label="Stock items / products",
        fields=(
            _f("name", "Item name", required=True),
            _f("code", "Item code / SKU"),
            _f("parent", "Item group"),
            _f("category", "Category / brand"),
            _f("hsn", "HSN / SAC"),
            _f("uom", "Unit"),
            _f("gst_rate", "GST rate %", kind="amount"),
            _f("standard_rate", "Selling rate", kind="amount"),
            _f("purchase_rate", "Purchase rate", kind="amount"),
            _f("description", "Description"),
            _f("income_account", "Sales / income account"),
            _f("expense_account", "Purchase / expense account"),
            _f("opening_qty", "Opening quantity", kind="qty"),
            _f("opening_value", "Opening value", kind="amount"),
            _f("is_active", "Active?", kind="bool"),
        ),
    ),
    "simple_master": KindSpec(
        key="simple_master",
        label="Simple master (unit, godown, cost centre, item group)",
        fields=(
            _f("name", "Name", required=True),
            _f("parent", "Parent"),
            _f("code", "Code / symbol"),
            _f("description", "Description"),
            # Units only: 0 decimal places means the unit cannot be fractional,
            # which is what stops half a box being issued.
            _f("decimals", "Decimal places (units)"),
        ),
    ),
    "opening_ledger": KindSpec(
        key="opening_ledger",
        label="Opening balances (ledgers)",
        fields=(
            _f("name", "Ledger name", required=True),
            _f("opening", "Opening balance", required=True, kind="amount"),
            _f("dr_cr", "Dr/Cr", kind="choice"),
            _f("date", "As of", kind="date"),
        ),
    ),
    "opening_stock": KindSpec(
        key="opening_stock",
        label="Opening stock",
        fields=(
            _f("name", "Item name", required=True),
            _f("qty", "Quantity", required=True, kind="qty"),
            _f("value", "Value", kind="amount"),
            _f("uom", "Unit"),
            _f("godown", "Warehouse / godown"),
        ),
    ),
    "voucher_relational": KindSpec(
        key="voucher_relational",
        label="Vouchers with separate line sheets",
        fields=_DOC_FIELDS + (_f("voucher_type", "Voucher type", required=True),),
        child_roles=("ledger_lines", "item_lines", "bill_allocations", "bank_allocations"),
        notes="The faithful shape: the source already holds the double entry, so "
              "amounts are carried across rather than rebuilt.",
    ),
    "voucher_flat": KindSpec(
        key="voucher_flat",
        label="Vouchers, one row each (debit/credit ledger columns)",
        fields=_DOC_FIELDS
        + (
            _f("voucher_type", "Voucher type", required=True),
            _f("debit_ledger", "Debit ledger", required=True),
            _f("credit_ledger", "Credit ledger", required=True),
            _f("bank_ledger", "Bank / cash ledger"),
            _f("item", "Item name"),
            _f("qty", "Quantity", kind="qty"),
            _f("uom", "Unit"),
            _f("rate", "Rate", kind="amount"),
            _f("hsn", "HSN / SAC"),
        ),
        notes="Rows sharing a document number are folded into one voucher, so a "
              "multi-line invoice spread over several rows imports as one invoice.",
    ),
    "document": KindSpec(
        key="document",
        label="Invoices / bills / orders (double entry rebuilt)",
        fields=_DOC_FIELDS + (_f("voucher_type", "Voucher type"),),
        child_roles=("item_lines",),
        notes="For sources that ship documents rather than journals. The party, "
              "revenue and tax postings are derived from the document's own "
              "totals and checked to balance before anything is staged.",
    ),
    "payment": KindSpec(
        key="payment",
        label="Payments / receipts",
        fields=(
            _f("key", "Payment ID"),
            _f("number", "Payment number", required=True),
            _f("date", "Date", required=True, kind="date"),
            _f("party", "Party", required=True),
            _f("bank", "Paid through / deposited to", required=True),
            _f("mode", "Payment mode"),
            _f("reference", "Reference / UTR"),
            _f("amount", "Amount", required=True, kind="amount"),
            _f("unused", "Unapplied amount", kind="amount"),
            _f("narration", "Notes"),
            _f("voucher_type", "Voucher type"),
        ),
        child_roles=("bill_allocations",),
    ),
    "journal": KindSpec(
        key="journal",
        label="Manual journals",
        fields=(
            _f("key", "Journal ID"),
            _f("number", "Journal number", required=True),
            _f("date", "Date", required=True, kind="date"),
            _f("reference", "Reference"),
            _f("narration", "Notes"),
            _f("status", "Status"),
        ),
        child_roles=("ledger_lines",),
    ),
    "transfer": KindSpec(
        key="transfer",
        label="Bank / cash transfers",
        fields=(
            _f("number", "Reference", required=True),
            _f("date", "Date", required=True, kind="date"),
            _f("from_account", "From account", required=True),
            _f("to_account", "To account", required=True),
            _f("amount", "Amount", required=True, kind="amount"),
            _f("narration", "Description"),
        ),
    ),
    "expense": KindSpec(
        key="expense",
        label="Expenses paid directly",
        fields=(
            _f("number", "Reference", required=True),
            _f("date", "Date", required=True, kind="date"),
            _f("account", "Expense account", required=True),
            _f("bank", "Paid through", required=True),
            _f("party", "Vendor"),
            _f("amount", "Amount before tax", required=True, kind="amount"),
            _f("cgst", "CGST", kind="amount"),
            _f("sgst", "SGST", kind="amount"),
            _f("igst", "IGST", kind="amount"),
            _f("tax_total", "Tax amount", kind="amount"),
            _f("total", "Total", kind="amount"),
            _f("cost_centre", "Cost centre"),
            _f("narration", "Notes"),
        ),
    ),
    "stock_adjustment": KindSpec(
        key="stock_adjustment",
        label="Inventory adjustments",
        fields=(
            _f("number", "Reference", required=True),
            _f("date", "Date", required=True, kind="date"),
            _f("item", "Item", required=True),
            _f("qty", "Quantity adjusted", required=True, kind="qty"),
            _f("value", "Value adjusted", kind="amount"),
            _f("uom", "Unit"),
            _f("godown", "Warehouse / godown"),
            _f("account", "Adjustment account"),
            _f("narration", "Reason"),
        ),
    ),
    "order": KindSpec(
        key="order",
        label="Quotations / sales & purchase orders",
        fields=_DOC_FIELDS + (_f("voucher_type", "Order type", required=True),),
        child_roles=("item_lines",),
        notes="An order is a commitment, not a posting: nothing reaches the "
              "ledger, so only the party and the item lines are carried across. "
              "A line sheet is therefore not optional here — an order with no "
              "lines is a total, not an order, and each row says so rather than "
              "importing a document nobody can fulfil.",
    ),
    "payment_terms": KindSpec(
        key="payment_terms",
        label="Payment terms",
        fields=(
            _f("name", "Term name", required=True),
            _f("credit_days", "Days"),
            _f("portion", "Portion of invoice %", kind="amount"),
            _f("description", "Description"),
        ),
        notes="'Net 30' becomes a template with one 100% term due 30 days after "
              "the invoice date. Split terms (50/50) need one row per instalment, "
              "sharing a name.",
    ),
    "party_address": KindSpec(
        key="party_address",
        label="Party addresses",
        fields=(
            _f("party", "Party (name, or the id the party sheet uses)", required=True),
            _f("title", "Address title"),
            _f("address_type", "Billing / Shipping"),
            _f("attention", "Attention / care of"),
            _f("line1", "Address line 1", required=True),
            _f("line2", "Address line 2"),
            _f("city", "City"),
            _f("state", "State"),
            _f("state_code", "State code"),
            _f("pincode", "PIN / ZIP"),
            _f("country", "Country"),
            _f("phone", "Phone"),
            _f("is_active", "Active?", kind="bool"),
        ),
        notes="Joined to the party by whatever the party sheet is keyed on — a "
              "name, or the source's own contact id.",
    ),
    "party_contact": KindSpec(
        key="party_contact",
        label="Party contact people",
        fields=(
            _f("party", "Party (name, or the id the party sheet uses)", required=True),
            _f("first_name", "First name", required=True),
            _f("last_name", "Last name"),
            _f("full_name", "Full name (split when first/last are absent)"),
            _f("salutation", "Salutation"),
            _f("email", "Email"),
            _f("phone", "Phone"),
            _f("mobile", "Mobile"),
            _f("designation", "Designation"),
            _f("is_primary", "Primary contact?", kind="bool"),
            _f("is_active", "Active?", kind="bool"),
        ),
    ),
    "bank_account": KindSpec(
        key="bank_account",
        label="Bank accounts",
        fields=(
            _f("name", "Account name", required=True),
            _f("key", "Source id (joins to the statement-line sheet)"),
            _f("bank", "Bank name"),
            _f("account_no", "Account number"),
            _f("ifsc", "IFSC / SWIFT"),
            _f("ledger", "Ledger account it posts to"),
            _f("currency", "Currency"),
            _f("opening", "Opening balance", kind="amount"),
            _f("is_default", "Default account?", kind="bool"),
        ),
        notes="Links to the ledger account of the same name when no explicit one "
              "is given, so the reconciliation tool has a book side to match.",
    ),
    "bank_transaction": KindSpec(
        key="bank_transaction",
        label="Bank statement lines",
        fields=(
            _f("bank", "Bank account", required=True),
            _f("date", "Date", required=True, kind="date"),
            _f("amount", "Amount", required=True, kind="amount"),
            _f("dr_cr", "Deposit / withdrawal", kind="choice"),
            _f("txn_type", "Transaction type"),
            _f("description", "Description"),
            _f("reference", "Reference number"),
            _f("number", "Statement line id"),
            _f("status", "Matched status"),
        ),
        notes="Reconciliation input, never an accounting entry: these post "
              "nothing and arrive unreconciled for the Bank Reconciliation tool.",
    ),
    "tax_master": KindSpec(
        key="tax_master",
        label="Tax rates / templates",
        fields=(
            _f("name", "Tax name", required=True),
            _f("rate", "Rate %", required=True, kind="amount"),
            _f("tax_type", "Tax type (GST / VAT / TDS)"),
            _f("applies_to", "Sales or Purchase", kind="choice"),
            _f("account", "Tax account"),
            _f("input_account", "Input / purchase account"),
            _f("output_account", "Output / sales account"),
            _f("is_default", "Default?", kind="bool"),
        ),
        notes="Becomes a Tax Template for documents raised *after* the migration. "
              "Imported documents keep carrying their own tax amounts across, so "
              "nothing here is recomputed onto history.",
    ),
    "currency_master": KindSpec(
        key="currency_master",
        label="Currencies and exchange rates",
        fields=(
            _f("code", "ISO code", required=True),
            _f("name", "Currency name"),
            _f("symbol", "Symbol"),
            _f("rate", "Rate against the base currency", kind="amount"),
            _f("is_base", "Base currency?", kind="bool"),
        ),
        notes="Currencies are matched on ISO code against the built-in list and "
              "never invented; a rate becomes a Currency Exchange.",
    ),
    "price_list_master": KindSpec(
        key="price_list_master",
        label="Price lists",
        fields=(
            _f("name", "Price list name", required=True),
            _f("applies_to", "Selling or Buying", kind="choice"),
            _f("currency", "Currency"),
            _f("description", "Description"),
        ),
    ),
    "budget_master": KindSpec(
        key="budget_master",
        label="Budgets",
        fields=(
            _f("name", "Budget name", required=True),
            _f("account", "Account or group", required=True),
            _f("amount", "Budget amount", required=True, kind="amount"),
            _f("from_date", "Period from", kind="date"),
            _f("to_date", "Period to", kind="date"),
            _f("cost_centre", "Cost centre"),
        ),
        notes="Rows sharing a budget name are folded into one Budget with an "
              "account line each.",
    ),
    "asset_master": KindSpec(
        key="asset_master",
        label="Fixed assets",
        fields=(
            _f("name", "Asset name", required=True),
            _f("code", "Asset code / id"),
            _f("category", "Asset group / category"),
            _f("purchase_date", "Purchase date", kind="date"),
            _f("cost", "Original cost", required=True, kind="amount"),
            _f("accumulated", "Accumulated depreciation to date", kind="amount"),
            _f("wdv", "Written-down value", kind="amount"),
            _f("method", "Depreciation method"),
            _f("rate", "Depreciation rate %", kind="amount"),
            _f("life", "Useful life (years)"),
            _f("location", "Location"),
            _f("custodian", "Custodian"),
        ),
        notes="Arrives as a Draft Asset carrying its accumulated depreciation, so "
              "book value is right from day one. Past depreciation postings are "
              "not replayed — they are already inside the opening balances.",
    ),
    "reference": KindSpec(
        key="reference",
        label="Read but not imported (no target module)",
        fields=(_f("name", "Anything identifying the row"),),
        notes="Staged and counted so the gap is visible, never posted. This is "
              "what an honest 'we cannot import this yet' looks like.",
    ),
}


# --------------------------------------------------------------------------------------
# Profile model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetSpec:
    """One sheet, and what it contributes."""

    sheet: str
    kind: str
    #: Catalogue entity key for masters/openings. Vouchers resolve their entity
    #: from the voucher type instead, so this is None for those kinds.
    entity: str | None = None
    #: ``{canonical field: (header alias, ...)}`` — aliases are folded headers.
    columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Column joining this sheet's rows to its children's.
    key: str | None = None
    children: tuple["ChildSpec", ...] = ()
    #: Values forced onto every row, e.g. ``{"voucher_type": "Sales"}`` for a
    #: sheet that is definitionally invoices.
    constants: dict[str, str] = field(default_factory=dict)
    #: Why this sheet is not imported, when ``kind == "reference"``.
    reason: str = ""

    def spec(self) -> KindSpec:
        try:
            return KINDS[self.kind]
        except KeyError:
            raise ValidationError(
                f"'{self.kind}' is not a shape this importer can build. Valid "
                f"shapes: {', '.join(sorted(KINDS))}.",
                field="kind",
            ) from None


@dataclass(frozen=True)
class ChildSpec:
    """A line sheet hanging off a header sheet."""

    sheet: str
    role: str
    key: str
    columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceProfile:
    """A whole workbook shape."""

    key: str
    label: str
    app: str
    sheets: tuple[SheetSpec, ...] = ()
    #: Source's account-type vocabulary -> Tally reserved group, so a chart of
    #: accounts classifies into the same tree a Tally import produces.
    account_types: dict[str, str] = field(default_factory=dict)
    #: Parent-account name -> Tally reserved group, checked *before*
    #: ``account_types``. Zoho files its GST accounts under a parent called
    #: "Taxes" while typing them as ordinary current assets/liabilities; without
    #: this they would import as plain balance-sheet accounts and every invoice's
    #: GST would post outside Duties & Taxes.
    account_parents: dict[str, str] = field(default_factory=dict)
    #: Source's party vocabulary -> Tally reserved group.
    party_types: dict[str, str] = field(default_factory=dict)
    #: Sheets whose presence identifies this shape.
    signature: tuple[str, ...] = ()
    #: Ledger names used when synthesising a posting the source left implicit.
    defaults: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    #: True for a profile loaded from the database rather than shipped in code.
    saved: bool = False

    def sheet_for(self, name: str) -> SheetSpec | None:
        folded = normalise_header(name)
        for spec in self.sheets:
            if normalise_header(spec.sheet) == folded:
                return spec
        return None

    def ledger(self, role: str, fallback: str) -> str:
        return self.defaults.get(role, fallback)

    # -- serialisation ---------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "app": self.app,
            "notes": self.notes,
            "account_types": dict(self.account_types),
            "account_parents": dict(self.account_parents),
            "party_types": dict(self.party_types),
            "defaults": dict(self.defaults),
            "signature": list(self.signature),
            "sheets": [
                {
                    "sheet": s.sheet,
                    "kind": s.kind,
                    "entity": s.entity,
                    "key": s.key,
                    "reason": s.reason,
                    "columns": {k: list(v) for k, v in s.columns.items()},
                    "constants": dict(s.constants),
                    "children": [
                        {
                            "sheet": c.sheet,
                            "role": c.role,
                            "key": c.key,
                            "columns": {k: list(v) for k, v in c.columns.items()},
                            "constants": dict(c.constants),
                        }
                        for c in s.children
                    ],
                }
                for s in self.sheets
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, saved: bool = False) -> "SourceProfile":
        """Rebuild a profile, rejecting anything the normaliser cannot honour.

        Validated rather than trusted because a profile row may have been saved by
        an older build, or hand-edited through the API. A shape we no longer
        support has to fail here, loudly, rather than silently importing a sheet
        as something else.
        """
        if not isinstance(payload, dict):
            raise ValidationError("A source profile must be an object", field="definition")

        sheets: list[SheetSpec] = []
        for raw in payload.get("sheets") or []:
            kind = str(raw.get("kind") or "")
            if kind not in KINDS:
                raise ValidationError(
                    f"Sheet '{raw.get('sheet')}' claims shape '{kind}', which this "
                    "version of OptiERP cannot build. Re-map it in the wizard.",
                    field="definition",
                )
            allowed = KINDS[kind].child_roles
            children: list[ChildSpec] = []
            for child in raw.get("children") or []:
                role = str(child.get("role") or "")
                if role not in allowed:
                    raise ValidationError(
                        f"Sheet '{raw.get('sheet')}' cannot have a '{role}' line "
                        f"sheet; a '{kind}' accepts {', '.join(allowed) or 'none'}.",
                        field="definition",
                    )
                children.append(
                    ChildSpec(
                        sheet=str(child.get("sheet") or ""),
                        role=role,
                        key=str(child.get("key") or ""),
                        columns=_columns(child.get("columns")),
                        constants={k: str(v) for k, v in (child.get("constants") or {}).items()},
                    )
                )
            sheets.append(
                SheetSpec(
                    sheet=str(raw.get("sheet") or ""),
                    kind=kind,
                    entity=raw.get("entity") or None,
                    columns=_columns(raw.get("columns")),
                    key=raw.get("key") or None,
                    children=tuple(children),
                    constants={k: str(v) for k, v in (raw.get("constants") or {}).items()},
                    reason=str(raw.get("reason") or ""),
                )
            )

        return cls(
            key=str(payload.get("key") or "custom"),
            label=str(payload.get("label") or "Custom mapping"),
            app=str(payload.get("app") or "Custom"),
            sheets=tuple(sheets),
            account_types={k: str(v) for k, v in (payload.get("account_types") or {}).items()},
            account_parents={k: str(v) for k, v in (payload.get("account_parents") or {}).items()},
            party_types={k: str(v) for k, v in (payload.get("party_types") or {}).items()},
            signature=tuple(payload.get("signature") or ()),
            defaults={k: str(v) for k, v in (payload.get("defaults") or {}).items()},
            notes=str(payload.get("notes") or ""),
            saved=saved,
        )

    def with_sheets(self, sheets: Iterable[SheetSpec]) -> "SourceProfile":
        return replace(self, sheets=tuple(sheets))


def _columns(raw: Any) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for name, aliases in (raw or {}).items():
        if isinstance(aliases, str):
            aliases = [aliases]
        folded = tuple(normalise_header(a) for a in aliases if str(a).strip())
        if folded:
            out[str(name)] = folded
    return out


# --------------------------------------------------------------------------------------
# Reading a row through a spec
# --------------------------------------------------------------------------------------


def value(row: dict[str, Any], spec: SheetSpec | ChildSpec, field_name: str) -> Any:
    """Read one canonical field off a row, trying each alias in order."""
    for alias in spec.columns.get(field_name, ()):
        found = row.get(alias)
        if found is not None:
            return found
    constant = getattr(spec, "constants", {}).get(field_name)
    return constant if constant is not None else None


def resolve(spec: SheetSpec | ChildSpec, sheet: Sheet) -> SheetSpec | ChildSpec:
    """Drop aliases the sheet does not actually have.

    Keeps the hot path cheap and, more usefully, lets the wizard report "this
    profile expects a `Due_Date` column your file does not have" from the same
    data the normaliser uses.
    """
    present = set(sheet.headers)
    trimmed = {
        name: tuple(a for a in aliases if a in present)
        for name, aliases in spec.columns.items()
    }
    return replace(spec, columns={k: v for k, v in trimmed.items() if v})


def missing_required(spec: SheetSpec | ChildSpec, sheet: Sheet) -> list[str]:
    """Required fields of this shape that the sheet has no column for."""
    if isinstance(spec, ChildSpec):
        fields = CHILD_FIELDS.get(spec.role, ())
    else:
        fields = spec.spec().fields
    present = set(sheet.headers)
    gaps = []
    for f in fields:
        if not f.required:
            continue
        if f.name in spec.constants:
            continue
        if not any(a in present for a in spec.columns.get(f.name, ())):
            gaps.append(f.name)
    return gaps
