"""Manufacturing module schemas (BOM + Work Order + reports).

Covers the two bespoke documents — BOM (recipe) and Work Order (make N) — plus the inputs
for the Finish action and the read-only reports. Decimals serialize as strings (like the
rest of the API); create/input payloads use ``Decimal`` with validation.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel


# --- BOM -----------------------------------------------------------------------------


class BOMItemIn(BaseModel):
    """One component line supplied when creating/updating a BOM."""

    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    uom: str | None = None
    rate: Decimal | None = Field(default=None, ge=0)  # override; else sourced from valuation / child BOM
    source_warehouse_id: uuid.UUID | None = None
    allow_alternative_item: bool = False


class BOMScrapItemIn(BaseModel):
    """Expected scrap / by-product line on a BOM (recovery value optional)."""

    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    uom: str | None = None
    rate: Decimal = Field(default=Decimal("0"), ge=0)  # recovery value per stock unit
    stock_warehouse_id: uuid.UUID | None = None


class BOMOperationIn(BaseModel):
    """One process step on a BOM (time × hour_rate → operating cost)."""

    operation_id: uuid.UUID
    workstation_id: uuid.UUID | None = None
    time_in_mins: Decimal = Field(ge=0, default=Decimal("0"))
    hour_rate: Decimal | None = Field(default=None, ge=0)  # else workstation / operation default
    description: str | None = None


class BOMCreate(BaseModel):
    production_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, default=Decimal("1"))
    uom: str | None = None
    # Flat labour; operation time×rate is added on top into stored operating_cost.
    operating_cost: Decimal = Field(ge=0, le=Decimal("999999999999999"), default=Decimal("0"))
    is_default: bool = False
    is_phantom: bool = False
    routing_id: uuid.UUID | None = None  # optional: seed operations from a Routing
    currency: str | None = None  # defaults to the company currency
    remarks: str | None = None
    items: list[BOMItemIn] = Field(min_length=1)
    scrap_items: list[BOMScrapItemIn] = []
    operations: list[BOMOperationIn] = []


class BOMUpdate(BaseModel):
    """Replace a draft BOM's editable fields + component/scrap/ops lists (full replace)."""

    quantity: Decimal | None = Field(default=None, gt=0)
    uom: str | None = None
    operating_cost: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999999"))
    is_default: bool | None = None
    is_phantom: bool | None = None
    routing_id: uuid.UUID | None = None
    remarks: str | None = None
    items: list[BOMItemIn] | None = Field(default=None, min_length=1)
    scrap_items: list[BOMScrapItemIn] | None = None
    operations: list[BOMOperationIn] | None = None


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
    allow_alternative_item: bool = False


class BOMScrapItemResponse(ORMModel):
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
    stock_warehouse_id: uuid.UUID | None


class BOMOperationResponse(ORMModel):
    id: uuid.UUID
    idx: int
    operation_id: uuid.UUID
    operation_name: str | None
    workstation_id: uuid.UUID | None
    workstation_name: str | None
    time_in_mins: Decimal
    hour_rate: Decimal
    operating_cost: Decimal
    description: str | None


class BOMResponse(DocumentMeta):
    name: str
    production_item_id: uuid.UUID
    production_item_code: str | None
    production_item_name: str | None
    uom: str | None
    quantity: Decimal
    is_active: bool
    is_default: bool
    is_phantom: bool = False
    routing_id: uuid.UUID | None = None
    currency: str
    operating_cost: Decimal
    raw_material_cost: Decimal
    scrap_cost: Decimal = Decimal("0")
    total_cost: Decimal
    cost_per_unit: Decimal
    remarks: str | None
    company_id: uuid.UUID
    items: list[BOMItemResponse]
    scrap_items: list[BOMScrapItemResponse] = []
    operations: list[BOMOperationResponse] = []


