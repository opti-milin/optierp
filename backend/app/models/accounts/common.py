"""Module 02 (Accounts) — shared enums, constants, and column mixins."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, Enum, ForeignKey, Integer, Numeric, String, Text, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


ROOT_TYPES = ("Asset", "Liability", "Equity", "Income", "Expense")
REPORT_TYPES = ("Balance Sheet", "Profit and Loss")

# Normal balance side per root type (Section 3, Module 02, rule 4)
ROOT_TYPE_BALANCE = {
    "Asset": "Debit",
    "Expense": "Debit",
    "Liability": "Credit",
    "Equity": "Credit",
    "Income": "Credit",
}

ROOT_TYPE_REPORT = {
    "Asset": "Balance Sheet",
    "Liability": "Balance Sheet",
    "Equity": "Balance Sheet",
    "Income": "Profit and Loss",
    "Expense": "Profit and Loss",
}

root_type_enum = Enum(*ROOT_TYPES, name="account_root_type")
report_type_enum = Enum(*REPORT_TYPES, name="account_report_type")


CHARGE_TYPES = ("Actual", "On Net Total", "On Previous Row Amount", "On Previous Row Total", "On Item Quantity")
charge_type_enum = Enum(*CHARGE_TYPES, name="tax_charge_type")

PARTY_TYPES = ("Customer", "Supplier")
party_type_enum = Enum(*PARTY_TYPES, name="party_type")

INVOICE_STATUSES = ("Draft", "Unpaid", "Partly Paid", "Paid", "Overdue", "Cancelled", "Return")
invoice_status_enum = Enum(*INVOICE_STATUSES, name="invoice_status")


class TaxRowMixin:
    """Shared columns for Sales/Purchase Taxes and Charges rows
    (source: erpnext Sales Taxes and Charges child doctype)."""

    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    charge_type: Mapped[str] = mapped_column(charge_type_enum, nullable=False)
    row_id: Mapped[int | None] = mapped_column(Integer)  # 1-based ref for On Previous Row *
    description: Mapped[str | None] = mapped_column(String(300))
    rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    base_total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    # When true, the item rate is treated as tax-inclusive (MRP/GST-inclusive)
    # and the net rate is back-calculated by the taxes_and_totals engine.
    included_in_print_rate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    @declared_attr
    def account_head_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)

    @declared_attr
    def cost_center_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(UUID(as_uuid=True), ForeignKey("cost_centers.id"))


class VoucherMixin:
    """Common columns for submittable financial documents."""

    name: Mapped[str] = mapped_column(String(140), nullable=False)  # naming-series doc number
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    workflow_state: Mapped[str | None] = mapped_column(String(100))
    remarks: Mapped[str | None] = mapped_column(Text)
    amended_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class TotalsMixin:
    """taxes_and_totals output columns, shared by invoices AND order documents
    (Quotation / Sales Order / Purchase Order arrive with Modules 04-05)."""

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    conversion_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    total_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    net_total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_net_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    total_taxes_and_charges: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    base_total_taxes_and_charges: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    apply_discount_on: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Grand Total", server_default=text("'Grand Total'")
    )
    additional_discount_percentage: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    grand_total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_grand_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rounded_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rounding_adjustment: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )


class InvoiceMixin(VoucherMixin, TotalsMixin):
    """Shared invoice header fields (totals + receivable/payable state)."""

    due_date: Mapped[date | None] = mapped_column(Date)
    outstanding_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    advance_paid: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    is_return: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # India GST: place of supply, recorded as the portal "NN-State" label (e.g.
    # "27-Maharashtra"). Phase 1 stores + prints it (and feeds the GSTR-1 POS column);
    # the CGST+SGST-vs-IGST split is still derived from the GSTIN prefixes in
    # resolve_tax_template, not from this field. String(64): longest label is 43 chars.
    place_of_supply: Mapped[str | None] = mapped_column(String(64))
    is_reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Opening (migration-in) invoice: posts the outstanding against Temporary
    # Opening instead of income/expense, and is excluded from sales/purchase registers.
    is_opening: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # India TDS/TCS: the chosen withholding category and the computed amount
    # (base currency). On purchase it reduces the payable; on sales it adds to
    # the receivable. Null/0 = no withholding.
    tax_withholding_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        invoice_status_enum, nullable=False, default="Draft", server_default=text("'Draft'")
    )

    @declared_attr
    def tax_withholding_category_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(UUID(as_uuid=True), ForeignKey("tax_withholding_categories.id"))


class InvoiceItemMixin:
    """Shared invoice line fields. item_code is free-text until Module 03
    introduces the Item master (then it gains an FK)."""

    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_code: Mapped[str | None] = mapped_column(String(140))
    item_name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # India GST: HSN/SAC code snapshotted from the item at billing (legally required on a
    # tax invoice line; the GSTR-1 HSN summary groups by it).
    hsn_sac_code: Mapped[str | None] = mapped_column(String(8))
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=1, server_default=text("1"))
    uom: Mapped[str | None] = mapped_column(String(140))
    # Multi-UOM (Phase 4): qty is in the transaction UOM; stock_qty = qty *
    # conversion_factor is in the item's stock UOM and drives Bin/demand/caps.
    conversion_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    stock_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # Per-line pricing: price_list_rate is the pre-discount base; rate is the
    # post-discount net rate. discount_% / discount_amount describe the relation
    # (cf. ERPNext sales/purchase item: price_list_rate -> discount -> rate).
    price_list_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    base_price_list_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    discount_percentage: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_net_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    @declared_attr
    def cost_center_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(UUID(as_uuid=True), ForeignKey("cost_centers.id"))


