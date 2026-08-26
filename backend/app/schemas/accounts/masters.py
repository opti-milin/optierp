"""Accounts schemas — masters (customer/supplier/account/tax/opening tool)."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel

from app.schemas.accounts.common import TaxRowIn

class CustomerCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=140)
    customer_type: str = "Company"
    tax_id: str | None = None
    email_id: str | None = None
    default_currency: str | None = None
    receivable_account_id: uuid.UUID | None = None
    credit_limit: Decimal | None = None
    tax_category_id: uuid.UUID | None = None


class CustomerResponse(DocumentMeta):
    customer_name: str
    customer_type: str
    tax_id: str | None
    email_id: str | None
    default_currency: str | None
    receivable_account_id: uuid.UUID | None
    credit_limit: Decimal | None
    tax_category_id: uuid.UUID | None
    payment_terms_template_id: uuid.UUID | None = None
    customer_group_id: uuid.UUID | None = None
    territory_id: uuid.UUID | None = None
    notes: str | None = None
    disabled: bool
    company_id: uuid.UUID


class SupplierCreate(BaseModel):
    supplier_name: str = Field(min_length=1, max_length=140)
    supplier_type: str = "Company"
    tax_id: str | None = None
    email_id: str | None = None
    default_currency: str | None = None
    payable_account_id: uuid.UUID | None = None
    tax_category_id: uuid.UUID | None = None


class SupplierResponse(DocumentMeta):
    supplier_name: str
    supplier_type: str
    tax_id: str | None
    email_id: str | None
    default_currency: str | None
    payable_account_id: uuid.UUID | None
    tax_category_id: uuid.UUID | None
    payment_terms_template_id: uuid.UUID | None = None
    supplier_group_id: uuid.UUID | None = None
    notes: str | None = None
    disabled: bool
    company_id: uuid.UUID


class PaymentRequestCreate(BaseModel):
    customer_id: uuid.UUID
    reference_invoice_id: uuid.UUID | None = None
    posting_date: date
    due_date: date | None = None
    amount: Decimal = Field(gt=0)
    currency: str | None = None
    message: str | None = None
    payment_url: str | None = None


class PaymentRequestResponse(DocumentMeta):
    name: str
    customer_id: uuid.UUID
    customer_name: str | None
    reference_invoice_id: uuid.UUID | None
    posting_date: date
    due_date: date | None
    amount: Decimal
    currency: str | None
    status: str
    message: str | None
    payment_url: str | None
    company_id: uuid.UUID


class PaymentRequestListItem(ORMModel):
    id: uuid.UUID
    name: str
    customer_name: str | None
    posting_date: date
    amount: Decimal
    status: str


# --- Account (full CRUD arrives with this module) ----------------------------------


class AccountCreate(BaseModel):
    account_name: str = Field(min_length=1, max_length=140)
    parent_account_id: uuid.UUID
    account_number: str | None = None
    account_type: str | None = None
    cm_class: str | None = None
    is_group: bool = False
    account_currency: str | None = None


class AccountResponse(DocumentMeta):
    account_name: str
    account_number: str | None
    parent_account_id: uuid.UUID | None
    root_type: str
    report_type: str
    account_type: str | None
    cm_class: str | None = None
    is_group: bool
    account_currency: str | None
    freeze_account: bool
    disabled: bool
    path: str
    company_id: uuid.UUID


class AccountUpdate(BaseModel):
    """Partial update of an account. root_type / report_type / parent and
    is_group are structural and not editable here. A rename cascades the ltree
    path to descendants in the service."""

    account_name: str | None = Field(default=None, min_length=1, max_length=140)
    account_number: str | None = None
    account_type: str | None = None
    cm_class: str | None = None
    account_currency: str | None = None
    freeze_account: bool | None = None
    disabled: bool | None = None


# --- Tax categories ------------------------------------------------------------------


class TaxCategoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    is_inter_state: bool = False  # True ⇒ out-of-state (IGST) for GSTIN auto place-of-supply
    disabled: bool = False


class TaxCategoryResponse(DocumentMeta):
    title: str
    is_inter_state: bool
    disabled: bool
    company_id: uuid.UUID


# --- Tax templates -------------------------------------------------------------------


class TaxTemplateCreate(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    kind: str = Field(pattern="^(sales|purchase)$")
    is_default: bool = False
    tax_category_id: uuid.UUID | None = None
    details: list[TaxRowIn] = Field(default_factory=list)


class TaxTemplateUpdate(BaseModel):
    """Full replace of a template's header + rows. ``kind`` is immutable."""

    title: str = Field(min_length=1, max_length=140)
    is_default: bool = False
    tax_category_id: uuid.UUID | None = None
    details: list[TaxRowIn] = Field(default_factory=list)


class TaxTemplateDetailResponse(DocumentMeta):
    idx: int
    charge_type: str
    rate: Decimal
    tax_amount: Decimal
    row_id: int | None
    account_head_id: uuid.UUID
    cost_center_id: uuid.UUID | None
    description: str | None
    add_deduct_tax: str
    category: str
    included_in_print_rate: bool


class TaxTemplateResponse(DocumentMeta):
    title: str
    kind: str
    is_default: bool
    disabled: bool
    tax_category_id: uuid.UUID | None
    company_id: uuid.UUID
    details: list[TaxTemplateDetailResponse]


# --- Journal Entry --------------------------------------------------------------------


class OpeningInvoiceRow(BaseModel):
    party_id: uuid.UUID | None = None  # an existing customer/supplier
    party_name: str | None = None  # used to find-or-create when create_missing_party is on
    item_name: str = "Opening Invoice"
    outstanding_amount: Decimal = Field(gt=0)
    bill_no: str | None = None  # purchase: original supplier bill no
    posting_date: date | None = None  # falls back to the tool's posting_date
    due_date: date | None = None  # falls back to posting_date
    remarks: str | None = None


class OpeningInvoiceTool(BaseModel):
    invoice_type: str = Field(pattern="^(sales|purchase)$")
    posting_date: date  # the go-live / cutover date (default for rows without one)
    create_missing_party: bool = False  # create the customer/supplier from party_name if absent
    rows: list[OpeningInvoiceRow] = Field(min_length=1)


class OpeningInvoiceResult(BaseModel):
    created: list[str]
    count: int


