// Module 02 — Accounts: TypeScript mirrors of the backend Pydantic schemas.

import type { DocumentMeta } from "./core";

export interface Customer extends DocumentMeta {
  customer_name: string;
  customer_type: string;
  tax_id: string | null;
  disabled: boolean;
}

export interface Supplier extends DocumentMeta {
  supplier_name: string;
  supplier_type: string;
  tax_id: string | null;
  disabled: boolean;
}

export interface InvoiceItemIn {
  item_code?: string | null;
  item_name: string;
  description?: string | null;
  qty: number;
  uom?: string | null;
  rate: number;
  price_list_rate?: number | null;
  discount_percentage?: number | null;
  discount_amount?: number | null;
  account_id?: string | null;
  // India GST: HSN/SAC override; defaults from the item master when omitted
  hsn_sac_code?: string | null;
  // Module 03-05 cycle links
  item_id?: string | null;
  sales_order_item_id?: string | null;
  delivery_note_item_id?: string | null;
  purchase_order_item_id?: string | null;
  purchase_receipt_item_id?: string | null;
  _rowKey?: string; // client-only editable-grid key (ignored by the backend)
}

export interface TaxRowIn {
  charge_type: string;
  rate: number;
  tax_amount?: number;
  row_id?: number | null;
  account_head_id: string;
  description?: string | null;
  add_deduct_tax?: string;
  category?: string;
  included_in_print_rate?: boolean;
}

export interface TaxTemplateDetail {
  charge_type: string;
  rate: string;
  tax_amount: string;
  row_id: number | null;
  account_head_id: string;
  cost_center_id: string | null;
  description: string | null;
  add_deduct_tax: string;
  category: string;
  included_in_print_rate: boolean;
}

export interface TaxTemplate {
  id: string;
  title: string;
  kind: string; // "sales" | "purchase"
  is_default: boolean;
  tax_category_id: string | null;
  details: TaxTemplateDetail[];
}

export interface CostCenter {
  id: string;
  cost_center_name: string;
  is_group: boolean;
}

export interface InvoiceListItem {
  id: string;
  name: string;
  posting_date: string;
  due_date: string | null;
  currency: string | null;
  customer_name: string | null;
  supplier_name: string | null;
  grand_total: string;
  outstanding_amount: string;
  status: string;
  docstatus: number;
}

export interface InvoiceDetail extends DocumentMeta {
  name: string;
  posting_date: string;
  due_date: string | null;
  currency: string;
  net_total: string;
  total_taxes_and_charges: string;
  discount_amount: string;
  grand_total: string;
  rounded_total: string;
  rounding_adjustment: string;
  outstanding_amount: string;
  tax_withholding_amount: string;
  status: string;
  is_return: boolean;
  po_no?: string | null;
  po_date?: string | null;
  terms?: string | null;
  payment_terms_template_id?: string | null;
  customer_address_id?: string | null;
  supplier_address_id?: string | null;
  shipping_address_id?: string | null;
  contact_person_id?: string | null;
  remarks: string | null;
  items: Array<{
    idx: number;
    item_name: string;
    qty: string;
    price_list_rate?: string;
    discount_percentage?: string;
    rate: string;
    amount: string;
    net_amount: string;
  }>;
  taxes: Array<{
    idx: number;
    charge_type: string;
    rate: string;
    description: string | null;
    tax_amount: string;
    total: string;
  }>;
}

export interface SalesInvoiceDetail extends InvoiceDetail {
  customer_id: string;
  customer_name: string | null;
}

export interface PurchaseInvoiceDetail extends InvoiceDetail {
  supplier_id: string;
  supplier_name: string | null;
  bill_no: string | null;
  bill_date: string | null;
}

export interface JournalEntryRowIn {
  account_id: string;
  debit: number;
  credit: number;
  party_type?: string | null;
  party_id?: string | null;
  user_remark?: string | null;
}

export interface JournalEntryListItem {
  id: string;
  name: string;
  posting_date: string;
  voucher_type: string;
  total_debit: string;
  docstatus: number;
}

export interface PaymentEntryListItem {
  id: string;
  name: string;
  posting_date: string;
  payment_type: string;
  party_type: string | null;
  party_id: string | null;
  paid_amount: string;
  unallocated_amount: string | null;
  reference_no: string | null;
  status: string;
  docstatus: number;
}

export interface PaymentReferenceRow {
  idx: number;
  reference_doctype: string; // Sales Invoice | Purchase Invoice
  reference_id: string;
  reference_name: string | null;
  total_amount: string;
  outstanding_amount: string;
  allocated_amount: string;
}

export interface PaymentEntryDetail {
  id: string;
  name: string;
  posting_date: string;
  payment_type: string;
  party_type: string | null;
  party_id: string | null;
  paid_from_id: string;
  paid_to_id: string;
  paid_amount: string;
  received_amount: string;
  total_allocated_amount: string;
  unallocated_amount: string;
  reference_no: string | null;
  reference_date: string | null;
  clearance_date: string | null;
  status: string;
  remarks: string | null;
  references: PaymentReferenceRow[];
}

