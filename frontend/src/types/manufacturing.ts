// Manufacturing module types (mirror backend/app/schemas/manufacturing.py).
// Decimals arrive as strings; only *In payload types use number.
import type { DocumentMeta } from "@/types/core";

// --- BOM ---------------------------------------------------------------------

export interface BomItemRow {
  id: string;
  idx: number;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  qty: string;
  uom: string | null;
  conversion_factor: string;
  stock_qty: string;
  rate: string;
  amount: string;
  source_warehouse_id: string | null;
  allow_alternative_item: boolean;
}

export interface BomScrapItemRow {
  id: string;
  idx: number;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  qty: string;
  uom: string | null;
  conversion_factor: string;
  stock_qty: string;
  rate: string;
  amount: string;
  stock_warehouse_id: string | null;
}

export interface BomDetail extends DocumentMeta {
  name: string;
  production_item_id: string;
  production_item_code: string | null;
  production_item_name: string | null;
  uom: string | null;
  quantity: string;
  is_active: boolean;
  is_default: boolean;
  is_phantom: boolean;
  currency: string;
  operating_cost: string;
  raw_material_cost: string;
  scrap_cost: string;
  total_cost: string;
  cost_per_unit: string;
  remarks: string | null;
  company_id: string;
  items: BomItemRow[];
  scrap_items: BomScrapItemRow[];
  operations: BomOperationRow[];
  routing_id?: string | null;
}

export interface BomListItem {
  id: string;
  name: string;
  production_item_code: string | null;
  production_item_name: string | null;
  quantity: string;
  is_active: boolean;
  is_default: boolean;
  is_phantom: boolean;
  total_cost: string;
  cost_per_unit: string;
  docstatus: number;
}

export interface BomItemIn {
  item_id: string;
  qty: number;
  uom?: string | null;
  rate?: number | null;
  source_warehouse_id?: string | null;
  allow_alternative_item?: boolean;
}

export interface BomScrapItemIn {
  item_id: string;
  qty: number;
  uom?: string | null;
  rate?: number;
  stock_warehouse_id?: string | null;
}

export interface BomOperationIn {
  operation_id: string;
  workstation_id?: string | null;
  time_in_mins?: number;
  hour_rate?: number | null;
  description?: string | null;
}

export interface BomOperationRow {
  id: string;
  idx: number;
  operation_id: string;
  operation_name: string | null;
  workstation_id: string | null;
  workstation_name: string | null;
  time_in_mins: string;
  hour_rate: string;
  operating_cost: string;
  description: string | null;
}

// --- Work Order --------------------------------------------------------------

export interface WorkOrderItemRow {
  id: string;
  idx: number;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  required_qty: string;
  transferred_qty: string;
  consumed_qty: string;
  source_warehouse_id: string | null;
  rate: string;
  amount: string;
  allow_alternative_item: boolean;
  has_serial_no: boolean;
  has_batch_no: boolean;
}

export interface WorkOrderOperationRow {
  id: string;
  idx: number;
  operation_id: string;
  operation_name: string | null;
  workstation_id: string | null;
  workstation_name: string | null;
  time_in_mins: string;
  hour_rate: string;
  planned_operating_cost: string;
  completed_qty: string;
  status: string;
  description: string | null;
}

export interface WorkOrderDetail extends DocumentMeta {
  name: string;
  production_item_id: string;
  production_item_code: string | null;
  production_item_name: string | null;
  production_has_serial_no: boolean;
  production_has_batch_no: boolean;
  bom_id: string;
  bom_name: string | null;
  qty: string;
  produced_qty: string;
  material_transferred_qty: string;
  source_warehouse_id: string | null;
  wip_warehouse_id: string | null;
  fg_warehouse_id: string;
  skip_transfer: boolean;
  operating_cost: string;
  operating_cost_account_id: string | null;
  status: string; // Draft | Not Started | In Process | Completed | Stopped | Cancelled
  planned_start_date: string | null;
  planned_end_date: string | null;
  actual_start_date: string | null;
  actual_end_date: string | null;
  sales_order_id: string | null;
  remarks: string | null;
  company_id: string;
  items: WorkOrderItemRow[];
  operations: WorkOrderOperationRow[];
  warnings?: string[];
}

export interface WorkOrderListItem {
  id: string;
  name: string;
  production_item_code: string | null;
  production_item_name: string | null;
  qty: string;
  produced_qty: string;
  status: string;
  planned_start_date: string | null;
  docstatus: number;
}

// --- material availability ---------------------------------------------------

export interface MaterialAvailabilityRow {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  required_qty: string;
  consumed_qty: string;
  pending_qty: string;
  source_warehouse_id: string | null;
  available_qty: string;
  shortfall_qty: string;
}

export interface MaterialAvailability {
  work_order_id: string;
  can_finish_qty: string;
  rows: MaterialAvailabilityRow[];
}

// --- Manufacturing Settings --------------------------------------------------

export type OrderFulfillmentMode = "off" | "warn" | "block";

