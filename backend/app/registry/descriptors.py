"""Descriptor definitions — the one place a simple master is declared.

Each ``register(...)`` call adds a DocType to the generic engine. Phase 0 ships
Campaign (the engine smoke-test pilot); Phases 1–2 add the tree masters and the
rest of the simple-master long tail here.
"""

from __future__ import annotations

from app.models.accounts import (
    Account,
    Bank,
    BankAccount,
    CostCenter,
    DunningType,
    FiscalYear,
    ItemTaxTemplate,
    ItemTaxTemplateDetail,
    ModeOfPayment,
    PaymentTermsTemplate,
    PaymentTermsTemplateDetail,
    Shareholder,
    ShareType,
    SubscriptionPlan,
    TaxCategory,
    TaxWithholdingCategory,
)
from app.models.assets import Asset, AssetCategory, AssetMaintenance, Location
from app.models.buying import Supplier, SupplierGroup
from app.models.manufacturing import (
    Operation,
    Routing,
    RoutingOperation,
    Workstation,
)
from app.models.selling import (
    Address,
    BlanketOrder,
    BlanketOrderItem,
    Campaign,
    Contact,
    CouponCode,
    Customer,
    CustomerGroup,
    MonthlyDistribution,
    PricingRule,
    ProductBundle,
    ProductBundleItem,
    PromotionalScheme,
    PromotionalSchemeTier,
    SalesPartner,
    SalesPerson,
    ShippingRule,
    TermsTemplate,
    Territory,
    UTMSource,
)
from app.models.cm_planning import CmCostRate
from sqlalchemy import inspect as sa_inspect, select

from app.core.exceptions import ValidationError
from app.models.stock import (
    Batch,
    DeliveryNoteItem,
    Item,
    ItemAlternative,
    ItemGroup,
    PurchaseReceiptItem,
    StockEntryItem,
)
from app.registry.base import (
    REGISTRY,
    ChildSpec,
    DocTypeDescriptor,
    FieldSpec,
    LinkSpec,
    register,
)

# Common permission bundles for selling masters.
_SALES_MANAGER = ("read", "write", "create", "delete", "report")
_SALES_USER = ("read", "report")

# Buying masters (Purchase Manager / User roles exist in scripts.seed).
_PURCHASE_MANAGER = ("read", "write", "create", "delete", "report")
_PURCHASE_USER_RW = ("read", "write", "create", "report")

# Accounts masters (Accounts Manager / User roles exist in scripts.seed).
_ACCOUNTS_MANAGER = ("read", "write", "create", "delete", "report")
_ACCOUNTS_USER = ("read", "report")

# Stock masters (Stock Manager / User roles exist in scripts.seed).
_STOCK_MANAGER = ("read", "write", "create", "delete", "report")
_STOCK_USER_RW = ("read", "write", "create", "report")
# Read-only grant for roles that reference a master via a Link field (so the
# typeahead populates) but don't manage it — e.g. Tax Category / Payment Terms
# Template are picked on Customer (Sales) and Supplier (Purchase) forms.
_LINK_READERS = {
    "Sales Manager": ("read",),
    "Sales User": ("read",),
    "Purchase Manager": ("read",),
    "Purchase User": ("read",),
}


# --- Selling: Campaign (Phase 0 pilot) ---------------------------------------
register(
    DocTypeDescriptor(
        name="Campaign",
        slug="campaign",
        model=Campaign,
        title_field="campaign_name",
        naming="field:campaign_name",
        group="Selling",
        permission_name="Campaign",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec(
                "campaign_name", "Campaign Name", "Data",
                required=True, in_list=True, span=2, unique=True,
            ),
            FieldSpec("status", "Status", "Select", options="Active\nInactive", in_list=True),
            FieldSpec("campaign_desc", "Description", "Text", span=2),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("campaign_name", "status", "disabled"),
    )
)


