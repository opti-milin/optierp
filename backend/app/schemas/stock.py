"""Module 03 — Stock Pydantic schemas."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.gst_states import GST_TREATMENT_PATTERN
from app.schemas.common import DocumentMeta, ORMModel

# --- masters ------------------------------------------------------------------------


class ItemGroupCreate(BaseModel):
    item_group_name: str = Field(min_length=1, max_length=140)
    parent_item_group_id: uuid.UUID | None = None
    is_group: bool = False


class ItemGroupResponse(DocumentMeta):
    item_group_name: str
    parent_item_group_id: uuid.UUID | None
    is_group: bool
    disabled: bool
    company_id: uuid.UUID


class WarehouseCreate(BaseModel):
    warehouse_name: str = Field(min_length=1, max_length=140)
    parent_warehouse_id: uuid.UUID | None = None
    is_group: bool = False
    warehouse_type: str | None = None
    account_id: uuid.UUID | None = None


class WarehouseUpdate(BaseModel):
    warehouse_name: str | None = Field(default=None, min_length=1, max_length=140)
    parent_warehouse_id: uuid.UUID | None = None
    warehouse_type: str | None = None
    account_id: uuid.UUID | None = None
    disabled: bool | None = None


class WarehouseResponse(DocumentMeta):
    warehouse_name: str
    parent_warehouse_id: uuid.UUID | None
    is_group: bool
    warehouse_type: str | None
    account_id: uuid.UUID | None
    disabled: bool
    company_id: uuid.UUID


class ItemCreate(BaseModel):
    item_code: str = Field(min_length=1, max_length=140)
    item_name: str | None = None  # defaults to item_code
    description: str | None = None
    item_group_id: uuid.UUID | None = None
    stock_uom: str = "Nos"
    purchase_uom: str | None = None
    purchase_uom_factor: Decimal = Field(gt=0, default=Decimal("1"))
    sales_uom: str | None = None
    sales_uom_factor: Decimal = Field(gt=0, default=Decimal("1"))
    is_stock_item: bool = True
    is_sales_item: bool = True
    is_purchase_item: bool = True
    has_serial_no: bool = False
    has_batch_no: bool = False
    valuation_method: str = Field(default="Moving Average", pattern="^(Moving Average|FIFO)$")
    standard_rate: Decimal = Field(ge=0, default=Decimal("0"))
    valuation_rate: Decimal = Field(ge=0, default=Decimal("0"))
    income_account_id: uuid.UUID | None = None
    expense_account_id: uuid.UUID | None = None
    item_tax_template_id: uuid.UUID | None = None
    default_warehouse_id: uuid.UUID | None = None
    reorder_level: Decimal = Field(ge=0, default=Decimal("0"))
    reorder_qty: Decimal = Field(ge=0, default=Decimal("0"))
    lead_time_days: int = 0
    brand: str | None = None
    barcode: str | None = None
    is_fixed_asset: bool = False
    asset_category_id: uuid.UUID | None = None
    hsn_sac_code: str | None = Field(default=None, max_length=8)
    gst_treatment: str = Field(default="Taxable", pattern=GST_TREATMENT_PATTERN)


class ItemUpdate(BaseModel):
    item_name: str | None = None
    description: str | None = None
    item_group_id: uuid.UUID | None = None
    purchase_uom: str | None = None
    purchase_uom_factor: Decimal | None = Field(gt=0, default=None)
    sales_uom: str | None = None
    sales_uom_factor: Decimal | None = Field(gt=0, default=None)
    has_serial_no: bool | None = None
    has_batch_no: bool | None = None
    standard_rate: Decimal | None = Field(ge=0, default=None)
    income_account_id: uuid.UUID | None = None
    expense_account_id: uuid.UUID | None = None
    item_tax_template_id: uuid.UUID | None = None
    default_warehouse_id: uuid.UUID | None = None
    reorder_level: Decimal | None = Field(ge=0, default=None)
    reorder_qty: Decimal | None = Field(ge=0, default=None)
    lead_time_days: int | None = None
    brand: str | None = None
    barcode: str | None = None
    is_fixed_asset: bool | None = None
    asset_category_id: uuid.UUID | None = None
    hsn_sac_code: str | None = Field(default=None, max_length=8)
    gst_treatment: str | None = Field(default=None, pattern=GST_TREATMENT_PATTERN)
    disabled: bool | None = None


class ItemResponse(DocumentMeta):
    item_code: str
    item_name: str
    description: str | None
    item_group_id: uuid.UUID | None
    item_group_name: str | None = None
    stock_uom: str
    purchase_uom: str | None
    purchase_uom_factor: Decimal
    sales_uom: str | None
    sales_uom_factor: Decimal
    is_stock_item: bool
    is_sales_item: bool
    is_purchase_item: bool
    has_serial_no: bool
    has_batch_no: bool
    valuation_method: str
    standard_rate: Decimal
    valuation_rate: Decimal
    last_purchase_rate: Decimal
    income_account_id: uuid.UUID | None
    expense_account_id: uuid.UUID | None
    item_tax_template_id: uuid.UUID | None = None
    default_warehouse_id: uuid.UUID | None
    reorder_level: Decimal
    reorder_qty: Decimal
    lead_time_days: int
    brand: str | None
    barcode: str | None
    is_fixed_asset: bool
    asset_category_id: uuid.UUID | None
    hsn_sac_code: str | None = None
    gst_treatment: str = "Taxable"
    disabled: bool
    company_id: uuid.UUID


class ItemListItem(ORMModel):
    id: uuid.UUID
    item_code: str
    item_name: str
    item_group_name: str | None = None
    stock_uom: str
    purchase_uom: str | None = None
    purchase_uom_factor: Decimal = Decimal("1")
    sales_uom: str | None = None
    sales_uom_factor: Decimal = Decimal("1")
    has_serial_no: bool = False
    has_batch_no: bool = False
    is_stock_item: bool
    standard_rate: Decimal
    hsn_sac_code: str | None = None
    disabled: bool


class PriceListCreate(BaseModel):
    price_list_name: str = Field(min_length=1, max_length=140)
    # defaults to company currency
    currency: str | None = Field(default=None, pattern="^[A-Za-z]{3}$")
    buying: bool = False
    selling: bool = False


class PriceListResponse(DocumentMeta):
    price_list_name: str
    currency: str
    buying: bool
    selling: bool
    enabled: bool
    company_id: uuid.UUID


class ItemPriceCreate(BaseModel):
    item_id: uuid.UUID
    price_list_id: uuid.UUID
    price_list_rate: Decimal = Field(ge=0)
    valid_from: date | None = None
    valid_upto: date | None = None


class ItemPriceResponse(DocumentMeta):
    item_id: uuid.UUID
    price_list_id: uuid.UUID
    price_list_rate: Decimal
    currency: str | None
    valid_from: date | None
    valid_upto: date | None
    company_id: uuid.UUID


class ItemRateResponse(BaseModel):
    """Resolved default rate for a document line (Item Price, else item master)."""

    item_id: uuid.UUID
    rate: Decimal
    source: str  # "Item Price" | "Standard Rate" | "Last Purchase Rate" | "Valuation Rate"
    uom: str
    item_name: str
    description: str | None = None


# --- stock entry ----------------------------------------------------------------------


class StockEntryItemIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    basic_rate: Decimal = Field(ge=0, default=Decimal("0"))  # receipts; issues use valuation
    uom: str | None = None
    serial_nos: list[str] | None = None  # required (count == stock_qty) for serialised items
    batch_no: str | None = None  # required for batched items; must be an existing batch
    source_warehouse_id: uuid.UUID | None = None
    target_warehouse_id: uuid.UUID | None = None


class StockEntryCreate(BaseModel):
    purpose: str = Field(pattern="^(Material Receipt|Material Issue|Material Transfer)$")
    posting_date: date
    from_warehouse_id: uuid.UUID | None = None
    to_warehouse_id: uuid.UUID | None = None
    remarks: str | None = None
    items: list[StockEntryItemIn] = Field(min_length=1)


class StockEntryItemResponse(DocumentMeta):
    idx: int
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    source_warehouse_id: uuid.UUID | None
    target_warehouse_id: uuid.UUID | None
    qty: Decimal
    uom: str | None
    conversion_factor: Decimal
    stock_qty: Decimal
    basic_rate: Decimal
    amount: Decimal
    serial_nos: str | None = None
    batch_no: str | None = None


class StockEntryResponse(DocumentMeta):
    name: str
    posting_date: date
    purpose: str
    from_warehouse_id: uuid.UUID | None
    to_warehouse_id: uuid.UUID | None
    total_amount: Decimal
    remarks: str | None
    company_id: uuid.UUID
    items: list[StockEntryItemResponse]


class StockEntryListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    purpose: str
    total_amount: Decimal
    docstatus: int


# --- material request -------------------------------------------------------------------


class MaterialRequestItemIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    uom: str | None = None
    warehouse_id: uuid.UUID | None = None
    schedule_date: date | None = None


class MaterialRequestCreate(BaseModel):
    material_request_type: str = Field(
        default="Purchase", pattern="^(Purchase|Material Transfer|Material Issue)$"
    )
    posting_date: date
    schedule_date: date | None = None
    remarks: str | None = None
    items: list[MaterialRequestItemIn] = Field(min_length=1)


class MaterialRequestItemResponse(DocumentMeta):
    idx: int
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    warehouse_id: uuid.UUID | None
    qty: Decimal
    uom: str | None
    conversion_factor: Decimal
    stock_qty: Decimal
    ordered_qty: Decimal
    schedule_date: date | None


class MaterialRequestResponse(DocumentMeta):
    name: str
    posting_date: date
    material_request_type: str
    schedule_date: date | None
    status: str
    per_ordered: Decimal
    remarks: str | None
    company_id: uuid.UUID
    items: list[MaterialRequestItemResponse]


class MaterialRequestListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    material_request_type: str
    status: str
    per_ordered: Decimal
    docstatus: int


# --- purchase receipt ---------------------------------------------------------------------


class PurchaseReceiptItemIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)  # accepted qty
    rate: Decimal = Field(ge=0, default=Decimal("0"))
    uom: str | None = None
    warehouse_id: uuid.UUID | None = None  # falls back to set_warehouse / item default
    rejected_qty: Decimal = Field(ge=0, default=Decimal("0"))
    rejected_warehouse_id: uuid.UUID | None = None  # required when rejected_qty > 0
    serial_nos: list[str] | None = None  # required (count == stock_qty) for serialised items
    batch_no: str | None = None  # required for batched items; must be an existing batch
    purchase_order_item_id: uuid.UUID | None = None


class PurchaseReceiptChargeIn(BaseModel):
    description: str = Field(min_length=1, max_length=180)
    account_id: uuid.UUID  # charge clearing/expense account credited on submit
    amount: Decimal = Field(gt=0)  # document currency


class PurchaseReceiptCreate(BaseModel):
    supplier_id: uuid.UUID
    posting_date: date
    currency: str | None = Field(default=None, pattern="^[A-Za-z]{3}$")
    conversion_rate: Decimal = Field(gt=0, default=Decimal("1"))
    set_warehouse_id: uuid.UUID | None = None
    is_return: bool = False  # a return sends goods back to the supplier (SLE out / reverse SRBNB)
    return_against_id: uuid.UUID | None = None  # required when is_return
    supplier_delivery_note: str | None = Field(default=None, max_length=140)
    remarks: str | None = None
    items: list[PurchaseReceiptItemIn] = Field(min_length=1)
    charges: list[PurchaseReceiptChargeIn] = Field(default_factory=list)  # landed cost


class PurchaseReceiptItemResponse(DocumentMeta):
    idx: int
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    warehouse_id: uuid.UUID
    qty: Decimal
    uom: str | None
    conversion_factor: Decimal
    stock_qty: Decimal
    rate: Decimal
    amount: Decimal
    base_amount: Decimal
    rejected_qty: Decimal
    rejected_warehouse_id: uuid.UUID | None
    serial_nos: str | None = None
    batch_no: str | None = None
    purchase_order_item_id: uuid.UUID | None
    billed_qty: Decimal


class PurchaseReceiptChargeResponse(DocumentMeta):
    idx: int
    description: str
    account_id: uuid.UUID
    amount: Decimal


class PurchaseReceiptResponse(DocumentMeta):
    name: str
    posting_date: date
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    currency: str
    conversion_rate: Decimal
    set_warehouse_id: uuid.UUID | None
    is_return: bool
    return_against_id: uuid.UUID | None = None
    supplier_delivery_note: str | None = None
    total_qty: Decimal
    grand_total: Decimal
    base_grand_total: Decimal
    status: str
    per_billed: Decimal
    remarks: str | None
    company_id: uuid.UUID
    items: list[PurchaseReceiptItemResponse]
    charges: list[PurchaseReceiptChargeResponse] = []


class PurchaseReceiptListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    supplier_name: str | None = None
    currency: str | None = None
    grand_total: Decimal
    status: str
    per_billed: Decimal
    is_return: bool
    docstatus: int


# --- delivery note --------------------------------------------------------------------------


class DeliveryNoteItemIn(BaseModel):
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    rate: Decimal = Field(ge=0, default=Decimal("0"))
    uom: str | None = None
    warehouse_id: uuid.UUID | None = None
    serial_nos: list[str] | None = None  # required (count == stock_qty) for serialised items
    batch_no: str | None = None  # required for batched items; must be an existing batch
    sales_order_item_id: uuid.UUID | None = None


class DeliveryNoteCreate(BaseModel):
    customer_id: uuid.UUID
    posting_date: date
    currency: str | None = Field(default=None, pattern="^[A-Za-z]{3}$")
    conversion_rate: Decimal = Field(gt=0, default=Decimal("1"))
    set_warehouse_id: uuid.UUID | None = None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    is_return: bool = False  # a return takes goods back into stock (SLE in / reverse COGS)
    return_against_id: uuid.UUID | None = None  # required when is_return
    remarks: str | None = None
    items: list[DeliveryNoteItemIn] = Field(min_length=1)


class DeliveryNoteItemResponse(DocumentMeta):
    idx: int
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    warehouse_id: uuid.UUID
    qty: Decimal
    uom: str | None
    conversion_factor: Decimal
    stock_qty: Decimal
    rate: Decimal
    amount: Decimal
    base_amount: Decimal
    serial_nos: str | None = None
    batch_no: str | None = None
    sales_order_item_id: uuid.UUID | None
    billed_qty: Decimal


class DeliveryNoteResponse(DocumentMeta):
    name: str
    posting_date: date
    customer_id: uuid.UUID
    customer_name: str | None = None
    currency: str
    conversion_rate: Decimal
    set_warehouse_id: uuid.UUID | None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    is_return: bool
    return_against_id: uuid.UUID | None = None
    total_qty: Decimal
    grand_total: Decimal
    base_grand_total: Decimal
    status: str
    per_billed: Decimal
    remarks: str | None
    company_id: uuid.UUID
    items: list[DeliveryNoteItemResponse]


class DeliveryNoteListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    customer_name: str | None = None
    currency: str | None = None
    grand_total: Decimal
    status: str
    per_billed: Decimal
    is_return: bool
    docstatus: int


# --- stock reconciliation --------------------------------------------------------------------


class StockReconciliationItemIn(BaseModel):
    item_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None  # falls back to set_warehouse / item default
    qty: Decimal = Field(ge=0)  # target absolute quantity
    valuation_rate: Decimal | None = Field(default=None, ge=0)  # None/0 = resolve at submit
    uom: str | None = None


class StockReconciliationCreate(BaseModel):
    purpose: str = Field(default="Stock Reconciliation", pattern="^(Opening Stock|Stock Reconciliation)$")
    posting_date: date
    set_warehouse_id: uuid.UUID | None = None
    remarks: str | None = None
    items: list[StockReconciliationItemIn] = Field(min_length=1)


class StockReconciliationItemResponse(DocumentMeta):
    idx: int
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    warehouse_id: uuid.UUID
    qty: Decimal
    uom: str | None
    valuation_rate: Decimal
    current_qty: Decimal
    current_valuation_rate: Decimal
    amount_difference: Decimal


class StockReconciliationResponse(DocumentMeta):
    name: str
    posting_date: date
    purpose: str
    set_warehouse_id: uuid.UUID | None
    difference_amount: Decimal
    remarks: str | None
    company_id: uuid.UUID
    items: list[StockReconciliationItemResponse]


class StockReconciliationListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    purpose: str
    difference_amount: Decimal
    docstatus: int


# --- service credits -------------------------------------------------------------------------


class ServiceCreditCreate(BaseModel):
    item_id: uuid.UUID
    supplier_id: uuid.UUID | None = None
    purchase_date: date
    purchased_qty: Decimal = Field(gt=0)
    rate: Decimal = Field(ge=0, default=Decimal("0"))
    valid_upto: date | None = None
    remarks: str | None = None
    # accounting (optional — set both accounts to post GL on usage)
    purchase_invoice_id: uuid.UUID | None = None
    prepaid_account_id: uuid.UUID | None = None
    expense_account_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None


class ServiceCreditUsageIn(BaseModel):
    usage_date: date
    qty: Decimal = Field(gt=0)
    remarks: str | None = None


class ServiceCreditUsageResponse(DocumentMeta):
    idx: int
    usage_date: date
    qty: Decimal
    remarks: str | None


class ServiceCreditResponse(DocumentMeta):
    name: str
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    supplier_id: uuid.UUID | None
    supplier_name: str | None = None
    purchase_date: date
    purchased_qty: Decimal
    consumed_qty: Decimal
    balance_qty: Decimal
    rate: Decimal
    uom: str | None
    valid_upto: date | None
    status: str
    remarks: str | None
    purchase_invoice_id: uuid.UUID | None = None
    purchase_invoice_name: str | None = None
    prepaid_account_id: uuid.UUID | None = None
    expense_account_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    company_id: uuid.UUID
    usages: list[ServiceCreditUsageResponse]


class ServiceCreditListItem(ORMModel):
    id: uuid.UUID
    name: str
    item_code: str | None = None
    item_name: str | None = None
    supplier_name: str | None = None
    purchased_qty: Decimal
    consumed_qty: Decimal
    balance_qty: Decimal
    uom: str | None = None
    status: str


# --- reports ----------------------------------------------------------------------------------


class StockBalanceRow(BaseModel):
    item_id: uuid.UUID
    item_code: str
    item_name: str
    warehouse_id: uuid.UUID
    warehouse_name: str
    actual_qty: Decimal
    reserved_qty: Decimal
    ordered_qty: Decimal
    projected_qty: Decimal
    valuation_rate: Decimal
    stock_value: Decimal


class StockLedgerRow(BaseModel):
    posting_date: date
    item_code: str
    item_name: str
    warehouse_name: str
    voucher_type: str
    voucher_no: str
    actual_qty: Decimal
    qty_after_transaction: Decimal
    incoming_rate: Decimal
    valuation_rate: Decimal
    stock_value_difference: Decimal


class StockAgeingRow(BaseModel):
    item_id: uuid.UUID
    item_code: str
    item_name: str
    warehouse_id: uuid.UUID
    warehouse_name: str
    total_qty: Decimal
    average_age_days: int
    bucket_0_30: Decimal
    bucket_31_60: Decimal
    bucket_61_90: Decimal
    bucket_90_plus: Decimal
    stock_value: Decimal


class ReorderRow(BaseModel):
    """An item whose company-wide projected qty is below its reorder level."""

    item_id: uuid.UUID
    item_code: str
    item_name: str
    default_warehouse_id: uuid.UUID | None
    default_warehouse_name: str | None
    projected_qty: Decimal
    reorder_level: Decimal
    reorder_qty: Decimal
    shortfall: Decimal
    suggested_qty: Decimal


class ReorderMaterialRequest(BaseModel):
    """Body for the auto-reorder action; empty = all items below reorder level."""

    item_ids: list[uuid.UUID] | None = None


# --- serial numbers ---------------------------------------------------------------------------


class SerialNoResponse(DocumentMeta):
    serial_no: str
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    warehouse_id: uuid.UUID | None
    status: str
    purchase_voucher_type: str | None
    purchase_voucher_id: uuid.UUID | None
    delivery_voucher_id: uuid.UUID | None
    warranty_expiry: date | None
    company_id: uuid.UUID


class SerialNoListItem(ORMModel):
    id: uuid.UUID
    serial_no: str
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    warehouse_id: uuid.UUID | None = None
    status: str
    warranty_expiry: date | None = None