export interface ManufacturingSettings {
  default_source_warehouse_id: string | null;
  default_wip_warehouse_id: string | null;
  default_fg_warehouse_id: string | null;
  over_production_percentage: string;
  capacity_planning_enabled: boolean;
  order_fulfillment_mode: OrderFulfillmentMode;
  /** Calendar days warehouse → customer when Shipping Rule has no transit_days. */
  outbound_delivery_days: number;
}

export interface JobCardTimeLogRow {
  id: string;
  idx: number;
  from_time: string;
  to_time: string | null;
  time_in_mins: string;
  completed_qty: string;
}

export interface JobCardDetail extends DocumentMeta {
  name: string;
  work_order_id: string;
  work_order_name: string | null;
  work_order_operation_id: string;
  operation_id: string;
  operation_name: string | null;
  workstation_id: string | null;
  workstation_name: string | null;
  for_quantity: string;
  total_completed_qty: string;
  time_in_mins: string;
  status: string;
  remarks: string | null;
  company_id: string;
  time_logs: JobCardTimeLogRow[];
}

export interface JobCardListItem {
  id: string;
  name: string;
  work_order_name: string | null;
  operation_name: string | null;
  workstation_name: string | null;
  for_quantity: string;
  total_completed_qty: string;
  status: string;
  docstatus: number;
}

// --- Production Plan ---------------------------------------------------------

export interface ProductionPlanItemRow {
  id: string;
  idx: number;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  bom_id: string | null;
  bom_name: string | null;
  sales_order_id: string | null;
  sales_order_name: string | null;
  sales_order_item_id: string | null;
  planned_qty: string;
  pending_qty: string;
  ordered_qty: string;
  warehouse_id: string | null;
  planned_start_date: string | null;
  work_order_id: string | null;
  description: string | null;
}

export interface ProductionPlanMRRow {
  id: string;
  idx: number;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  warehouse_id: string | null;
  required_qty: string;
  available_qty: string;
  shortfall_qty: string;
  material_request_id: string | null;
}

export interface ProductionPlanDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  from_date: string | null;
  to_date: string | null;
  get_items_from: string;
  fg_warehouse_id: string | null;
  source_warehouse_id: string | null;
  status: string;
  work_orders_created: boolean;
  material_requests_created: boolean;
  remarks: string | null;
  company_id: string;
  items: ProductionPlanItemRow[];
  material_requests: ProductionPlanMRRow[];
}

export interface ProductionPlanListItem {
  id: string;
  name: string;
  posting_date: string;
  from_date: string | null;
  to_date: string | null;
  status: string;
  work_orders_created: boolean;
  material_requests_created: boolean;
  docstatus: number;
}

export interface ProductionPlanCreateResult {
  production_plan_id: string;
  created_ids: string[];
  created_names: string[];
  count: number;
}

// --- reports -----------------------------------------------------------------

export interface MaterialShortageRow {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  warehouse_id: string | null;
  warehouse_name: string | null;
  pending_qty: string;
  available_qty: string;
  shortfall_qty: string;
  work_orders: string[];
}

export interface ProductionRegisterRow {
  work_order_id: string;
  name: string;
  production_item_code: string | null;
  production_item_name: string | null;
  bom_name: string | null;
  status: string;
  qty: string;
  produced_qty: string;
  pending_qty: string;
  planned_start_date: string | null;
  actual_end_date: string | null;
  estimated_cost: string;
  operating_cost: string;
}

export interface BomWhereUsedRow {
  bom_id: string;
  bom_name: string;
  production_item_code: string | null;
  production_item_name: string | null;
  is_active: boolean;
  is_default: boolean;
  qty_per_batch: string;
}

export interface BomStockReportRow {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  required_qty: string;
  available_qty: string;
  shortfall_qty: string;
}

export interface BomStockReport {
  bom_id: string;
  bom_name: string;
  for_qty: string;
  buildable_qty: string;
  rows: BomStockReportRow[];
}

export interface BomExplorerRow {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  stock_qty: string;
  rate: string;
  amount: string;
  level: number;
  source_warehouse_id: string | null;
  is_leaf: boolean;
}

export interface BomExplorerReport {
  bom_id: string;
  bom_name: string;
  production_item_code: string | null;
  production_item_name: string | null;
  for_qty: string;
  flatten_all: boolean;
  raw_material_cost: string;
  scrap_cost: string;
  operating_cost: string;
  total_cost: string;
  rows: BomExplorerRow[];
  scrap_rows: BomExplorerRow[];
}

// --- Subcontract Job ---------------------------------------------------------

export interface SubcontractJobListItem {
  id: string;
  name: string;
  supplier_name: string | null;
  production_item_code: string | null;
  bom_name: string | null;
  qty: string;
  sent_qty: string;
  received_qty: string;
  status: string;
  docstatus: number;
  posting_date: string;
}

export interface SubcontractJobItemRow {
  id: string;
  idx: number;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  required_qty: string;
  sent_qty: string;
  consumed_qty: string;
  source_warehouse_id: string | null;
  rate: string;
  amount: string;
}

