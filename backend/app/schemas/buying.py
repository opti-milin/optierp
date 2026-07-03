"""Module 04 — Buying Pydantic schemas.

OrderItemIn / order response bases are shared with Module 05 (selling
imports them) — orders on both sides run the same taxes_and_totals engine.
"""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.schemas.accounts import InvoiceTaxResponse, TaxRowIn
from app.schemas.common import DocumentMeta, ORMModel

# --- shared order building blocks ---------------------------------------------------


class OrderItemIn(BaseModel):
    item_id: uuid.UUID | None = None  # None = free-text line (a hand-typed item_name)
    item_name: str | None = Field(default=None, max_length=140)
    qty: Decimal = Field(gt=0)
    rate: Decimal | None = Field(ge=0, default=None)  # None -> resolved from price list / item
    # Per-line discount (ERPNext parity): a % derives the absolute amount; the
    # resolved rate becomes the price_list_rate base unless one is supplied.
    price_list_rate: Decimal | None = Field(ge=0, default=None)
    discount_percentage: Decimal = Field(ge=0, le=100, default=Decimal("0"))
    discount_amount: Decimal = Field(ge=0, default=Decimal("0"))
    uom: str | None = None
    description: str | None = None
    warehouse_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    schedule_date: date | None = None  # purchase side
    delivery_date: date | None = None  # sales side
    material_request_item_id: uuid.UUID | None = None  # purchase side
    quotation_item_id: uuid.UUID | None = None  # sales side

    @model_validator(mode="after")
    def _require_item(self) -> "OrderItemIn":
        # a line must identify an item: either a master item_id, or a free-text item_name
        if self.item_id is None and not (self.item_name and self.item_name.strip()):
            raise ValueError("item_name is required for a free-text line (no item_id)")
        return self


class OrderCreateBase(BaseModel):
    posting_date: date
    currency: str | None = Field(default=None, pattern="^[A-Za-z]{3}$")
    conversion_rate: Decimal = Field(gt=0, default=Decimal("1"))
    apply_discount_on: str = "Grand Total"
    additional_discount_percentage: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    remarks: str | None = None
    items: list[OrderItemIn] = Field(min_length=1)
    taxes: list[TaxRowIn] = Field(default_factory=list)
    tax_template_id: uuid.UUID | None = None
    payment_terms_template_id: uuid.UUID | None = None


class OrderItemResponse(DocumentMeta):
    idx: int
    item_id: uuid.UUID | None
    item_code: str | None
    item_name: str
    description: str | None
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
    warehouse_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None


class OrderResponseBase(DocumentMeta):
    name: str
    posting_date: date
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
    status: str
    remarks: str | None
    company_id: uuid.UUID
    payment_terms_template_id: uuid.UUID | None = None


# --- purchase order -------------------------------------------------------------------


class PurchaseOrderCreate(OrderCreateBase):
    supplier_id: uuid.UUID
    schedule_date: date | None = None
    terms: str | None = None
    supplier_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    set_warehouse_id: uuid.UUID | None = None
    supplier_quotation_id: uuid.UUID | None = None


class PurchaseOrderItemResponse(OrderItemResponse):
    schedule_date: date | None = None
    received_qty: Decimal
    billed_amt: Decimal
    material_request_item_id: uuid.UUID | None = None


class PurchaseOrderResponse(OrderResponseBase):
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    schedule_date: date | None
    terms: str | None = None
    supplier_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    set_warehouse_id: uuid.UUID | None
    supplier_quotation_id: uuid.UUID | None
    per_received: Decimal
    per_billed: Decimal
    items: list[PurchaseOrderItemResponse]
    taxes: list[InvoiceTaxResponse]


class OrderListItem(ORMModel):
    """Shared list row for PO / SO / quotations."""

    id: uuid.UUID
    name: str
    posting_date: date
    customer_name: str | None = None
    supplier_name: str | None = None
    currency: str | None = None
    grand_total: Decimal
    status: str
    per_received: Decimal | None = None
    per_delivered: Decimal | None = None
    per_billed: Decimal | None = None
    docstatus: int


# --- request for quotation ---------------------------------------------------------------


class RFQItemIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    uom: str | None = None
    warehouse_id: uuid.UUID | None = None
    schedule_date: date | None = None


class RFQCreate(BaseModel):
    posting_date: date
    schedule_date: date | None = None
    message_for_supplier: str | None = None
    remarks: str | None = None
    items: list[RFQItemIn] = Field(min_length=1)
    supplier_ids: list[uuid.UUID] = Field(min_length=1)


class RFQItemResponse(DocumentMeta):
    idx: int
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    qty: Decimal
    uom: str | None
    warehouse_id: uuid.UUID | None
    schedule_date: date | None


class RFQSupplierResponse(DocumentMeta):
    idx: int
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    quote_status: str


class RFQResponse(DocumentMeta):
    name: str
    posting_date: date
    schedule_date: date | None
    message_for_supplier: str | None
    status: str
    remarks: str | None
    company_id: uuid.UUID
    items: list[RFQItemResponse]
    suppliers: list[RFQSupplierResponse]


class RFQListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    status: str
    docstatus: int


# --- supplier quotation ---------------------------------------------------------------------


class SupplierQuotationItemIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    rate: Decimal = Field(ge=0)
    uom: str | None = None
    rfq_item_id: uuid.UUID | None = None


class SupplierQuotationCreate(BaseModel):
    supplier_id: uuid.UUID
    posting_date: date
    valid_till: date | None = None
    rfq_id: uuid.UUID | None = None
    currency: str | None = Field(default=None, pattern="^[A-Za-z]{3}$")
    conversion_rate: Decimal = Field(gt=0, default=Decimal("1"))
    remarks: str | None = None
    items: list[SupplierQuotationItemIn] = Field(min_length=1)


class SupplierQuotationItemResponse(OrderItemResponse):
    rfq_item_id: uuid.UUID | None = None


class SupplierQuotationResponse(OrderResponseBase):
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    rfq_id: uuid.UUID | None
    valid_till: date | None
    items: list[SupplierQuotationItemResponse]
