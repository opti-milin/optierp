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
  currency: string;
  operating_cost: string;
  raw_material_cost: string;
  total_cost: string;
  cost_per_unit: string;
  remarks: string | null;
  company_id: string;
  items: BomItemRow[];
}

export interface BomListItem {
  id: string;
  name: string;
  production_item_code: string | null;
  production_item_name: string | null;
  quantity: string;
  is_active: boolean;
  is_default: boolean;
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
}

export interface WorkOrderDetail extends DocumentMeta {
  name: string;
  production_item_id: string;
  production_item_code: string | null;
  production_item_name: string | null;
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

// --- Phase 3: material availability ------------------------------------------

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

// --- Manufacturing Settings (lean per-company defaults) -----------------------

export interface ManufacturingSettings {
  default_source_warehouse_id: string | null;
  default_wip_warehouse_id: string | null;
  default_fg_warehouse_id: string | null;
  over_production_percentage: string;
}

// --- Phase 4: reports --------------------------------------------------------

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