export interface SubcontractJobDetail extends DocumentMeta {
  name: string;
  supplier_id: string;
  supplier_name: string | null;
  production_item_id: string;
  production_item_code: string | null;
  production_item_name: string | null;
  bom_id: string;
  bom_name: string | null;
  qty: string;
  sent_qty: string;
  received_qty: string;
  source_warehouse_id: string;
  supplier_warehouse_id: string;
  fg_warehouse_id: string;
  service_cost: string;
  service_cost_account_id: string | null;
  status: string;
  posting_date: string;
  remarks: string | null;
  company_id: string;
  items: SubcontractJobItemRow[];
}

// --- Quality Inspection ------------------------------------------------------

export interface QualityInspectionListItem {
  id: string;
  name: string;
  reference_type: string;
  reference_name: string | null;
  item_code: string | null;
  qty: string;
  status: string;
  docstatus: number;
  inspection_date: string;
}

export interface QualityInspectionDetail extends DocumentMeta {
  name: string;
  reference_type: string;
  reference_id: string;
  reference_name: string | null;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  qty: string;
  status: string;
  inspection_date: string;
  inspected_by: string | null;
  remarks: string | null;
  company_id: string;
}

export interface WorkOrderSummaryRow {
  status: string;
  count: number;
  total_qty: string;
  total_produced_qty: string;
  total_pending_qty: string;
  total_estimated_cost: string;
}

export interface ProductionAnalyticsRow {
  period: string;
  work_orders_completed: number;
  qty_produced: string;
  estimated_cost: string;
}

/** Phase 7.0 capable-to-promise / lead-time estimate. */
export interface LeadTimeComponentRow {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  required_qty: string;
  available_qty: string;
  shortfall_qty: string;
  lead_time_days: number;
  drives_wait: boolean;
  supply_ready_date?: string | null;
  supply_source?: string | null;
}

export interface LeadTimeEstimate {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  bom_id: string | null;
  bom_name: string | null;
  qty: string;
  as_of: string;
  warehouse_id: string | null;
  procurement_days: number;
  manufacturing_days: number;
  outbound_days: number;
  total_days: number;
  ready_to_dispatch_date: string;
  earliest_promise_date: string;
  operation_mins: string;
  components: LeadTimeComponentRow[];
  notes: string[];
}

export interface ProcurementSuggestion {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  shortfall_qty: string;
  lead_time_days: number;
  latest_order_date: string;
  days_until_order: number;
}

/** Phase 7.1 reverse schedule from a customer receipt date. */
export interface ReverseSchedule {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  bom_id: string | null;
  bom_name: string | null;
  qty: string;
  as_of: string;
  delivery_date: string;
  warehouse_id: string | null;
  procurement_days: number;
  manufacturing_days: number;
  outbound_days: number;
  total_days: number;
  earliest_promise_date: string;
  ready_to_dispatch_date: string;
  manufacturing_start_date: string;
  materials_ready_by: string;
  on_time: boolean;
  slack_days: number;
  operation_mins: string;
  procurement: ProcurementSuggestion[];
  components: LeadTimeComponentRow[];
  notes: string[];
}

export interface PeggingRow {
  side: string;
  source_type: string;
  source_id: string | null;
  source_name: string | null;
  qty: string;
  due_date: string | null;
  notes: string | null;
}

/** Phase 7.2 demand → supply pegging. */
export interface PeggingTimeline {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  as_of: string;
  warehouse_id: string | null;
  demand_qty: string;
  supply_qty: string;
  net_shortfall: string;
  earliest_promise_date: string | null;
  ctp: LeadTimeEstimate | null;
  rows: PeggingRow[];
  notes: string[];
}

/** Phase 7.3 light demand forecast. */
export interface ForecastHistoryRow {
  period: string;
  demand_qty: string;
  is_forecast: boolean;
}

export interface DemandForecast {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  lookback_months: number;
  horizon_months: number;
  method: string;
  average_monthly_demand: string;
  rows: ForecastHistoryRow[];
  notes: string[];
}

export interface WhatIfStockOverride {
  item_id: string;
  extra_qty: number;
}

export interface WhatIfLeadOverride {
  item_id: string;
  lead_time_days: number;
}

export interface WhatIfCtpIn {
  item_id: string;
  qty: number;
  as_of?: string | null;
  warehouse_id?: string | null;
  extra_stock?: WhatIfStockOverride[];
  lead_time_overrides?: WhatIfLeadOverride[];
}

/** Phase 7.5 soft capacity board. */
export interface CapacityBoardRow {
  workstation_id: string;
  workstation_name: string;
  working_hours_per_day: string;
  capacity_mins_per_day: string;
  planned_mins: string;
  open_work_orders: number;
  utilization_pct: string;
  overloaded: boolean;
}

export interface CapacityBoard {
  as_of: string;
  rows: CapacityBoardRow[];
  notes: string[];
}

export interface PlanningContextLine {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  qty: string;
  delivery_date: string | null;
  warehouse_id: string | null;
  source_label: string | null;
}

export interface PlanningContext {
  context_type: string;
  document_id: string | null;
  document_name: string | null;
  lines: PlanningContextLine[];
}