class BOMListItem(ORMModel):
    id: uuid.UUID
    name: str
    production_item_code: str | None
    production_item_name: str | None
    quantity: Decimal
    is_active: bool
    is_default: bool
    is_phantom: bool = False
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


class WorkOrderFinishLineIn(BaseModel):
    """Serial/batch picks (and optional alternate) for one consumed component on Finish."""

    item_id: uuid.UUID  # the Work Order required item (planned)
    substitute_item_id: uuid.UUID | None = None  # consume this instead when allowed
    serial_nos: list[str] | None = None
    batch_no: str | None = None


class WorkOrderFinishIn(BaseModel):
    """Finish (manufacture) some or all of a Work Order's quantity."""

    qty: Decimal = Field(gt=0)  # FG units to produce now (≤ remaining)
    posting_date: date
    operating_cost_account_id: uuid.UUID | None = None  # overrides the WO default
    # Tracking picks (required when the FG / components are serial or batch tracked).
    consumed: list[WorkOrderFinishLineIn] = []
    finished_serial_nos: list[str] | None = None
    finished_batch_no: str | None = None


class WorkOrderTransferIn(BaseModel):
    """Optional: transfer required materials source → WIP warehouse."""

    qty: Decimal = Field(gt=0)  # FG-equivalent qty whose materials to move
    posting_date: date
    consumed: list[WorkOrderFinishLineIn] = []


class WorkOrderConsumeIn(BaseModel):
    """Mid-process Material Consumption for Manufacture (independent of Finish)."""

    qty: Decimal = Field(gt=0)  # FG-equivalent qty whose materials to consume now
    posting_date: date
    consumed: list[WorkOrderFinishLineIn] = []


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
    allow_alternative_item: bool = False
    has_serial_no: bool = False
    has_batch_no: bool = False


class WorkOrderOperationResponse(ORMModel):
    id: uuid.UUID
    idx: int
    operation_id: uuid.UUID
    operation_name: str | None
    workstation_id: uuid.UUID | None
    workstation_name: str | None
    time_in_mins: Decimal
    hour_rate: Decimal
    planned_operating_cost: Decimal
    completed_qty: Decimal
    status: str
    description: str | None


class WorkOrderResponse(DocumentMeta):
    name: str
    production_item_id: uuid.UUID
    production_item_code: str | None
    production_item_name: str | None
    production_has_serial_no: bool = False
    production_has_batch_no: bool = False
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
    operations: list[WorkOrderOperationResponse] = []
    warnings: list[str] = Field(default_factory=list)


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


# --- material availability -----------------------------------------------------------


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
    """Lean Manufacturing Settings blob (SystemSetting JSON per company)."""

    default_source_warehouse_id: uuid.UUID | None = None
    default_wip_warehouse_id: uuid.UUID | None = None
    default_fg_warehouse_id: uuid.UUID | None = None
    over_production_percentage: Decimal = Field(ge=0, le=100, default=Decimal("0"))
    capacity_planning_enabled: bool = False
    # off = skip SO/QTN check; warn = soft warnings on submit; block = hard 422
    order_fulfillment_mode: str = Field(default="warn", pattern="^(off|warn|block)$")
    # Calendar days FG warehouse → customer (when Shipping Rule has no transit_days)
    outbound_delivery_days: int = Field(default=0, ge=0, le=365)


# --- Job Card ------------------------------------------------------------------------


class JobCardTimeLogResponse(ORMModel):
    id: uuid.UUID
    idx: int
    from_time: datetime
    to_time: datetime | None = None
    time_in_mins: Decimal
    completed_qty: Decimal


class JobCardResponse(DocumentMeta):
    name: str
    work_order_id: uuid.UUID
    work_order_name: str | None
    work_order_operation_id: uuid.UUID
    operation_id: uuid.UUID
    operation_name: str | None
    workstation_id: uuid.UUID | None
    workstation_name: str | None
    for_quantity: Decimal
    total_completed_qty: Decimal
    time_in_mins: Decimal
    status: str
    remarks: str | None
    company_id: uuid.UUID
    time_logs: list[JobCardTimeLogResponse] = []


