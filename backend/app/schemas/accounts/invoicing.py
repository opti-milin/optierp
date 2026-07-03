"""Accounts schemas — Sales & Purchase Invoices."""

import uuid
from datetime import date
from decimal import Decimal


from app.schemas.common import DocumentMeta, ORMModel

from app.schemas.accounts.common import InvoiceCreateBase

class SalesInvoiceCreate(InvoiceCreateBase):
    customer_id: uuid.UUID
    debit_to_id: uuid.UUID | None = None  # defaults from customer/company
    po_no: str | None = None
    po_date: date | None = None
    terms: str | None = None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None


class PurchaseInvoiceCreate(InvoiceCreateBase):
    supplier_id: uuid.UUID
    credit_to_id: uuid.UUID | None = None
    bill_no: str | None = None
    bill_date: date | None = None
    terms: str | None = None
    supplier_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None


# --- Opening Invoice Creation Tool -----------------------------------------------------------


class InvoiceItemResponse(DocumentMeta):
    idx: int
    item_code: str | None
    item_name: str
    hsn_sac_code: str | None = None
    qty: Decimal
    uom: str | None
    conversion_factor: Decimal
    stock_qty: Decimal
    price_list_rate: Decimal
    discount_percentage: Decimal
    discount_amount: Decimal
    rate: Decimal
    amount: Decimal
    net_amount: Decimal
    base_net_amount: Decimal
    cost_center_id: uuid.UUID | None
    item_id: uuid.UUID | None = None
    sales_order_item_id: uuid.UUID | None = None
    delivery_note_item_id: uuid.UUID | None = None
    purchase_order_item_id: uuid.UUID | None = None
    purchase_receipt_item_id: uuid.UUID | None = None


class InvoiceTaxResponse(DocumentMeta):
    idx: int
    charge_type: str
    rate: Decimal
    row_id: int | None
    account_head_id: uuid.UUID
    description: str | None
    tax_amount: Decimal
    total: Decimal
    base_tax_amount: Decimal
    base_total: Decimal


class InvoiceResponseBase(DocumentMeta):
    name: str
    posting_date: date
    due_date: date | None
    currency: str
    conversion_rate: Decimal
    total_qty: Decimal
    total: Decimal
    net_total: Decimal
    base_net_total: Decimal
    total_taxes_and_charges: Decimal
    discount_amount: Decimal
    grand_total: Decimal
    base_grand_total: Decimal
    rounded_total: Decimal
    rounding_adjustment: Decimal
    outstanding_amount: Decimal
    advance_paid: Decimal
    is_return: bool
    tax_withholding_amount: Decimal
    tax_withholding_category_id: uuid.UUID | None
    place_of_supply: str | None = None
    is_reverse_charge: bool = False
    status: str
    remarks: str | None
    workflow_state: str | None
    company_id: uuid.UUID
    payment_terms_template_id: uuid.UUID | None = None
    items: list[InvoiceItemResponse]
    taxes: list[InvoiceTaxResponse]


class SalesInvoiceResponse(InvoiceResponseBase):
    customer_id: uuid.UUID
    customer_name: str | None = None
    debit_to_id: uuid.UUID
    return_against_id: uuid.UUID | None
    po_no: str | None = None
    po_date: date | None = None
    terms: str | None = None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None


class PurchaseInvoiceResponse(InvoiceResponseBase):
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    credit_to_id: uuid.UUID
    return_against_id: uuid.UUID | None
    bill_no: str | None
    bill_date: date | None
    terms: str | None = None
    supplier_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None


class InvoiceListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    due_date: date | None = None
    currency: str | None = None
    # one of the two is set, depending on the list (sales vs purchase)
    customer_name: str | None = None
    supplier_name: str | None = None
    grand_total: Decimal
    outstanding_amount: Decimal
    status: str
    docstatus: int


# --- Payment Entry ------------------------------------------------------------------------


