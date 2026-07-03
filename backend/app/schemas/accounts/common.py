"""Module 02 (Accounts) schemas — shared bases (tax row, invoice item, invoice create base)."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class InvoiceTaxLinePreview(BaseModel):
    """One computed tax row for the draft-invoice GST preview (display only)."""

    description: str
    rate: Decimal
    tax_amount: Decimal


class InvoiceTaxPreview(BaseModel):
    """Server-computed taxes + totals for a draft invoice, so the form can show
    the GST that create will apply — before anything is saved."""

    net_total: Decimal
    total_taxes_and_charges: Decimal
    grand_total: Decimal
    place_of_supply: str | None = None
    taxes: list[InvoiceTaxLinePreview] = []


class TaxRowIn(BaseModel):
    charge_type: str = "On Net Total"
    rate: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")  # input for charge_type Actual
    row_id: int | None = None
    account_head_id: uuid.UUID
    cost_center_id: uuid.UUID | None = None
    description: str | None = None
    add_deduct_tax: str = "Add"
    category: str = "Total"
    included_in_print_rate: bool = False


class InvoiceItemIn(BaseModel):
    item_code: str | None = None
    item_name: str = Field(min_length=1, max_length=140)
    description: str | None = None
    qty: Decimal = Field(gt=0, default=Decimal("1"))
    uom: str | None = None
    rate: Decimal = Field(ge=0, default=Decimal("0"))
    # Per-line discount (ERPNext parity); see schemas.buying.OrderItemIn.
    # Distinct from the document-level discount_amount on InvoiceCreateBase.
    price_list_rate: Decimal | None = Field(ge=0, default=None)
    discount_percentage: Decimal = Field(ge=0, le=100, default=Decimal("0"))
    discount_amount: Decimal = Field(ge=0, default=Decimal("0"))
    cost_center_id: uuid.UUID | None = None
    # sales: income account; purchase: expense account (falls back to company default)
    account_id: uuid.UUID | None = None
    # India GST: HSN/SAC override; if omitted it's snapshotted from the item master
    hsn_sac_code: str | None = Field(default=None, max_length=8)
    # Module 03-05 cycle links (all optional; free-text items still work)
    item_id: uuid.UUID | None = None
    sales_order_item_id: uuid.UUID | None = None  # sales invoices only
    delivery_note_item_id: uuid.UUID | None = None  # sales invoices only
    purchase_order_item_id: uuid.UUID | None = None  # purchase invoices only
    purchase_receipt_item_id: uuid.UUID | None = None  # purchase invoices only


class InvoiceCreateBase(BaseModel):
    posting_date: date
    due_date: date | None = None
    currency: str | None = None  # defaults to company currency
    conversion_rate: Decimal = Field(gt=0, default=Decimal("1"))
    apply_discount_on: str = "Grand Total"
    additional_discount_percentage: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    remarks: str | None = None
    items: list[InvoiceItemIn] = Field(min_length=1)
    taxes: list[TaxRowIn] = Field(default_factory=list)
    tax_template_id: uuid.UUID | None = None  # alternative to inline taxes
    payment_terms_template_id: uuid.UUID | None = None
    is_return: bool = False
    return_against_id: uuid.UUID | None = None
    is_opening: bool = False  # migration-in opening invoice (posts vs Temporary Opening)
    tax_withholding_category_id: uuid.UUID | None = None  # India TDS (purchase) / TCS (sales)
    # India GST: place of supply ("NN-State"); defaults from the party/company GSTIN when omitted
    place_of_supply: str | None = Field(default=None, max_length=64)
    is_reverse_charge: bool = False