class JobCardListItem(ORMModel):
    id: uuid.UUID
    name: str
    work_order_name: str | None
    operation_name: str | None
    workstation_name: str | None
    for_quantity: Decimal
    total_completed_qty: Decimal
    status: str
    docstatus: int


class JobCardCompleteIn(BaseModel):
    """Stop the open time log and/or record completed qty on a Job Card."""

    completed_qty: Decimal = Field(ge=0, default=Decimal("0"))
    to_time: datetime | None = None  # defaults to now


# --- Production Plan ------------------------------------------------------------------


class ProductionPlanItemIn(BaseModel):
    """Manual FG demand row (or override after get-items)."""

    item_id: uuid.UUID
    bom_id: uuid.UUID | None = None
    planned_qty: Decimal = Field(gt=0)
    warehouse_id: uuid.UUID | None = None
    planned_start_date: date | None = None
    sales_order_id: uuid.UUID | None = None
    description: str | None = None


class ProductionPlanCreate(BaseModel):
    posting_date: date
    from_date: date | None = None
    to_date: date | None = None
    get_items_from: str = Field(default="Sales Order", pattern="^(Sales Order|Manual)$")
    fg_warehouse_id: uuid.UUID | None = None
    source_warehouse_id: uuid.UUID | None = None
    remarks: str | None = None
    items: list[ProductionPlanItemIn] = []


class ProductionPlanUpdate(BaseModel):
    posting_date: date | None = None
    from_date: date | None = None
    to_date: date | None = None
    get_items_from: str | None = Field(default=None, pattern="^(Sales Order|Manual)$")
    fg_warehouse_id: uuid.UUID | None = None
    source_warehouse_id: uuid.UUID | None = None
    remarks: str | None = None
    items: list[ProductionPlanItemIn] | None = None


class ProductionPlanItemResponse(ORMModel):
    id: uuid.UUID
    idx: int
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    bom_id: uuid.UUID | None
    bom_name: str | None
    sales_order_id: uuid.UUID | None
    sales_order_name: str | None
    sales_order_item_id: uuid.UUID | None
    planned_qty: Decimal
    pending_qty: Decimal
    ordered_qty: Decimal
    warehouse_id: uuid.UUID | None
    planned_start_date: date | None
    work_order_id: uuid.UUID | None
    description: str | None


class ProductionPlanMRResponse(ORMModel):
    id: uuid.UUID
    idx: int
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    warehouse_id: uuid.UUID | None
    required_qty: Decimal
    available_qty: Decimal
    shortfall_qty: Decimal
    material_request_id: uuid.UUID | None


class ProductionPlanResponse(DocumentMeta):
    name: str
    posting_date: date
    from_date: date | None
    to_date: date | None
    get_items_from: str
    fg_warehouse_id: uuid.UUID | None
    source_warehouse_id: uuid.UUID | None
    status: str
    work_orders_created: bool
    material_requests_created: bool
    remarks: str | None
    company_id: uuid.UUID
    items: list[ProductionPlanItemResponse] = []
    material_requests: list[ProductionPlanMRResponse] = []


class ProductionPlanListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    from_date: date | None
    to_date: date | None
    status: str
    work_orders_created: bool
    material_requests_created: bool
    docstatus: int


class ProductionPlanCreateResult(BaseModel):
    """Outcome of create-work-orders / create-material-requests."""

    production_plan_id: uuid.UUID
    created_ids: list[uuid.UUID]
    created_names: list[str]
    count: int


# --- reports -------------------------------------------------------------------------


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


class BOMExplorerRow(BaseModel):
    """One leaf (or stocked sub-assembly) line in a flattened BOM Explorer tree."""

    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    stock_qty: Decimal  # absolute qty for for_qty finished units
    rate: Decimal
    amount: Decimal
    level: int
    source_warehouse_id: uuid.UUID | None = None
    is_leaf: bool = True


