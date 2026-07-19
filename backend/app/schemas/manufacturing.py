"""Manufacturing module schemas (BOM + Work Order + reports).

Covers the two bespoke documents — BOM (recipe) and Work Order (make N) — plus the inputs
for the Finish action and the read-only reports. Decimals serialize as strings (like the
rest of the API); create/input payloads use ``Decimal`` with validation.
"""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel


# --- BOM -----------------------------------------------------------------------------


class BOMItemIn(BaseModel):
    """One component line supplied when creating/updating a BOM."""

    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    uom: str | None = None
    rate: Decimal | None = Field(default=None, ge=0)  # override; else sourced from valuation
    source_warehouse_id: uuid.UUID | None = None


class BOMCreate(BaseModel):
    production_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, default=Decimal("1"))
    uom: str | None = None
    operating_cost: Decimal = Field(ge=0, le=Decimal("999999999999999"), default=Decimal("0"))
    is_default: bool = False
    currency: str | None = None  # defaults to the company currency
    remarks: str | None = None
    items: list[BOMItemIn] = Field(min_length=1)


class BOMUpdate(BaseModel):
    """Replace a draft BOM's editable fields + component list (full replace of items)."""

    quantity: Decimal | None = Field(default=None, gt=0)
    uom: str | None = None
    operating_cost: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999999"))
    is_default: bool | None = None
    remarks: str | None = None
    items: list[BOMItemIn] | None = Field(default=None, min_length=1)


class BOMItemResponse(ORMModel):
    id: uuid.UUID
    idx: int
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    qty: Decimal
    uom: str | None
    conversion_factor: Decimal
    stock_qty: Decimal
    rate: Decimal
    amount: Decimal
    source_warehouse_id: uuid.UUID | None


class BOMResponse(DocumentMeta):
    name: str
    production_item_id: uuid.UUID
    production_item_code: str | None
    production_item_name: str | None
    uom: str | None
    quantity: Decimal
    is_active: bool
    is_default: bool
    currency: str
    operating_cost: Decimal
    raw_material_cost: Decimal
    total_cost: Decimal
    cost_per_unit: Decimal
    remarks: str | None
    company_id: uuid.UUID
    items: list[BOMItemResponse]


class BOMListItem(ORMModel):
    id: uuid.UUID
    name: str
    production_item_code: str | None
    production_item_name: str | None
    quantity: Decimal
    is_active: bool
    is_default: bool
    total_cost: Decimal
    cost_per_unit: Decimal
    docstatus: int


# --- Work Order ----------------------------------------------------------------------


class WorkOrderCreate(BaseModel):
    bom_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    # warehouses fall back to the company's Manufacturing Settings defaults when omitted;
    # a finished-goods warehouse must resolve from one or the other.
    fg_warehouse_id: uuid.UUID | None = None
    source_warehouse_id: uuid.UUID | None = None  # default source for all components
    wip_warehouse_id: uuid.UUID | None = None
    skip_transfer: bool = True
    operating_cost_account_id: uuid.UUID | None = None
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    sales_order_id: uuid.UUID | None = None
    remarks: str | None = None


class WorkOrderFinishIn(BaseModel):
    """Finish (manufacture) some or all of a Work Order's quantity."""

    qty: Decimal = Field(gt=0)  # FG units to produce now (≤ remaining)
    posting_date: date
    operating_cost_account_id: uuid.UUID | None = None  # overrides the WO default


class WorkOrderTransferIn(BaseModel):
    """Optional: transfer required materials source → WIP warehouse (Phase 3)."""

    qty: Decimal = Field(gt=0)  # FG-equivalent qty whose materials to move
    posting_date: date


class WorkOrderItemResponse(ORMModel):
    id: uuid.UUID
    idx: int
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    required_qty: Decimal
    transferred_qty: Decimal
    consumed_qty: Decimal
    source_warehouse_id: uuid.UUID | None
    rate: Decimal
    amount: Decimal


