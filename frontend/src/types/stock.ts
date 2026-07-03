// Module 03 — Stock types (mirror backend/app/schemas/stock.py)

import type { DocumentMeta } from "@/types/core";

export interface ItemGroup extends DocumentMeta {
  item_group_name: string;
  parent_item_group_id: string | null;
  is_group: boolean;
  disabled: boolean;
}

export interface Warehouse extends DocumentMeta {
  warehouse_name: string;
  parent_warehouse_id: string | null;
  is_group: boolean;
  warehouse_type: string | null;
  account_id: string | null;
  disabled: boolean;
}

export interface Item extends DocumentMeta {
  item_code: string;
  item_name: string;
  description: string | null;
  item_group_id: string | null;
  item_group_name: string | null;
  stock_uom: string;
  purchase_uom: string | null;
  purchase_uom_factor: string;
  sales_uom: string | null;
  sales_uom_factor: string;
  has_serial_no: boolean;
  has_batch_no: boolean;
  is_stock_item: boolean;
  is_sales_item: boolean;
  is_purchase_item: boolean;
  valuation_method: string;
  standard_rate: string;
  valuation_rate: string;
  last_purchase_rate: string;
  income_account_id: string | null;
  expense_account_id: string | null;
  item_tax_template_id: string | null;
  default_warehouse_id: string | null;
  reorder_level: string;
  reorder_qty: string;
  lead_time_days: number;
  brand: string | null;
  barcode: string | null;
  is_fixed_asset: boolean;
  asset_category_id: string | null;
  hsn_sac_code: string | null;
  gst_treatment: string;
  disabled: boolean;
}

export interface ItemListItem {
  id: string;
  item_code: string;
  item_name: string;
  item_group_name: string | null;
  stock_uom: string;
  purchase_uom: string | null;
  purchase_uom_factor: string;
  sales_uom: string | null;
  sales_uom_factor: string;
  has_serial_no: boolean;
  has_batch_no: boolean;
  is_stock_item: boolean;
  standard_rate: string;
  hsn_sac_code: string | null;
  disabled: boolean;
}

export interface PriceList extends DocumentMeta {
  price_list_name: string;
  currency: string;
  buying: boolean;
  selling: boolean;
  enabled: boolean;
}

export interface ItemRate {
  item_id: string;
  rate: string;
  source: string;
  uom: string;
  item_name: string;
  description: string | null;
}

export interface StockEntryItemIn {
  item_id: string;
  qty: number;
  basic_rate?: number;
  uom?: string | null;
  source_warehouse_id?: string | null;
  target_warehouse_id?: string | null;
}

export interface StockEntryListItem {
  id: string;
  name: string;
  posting_date: string;
  purpose: string;
  total_amount: string;
  docstatus: number;
}

export interface StockEntryDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  purpose: string;
  from_warehouse_id: string | null;
  to_warehouse_id: string | null;
  total_amount: string;
  remarks: string | null;
  items: Array<{
    idx: number;
    item_id: string;
    item_code: string | null;
    item_name: string | null;
    qty: string;
    uom: string | null;
    basic_rate: string;
    amount: string;
  }>;
}

export interface StockReconciliationItemIn {
  item_id: string;
  warehouse_id?: string | null;
  qty: number;
  valuation_rate?: number | null;
  uom?: string | null;
}

export interface StockReconciliationListItem {
  id: string;
  name: string;
  posting_date: string;
  purpose: string;
  difference_amount: string;
  docstatus: number;
}

export interface StockReconciliationDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  purpose: string;
  set_warehouse_id: string | null;
  difference_amount: string;
  remarks: string | null;
  items: Array<{
    idx: number;
    item_id: string;
    item_code: string | null;
    item_name: string | null;
    warehouse_id: string;
    qty: string;
    uom: string | null;
    valuation_rate: string;
    current_qty: string;
    current_valuation_rate: string;
    amount_difference: string;
  }>;
}

export interface MaterialRequestListItem {
  id: string;
  name: string;
  posting_date: string;
  material_request_type: string;
  status: string;
  per_ordered: string;
  docstatus: number;
}