class BOMExplorerReport(BaseModel):
    """Flatten a nested BOM to its material requirements (phantoms always exploded)."""

    bom_id: uuid.UUID
    bom_name: str
    production_item_code: str | None
    production_item_name: str | None
    for_qty: Decimal
    flatten_all: bool
    raw_material_cost: Decimal
    scrap_cost: Decimal
    operating_cost: Decimal
    total_cost: Decimal
    rows: list[BOMExplorerRow]
    scrap_rows: list[BOMExplorerRow] = []


# --- Subcontract Job (Phase 4) --------------------------------------------------------


class SubcontractJobCreate(BaseModel):
    bom_id: uuid.UUID
    supplier_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    posting_date: date
    supplier_warehouse_id: uuid.UUID
    source_warehouse_id: uuid.UUID | None = None
    fg_warehouse_id: uuid.UUID | None = None
    service_cost: Decimal = Field(ge=0, default=Decimal("0"))
    service_cost_account_id: uuid.UUID | None = None
    remarks: str | None = None


class SubcontractJobSendIn(BaseModel):
    qty: Decimal = Field(gt=0)
    posting_date: date


class SubcontractJobReceiveIn(BaseModel):
    qty: Decimal = Field(gt=0)
    posting_date: date
    service_cost_account_id: uuid.UUID | None = None


class SubcontractJobItemResponse(ORMModel):
    id: uuid.UUID
    idx: int
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    required_qty: Decimal
    sent_qty: Decimal
    consumed_qty: Decimal
    source_warehouse_id: uuid.UUID | None = None
    rate: Decimal
    amount: Decimal


class SubcontractJobResponse(DocumentMeta):
    name: str
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    production_item_id: uuid.UUID
    production_item_code: str | None = None
    production_item_name: str | None = None
    bom_id: uuid.UUID
    bom_name: str | None = None
    qty: Decimal
    sent_qty: Decimal
    received_qty: Decimal
    source_warehouse_id: uuid.UUID
    supplier_warehouse_id: uuid.UUID
    fg_warehouse_id: uuid.UUID
    service_cost: Decimal
    service_cost_account_id: uuid.UUID | None = None
    status: str
    posting_date: date
    remarks: str | None = None
    company_id: uuid.UUID
    items: list[SubcontractJobItemResponse] = []


class SubcontractJobListItem(ORMModel):
    id: uuid.UUID
    name: str
    supplier_name: str | None = None
    production_item_code: str | None = None
    bom_name: str | None = None
    qty: Decimal
    sent_qty: Decimal
    received_qty: Decimal
    status: str
    docstatus: int
    posting_date: date


class SubcontractJobActionResult(BaseModel):
    job: SubcontractJobResponse
    stock_entry_id: uuid.UUID
    stock_entry_name: str


# --- Phase 6 reports ------------------------------------------------------------------


class WorkOrderSummaryRow(BaseModel):
    status: str
    count: int
    total_qty: Decimal
    total_produced_qty: Decimal
    total_pending_qty: Decimal
    total_estimated_cost: Decimal


class ProductionAnalyticsRow(BaseModel):
    period: str  # YYYY-MM
    work_orders_completed: int
    qty_produced: Decimal
    estimated_cost: Decimal


# --- Phase 7.0 lead-time / CTP --------------------------------------------------------


class LeadTimeComponentRowOut(BaseModel):
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    required_qty: Decimal
    available_qty: Decimal
    shortfall_qty: Decimal
    lead_time_days: int
    drives_wait: bool = False
    supply_ready_date: date | None = None
    supply_source: str | None = None