class WorkOrderResponse(DocumentMeta):
    name: str
    production_item_id: uuid.UUID
    production_item_code: str | None
    production_item_name: str | None
    bom_id: uuid.UUID
    bom_name: str | None
    qty: Decimal
    produced_qty: Decimal
    material_transferred_qty: Decimal
    source_warehouse_id: uuid.UUID | None
    wip_warehouse_id: uuid.UUID | None
    fg_warehouse_id: uuid.UUID
    skip_transfer: bool
    operating_cost: Decimal
    operating_cost_account_id: uuid.UUID | None
    status: str
    planned_start_date: date | None
    planned_end_date: date | None
    actual_start_date: date | None
    actual_end_date: date | None
    sales_order_id: uuid.UUID | None
    remarks: str | None
    company_id: uuid.UUID
    items: list[WorkOrderItemResponse]


class WorkOrderListItem(ORMModel):
    id: uuid.UUID
    name: str
    production_item_code: str | None
    production_item_name: str | None
    qty: Decimal
    produced_qty: Decimal
    status: str
    planned_start_date: date | None
    docstatus: int


class WorkOrderFinishResult(BaseModel):
    """Outcome of a Finish — the Manufacture Stock Entry it posted + new WO state."""

    work_order_id: uuid.UUID
    stock_entry_id: uuid.UUID
    stock_entry_no: str
    produced_qty: Decimal
    status: str


# --- Phase 3: material availability --------------------------------------------------


class MaterialAvailabilityRow(BaseModel):
    """One component's on-hand vs still-required position for a Work Order."""

    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    required_qty: Decimal  # total for the order (stock UOM)
    consumed_qty: Decimal
    pending_qty: Decimal  # required − consumed (still to consume)
    source_warehouse_id: uuid.UUID | None
    available_qty: Decimal  # on-hand at the source warehouse (or company-wide if none)
    shortfall_qty: Decimal  # max(pending − available, 0)


class MaterialAvailabilityResponse(BaseModel):
    work_order_id: uuid.UUID
    can_finish_qty: Decimal  # how many FG units the current stock supports
    rows: list[MaterialAvailabilityRow]


# --- Manufacturing Settings (lean per-company defaults) -------------------------------


class ManufacturingSettings(BaseModel):
    """The lean 4-field slice of ERPNext's Manufacturing Settings we actually need.

    Stored as one SystemSetting JSON value per company; every field optional."""

    default_source_warehouse_id: uuid.UUID | None = None
    default_wip_warehouse_id: uuid.UUID | None = None
    default_fg_warehouse_id: uuid.UUID | None = None
    over_production_percentage: Decimal = Field(ge=0, le=100, default=Decimal("0"))


# --- Phase 4: reports ----------------------------------------------------------------


class MaterialShortageRow(BaseModel):
    """One component's aggregate position across ALL open Work Orders."""

    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    warehouse_id: uuid.UUID | None  # consume-from warehouse (None = company-wide)
    warehouse_name: str | None
    pending_qty: Decimal  # still to consume across open Work Orders
    available_qty: Decimal  # on-hand at that warehouse (or company-wide)
    shortfall_qty: Decimal  # max(pending − available, 0)
    work_orders: list[str]  # the Work Order names that need this component


class ProductionRegisterRow(BaseModel):
    """One Work Order's planned-vs-produced position + cost."""

    work_order_id: uuid.UUID
    name: str
    production_item_code: str | None
    production_item_name: str | None
    bom_name: str | None
    status: str
    qty: Decimal
    produced_qty: Decimal
    pending_qty: Decimal
    planned_start_date: date | None
    actual_end_date: date | None
    estimated_cost: Decimal  # BOM cost_per_unit × qty
    operating_cost: Decimal


class BOMWhereUsedRow(BaseModel):
    """A BOM that consumes a given item (where-used lookup)."""

    bom_id: uuid.UUID
    bom_name: str
    production_item_code: str | None
    production_item_name: str | None
    is_active: bool
    is_default: bool
    qty_per_batch: Decimal  # stock qty of the searched item per BOM batch


class BOMStockReportRow(BaseModel):
    """Can-I-build report: each component's need vs on-hand for building N of a BOM."""

    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    required_qty: Decimal  # to build the requested number of finished units
    available_qty: Decimal
    shortfall_qty: Decimal


class BOMStockReport(BaseModel):
    bom_id: uuid.UUID
    bom_name: str
    for_qty: Decimal  # finished units the report was run for
    buildable_qty: Decimal  # how many finished units current stock supports
    rows: list[BOMStockReportRow]
