// Modules 04/05 — order document types (PO / SO / Quotation / Supplier Quotation / RFQ)

import type { DocumentMeta } from "@/types/core";

export interface OrderItemIn {
  item_id: string | null; // null = free-text line
  item_name?: string | null;
  qty: number;
  rate?: number | null;
  price_list_rate?: number | null;
  discount_percentage?: number | null;
  discount_amount?: number | null;
  uom?: string | null;
  description?: string | null;
  warehouse_id?: string | null;
  schedule_date?: string | null;
  delivery_date?: string | null;
  material_request_item_id?: string | null;
  quotation_item_id?: string | null;
  _uomOptions?: { value: string; label: string }[]; // client-only per-row UOM options
  _rowKey?: string; // client-only editable-grid key (ignored by the backend)
}

export interface OrderListItem {
  id: string;
  name: string;
  posting_date: string;
  customer_name?: string | null;
  supplier_name?: string | null;
  currency: string | null;
  grand_total: string;
  status: string;
  per_received?: string | null;
  per_delivered?: string | null;
  per_billed?: string | null;
  docstatus: number;
}

export interface OrderItemDetail {
  id: string;
  idx: number;
  item_id: string | null;
  item_code: string | null;
  item_name: string;
  description: string | null;
  qty: string;
  uom: string | null;
  conversion_factor?: string;
  stock_qty?: string;
  price_list_rate?: string;
  discount_percentage?: string;
  discount_amount?: string;
  rate: string;
  amount: string;
  warehouse_id?: string | null;
  received_qty?: string;
  delivered_qty?: string;
  billed_amt?: string;
}

export interface OrderTaxDetail {
  idx: number;
  charge_type: string;
  rate: string;
  description: string | null;
  tax_amount: string;
}

export interface OrderDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  currency: string;
  total_qty: string;
  net_total: string;
  total_taxes_and_charges: string;
  discount_amount: string;
  grand_total: string;
  rounded_total: string;
  status: string;
  remarks: string | null;
  items: OrderItemDetail[];
  taxes: OrderTaxDetail[];
  // party (one of the two)
  customer_id?: string;
  customer_name?: string | null;
  supplier_id?: string;
  supplier_name?: string | null;
  // purchase order
  schedule_date?: string | null;
  per_received?: string;
  // sales order
  delivery_date?: string | null;
  per_delivered?: string;
  quotation_id?: string | null;
  order_type?: string;
  po_no?: string | null;
  po_date?: string | null;
  terms?: string | null;
  customer_address_id?: string | null;
  supplier_address_id?: string | null;
  shipping_address_id?: string | null;
  contact_person_id?: string | null;
  warnings?: string[];
  // shared
  per_billed?: string;
  valid_till?: string | null;
  payment_terms_template_id?: string | null;
}

export interface RFQListItem {
  id: string;
  name: string;
  posting_date: string;
  status: string;
  docstatus: number;
}

export interface RFQDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  schedule_date: string | null;
  message_for_supplier: string | null;
  status: string;
  items: Array<{
    id: string;
    idx: number;
    item_id: string;
    item_code: string | null;
    item_name: string | null;
    qty: string;
    uom: string | null;
  }>;
  suppliers: Array<{
    idx: number;
    supplier_id: string;
    supplier_name: string | null;
    quote_status: string;
  }>;
}

/** Capable-to-promise + BOM cost estimate from check-fulfillment endpoints. */
export interface OrderFulfillmentShortfall {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  shortfall_qty: string;
  estimated_buy_cost: string;
}

export interface OrderFulfillmentLine {
  item_id: string;
  item_code: string | null;
  item_name: string | null;
  qty: string;
  delivery_date: string | null;
  warehouse_id: string | null;
  bom_id: string | null;
  bom_name: string | null;
  on_time: boolean | null;
  earliest_promise_date: string | null;
  bom_cost_per_unit: string;
  estimated_cost: string;
  selling_amount: string;
  estimated_margin: string;
  estimated_margin_pct: string | null;
  shortfalls: OrderFulfillmentShortfall[];
  notes: string[];
  skipped: boolean;
}

export interface OrderFulfillment {
  mode: string;
  can_fulfill_on_time: boolean | null;
  earliest_promise_date: string | null;
  estimated_cost: string;
  estimated_selling_amount: string;
  estimated_margin: string;
  estimated_margin_pct: string | null;
  lines: OrderFulfillmentLine[];
  warnings: string[];
  hard_block_reasons: string[];
  planning_dashboard_path: string;
}