class LeadTimeEstimateOut(BaseModel):
    """Capable-to-promise: materials + manufacture + outbound → customer receipt."""

    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    bom_id: uuid.UUID | None = None
    bom_name: str | None = None
    qty: Decimal
    as_of: date
    warehouse_id: uuid.UUID | None = None
    procurement_days: int
    manufacturing_days: int
    outbound_days: int = 0
    total_days: int
    ready_to_dispatch_date: date | None = None
    earliest_promise_date: date
    operation_mins: Decimal
    components: list[LeadTimeComponentRowOut] = []
    notes: list[str] = []


class ProcurementSuggestionOut(BaseModel):
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    shortfall_qty: Decimal
    lead_time_days: int
    latest_order_date: date
    days_until_order: int


class ReverseScheduleOut(BaseModel):
    """Phase 7.1: reverse schedule from customer receipt date + procurement order-by."""

    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    bom_id: uuid.UUID | None = None
    bom_name: str | None = None
    qty: Decimal
    as_of: date
    delivery_date: date
    warehouse_id: uuid.UUID | None = None
    procurement_days: int
    manufacturing_days: int
    outbound_days: int = 0
    total_days: int
    earliest_promise_date: date
    ready_to_dispatch_date: date | None = None
    manufacturing_start_date: date
    materials_ready_by: date
    on_time: bool
    slack_days: int
    operation_mins: Decimal
    procurement: list[ProcurementSuggestionOut] = []
    components: list[LeadTimeComponentRowOut] = []
    notes: list[str] = []


class PeggingRowOut(BaseModel):
    side: str  # demand | supply
    source_type: str
    source_id: uuid.UUID | None = None
    source_name: str | None = None
    qty: Decimal
    due_date: date | None = None
    notes: str | None = None


class PeggingTimelineOut(BaseModel):
    """Phase 7.2: demand → supply pegging for one item + CTP for uncovered qty."""

    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    as_of: date
    warehouse_id: uuid.UUID | None = None
    demand_qty: Decimal
    supply_qty: Decimal
    net_shortfall: Decimal
    earliest_promise_date: date | None = None
    ctp: LeadTimeEstimateOut | None = None
    rows: list[PeggingRowOut] = []
    notes: list[str] = []


class ForecastHistoryRowOut(BaseModel):
    period: str
    demand_qty: Decimal
    is_forecast: bool = False


class DemandForecastOut(BaseModel):
    """Phase 7.3: light moving-average demand forecast from Sales Order history."""

    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    lookback_months: int
    horizon_months: int
    method: str
    average_monthly_demand: Decimal
    rows: list[ForecastHistoryRowOut] = []
    notes: list[str] = []


class WhatIfStockOverride(BaseModel):
    item_id: uuid.UUID
    extra_qty: Decimal = Field(ge=0)


class WhatIfLeadOverride(BaseModel):
    item_id: uuid.UUID
    lead_time_days: int = Field(ge=0)


class WhatIfCtpIn(BaseModel):
    """Phase 7.4: non-persistent CTP with stock / lead-time overrides."""

    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    as_of: date | None = None
    warehouse_id: uuid.UUID | None = None
    extra_stock: list[WhatIfStockOverride] = []
    lead_time_overrides: list[WhatIfLeadOverride] = []


class CapacityBoardRowOut(BaseModel):
    workstation_id: uuid.UUID
    workstation_name: str
    working_hours_per_day: Decimal
    capacity_mins_per_day: Decimal
    planned_mins: Decimal
    open_work_orders: int
    utilization_pct: Decimal
    overloaded: bool


class CapacityBoardOut(BaseModel):
    """Phase 7.5: soft workstation load vs daily capacity."""

    as_of: date
    rows: list[CapacityBoardRowOut] = []
    notes: list[str] = []


# --- Planning Dashboard context -------------------------------------------------------


class PlanningContextLineOut(BaseModel):
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    qty: Decimal
    delivery_date: date | None = None
    warehouse_id: uuid.UUID | None = None
    source_label: str | None = None


class PlanningContextOut(BaseModel):
    context_type: str
    document_id: uuid.UUID | None = None
    document_name: str | None = None
    lines: list[PlanningContextLineOut] = []
