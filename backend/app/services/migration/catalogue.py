"""Tally -> OptiERP mapping catalogue — the single source of truth for the importer.

Everything the importer knows about Tally's data model lives here as data, not
code: which Tally entity becomes which OptiERP DocType, how Tally's 28 reserved
ledger groups classify into our Chart of Accounts, and which of Tally's reserved
voucher types becomes which transaction.

Three tables drive the whole feature:

``ENTITIES``
    One row per importable Tally entity (masters + vouchers). Drives the
    coverage matrix in the UI, the staging pipeline order, and the docs.
``PRIMARY_GROUPS``
    Tally's reserved ledger groups -> (root_type, account_type, party_type).
    This is what lets us decide that a ledger under "Sundry Debtors" is a
    Customer, one under "Bank Accounts" is a Bank account, and so on.
``VOUCHER_TYPES``
    Tally's reserved voucher types -> target DocType + the flags needed to
    build it (return? payment direction? stock purpose?).

Anything not in these tables is still parsed and staged — it just lands with a
``Skipped`` status and a reason, so nothing from the customer's Tally file is
silently dropped.

Sources: Tally.ERP 9 / TallyPrime XML schema (TallyMessage envelopes), and the
ERPNext data-migration mappings under reference/erpnext.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------
# Entity catalogue
# --------------------------------------------------------------------------------------

# Pipeline stages. Masters must land before the vouchers that reference them,
# and inside masters the trees must land before their leaves. `stage` is the
# sort key the runner uses; equal stages may be processed in any order.
STAGE_TREE = 10  # group/tree masters (account groups, item groups, godowns)
STAGE_MASTER = 20  # leaf masters (ledgers, stock items, units, cost centres)
STAGE_PARTY = 30  # customers/suppliers derived from ledgers
STAGE_PARTY_LINK = 35  # addresses/contacts, which need their party to exist first
STAGE_OPENING = 40  # opening balances (ledger + stock)
STAGE_VOUCHER = 50  # transactional vouchers

# What we do with an entity we recognise but cannot represent.
SUPPORT_FULL = "full"  # imported into a real DocType
SUPPORT_PARTIAL = "partial"  # imported, but some Tally fields have no home here
SUPPORT_REFERENCE = "reference"  # staged + kept for lookup, never becomes a document
SUPPORT_NONE = "none"  # recognised, staged, reported — no target module exists yet


@dataclass(frozen=True)
class EntitySpec:
    """One importable Tally entity."""

    key: str  # stable slug used in the API + staging rows
    label: str  # human label shown in the UI
    tally_tag: str  # XML tag inside <TALLYMESSAGE> (or "VOUCHER" + voucher type)
    target: str  # OptiERP DocType (or a note when support != full)
    module: str  # OptiERP module the target belongs to
    stage: int
    support: str = SUPPORT_FULL
    notes: str = ""


ENTITIES: tuple[EntitySpec, ...] = (
    # --- Setup / core -----------------------------------------------------------------
    EntitySpec(
        key="currency", label="Currency", tally_tag="CURRENCY",
        target="Currency + Currency Exchange", module="Core", stage=STAGE_TREE,
        notes="Matched on ISO code against the currency list, never invented. A "
              "rate against the base currency becomes a Currency Exchange dated "
              "from the migration cut-off.",
    ),
    EntitySpec(
        key="unit", label="Unit of Measure", tally_tag="UNIT",
        target="UOM", module="Core", stage=STAGE_TREE,
        notes="Tally compound units (Box of 12 Nos) become a UOM Conversion.",
    ),
    EntitySpec(
        key="payment_terms", label="Payment Terms Template", tally_tag="—",
        target="Payment Terms Template", module="Accounts", stage=STAGE_TREE,
        notes="A named credit period (\"Net 30\") becomes a template with one "
              "100% term, which parties and documents then point at. Imported "
              "before parties, because a Customer's default terms reference it.",
    ),
    # --- Chart of accounts ------------------------------------------------------------
    EntitySpec(
        key="group", label="Ledger Group", tally_tag="GROUP",
        target="Account (group)", module="Accounts", stage=STAGE_TREE,
        notes="Tally's group tree becomes the Chart of Accounts tree; the 28 "
              "reserved primary groups fix each branch's root type.",
    ),
    EntitySpec(
        key="ledger", label="Ledger", tally_tag="LEDGER",
        target="Account (leaf)", module="Accounts", stage=STAGE_MASTER,
        notes="Every ledger becomes a COA leaf. Ledgers under Sundry Debtors / "
              "Sundry Creditors additionally become Customer / Supplier records.",
    ),
    EntitySpec(
        key="customer", label="Customer (from Sundry Debtors)", tally_tag="LEDGER",
        target="Customer", module="Selling", stage=STAGE_PARTY,
        notes="Derived from ledgers under Sundry Debtors. GSTIN -> tax_id, "
              "mailing details -> Address + Contact.",
    ),
    EntitySpec(
        key="supplier", label="Supplier (from Sundry Creditors)", tally_tag="LEDGER",
        target="Supplier", module="Buying", stage=STAGE_PARTY,
        notes="Derived from ledgers under Sundry Creditors.",
    ),
    EntitySpec(
        key="address", label="Address", tally_tag="LEDGER/ADDRESS",
        target="Address", module="Core", stage=STAGE_PARTY_LINK,
        notes="A Tally ledger carries its address inline; a spreadsheet source "
              "usually ships one address sheet joined to the party by id. Both "
              "land as the same Address rows.",
    ),
    EntitySpec(
        key="contact", label="Contact Person", tally_tag="LEDGER/CONTACT",
        target="Contact", module="Core", stage=STAGE_PARTY_LINK,
        notes="Named people at a customer or supplier, with their own phone, "
              "mobile, email and designation.",
    ),
    EntitySpec(
        key="bank_account", label="Bank Account", tally_tag="LEDGER/BANKDETAILS",
        target="Bank Account", module="Accounts", stage=STAGE_MASTER,
        notes="Account number, IFSC and the bank itself, linked to the ledger "
              "account of the same name so reconciliation has something to sit on.",
    ),
    EntitySpec(
        key="tax_template", label="Tax Rate / Template", tally_tag="—",
        target="Tax Template", module="Accounts", stage=STAGE_MASTER,
        support=SUPPORT_PARTIAL,
        notes="A rate master becomes a Tax Template with one GST row. Documents "
              "still carry their own tax amounts across; the template is for the "
              "invoices raised *after* the migration.",
    ),
    EntitySpec(
        key="asset", label="Fixed Asset", tally_tag="—",
        target="Asset", module="Assets", stage=STAGE_MASTER, support=SUPPORT_PARTIAL,
        notes="Cost, purchase date and accumulated depreciation to date arrive as "
              "a Draft Asset with its opening accumulated depreciation set, so "
              "book value is right from day one. Historic depreciation postings "
              "are not replayed — they are already in the opening balances.",
    ),
    EntitySpec(
        key="cost_centre", label="Cost Centre", tally_tag="COSTCENTRE",
        target="Cost Center", module="Accounts", stage=STAGE_TREE,
    ),
    EntitySpec(
        key="cost_category", label="Cost Category", tally_tag="COSTCATEGORY",
        target="—", module="Accounts", stage=STAGE_TREE, support=SUPPORT_REFERENCE,
        notes="Tally allows a second, orthogonal cost dimension. OptiERP has a "
              "single Cost Center tree with no column for one, so the category "
              "is kept on the staging row for reference and nothing else.",
    ),
    EntitySpec(
        key="budget", label="Budget", tally_tag="BUDGET",
        target="Budget", module="Accounts", stage=STAGE_MASTER, support=SUPPORT_PARTIAL,
        notes="Ledger-wise budget amounts import; Tally's cost-centre and "
              "closing-balance budget types are reported as unsupported rows.",
    ),
    # --- Stock ------------------------------------------------------------------------
    EntitySpec(
        key="stock_group", label="Stock Group", tally_tag="STOCKGROUP",
        target="Item Group", module="Stock", stage=STAGE_TREE,
    ),
    EntitySpec(
        key="stock_category", label="Stock Category", tally_tag="STOCKCATEGORY",
        target="—", module="Stock", stage=STAGE_TREE, support=SUPPORT_REFERENCE,
        notes="An orthogonal classification with no OptiERP equivalent; kept on "
              "the staging row and written to Item.brand when the item has none.",
    ),
    EntitySpec(
        key="stock_item", label="Stock Item", tally_tag="STOCKITEM",
        target="Item", module="Stock", stage=STAGE_MASTER,
        notes="HSN/SAC + GST rate map to the item's tax fields; opening balance "
              "and opening value become the opening stock reconciliation.",
    ),
    EntitySpec(
        key="godown", label="Godown / Location", tally_tag="GODOWN",
        target="Warehouse", module="Stock", stage=STAGE_TREE,
    ),
    EntitySpec(
        key="price_list", label="Price List (Price Level)", tally_tag="PRICELIST",
        target="Price List + Item Price", module="Stock", stage=STAGE_MASTER,
        support=SUPPORT_PARTIAL,
        notes="Rates import per item; Tally's quantity-slab pricing collapses to "
              "the lowest slab rate and the slabs are reported.",
    ),
    # --- Config -----------------------------------------------------------------------
    EntitySpec(
        key="voucher_type", label="Voucher Type", tally_tag="VOUCHERTYPE",
        target="Tally Voucher Type Mapping", module="Data Migration", stage=STAGE_TREE,
        support=SUPPORT_REFERENCE,
        notes="User-defined voucher types (e.g. 'Cash Sales') inherit their "
              "parent reserved type's mapping; editable before the run.",
    ),
    # --- Opening balances -------------------------------------------------------------
    EntitySpec(
        key="opening_ledger", label="Opening Balances (ledgers)", tally_tag="LEDGER/OPENINGBALANCE",
        target="Journal Entry (opening)", module="Accounts", stage=STAGE_OPENING,
        notes="One balanced opening Journal Entry against Temporary Opening. "
              "Party ledgers with bill-wise details become Opening Invoices "
              "instead, so ageing survives the move.",
    ),
    EntitySpec(
        key="opening_stock", label="Opening Stock", tally_tag="STOCKITEM/OPENINGBALANCE",
        target="Stock Reconciliation (opening)", module="Stock", stage=STAGE_OPENING,
        notes="Qty + value per item/godown as at the migration cut-off date.",
    ),
    # --- Vouchers ---------------------------------------------------------------------
    EntitySpec(
        key="voucher_sales", label="Sales Voucher", tally_tag="VOUCHER:Sales",
        target="Sales Invoice", module="Accounts", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_credit_note", label="Credit Note", tally_tag="VOUCHER:Credit Note",
        target="Sales Invoice (return)", module="Accounts", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_purchase", label="Purchase Voucher", tally_tag="VOUCHER:Purchase",
        target="Purchase Invoice", module="Accounts", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_debit_note", label="Debit Note", tally_tag="VOUCHER:Debit Note",
        target="Purchase Invoice (return)", module="Accounts", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_receipt", label="Receipt", tally_tag="VOUCHER:Receipt",
        target="Payment Entry (Receive)", module="Accounts", stage=STAGE_VOUCHER,
        notes="Tally bill-wise 'Agst Ref' allocations become payment references "
              "against the matching invoice, preserving outstanding amounts.",
    ),
    EntitySpec(
        key="voucher_payment", label="Payment", tally_tag="VOUCHER:Payment",
        target="Payment Entry (Pay)", module="Accounts", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_contra", label="Contra", tally_tag="VOUCHER:Contra",
        target="Payment Entry (Internal Transfer)", module="Accounts", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_journal", label="Journal", tally_tag="VOUCHER:Journal",
        target="Journal Entry", module="Accounts", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_sales_order", label="Sales Order", tally_tag="VOUCHER:Sales Order",
        target="Sales Order", module="Selling", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_purchase_order", label="Purchase Order", tally_tag="VOUCHER:Purchase Order",
        target="Purchase Order", module="Buying", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_delivery_note", label="Delivery Note", tally_tag="VOUCHER:Delivery Note",
        target="Delivery Note", module="Stock", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_receipt_note", label="Receipt Note", tally_tag="VOUCHER:Receipt Note",
        target="Purchase Receipt", module="Stock", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_rejections_in", label="Rejections In", tally_tag="VOUCHER:Rejections In",
        target="Delivery Note (return)", module="Stock", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_rejections_out", label="Rejections Out", tally_tag="VOUCHER:Rejections Out",
        target="Purchase Receipt (return)", module="Stock", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_stock_journal", label="Stock Journal", tally_tag="VOUCHER:Stock Journal",
        target="Stock Entry", module="Stock", stage=STAGE_VOUCHER,
        notes="Source-only -> Material Issue, destination-only -> Material "
              "Receipt, both -> Material Transfer.",
    ),
    EntitySpec(
        key="voucher_manufacturing_journal", label="Manufacturing Journal",
        tally_tag="VOUCHER:Manufacturing Journal",
        target="Stock Entry (Manufacture)", module="Manufacturing", stage=STAGE_VOUCHER,
        notes="Consumption + finished-goods lines become a Repack/Manufacture "
              "Stock Entry; Tally's BOM is not carried over.",
    ),
    EntitySpec(
        key="voucher_physical_stock", label="Physical Stock", tally_tag="VOUCHER:Physical Stock",
        target="Stock Reconciliation", module="Stock", stage=STAGE_VOUCHER,
    ),
    EntitySpec(
        key="voucher_memorandum", label="Memorandum", tally_tag="VOUCHER:Memorandum",
        target="—", module="Accounts", stage=STAGE_VOUCHER, support=SUPPORT_REFERENCE,
        notes="Non-accounting scratch voucher in Tally; staged for review, never posted.",
    ),
    EntitySpec(
        key="voucher_reversing_journal", label="Reversing Journal",
        tally_tag="VOUCHER:Reversing Journal",
        target="Journal Entry", module="Accounts", stage=STAGE_VOUCHER, support=SUPPORT_PARTIAL,
        notes="Imported as a normal Journal Entry; Tally's auto-reversal on the "
              "applicable date is not reproduced.",
    ),
    EntitySpec(
        key="bank_transaction", label="Bank Statement Line", tally_tag="—",
        target="Bank Transaction", module="Accounts", stage=STAGE_VOUCHER,
        support=SUPPORT_PARTIAL,
        notes="Imported statement lines are reconciliation *input*, not accounting "
              "entries: they post nothing to the ledger and arrive Unreconciled "
              "for the Bank Reconciliation tool to match. Run last, so the "
              "payments they match against already exist.",
    ),
    EntitySpec(
        key="voucher_quotation", label="Quotation", tally_tag="VOUCHER:Quotation",
        target="Quotation", module="Selling", stage=STAGE_VOUCHER,
        notes="Needs its item lines like any other order; a quotation with no "
              "lines is a total, not a quotation.",
    ),
    EntitySpec(
        key="voucher_payroll", label="Payroll / Attendance",
        tally_tag="VOUCHER:Payroll", target="—", module="HR", stage=STAGE_VOUCHER,
        support=SUPPORT_NONE,
        notes="OptiERP has no HR module yet. Rows are staged and counted so the "
              "gap is visible; the net payroll journal can be imported as a "
              "Journal Entry by remapping the voucher type.",
    ),
)

ENTITY_BY_KEY: dict[str, EntitySpec] = {e.key: e for e in ENTITIES}

#: Master entities, in the order the runner must process them.
MASTER_KEYS: tuple[str, ...] = tuple(
    e.key for e in sorted(ENTITIES, key=lambda e: e.stage) if e.stage < STAGE_VOUCHER
)


# --------------------------------------------------------------------------------------
# Tally reserved primary groups -> Chart of Accounts classification
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSpec:
    """How one Tally reserved group classifies in our COA."""

    root_type: str  # Asset | Liability | Equity | Income | Expense
    account_type: str | None = None  # OptiERP Account.account_type
    party_type: str | None = None  # Customer | Supplier -> ledgers also become parties
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Tally ships 28 reserved primary groups (plus 15 reserved sub-groups). Users may
# rename them, which is why each entry carries the common aliases too; unknown
# user-created groups inherit their parent's classification by walking up.
PRIMARY_GROUPS: dict[str, GroupSpec] = {
    # Equity / capital
    "Capital Account": GroupSpec("Equity", "Equity"),
    "Reserves & Surplus": GroupSpec("Equity", "Equity", aliases=("Retained Earnings",)),
    # Liabilities
    "Current Liabilities": GroupSpec("Liability"),
    "Duties & Taxes": GroupSpec("Liability", "Tax"),
    "Provisions": GroupSpec("Liability"),
    "Sundry Creditors": GroupSpec("Liability", "Payable", party_type="Supplier"),
    "Loans (Liability)": GroupSpec("Liability"),
    "Secured Loans": GroupSpec("Liability"),
    "Unsecured Loans": GroupSpec("Liability"),
    "Bank OD A/c": GroupSpec("Liability", "Bank", aliases=("Bank OCC A/c",)),
    "Branch / Divisions": GroupSpec("Liability"),
    "Suspense A/c": GroupSpec("Liability", "Temporary"),
    # Assets
    "Current Assets": GroupSpec("Asset"),
    "Bank Accounts": GroupSpec("Asset", "Bank"),
    "Cash-in-Hand": GroupSpec("Asset", "Cash"),
    "Deposits (Asset)": GroupSpec("Asset"),
    "Loans & Advances (Asset)": GroupSpec("Asset"),
    "Stock-in-Hand": GroupSpec("Asset", "Stock"),
    "Sundry Debtors": GroupSpec("Asset", "Receivable", party_type="Customer"),
    "Fixed Assets": GroupSpec("Asset", "Fixed Asset"),
    "Investments": GroupSpec("Asset"),
    "Misc. Expenses (ASSET)": GroupSpec("Asset"),
    # Income
    "Sales Accounts": GroupSpec("Income", "Income Account"),
    "Direct Incomes": GroupSpec("Income", "Income Account", aliases=("Income (Direct)",)),
    "Indirect Incomes": GroupSpec("Income", "Income Account", aliases=("Income (Indirect)",)),
    # Expense
    "Purchase Accounts": GroupSpec("Expense", "Cost of Goods Sold"),
    "Direct Expenses": GroupSpec("Expense", "Cost of Goods Sold", aliases=("Expenses (Direct)",)),
    "Indirect Expenses": GroupSpec("Expense", aliases=("Expenses (Indirect)",)),
}

#: alias -> canonical primary group name
GROUP_ALIASES: dict[str, str] = {
    alias.casefold(): canonical
    for canonical, spec in PRIMARY_GROUPS.items()
    for alias in (canonical, *spec.aliases)
}

#: Tally's own root buckets, above the primary groups.
TALLY_ROOTS: dict[str, str] = {
    "primary": "Asset",  # only reached for malformed exports; resolved by name instead
}


def classify_group(name: str) -> GroupSpec | None:
    """Return the COA classification for a Tally reserved group name."""
    canonical = GROUP_ALIASES.get((name or "").strip().casefold())
    return PRIMARY_GROUPS.get(canonical) if canonical else None


def resolve_group_spec(
    name: str | None, group_parents: dict[str, str | None]
) -> GroupSpec | None:
    """Classify a group by walking up to its nearest reserved ancestor.

    Nobody files customers directly under "Sundry Debtors" — they create
    "Debtors - North", "Debtors - Export" and so on underneath it. Those inherit
    the reserved group's meaning, so a ledger's classification is whatever its
    nearest *reserved* ancestor says, not its immediate parent.

    ``group_parents`` maps a casefolded group name to its parent's name, built
    from the GROUP records in the same export.
    """
    seen: set[str] = set()
    current = (name or "").strip()
    while current:
        folded = current.casefold()
        if folded in seen:
            break  # a cycle in the customer's own group tree
        seen.add(folded)
        spec = classify_group(current)
        if spec is not None:
            return spec
        parent = group_parents.get(folded)
        if not parent:
            return None
        current = parent.strip()
    return None


# --------------------------------------------------------------------------------------
# Tally reserved voucher types -> target DocType
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VoucherSpec:
    """How one Tally voucher type becomes an OptiERP document."""

    entity_key: str  # links back to ENTITY_BY_KEY
    doctype: str  # target DocType
    is_return: bool = False
    payment_type: str | None = None  # Receive | Pay | Internal Transfer
    stock_purpose: str | None = None  # Stock Entry purpose
    submit: bool = True  # submit on import (vs leave as draft)


VOUCHER_TYPES: dict[str, VoucherSpec] = {
    "Sales": VoucherSpec("voucher_sales", "Sales Invoice"),
    "Credit Note": VoucherSpec("voucher_credit_note", "Sales Invoice", is_return=True),
    "Purchase": VoucherSpec("voucher_purchase", "Purchase Invoice"),
    "Debit Note": VoucherSpec("voucher_debit_note", "Purchase Invoice", is_return=True),
    "Receipt": VoucherSpec("voucher_receipt", "Payment Entry", payment_type="Receive"),
    "Payment": VoucherSpec("voucher_payment", "Payment Entry", payment_type="Pay"),
    "Contra": VoucherSpec("voucher_contra", "Payment Entry", payment_type="Internal Transfer"),
    "Journal": VoucherSpec("voucher_journal", "Journal Entry"),
    "Reversing Journal": VoucherSpec("voucher_reversing_journal", "Journal Entry"),
    "Quotation": VoucherSpec("voucher_quotation", "Quotation"),
    "Sales Order": VoucherSpec("voucher_sales_order", "Sales Order"),
    "Purchase Order": VoucherSpec("voucher_purchase_order", "Purchase Order"),
    "Delivery Note": VoucherSpec("voucher_delivery_note", "Delivery Note"),
    "Receipt Note": VoucherSpec("voucher_receipt_note", "Purchase Receipt"),
    "Rejections In": VoucherSpec("voucher_rejections_in", "Delivery Note", is_return=True),
    "Rejections Out": VoucherSpec("voucher_rejections_out", "Purchase Receipt", is_return=True),
    "Stock Journal": VoucherSpec(
        "voucher_stock_journal", "Stock Entry", stock_purpose="Material Transfer"
    ),
    "Manufacturing Journal": VoucherSpec(
        "voucher_manufacturing_journal", "Stock Entry", stock_purpose="Repack"
    ),
    "Physical Stock": VoucherSpec("voucher_physical_stock", "Stock Reconciliation"),
    "Memorandum": VoucherSpec("voucher_memorandum", "—", submit=False),
    "Payroll": VoucherSpec("voucher_payroll", "—", submit=False),
    "Attendance": VoucherSpec("voucher_payroll", "—", submit=False),
}


def classify_voucher(voucher_type: str, parent_type: str | None = None) -> VoucherSpec | None:
    """Resolve a voucher type, falling back to its reserved parent.

    Tally lets users define their own voucher types ("Cash Sales", "GST Sales")
    on top of a reserved one; the export names the user type in VOUCHERTYPENAME
    and the reserved parent in the VoucherType master. We try the exact name,
    then the parent.
    """
    for candidate in (voucher_type, parent_type):
        if candidate and candidate.strip() in VOUCHER_TYPES:
            return VOUCHER_TYPES[candidate.strip()]
    return None


# --------------------------------------------------------------------------------------
# Unit of measure normalisation
# --------------------------------------------------------------------------------------

#: Tally's symbols are free text ("Nos", "nos", "PCS"). Map the common ones onto
#: the UOMs seeded by scripts/seed.py so we reuse rather than duplicate.
UNIT_ALIASES: dict[str, str] = {
    "nos": "Nos", "no": "Nos", "no.": "Nos", "pcs": "Nos", "pc": "Nos",
    "piece": "Nos", "pieces": "Nos", "unit": "Unit", "units": "Unit",
    "kg": "Kg", "kgs": "Kg", "kilogram": "Kg", "kilograms": "Kg",
    "gm": "Gram", "gms": "Gram", "g": "Gram", "gram": "Gram", "grams": "Gram",
    "ltr": "Litre", "ltrs": "Litre", "lt": "Litre", "l": "Litre", "litre": "Litre",
    "ml": "Millilitre", "mtr": "Meter", "mtrs": "Meter", "m": "Meter", "meter": "Meter",
    "cm": "Centimeter", "mm": "Millimeter", "ft": "Foot", "inch": "Inch",
    "box": "Box", "boxes": "Box", "bag": "Bag", "bags": "Bag",
    "dozen": "Dozen", "dz": "Dozen", "pair": "Pair", "prs": "Pair",
    "set": "Set", "sets": "Set", "roll": "Roll", "sqft": "Square Foot",
    "sqmtr": "Square Meter", "sq.mtr": "Square Meter", "ton": "Tonne", "mt": "Tonne",
    "hrs": "Hour", "hr": "Hour", "hour": "Hour", "day": "Day", "days": "Day",
}


def normalise_unit(symbol: str | None) -> str | None:
    """Map a Tally unit symbol onto a seeded UOM name (or title-case it)."""
    if not symbol:
        return None
    cleaned = symbol.strip()
    return UNIT_ALIASES.get(cleaned.casefold(), cleaned)


# --------------------------------------------------------------------------------------
# GST / statutory field mapping
# --------------------------------------------------------------------------------------

#: Tally's GST registration types -> our Customer/Supplier gst_category vocabulary.
GST_REGISTRATION_TYPES: dict[str, str] = {
    "Regular": "Regular",
    "Composition": "Regular",
    "Consumer": "Unregistered",
    "Unregistered": "Unregistered",
    "Unknown": "Unregistered",
    "Regular - SEZ": "SEZ",
}

#: Tally duty heads on tax ledgers -> the tax component we book them under.
DUTY_HEADS: dict[str, str] = {
    "CGST": "CGST", "SGST": "SGST", "UTGST": "SGST", "IGST": "IGST", "Cess": "Cess",
    "Central Tax": "CGST", "State Tax": "SGST", "UT Tax": "SGST", "Integrated Tax": "IGST",
    "TDS": "TDS", "TCS": "TCS",
}


# --------------------------------------------------------------------------------------
# Coverage summary (drives the UI matrix + docs; keep in sync with ENTITIES)
# --------------------------------------------------------------------------------------


def coverage_matrix() -> list[dict[str, object]]:
    """The full Tally -> OptiERP coverage table, grouped for display."""
    return [
        {
            "key": e.key,
            "label": e.label,
            "tally_tag": e.tally_tag,
            "target": e.target,
            "module": e.module,
            "stage": e.stage,
            "support": e.support,
            "notes": e.notes,
        }
        for e in sorted(ENTITIES, key=lambda e: (e.stage, e.module, e.label))
    ]