export interface JournalEntryAccountRow {
  idx: number;
  account_id: string;
  debit: string;
  credit: string;
  party_type: string | null;
  party_id: string | null;
  cost_center_id: string | null;
  user_remark: string | null;
}

export interface JournalEntryDetail {
  id: string;
  name: string;
  posting_date: string;
  voucher_type: string;
  total_debit: string;
  total_credit: string;
  clearance_date: string | null;
  remarks: string | null;
  accounts: JournalEntryAccountRow[];
}

export interface TrialBalanceRow {
  account_id: string;
  account_name: string;
  root_type: string;
  is_group: boolean;
  path: string;
  opening_debit: string;
  opening_credit: string;
  debit: string;
  credit: string;
  closing_debit: string;
  closing_credit: string;
}

export interface StatementRow {
  account_id: string | null;
  account_name: string;
  root_type: string | null;
  is_group: boolean;
  indent: number;
  amount: string;
}

export interface AgingRow {
  party_name: string;
  voucher_no: string;
  posting_date: string;
  due_date: string | null;
  outstanding_amount: string;
  age_days: number;
  bucket_0_30: string;
  bucket_31_60: string;
  bucket_61_90: string;
  bucket_90_plus: string;
}

export interface PartyOutstandingSummaryRow {
  party_id: string;
  party_name: string;
  outstanding_amount: string;
  bucket_0_30: string;
  bucket_31_60: string;
  bucket_61_90: string;
  bucket_90_plus: string;
}

export interface CollectionSummaryRow {
  party_id: string;
  party_name: string;
  paid_invoices: number;
  avg_days_to_pay: number;
  total_collected: string;
}

export interface FiscalYearInfo extends DocumentMeta {
  year: string;
  year_start_date: string;
  year_end_date: string;
}

export interface BudgetAccountRow extends DocumentMeta {
  account_id: string;
  budget_amount: string;
}

export interface Budget extends DocumentMeta {
  fiscal_year_id: string;
  cost_center_id: string | null;
  action_if_annual_budget_exceeded: string;
  accounts: BudgetAccountRow[];
}

export interface UnreconciledInvoiceRow {
  invoice_type: string;
  invoice_id: string;
  name: string;
  posting_date: string;
  grand_total: string;
  outstanding_amount: string;
}

export interface UnreconciledPaymentRow {
  payment_entry_id: string;
  name: string;
  posting_date: string;
  paid_amount: string;
  unallocated_amount: string;
}

export interface UnreconciledResponse {
  invoices: UnreconciledInvoiceRow[];
  payments: UnreconciledPaymentRow[];
}

export interface BankReconUnclearedRow {
  voucher_type: string;
  voucher_id: string;
  voucher_no: string;
  posting_date: string;
  reference_no: string | null;
  amount: string;
}

export interface BankReconciliationReport {
  gl_account_id: string;
  as_of: string;
  balance_per_books: string;
  uncleared_amount: string;
  balance_per_bank: string;
  uncleared_entries: BankReconUnclearedRow[];
}

export interface RegisterRow {
  voucher_id: string;
  name: string;
  posting_date: string;
  party_name: string | null;
  net_total: string;
  total_taxes_and_charges: string;
  grand_total: string;
  outstanding_amount: string;
  status: string;
}

export interface RegisterReport {
  rows: RegisterRow[];
  total_net: string;
  total_tax: string;
  total_grand: string;
  total_outstanding: string;
}

export interface PartyLedgerSummaryRow {
  party_id: string;
  party_name: string;
  opening: string;
  debit: string;
  credit: string;
  closing: string;
}

export interface GrossProfitRow {
  item_code: string | null;
  item_name: string;
  qty: string;
  selling: string;
  cogs: string;
  gross_profit: string;
  margin_pct: string;
}

export interface GrossProfitReport {
  rows: GrossProfitRow[];
  total_selling: string;
  total_cogs: string;
  total_gross_profit: string;
  margin_pct: string;
}

export interface BudgetVarianceRow {
  account_id: string;
  account_name: string;
  budget: string;
  actual: string;
  variance: string;
  variance_pct: string;
}

export interface GeneralLedgerEntry {
  id: string;
  posting_date: string;
  account_id: string;
  party_type: string | null;
  party_id: string | null;
  debit: string;
  credit: string;
  voucher_type: string;
  voucher_no: string;
  against: string | null;
  is_cancellation: boolean;
  remarks: string | null;
}

export interface GeneralLedgerReport {
  opening_balance: string;
  entries: GeneralLedgerEntry[];
  total_debit: string;
  total_credit: string;
  closing_balance: string;
}

export interface ShareBalanceRow {
  shareholder_id: string;
  shareholder_name: string;
  share_type_id: string;
  share_type_name: string;
  no_of_shares: number;
  par_value: string;
  nominal_value: string;
  percent_of_type: string;
}

export interface ShareLedgerRow {
  id: string;
  name: string;
  transfer_date: string;
  transfer_type: string;
  share_type_name: string | null;
  from_shareholder_name: string | null;
  to_shareholder_name: string | null;
  no_of_shares: number;
  rate: string;
  amount: string;
}