export interface MaterialRequestDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  material_request_type: string;
  schedule_date: string | null;
  status: string;
  per_ordered: string;
  remarks: string | null;
  items: Array<{
    id: string;
    idx: number;
    item_id: string;
    item_code: string | null;
    item_name: string | null;
    warehouse_id: string | null;
    qty: string;
    uom: string | null;
    ordered_qty: string;
  }>;
}

export interface FulfilmentItem {
  id: string;
  idx: number;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  warehouse_id: string;
  qty: string;
  uom: string | null;
  rate: string;
  amount: string;
  billed_qty: string;
  rejected_qty?: string | null; // PR only
  rejected_warehouse_id?: string | null; // PR only
  serial_nos?: string | null; // newline-separated
  batch_no?: string | null; // lot label (batched items)
  sales_order_item_id?: string | null;
  purchase_order_item_id?: string | null;
}

// Delivery Note / Purchase Receipt share one list/detail shape in the UI
export interface FulfilmentListItem {
  id: string;
  name: string;
  posting_date: string;
  customer_name?: string | null;
  supplier_name?: string | null;
  currency: string | null;
  grand_total: string;
  status: string;
  per_billed: string;
  is_return: boolean;
  docstatus: number;
}

export interface FulfilmentDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  customer_id?: string;
  customer_name?: string | null;
  supplier_id?: string;
  supplier_name?: string | null;
  currency: string;
  total_qty: string;
  grand_total: string;
  status: string;
  per_billed: string;
  is_return: boolean;
  return_against_id?: string | null;
  supplier_delivery_note?: string | null; // PR only
  remarks: string | null;
  items: FulfilmentItem[];
}

export interface ServiceCreditListItem {
  id: string;
  name: string;
  item_code: string | null;
  item_name: string | null;
  supplier_name: string | null;
  purchased_qty: string;
  consumed_qty: string;
  balance_qty: string;
  uom: string | null;
  status: string;
}

export interface ServiceCreditUsageRow {
  idx: number;
  usage_date: string;
  qty: string;
  remarks: string | null;
}

export interface ServiceCreditDetail extends DocumentMeta {
  name: string;
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  supplier_id: string | null;
  supplier_name: string | null;
  purchase_date: string;
  purchased_qty: string;
  consumed_qty: string;
  balance_qty: string;
  rate: string;
  uom: string | null;
  valid_upto: string | null;
  status: string;
  remarks: string | null;
  purchase_invoice_id: string | null;
  purchase_invoice_name: string | null;
  prepaid_account_id: string | null;
  expense_account_id: string | null;
  cost_center_id: string | null;
  usages: ServiceCreditUsageRow[];
}

export interface StockBalanceRow {
  item_id: string;
  item_code: string;
  item_name: string;
  warehouse_id: string;
  warehouse_name: string;
  actual_qty: string;
  reserved_qty: string;
  ordered_qty: string;
  projected_qty: string;
  valuation_rate: string;
  stock_value: string;
}

export interface StockLedgerRow {
  posting_date: string;
  item_code: string;
  item_name: string;
  warehouse_name: string;
  voucher_type: string;
  voucher_no: string;
  actual_qty: string;
  qty_after_transaction: string;
  incoming_rate: string;
  valuation_rate: string;
  stock_value_difference: string;
}

export interface StockAgeingRow {
  item_id: string;
  item_code: string;
  item_name: string;
  warehouse_id: string;
  warehouse_name: string;
  total_qty: string;
  average_age_days: number;
  bucket_0_30: string;
  bucket_31_60: string;
  bucket_61_90: string;
  bucket_90_plus: string;
  stock_value: string;
}

export interface ReorderRow {
  item_id: string;
  item_code: string;
  item_name: string;
  default_warehouse_id: string | null;
  default_warehouse_name: string | null;
  projected_qty: string;
  reorder_level: string;
  reorder_qty: string;
  shortfall: string;
  suggested_qty: string;
}