# --- Selling: tree masters (Phase 1) -----------------------------------------
register(
    DocTypeDescriptor(
        name="Territory",
        slug="territory",
        model=Territory,
        title_field="territory_name",
        naming="field:territory_name",
        group="Selling",
        permission_name="Territory",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        is_tree=True,
        parent_field="parent_territory_id",
        fields=(
            FieldSpec("territory_name", "Territory Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("parent_territory_id", "Parent Territory", "Link", options="territory"),
            FieldSpec("is_group", "Is Group", "Check", in_list=True),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("territory_name", "is_group", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Customer Group",
        slug="customer-group",
        model=CustomerGroup,
        title_field="customer_group_name",
        naming="field:customer_group_name",
        group="Selling",
        permission_name="Customer Group",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        is_tree=True,
        parent_field="parent_customer_group_id",
        fields=(
            FieldSpec(
                "customer_group_name", "Customer Group Name", "Data",
                required=True, in_list=True, span=2,
            ),
            FieldSpec("parent_customer_group_id", "Parent Customer Group", "Link", options="customer-group"),
            FieldSpec("is_group", "Is Group", "Check", in_list=True),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("customer_group_name", "is_group", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Sales Person",
        slug="sales-person",
        model=SalesPerson,
        title_field="sales_person_name",
        naming="field:sales_person_name",
        group="Selling",
        permission_name="Sales Person",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        is_tree=True,
        parent_field="parent_sales_person_id",
        fields=(
            FieldSpec(
                "sales_person_name", "Sales Person Name", "Data",
                required=True, in_list=True, span=2,
            ),
            FieldSpec("parent_sales_person_id", "Parent Sales Person", "Link", options="sales-person"),
            FieldSpec("commission_rate", "Commission Rate (%)", "Float"),
            FieldSpec("is_group", "Is Group", "Check", in_list=True),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("sales_person_name", "is_group", "disabled"),
    )
)


# --- Flat simple masters (Phase 2) -------------------------------------------
register(
    DocTypeDescriptor(
        name="Sales Partner",
        slug="sales-partner",
        model=SalesPartner,
        title_field="partner_name",
        naming="field:partner_name",
        group="Selling",
        permission_name="Sales Partner",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("partner_name", "Partner Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("partner_type", "Partner Type", "Data"),
            FieldSpec("commission_rate", "Commission Rate (%)", "Float"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("partner_name", "partner_type", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="CM Cost Rate",
        slug="cm-cost-rate",
        model=CmCostRate,
        title_field="rate_name",
        naming="field:rate_name",
        group="Selling",
        permission_name="Contribution Margin Plan",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("rate_name", "Rate Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("driver", "Driver", "Select", options="packaging\nmaterial\nlabor", in_list=True),
            FieldSpec("item_id", "Item", "Link", options="Item"),
            FieldSpec("item_group_id", "Item Group", "Link", options="Item Group"),
            FieldSpec("rate", "Rate", "Currency", required=True, in_list=True),
            FieldSpec("uom", "UOM", "Data"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("rate_name", "driver", "rate", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Terms Template",
        slug="terms-template",
        model=TermsTemplate,
        title_field="template_name",
        naming="field:template_name",
        group="Selling",
        permission_name="Terms Template",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("template_name", "Template Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("terms", "Terms and Conditions", "Text", span=2),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("template_name", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="UTM Source",
        slug="utm-source",
        model=UTMSource,
        title_field="utm_source_name",
        naming="field:utm_source_name",
        group="Selling",
        permission_name="UTM Source",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("utm_source_name", "Source Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("utm_source_name", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Monthly Distribution",
        slug="monthly-distribution",
        model=MonthlyDistribution,
        title_field="distribution_name",
        naming="field:distribution_name",
        group="Selling",
        permission_name="Monthly Distribution",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("distribution_name", "Distribution Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("month_1", "January %", "Float"),
            FieldSpec("month_2", "February %", "Float"),
            FieldSpec("month_3", "March %", "Float"),
            FieldSpec("month_4", "April %", "Float"),
            FieldSpec("month_5", "May %", "Float"),
            FieldSpec("month_6", "June %", "Float"),
            FieldSpec("month_7", "July %", "Float"),
            FieldSpec("month_8", "August %", "Float"),
            FieldSpec("month_9", "September %", "Float"),
            FieldSpec("month_10", "October %", "Float"),
            FieldSpec("month_11", "November %", "Float"),
            FieldSpec("month_12", "December %", "Float"),
        ),
        list_fields=("distribution_name",),
    )
)

_SALES_USER_RW = ("read", "write", "create", "report")

register(
    DocTypeDescriptor(
        name="Address",
        slug="address",
        model=Address,
        title_field="address_title",
        naming="field:address_title",
        group="Selling",
        permission_name="Address",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER_RW},
        fields=(
            FieldSpec("address_title", "Address Title", "Data", required=True, in_list=True, span=2),
            FieldSpec("address_type", "Type", "Select",
                      options="Billing\nShipping\nOffice\nWarehouse", in_list=True),
            FieldSpec("address_line1", "Address Line 1", "Data", required=True, span=2),
            FieldSpec("address_line2", "Address Line 2", "Data", span=2),
            FieldSpec("city", "City", "Data"),
            FieldSpec("state", "State / Province", "Data"),
            FieldSpec("pincode", "Postal Code", "Data"),
            FieldSpec("country", "Country", "Data"),
            FieldSpec("customer_id", "Customer", "Link", options="customer"),
            FieldSpec("supplier_id", "Supplier", "Link", options="supplier"),
            FieldSpec("disabled", "Disabled", "Check"),
        ),
        list_fields=("address_title", "address_type", "city"),
    )
)

register(
    DocTypeDescriptor(
        name="Contact",
        slug="contact",
        model=Contact,
        title_field="first_name",
        naming="field:first_name",
        group="Selling",
        permission_name="Contact",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER_RW},
        fields=(
            FieldSpec("first_name", "First Name", "Data", required=True, in_list=True),
            FieldSpec("last_name", "Last Name", "Data", in_list=True),
            FieldSpec("email_id", "Email", "Email", in_list=True),
            FieldSpec("mobile_no", "Mobile", "Data"),
            FieldSpec("phone", "Phone", "Data"),
            FieldSpec("designation", "Designation", "Data"),
            FieldSpec("customer_id", "Customer", "Link", options="customer"),
            FieldSpec("supplier_id", "Supplier", "Link", options="supplier"),
            FieldSpec("disabled", "Disabled", "Check"),
        ),
        list_fields=("first_name", "last_name", "email_id"),
    )
)


# --- Selling: Customer (engine-served CRUD UI) -------------------------------
register(
    DocTypeDescriptor(
        name="Customer",
        slug="customer",
        model=Customer,
        title_field="customer_name",
        naming="field:customer_name",
        group="Selling",
        permission_name="Customer",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER_RW},
        fields=(
            FieldSpec("customer_name", "Customer Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("customer_type", "Type", "Select", options="Company\nIndividual\nPartnership", in_list=True),
            FieldSpec("customer_group_id", "Customer Group", "Link", options="customer-group", in_list=True),
            FieldSpec("territory_id", "Territory", "Link", options="territory"),
            FieldSpec("email_id", "Email", "Email", in_list=True,
                      help="Used to email invoices and statements to this customer."),
            FieldSpec("tax_id", "GSTIN", "Data",
                      help="15-char GSTIN. Its first 2 digits (state code) auto-decide "
                      "intra-state (CGST+SGST) vs inter-state (IGST) GST on invoices."),
            FieldSpec("tax_category_id", "Tax Category", "Link", options="tax-category",
                      help="Manual fallback used only when no GSTIN is set."),
            FieldSpec("default_currency", "Default Currency", "Data"),
            FieldSpec(
                "payment_terms_template_id", "Default Payment Terms", "Link",
                options="payment-terms-template",
            ),
            FieldSpec(
                "receivable_account_id", "Receivable Account", "Link", options="account",
                help="Overrides the company default receivable account for this customer.",
            ),
            FieldSpec("credit_limit", "Credit Limit", "Float"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
            FieldSpec("notes", "Notes", "Text", span=2),
        ),
        list_fields=("customer_name", "customer_type", "disabled"),
        links=(
            LinkSpec("address", "customer_id", "Addresses"),
            LinkSpec("contact", "customer_id", "Contacts"),
        ),
    )
)


# --- Buying: Supplier Group + Supplier (Phase BA) ----------------------------
register(
    DocTypeDescriptor(
        name="Supplier Group",
        slug="supplier-group",
        model=SupplierGroup,
        title_field="supplier_group_name",
        naming="field:supplier_group_name",
        group="Buying",
        permission_name="Supplier Group",
        permissions={"Purchase Manager": _PURCHASE_MANAGER, "Purchase User": _PURCHASE_USER_RW},
        is_tree=True,
        parent_field="parent_supplier_group_id",
        fields=(
            FieldSpec(
                "supplier_group_name", "Supplier Group Name", "Data",
                required=True, in_list=True, span=2,
            ),
            FieldSpec("parent_supplier_group_id", "Parent Supplier Group", "Link", options="supplier-group"),
            FieldSpec("is_group", "Is Group", "Check", in_list=True),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("supplier_group_name", "is_group", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Supplier",
        slug="supplier",
        model=Supplier,
        title_field="supplier_name",
        naming="field:supplier_name",
        group="Buying",
        permission_name="Supplier",
        permissions={"Purchase Manager": _PURCHASE_MANAGER, "Purchase User": _PURCHASE_USER_RW},
        fields=(
            FieldSpec("supplier_name", "Supplier Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("supplier_type", "Supplier Type", "Select", options="Company\nIndividual\nPartnership", in_list=True),
            FieldSpec("supplier_group_id", "Supplier Group", "Link", options="supplier-group", in_list=True),
            FieldSpec("email_id", "Email", "Email", in_list=True,
                      help="Used to email purchase orders and documents to this supplier."),
            FieldSpec("tax_id", "GSTIN", "Data",
                      help="15-char GSTIN. Its first 2 digits (state code) auto-decide "
                      "intra-state (CGST+SGST) vs inter-state (IGST) GST on invoices."),
            FieldSpec("tax_category_id", "Tax Category", "Link", options="tax-category",
                      help="Manual fallback used only when no GSTIN is set."),
            FieldSpec("default_currency", "Billing Currency", "Data"),
            FieldSpec(
                "payment_terms_template_id", "Default Payment Terms", "Link",
                options="payment-terms-template",
            ),
            FieldSpec(
                "payable_account_id", "Payable Account", "Link", options="account",
                help="Overrides the company default payable account for this supplier.",
            ),
            FieldSpec("on_hold", "Block Supplier", "Check"),
            FieldSpec("hold_type", "Hold Type", "Select", options="All\nInvoices\nPayments", depends_on="on_hold"),
            FieldSpec("release_date", "Release Date", "Date", depends_on="on_hold"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
            FieldSpec("notes", "Notes", "Text", span=2),
        ),
        list_fields=("supplier_name", "supplier_group_id", "supplier_type", "disabled"),
        links=(
            LinkSpec("address", "supplier_id", "Addresses"),
            LinkSpec("contact", "supplier_id", "Contacts"),
        ),
    )
)


# --- Accounts: Tax Category + Payment Terms (cross-cutting masters) -----------
# These models already exist (app.models.accounts) and are referenced by Link
# fields on Customer/Supplier and the documents; registering them surfaces a
# CRUD UI with no migration. Tax Category already carries bespoke endpoints —
# the engine descriptor adds the generic list/form view alongside them.
register(
    DocTypeDescriptor(
        name="Tax Category",
        slug="tax-category",
        model=TaxCategory,
        title_field="title",
        naming="field:title",
        group="Accounts",
        permission_name="Tax Category",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        fields=(
            FieldSpec("title", "Title", "Data", required=True, in_list=True, span=2, unique=True),
            FieldSpec("is_inter_state", "Inter-state (IGST)", "Check", in_list=True,
                      help="Tick for the out-of-state category. GST auto-picks this vs the "
                      "intra-state one by comparing the party's GSTIN state to the company's."),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("title", "is_inter_state", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Cost Center",
        slug="cost-center",
        model=CostCenter,
        title_field="cost_center_name",
        naming="field:cost_center_name",
        group="Accounts",
        permission_name="Cost Center",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        # Flat master: the CostCenter model has no ltree path column, so it is
        # not served as a nested tree (parent is kept as a plain reference link).
        fields=(
            FieldSpec("cost_center_name", "Cost Center Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("parent_cost_center_id", "Parent Cost Center", "Link", options="cost-center"),
            FieldSpec("is_group", "Is Group", "Check", in_list=True),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("cost_center_name", "is_group", "disabled"),
    )
)

# A Payment Terms Template answers one plain question: "how is the bill split
# into payments over time?" (e.g. 50% upfront, 50% within 30 days). We keep it
# self-contained — one master with simple installment rows — instead of
# ERPNext's two-level Payment Term + Template model with its due-date enums,
# mode-of-payment and early-payment-discount jargon.
register(
    DocTypeDescriptor(
        name="Payment Terms Template",
        slug="payment-terms-template",
        model=PaymentTermsTemplate,
        title_field="template_name",
        naming="field:template_name",
        group="Accounts",
        permission_name="Payment Terms Template",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        fields=(
            FieldSpec(
                "template_name", "Template Name", "Data",
                required=True, in_list=True, span=2, unique=True,
                help="A short name you'll pick on orders/invoices, e.g. \"50/50\" or \"Net 30\".",
            ),
        ),
        list_fields=("template_name",),
        children=(
            ChildSpec(
                field="terms",
                label="Installments — should add up to 100%",
                model=PaymentTermsTemplateDetail,
                fk_column="template_id",
                fields=(
                    FieldSpec(
                        "description", "Label", "Data",
                        help="What this part is for, e.g. Advance, On delivery, Balance.",
                    ),
                    FieldSpec(
                        "invoice_portion", "Pay (% of total)", "Float", required=True,
                        help="Share of the bill due in this installment.",
                    ),
                    FieldSpec(
                        "credit_days", "Due after (days)", "Int",
                        help="Days after the invoice date this part is due. 0 = straight away.",
                    ),
                ),
            ),
        ),
    )
)


# --- Accounts: Fiscal Year / Mode of Payment / Bank / Bank Account (Phase 0.5) ---
# Engine-served config masters. Fiscal Year keeps its bespoke GET /fiscal-years
# (used by the budget picker) — this descriptor adds the /m/fiscal-year CRUD UI.
register(
    DocTypeDescriptor(
        name="Fiscal Year",
        slug="fiscal-year",
        model=FiscalYear,
        title_field="year",
        naming="field:year",
        group="Accounts",
        permission_name="Fiscal Year",
        permissions={"Accounts Manager": _ACCOUNTS_MANAGER, "Accounts User": _ACCOUNTS_USER},
        fields=(
            FieldSpec("year", "Year", "Data", required=True, in_list=True, span=2, unique=True,
                      help="A label for the year, e.g. 2026-2027."),
            FieldSpec("year_start_date", "Start Date", "Date", required=True, in_list=True),
            FieldSpec("year_end_date", "End Date", "Date", required=True, in_list=True),
            FieldSpec("is_closed", "Closed", "Check", in_list=True, read_only=True,
                      help="Set automatically by Period Closing — not edited here."),
        ),
        list_fields=("year", "year_start_date", "year_end_date", "is_closed"),
    )
)

register(
    DocTypeDescriptor(
        name="Mode of Payment",
        slug="mode-of-payment",
        model=ModeOfPayment,
        title_field="mode_name",
        naming="field:mode_name",
        group="Accounts",
        permission_name="Mode of Payment",
        permissions={"Accounts Manager": _ACCOUNTS_MANAGER, "Accounts User": _ACCOUNTS_USER},
        fields=(
            FieldSpec("mode_name", "Mode Name", "Data", required=True, in_list=True, span=2, unique=True),
            FieldSpec("type", "Type", "Select", options="Cash\nBank\nGeneral", in_list=True),
            FieldSpec("default_account_id", "Default Account", "Link", options="account",
                      help="The cash/bank account this mode posts to by default."),
            FieldSpec("enabled", "Enabled", "Check", in_list=True),
        ),
        list_fields=("mode_name", "type", "enabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Bank",
        slug="bank",
        model=Bank,
        title_field="bank_name",
        naming="field:bank_name",
        group="Accounts",
        permission_name="Bank",
        permissions={"Accounts Manager": _ACCOUNTS_MANAGER, "Accounts User": _ACCOUNTS_USER},
        fields=(
            FieldSpec("bank_name", "Bank Name", "Data", required=True, in_list=True, span=2, unique=True),
            FieldSpec("swift_number", "SWIFT / BIC", "Data"),
        ),
        list_fields=("bank_name", "swift_number"),
    )
)

register(
    DocTypeDescriptor(
        name="Bank Account",
        slug="bank-account",
        model=BankAccount,
        title_field="account_name",
        naming="field:account_name",
        group="Accounts",
        permission_name="Bank Account",
        permissions={"Accounts Manager": _ACCOUNTS_MANAGER, "Accounts User": _ACCOUNTS_USER},
        fields=(
            FieldSpec("account_name", "Account Name", "Data", required=True, in_list=True, span=2, unique=True),
            FieldSpec("bank_id", "Bank", "Link", options="bank"),
            FieldSpec("gl_account_id", "Ledger Account", "Link", options="account",
                      help="The Chart-of-Accounts account this bank account posts to."),
            FieldSpec("account_number", "Account Number", "Data", in_list=True),
            FieldSpec("iban", "IBAN", "Data"),
            FieldSpec("is_company_account", "Company Account", "Check"),
            FieldSpec("is_default", "Default", "Check", in_list=True),
        ),
        list_fields=("account_name", "account_number", "is_default"),
    )
)


# --- Accounts: Tax Withholding (TDS/TCS) category (Phase 2) -------------------
register(
    DocTypeDescriptor(
        name="Tax Withholding Category",
        slug="tax-withholding-category",
        model=TaxWithholdingCategory,
        title_field="category_name",
        naming="field:category_name",
        group="Accounts",
        permission_name="Tax Withholding Category",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        fields=(
            FieldSpec("category_name", "Category Name", "Data", required=True, in_list=True, span=2, unique=True),
            FieldSpec("kind", "Kind", "Select", options="TDS\nTCS", in_list=True,
                      help="TDS = withheld on supplier payments; TCS = collected on customer bills."),
            FieldSpec("rate", "Rate (%)", "Float", required=True, in_list=True),
            FieldSpec("threshold", "Annual Threshold", "Currency",
                      help="0 = always apply (threshold tracking is informational for now)."),
            FieldSpec("account_id", "TDS/TCS Payable Account", "Link", options="account", required=True),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("category_name", "kind", "rate", "disabled"),
    )
)


# --- Accounts: Dunning Type (overdue-reminder tiers, Phase 3) -----------------
register(
    DocTypeDescriptor(
        name="Dunning Type",
        slug="dunning-type",
        model=DunningType,
        title_field="dunning_type",
        naming="field:dunning_type",
        group="Accounts",
        permission_name="Dunning Type",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        fields=(
            FieldSpec("dunning_type", "Dunning Type", "Data", required=True, in_list=True, span=2, unique=True),
            FieldSpec("grace_period_days", "Apply after (days overdue)", "Int", in_list=True,
                      help="Use this tier once an invoice is at least this many days past due."),
            FieldSpec("interest_rate", "Interest Rate (% p.a.)", "Float", in_list=True,
                      help="Charged on the overdue amount for the days it is late. 0 = no interest."),
            FieldSpec("dunning_fee", "Dunning Fee", "Currency",
                      help="A flat administrative charge added to the reminder. 0 = none."),
            FieldSpec("letter_intro", "Letter Message", "Text", span=2,
                      help="The tone/message for this tier (e.g. a gentle reminder vs a final notice)."),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("dunning_type", "grace_period_days", "interest_rate", "disabled"),
    )
)


# --- Accounts: Share Management (cap table, Phase 4) --------------------------
register(
    DocTypeDescriptor(
        name="Share Type",
        slug="share-type",
        model=ShareType,
        title_field="share_type_name",
        naming="field:share_type_name",
        group="Accounts",
        permission_name="Share Type",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        fields=(
            FieldSpec("share_type_name", "Share Type", "Data", required=True, in_list=True, span=2,
                      unique=True, help="e.g. Equity, Preference."),
            FieldSpec("par_value", "Par Value (per share)", "Currency", in_list=True,
                      help="Nominal/face value per share — informational."),
            FieldSpec("currency", "Currency", "Data",
                      help="3-letter code (e.g. INR). Blank = the company's default currency."),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("share_type_name", "par_value", "currency", "disabled"),
    )
)


register(
    DocTypeDescriptor(
        name="Shareholder",
        slug="shareholder",
        model=Shareholder,
        title_field="shareholder_name",
        naming="field:shareholder_name",
        group="Accounts",
        permission_name="Shareholder",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        fields=(
            FieldSpec("shareholder_name", "Shareholder", "Data", required=True, in_list=True, span=2,
                      unique=True),
            FieldSpec("contact_id", "Contact", "Link", options="contact",
                      help="Optional linked contact (phone/email)."),
            FieldSpec("folio_no", "Folio No", "Data", in_list=True),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("shareholder_name", "folio_no", "disabled"),
    )
)


# --- Accounts: Subscription Plan (recurring-billing plan, Phase 4) ------------
register(
    DocTypeDescriptor(
        name="Subscription Plan",
        slug="subscription-plan",
        model=SubscriptionPlan,
        title_field="plan_name",
        naming="field:plan_name",
        group="Accounts",
        permission_name="Subscription Plan",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            **_LINK_READERS,
        },
        fields=(
            FieldSpec("plan_name", "Plan Name", "Data", required=True, in_list=True, span=2, unique=True,
                      help="e.g. \"AMC — Premium (Monthly)\"."),
            FieldSpec("item_id", "Item", "Link", options="item", required=True, in_list=True,
                      help="The item billed each cycle (its income account is used for posting)."),
            FieldSpec("price", "Price (per cycle)", "Currency", required=True, in_list=True),
            FieldSpec("billing_interval", "Billing Interval", "Select",
                      options="Day\nWeek\nMonth\nYear", required=True, in_list=True),
            FieldSpec("interval_count", "Every (n intervals)", "Int", in_list=True,
                      help="2 + Month = bill every 2 months; 3 + Month = quarterly."),
            FieldSpec("currency", "Currency", "Data",
                      help="3-letter code (e.g. INR). Blank = the company's default currency."),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("plan_name", "billing_interval", "interval_count", "price", "disabled"),
    )
)


register(
    DocTypeDescriptor(
        name="Item Tax Template",
        slug="item-tax-template",
        model=ItemTaxTemplate,
        title_field="title",
        naming="field:title",
        group="Accounts",
        permission_name="Item Tax Template",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
            "Stock Manager": ("read", "write", "create", "report"),
            "Stock User": ("read",),
            **_LINK_READERS,
        },
        fields=(
            FieldSpec("title", "Title", "Data", required=True, in_list=True, span=2, unique=True,
                      help="e.g. \"GST 5%\" — picked on an Item to override its tax rate."),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("title", "disabled"),
        children=(
            ChildSpec(
                field="details",
                label="Tax rates (per tax account head)",
                model=ItemTaxTemplateDetail,
                fk_column="template_id",
                fields=(
                    FieldSpec("account_head_id", "Tax Account", "Link", options="account", required=True),
                    FieldSpec("rate", "Rate (%)", "Float", required=True),
                ),
            ),
        ),
    )
)


# --- Assets: Asset Category + Location (engine masters) -----------------------
# Accounts roles manage assets (no separate Assets role at MSME scale, matching
# ERPNext where Asset Category sits under Accounts).
register(
    DocTypeDescriptor(
        name="Asset Category",
        slug="asset-category",
        model=AssetCategory,
        title_field="category_name",
        naming="field:category_name",
        group="Assets",
        permission_name="Asset Category",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
        },
        fields=(
            FieldSpec("category_name", "Category Name", "Data", required=True, in_list=True, span=2,
                      unique=True, help="e.g. Vehicles, Computers, Plant & Machinery."),
            FieldSpec("is_non_depreciable", "Non-depreciable (e.g. land)", "Check", in_list=True,
                      help="Tick for land/freehold held at cost — no depreciation schedule. Its value "
                      "only changes via a Value Adjustment (revaluation up = appreciation)."),
            FieldSpec("depreciation_method", "Depreciation Method", "Select",
                      options="Straight Line\nWritten Down Value\nManual", required=True, in_list=True,
                      help="Straight Line spreads cost evenly; Written Down Value depreciates a fixed "
                      "% of the falling book value (needs a salvage value); Manual = you enter rows. "
                      "Ignored when 'Non-depreciable' is ticked."),
            FieldSpec("total_number_of_depreciations", "Number of Depreciations", "Int",
                      help="How many depreciation entries over the asset's life (e.g. 60)."),
            FieldSpec("frequency_of_depreciation_months", "Months Between Depreciations", "Int",
                      help="1 = monthly, 3 = quarterly, 12 = yearly. So 60 × 1 = 5 years monthly."),
            FieldSpec("salvage_value_percent", "Salvage Value (%)", "Float",
                      help="Residual value as a % of cost; depreciation never goes below it. 0 = none."),
            FieldSpec("rate_of_depreciation", "WDV Rate (%)", "Float",
                      help="Written Down Value only: an explicit rate (e.g. IT-Act 15%/40%). "
                      "Blank = derive it from salvage + life."),
            FieldSpec("tax_block_code", "IT Act Block Code", "Data", in_list=True,
                      help="Statutory depreciation block (e.g. PLANT_15, BUILDING_10). "
                      "Required for the tax depreciation register sync."),
            FieldSpec("daily_prorata", "Daily pro-rata", "Check",
                      help="Weight each period's depreciation by its actual day count "
                      "(Straight Line) instead of an equal split."),
            FieldSpec("fixed_asset_account_id", "Fixed Asset Account", "Link", options="account",
                      help="Balance-sheet account the asset's cost sits in."),
            FieldSpec("depreciation_expense_account_id", "Depreciation Expense Account", "Link",
                      options="account", help="Expense account each depreciation entry debits."),
            FieldSpec("accumulated_depreciation_account_id", "Accumulated Depreciation Account", "Link",
                      options="account", help="Contra-asset account each depreciation entry credits."),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("category_name", "depreciation_method", "total_number_of_depreciations", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Location",
        slug="location",
        model=Location,
        title_field="location_name",
        naming="field:location_name",
        group="Assets",
        permission_name="Location",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
        },
        fields=(
            FieldSpec("location_name", "Location Name", "Data", required=True, in_list=True, span=2,
                      unique=True, help="Where assets physically sit, e.g. Head Office, Warehouse A."),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("location_name", "disabled"),
    )
)

# One lean maintenance/repair log (auto-numbered, no GL) — preventive maintenance and
# breakdown repairs are the same record, distinguished by the "Type" (Repair is just one
# type), not two DocTypes. A repair that should hit the books is a separate Journal Entry.
register(
    DocTypeDescriptor(
        name="Asset Maintenance",
        slug="asset-maintenance",
        model=AssetMaintenance,
        title_field="name",
        naming="series:ASSET-MNT-.YYYY.-",
        group="Assets",
        permission_name="Asset Maintenance",
        permissions={
            "Accounts Manager": _ACCOUNTS_MANAGER,
            "Accounts User": _ACCOUNTS_USER,
        },
        fields=(
            FieldSpec("asset_id", "Asset", "Link", options="asset", required=True, in_list=True),
            FieldSpec("maintenance_type", "Type", "Select",
                      options="Preventive\nCalibration\nInspection\nRepair\nOther", in_list=True,
                      help="Use 'Repair' for a breakdown fix; the others are planned maintenance."),
            FieldSpec("maintenance_date", "Date", "Date", required=True, in_list=True),
            FieldSpec("periodicity", "Recurs", "Select",
                      options="One-time\nMonthly\nQuarterly\nHalf-yearly\nYearly",
                      help="How often this maintenance recurs (informational cadence)."),
            FieldSpec("next_due_date", "Next Due", "Date", in_list=True,
                      help="When the next service is due — drives the Maintenance Due report."),
            FieldSpec("assigned_to", "Assigned To", "Data", help="Who is responsible."),
            FieldSpec("description", "Description / Work Done", "Text", span=2),
            FieldSpec("cost", "Cost", "Currency", in_list=True,
                      help="Informational — post a Journal Entry if you need it in the books."),
            FieldSpec("status", "Status", "Select", options="Open\nCompleted\nCancelled", in_list=True),
        ),
        list_fields=("name", "asset_id", "maintenance_type", "next_due_date", "status"),
    )
)


# --- Items & Pricing: Pricing Rule (Phase 3) ---------------------------------
register(
    DocTypeDescriptor(
        name="Pricing Rule",
        slug="pricing-rule",
        model=PricingRule,
        title_field="title",
        naming="field:title",
        group="Selling",
        permission_name="Pricing Rule",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("title", "Title", "Data", required=True, in_list=True, span=2),
            FieldSpec("selling", "Applies to Selling", "Check"),
            FieldSpec("buying", "Applies to Buying", "Check"),
            FieldSpec("apply_on", "Apply On", "Select", options="Item\nItem Group", in_list=True),
            FieldSpec("item_id", "Item", "Link", options="item", help="When Apply On = Item"),
            FieldSpec("item_group_id", "Item Group", "Link", options="item-group",
                      help="When Apply On = Item Group"),
            FieldSpec("customer_id", "Customer", "Link", options="customer",
                      help="Leave blank to apply to all customers"),
            FieldSpec("customer_group_id", "Customer Group", "Link", options="customer-group",
                      help="Apply to a whole customer group"),
            FieldSpec("territory_id", "Territory", "Link", options="territory",
                      help="Apply to a whole territory"),
            FieldSpec("min_qty", "Min Qty", "Float"),
            FieldSpec("max_qty", "Max Qty", "Float", help="0 = no upper limit"),
            FieldSpec("valid_from", "Valid From", "Date"),
            FieldSpec("valid_upto", "Valid Upto", "Date"),
            FieldSpec("rate_or_discount", "Rate or Discount", "Select",
                      options="Discount Percentage\nDiscount Amount\nRate", in_list=True),
            FieldSpec("discount_percentage", "Discount %", "Float"),
            FieldSpec("discount_amount", "Discount Amount", "Float"),
            FieldSpec("rate", "Rate", "Float"),
            FieldSpec("priority", "Priority", "Int", help="Higher wins when several rules match"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("title", "apply_on", "rate_or_discount", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Coupon Code",
        slug="coupon-code",
        model=CouponCode,
        title_field="coupon_code",
        naming="field:coupon_code",
        group="Selling",
        permission_name="Coupon Code",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("coupon_code", "Coupon Code", "Data", required=True, in_list=True, span=2),
            FieldSpec("coupon_name", "Coupon Name", "Data"),
            FieldSpec("discount_percentage", "Discount %", "Float", in_list=True),
            FieldSpec("valid_from", "Valid From", "Date"),
            FieldSpec("valid_upto", "Valid Upto", "Date"),
            FieldSpec("maximum_use", "Maximum Use", "Int", help="0 = unlimited"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        # "used" is maintained by the engine on redemption — shown in the list, not editable.
        list_fields=("coupon_code", "discount_percentage", "used", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Shipping Rule",
        slug="shipping-rule",
        model=ShippingRule,
        title_field="shipping_rule_name",
        naming="field:shipping_rule_name",
        group="Selling",
        permission_name="Shipping Rule",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("shipping_rule_name", "Shipping Rule Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("shipping_amount", "Shipping Amount", "Currency", in_list=True),
            FieldSpec("free_above", "Free Above Subtotal", "Currency", help="0 = never free"),
            FieldSpec(
                "transit_days",
                "Transit Days",
                "Int",
                in_list=True,
                help="Calendar days from dispatch to customer receipt (outbound delivery)",
            ),
            FieldSpec("account_id", "Freight Account", "Link", options="account"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("shipping_rule_name", "shipping_amount", "transit_days", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Product Bundle",
        slug="product-bundle",
        model=ProductBundle,
        title_field="bundle_name",
        naming="field:bundle_name",
        group="Selling",
        permission_name="Product Bundle",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("bundle_name", "Bundle Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("item_id", "Bundle Item (SKU)", "Link", options="item"),
            FieldSpec("description", "Description", "Text", span=2),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("bundle_name", "disabled"),
        children=(
            ChildSpec(
                field="items",
                label="Bundle Components",
                model=ProductBundleItem,
                fk_column="bundle_id",
                fields=(
                    FieldSpec("item_id", "Item", "Link", options="item", required=True),
                    FieldSpec("qty", "Qty", "Float"),
                    FieldSpec("description", "Description", "Data"),
                ),
            ),
        ),
    )
)

register(
    DocTypeDescriptor(
        name="Blanket Order",
        slug="blanket-order",
        model=BlanketOrder,
        title_field="blanket_order_name",
        naming="field:blanket_order_name",
        group="Selling",
        permission_name="Blanket Order",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("blanket_order_name", "Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("order_type", "Order Type", "Select", options="Selling\nBuying", in_list=True),
            FieldSpec("customer_id", "Customer", "Link", options="customer"),
            FieldSpec("supplier_id", "Supplier", "Link", options="supplier"),
            FieldSpec("valid_from", "Valid From", "Date"),
            FieldSpec("valid_upto", "Valid Upto", "Date"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("blanket_order_name", "order_type", "disabled"),
        children=(
            ChildSpec(
                field="items",
                label="Items",
                model=BlanketOrderItem,
                fk_column="blanket_order_id",
                fields=(
                    FieldSpec("item_id", "Item", "Link", options="item", required=True),
                    FieldSpec("qty", "Qty", "Float"),
                    FieldSpec("rate", "Agreed Rate", "Currency"),
                ),
            ),
        ),
    )
)

register(
    DocTypeDescriptor(
        name="Promotional Scheme",
        slug="promotional-scheme",
        model=PromotionalScheme,
        title_field="scheme_name",
        naming="field:scheme_name",
        group="Selling",
        permission_name="Promotional Scheme",
        permissions={"Sales Manager": _SALES_MANAGER, "Sales User": _SALES_USER},
        fields=(
            FieldSpec("scheme_name", "Scheme Name", "Data", required=True, in_list=True, span=2),
            FieldSpec("apply_on", "Apply On", "Select", options="Item\nItem Group", in_list=True),
            FieldSpec("item_id", "Item", "Link", options="item"),
            FieldSpec("item_group_id", "Item Group", "Link", options="item-group"),
            FieldSpec("customer_id", "Customer", "Link", options="customer",
                      help="Leave blank to apply to all customers"),
            FieldSpec("valid_from", "Valid From", "Date"),
            FieldSpec("valid_upto", "Valid Upto", "Date"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("scheme_name", "apply_on", "disabled"),
        children=(
            ChildSpec(
                field="tiers",
                label="Discount Tiers",
                model=PromotionalSchemeTier,
                fk_column="scheme_id",
                fields=(
                    FieldSpec("min_qty", "Min Qty", "Float", required=True),
                    FieldSpec("discount_percentage", "Discount %", "Float"),
                ),
            ),
        ),
    )
)


# --- Stock: Batch (Phase 5B) -------------------------------------------------


async def _validate_batch(db, descriptor, obj, user):  # noqa: ANN001 — engine hook signature
    """A batch's identity (item + number) is fixed at creation; only its expiry /
    disabled state changes. The item must belong to this company and be batch-tracked.
    Closes cross-tenant item references, batches on non-batched items, and re-pointing
    an in-use batch to a different item (which would orphan stored line labels)."""
    insp = sa_inspect(obj)
    item_hist = insp.attrs.item_id.history
    if item_hist.deleted and item_hist.deleted[0] != obj.item_id:
        raise ValidationError("A batch's item cannot be changed after creation", field="item_id")
    batch_hist = insp.attrs.batch_no.history
    if batch_hist.deleted and batch_hist.deleted[0] != obj.batch_no:
        raise ValidationError("A batch number cannot be renamed after creation", field="batch_no")
    item = await db.get(Item, obj.item_id)
    if item is None or item.company_id != obj.company_id:
        raise ValidationError("Item not found in this company", field="item_id")
    if not item.has_batch_no:
        raise ValidationError(
            f"Item '{item.item_code}' is not batch-tracked — enable batch tracking on it first",
            field="item_id",
        )


async def _block_batch_delete_if_referenced(db, descriptor, obj, user):  # noqa: ANN001
    """A batch named on any stock document line can't be deleted (the line stores the
    batch_no as a string with no FK, so the engine's IntegrityError guard can't catch it)."""
    for model in (PurchaseReceiptItem, DeliveryNoteItem, StockEntryItem):
        ref = await db.scalar(
            select(model.id)
            .where(model.batch_no == obj.batch_no, model.item_id == obj.item_id)
            .limit(1)
        )
        if ref is not None:
            raise ValidationError(
                f"Cannot delete: batch '{obj.batch_no}' is referenced by stock documents",
                code="ERR_LINKED",
            )


register(
    DocTypeDescriptor(
        name="Batch",
        slug="batch",
        model=Batch,
        title_field="batch_no",
        naming="field:batch_no",
        group="Stock",
        permission_name="Batch",
        permissions={"Stock Manager": _STOCK_MANAGER, "Stock User": _STOCK_USER_RW},
        fields=(
            FieldSpec("batch_no", "Batch No", "Data", required=True, in_list=True, span=2, unique=True),
            FieldSpec("item_id", "Item", "Link", options="item", required=True, in_list=True),
            FieldSpec("expiry_date", "Expiry Date", "Date", in_list=True,
                      help="Deliveries/issues of an expired batch are blocked"),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("batch_no", "item_id", "expiry_date", "disabled"),
        hooks={"validate": _validate_batch, "before_delete": _block_batch_delete_if_referenced},
    )
)


# --- Stock: Item Alternative -------------------------------------------------


async def _validate_item_alternative(db, descriptor, obj, user):  # noqa: ANN001
    """Item ≠ alternative; both in-company stock items; set list title; optional reverse."""
    if obj.item_id == obj.alternative_item_id:
        raise ValidationError(
            "Item and alternative item must be different",
            field="alternative_item_id",
        )
    item = await db.get(Item, obj.item_id)
    if item is None or item.company_id != obj.company_id:
        raise ValidationError("Item not found in this company", field="item_id")
    alt = await db.get(Item, obj.alternative_item_id)
    if alt is None or alt.company_id != obj.company_id:
        raise ValidationError("Alternative item not found in this company", field="alternative_item_id")
    if not item.is_stock_item:
        raise ValidationError(f"'{item.item_code}' is not a stock item", field="item_id")
    if not alt.is_stock_item:
        raise ValidationError(
            f"'{alt.item_code}' is not a stock item",
            field="alternative_item_id",
        )

    obj.title = f"{item.item_code} → {alt.item_code}"

    # Idempotent reverse link when two_way is set (reverse row itself stays one-way).
    if obj.two_way:
        existing_rev = await db.scalar(
            select(ItemAlternative.id).where(
                ItemAlternative.company_id == obj.company_id,
                ItemAlternative.item_id == obj.alternative_item_id,
                ItemAlternative.alternative_item_id == obj.item_id,
            )
        )
        if existing_rev is None:
            db.add(
                ItemAlternative(
                    company_id=obj.company_id,
                    item_id=obj.alternative_item_id,
                    alternative_item_id=obj.item_id,
                    two_way=False,
                    title=f"{alt.item_code} → {item.item_code}",
                    owner=user.id,
                    modified_by=user.id,
                )
            )


register(
    DocTypeDescriptor(
        name="Item Alternative",
        slug="item-alternative",
        model=ItemAlternative,
        title_field="title",
        naming="field:title",
        group="Stock",
        permission_name="Item Alternative",
        permissions={
            "Stock Manager": _STOCK_MANAGER,
            "Stock User": _STOCK_USER_RW,
            "System Manager": _STOCK_MANAGER,
        },
        fields=(
            FieldSpec("item_id", "Item", "Link", options="item", required=True, in_list=True),
            FieldSpec(
                "alternative_item_id",
                "Alternative Item",
                "Link",
                options="item",
                required=True,
                in_list=True,
            ),
            FieldSpec(
                "two_way",
                "Two Way",
                "Check",
                in_list=True,
                help="Also allow the original item as a substitute for the alternative",
            ),
            FieldSpec("title", "Title", "Data", in_list=True, read_only=True,
                      help="Auto-set from item codes"),
        ),
        list_fields=("title", "item_id", "alternative_item_id", "two_way"),
        hooks={"validate": _validate_item_alternative},
    )
)


# --- Manufacturing: Operation / Workstation / Routing -------------------------


_MFG_MANAGER = ("read", "write", "create", "delete", "report")
_MFG_PERMS = {
    "Manufacturing Manager": _MFG_MANAGER,
    "System Manager": _MFG_MANAGER,
}


async def _validate_routing(db, descriptor, obj, user):  # noqa: ANN001
    """Unique sequence numbers; operations must belong to this company."""
    seen: set[int] = set()
    for row in obj.operations or []:
        if row.idx in seen:
            raise ValidationError(
                f"Duplicate sequence {row.idx} on Routing operations",
                field="operations",
            )
        seen.add(row.idx)
        op = await db.get(Operation, row.operation_id)
        if op is None or op.company_id != obj.company_id:
            raise ValidationError("Operation not found in this company", field="operations")
        if op.disabled:
            raise ValidationError(
                f"Operation '{op.operation_name}' is disabled",
                field="operations",
            )
        if row.workstation_id is not None:
            ws = await db.get(Workstation, row.workstation_id)
            if ws is None or ws.company_id != obj.company_id:
                raise ValidationError("Workstation not found in this company", field="operations")
            if ws.disabled:
                raise ValidationError(
                    f"Workstation '{ws.workstation_name}' is disabled",
                    field="operations",
                )
        if row.time_in_mins is not None and row.time_in_mins < 0:
            raise ValidationError("Time in minutes cannot be negative", field="operations")


register(
    DocTypeDescriptor(
        name="Operation",
        slug="operation",
        model=Operation,
        title_field="operation_name",
        naming="field:operation_name",
        group="Manufacturing",
        permission_name="Operation",
        permissions=_MFG_PERMS,
        fields=(
            FieldSpec(
                "operation_name", "Operation Name", "Data",
                required=True, in_list=True, span=2, unique=True,
            ),
            FieldSpec("default_hour_rate", "Default Hour Rate", "Currency", in_list=True),
            FieldSpec("description", "Description", "Text", span=2),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("operation_name", "default_hour_rate", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Workstation",
        slug="workstation",
        model=Workstation,
        title_field="workstation_name",
        naming="field:workstation_name",
        group="Manufacturing",
        permission_name="Workstation",
        permissions=_MFG_PERMS,
        fields=(
            FieldSpec(
                "workstation_name", "Workstation Name", "Data",
                required=True, in_list=True, span=2, unique=True,
            ),
            FieldSpec("hour_rate", "Hour Rate", "Currency", in_list=True),
            FieldSpec(
                "working_hours", "Working Hours / Day", "Float", in_list=True,
                help="Used for soft capacity warnings on Work Order submit.",
            ),
            FieldSpec("description", "Description", "Text", span=2),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("workstation_name", "hour_rate", "working_hours", "disabled"),
    )
)

register(
    DocTypeDescriptor(
        name="Routing",
        slug="routing",
        model=Routing,
        title_field="routing_name",
        naming="field:routing_name",
        group="Manufacturing",
        permission_name="Routing",
        permissions=_MFG_PERMS,
        fields=(
            FieldSpec(
                "routing_name", "Routing Name", "Data",
                required=True, in_list=True, span=2, unique=True,
            ),
            FieldSpec("disabled", "Disabled", "Check", in_list=True),
        ),
        list_fields=("routing_name", "disabled"),
        children=(
            ChildSpec(
                field="operations",
                label="Operations",
                model=RoutingOperation,
                fk_column="routing_id",
                fields=(
                    FieldSpec("idx", "Sequence", "Int", required=True),
                    FieldSpec(
                        "operation_id", "Operation", "Link",
                        options="operation", required=True,
                    ),
                    FieldSpec(
                        "workstation_id", "Workstation", "Link",
                        options="workstation",
                    ),
                    FieldSpec("time_in_mins", "Time (mins)", "Float"),
                ),
            ),
        ),
        hooks={"validate": _validate_routing},
    )
)


# --- Link sources ------------------------------------------------------------
# Lets engine Link fields target core/bespoke doctypes (not just engine masters),
# e.g. an Address's "customer" Link resolves Customers. Maps slug -> (model,
# title_field, permission_name). Engine descriptors are resolved first.
LINK_SOURCES: dict[str, tuple[type, str, str]] = {
    "customer": (Customer, "customer_name", "Customer"),
    # "supplier" is now a registered descriptor (resolved first) — no LINK_SOURCES entry needed.
    "item": (Item, "item_code", "Item"),
    "item-group": (ItemGroup, "item_group_name", "Item Group"),
    "account": (Account, "account_name", "Account"),
    # the bespoke Asset doc, so maintenance/repair logs can Link to it
    "asset": (Asset, "asset_name", "Asset"),
}


def resolve_link_source(slug: str) -> tuple[type, str, str, bool] | None:
    """Resolve a Link target slug to (model, title_field, permission_name, scoped).

    Checks registered descriptors first, then the LINK_SOURCES table for core
    doctypes. Returns None if the slug is unknown.
    """
    descriptor = REGISTRY.get(slug)
    if descriptor is not None:
        return (descriptor.model, descriptor.title_field, descriptor.permission_name, descriptor.scoped)
    src = LINK_SOURCES.get(slug)
    if src is not None:
        model, title_field, permission_name = src
        return (model, title_field, permission_name, True)
    return None
