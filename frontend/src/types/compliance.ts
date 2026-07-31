// India compliance — GST returns (GSTR-1 / GSTR-3B). Money fields are serialised
// as strings (Decimal). Mirrors backend app/schemas/compliance.py.

export interface Gstr1Invoice {
  invoice_id: string;
  name: string;
  posting_date: string;
  counterparty_gstin: string | null;
  invoice_value: string;
  place_of_supply: string;
  reverse_charge: boolean;
  rate: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
  is_return: boolean;
}

export interface Gstr1B2B {
  gstin: string;
  party_name: string | null;
  invoices: Gstr1Invoice[];
  taxable_value: string;
  total_tax: string;
}

export interface Gstr1B2CS {
  place_of_supply: string;
  supply_type: string;
  rate: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
}

export interface Gstr1Hsn {
  hsn_code: string | null;
  description: string | null;
  uqc: string | null;
  qty: string;
  rate: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
}

export interface Gstr1DocSummary {
  nature: string;
  from_no: string;
  to_no: string;
  total_count: number;
  cancelled: number;
  net_issued: number;
}

export interface Gstr1Totals {
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
  invoice_count: number;
}

export interface Gstr1Report {
  gstin: string | null;
  filing_period: string;
  from_date: string;
  to_date: string;
  b2b: Gstr1B2B[];
  b2cl: Gstr1Invoice[];
  b2cs: Gstr1B2CS[];
  cdnr: Gstr1Invoice[];
  cdnur: Gstr1Invoice[];
  hsn: Gstr1Hsn[];
  docs: Gstr1DocSummary[];
  totals: Gstr1Totals;
}

export interface Gstr3bTaxRow {
  label: string;
  taxable_value: string;
  igst: string;
  cgst: string;
  sgst: string;
  cess: string;
}

export interface Gstr3bInterStateRow {
  place_of_supply: string;
  taxable_value: string;
  igst: string;
}

export interface Gstr3bItcRow {
  label: string;
  igst: string;
  cgst: string;
  sgst: string;
  cess: string;
}

export interface Gstr3bReport {
  gstin: string | null;
  filing_period: string;
  from_date: string;
  to_date: string;
  outward: Gstr3bTaxRow[];
  inter_state_unreg: Gstr3bInterStateRow[];
  itc: Gstr3bItcRow[];
  net_tax_payable: Gstr3bTaxRow;
}

// GSTR-2B reconciliation (Phase 6.1)
export interface Gstr2bReconRow {
  supplier_gstin: string | null;
  supplier_name: string | null;
  invoice_no: string | null;
  invoice_date: string | null;
  books_ref: string | null;
  books_taxable: string | null;
  books_tax: string | null;
  portal_taxable: string | null;
  portal_tax: string | null;
  taxable_diff: string | null;
  tax_diff: string | null;
  status: string;
}

export interface Gstr2bReconSummary {
  books_count: number;
  portal_count: number;
  matched: number;
  mismatch: number;
  only_in_books: number;
  only_in_2b: number;
  books_itc: string;
  portal_itc: string;
  matched_itc: string;
  at_risk_itc: string;
}

export interface Gstr2bReconReport {
  from_date: string;
  to_date: string;
  summary: Gstr2bReconSummary;
  rows: Gstr2bReconRow[];
}

// TDS returns — Form 26Q + Form 16A (Phase 6.2)
export interface Tds26qDoc {
  voucher: string;
  date: string;
  base_amount: string;
  tds: string;
  rate: string;
}

export interface Tds26qRow {
  supplier_id: string | null;
  deductee_name: string | null;
  pan: string | null;
  gstin: string | null;
  section: string | null;
  category: string | null;
  rate: string;
  total_base: string;
  total_tds: string;
  doc_count: number;
  documents: Tds26qDoc[];
}

export interface Tds26qSummary {
  deductee_count: number;
  document_count: number;
  total_base: string;
  total_tds: string;
}

export interface Tds26qReport {
  deductor_name: string | null;
  deductor_gstin: string | null;
  deductor_tan: string | null;
  from_date: string;
  to_date: string;
  rows: Tds26qRow[];
  summary: Tds26qSummary;
}

export interface Form16A {
  deductor_name: string | null;
  deductor_gstin: string | null;
  deductor_tan: string | null;
  deductee_name: string | null;
  deductee_pan: string | null;
  deductee_gstin: string | null;
  from_date: string;
  to_date: string;
  sections: Tds26qRow[];
  total_base: string;
  total_tds: string;
}

// --- Income Tax (entity ITR) ------------------------------------------------

export interface IncomeTaxSettings {
  entity_type: string;
  filing_regime: string;
  default_assessment_year: string | null;
  itr_efile_provider: string | null;
  pan: string | null;
  tan: string | null;
}

export interface IncomeTaxAdjustmentLine {
  id: string;
  idx: number;
  category_id: string | null;
  provision_id?: string | null;
  rule_id?: string | null;
  section_code?: string;
  stage?: string;
  description: string;
  direction: string;
  amount: string;
  base_amount?: string;
  computed_amount?: string;
  override_amount?: string | null;
  final_amount?: string;
  status?: string;
  explanation?: Record<string, unknown>;
  inputs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  prior_year_line_id?: string | null;
}

export interface IncomeTaxSpecialIncomeLine {
  id: string;
  idx: number;
  special_rate_id: string | null;
  income_category_code: string;
  amount: string;
  rate_percent: string;
  tax_amount: string;
  description: string | null;
}

export interface IncomeTaxComputation {
  id: string;
  name: string;
  assessment_year: string;
  from_date: string;
  to_date: string;
  rate_table_id: string | null;
  policy_id: string | null;
  computation_method: string | null;
  assessee_mode: string;
  book_profit: string;
  salary_income: string;
  house_property_income: string;
  other_sources_income: string;
  capital_gains_income: string;
  chapter_via_deduction: string;
  standard_deduction: string;
  net_adjustments: string;
  taxable_income: string;
  tax_amount: string;
  surcharge_amount: string;
  cess_amount: string;
  rebate_amount: string;
  rebate_87a: string;
  marginal_relief_amount: string;
  total_tax: string;
  tds_credit: string;
  tcs_credit: string;
  salary_tds: string;
  advance_tax_paid: string;
  tax_payable: string;
  tax_breakdown: Record<string, unknown>;
  employer_name: string | null;
  employer_tan: string | null;
  employer_address: string | null;
  employee_name: string | null;
  employee_pan: string | null;
  gross_salary: string;
  exemptions_total: string;
  taxable_salary: string;
  tax_deducted: string;
  seed_source: string;
  employee_id: string | null;
  payroll_entry_id: string | null;
  salary_slip_ids: string[];
  status: string;
  docstatus: number;
  remarks: string | null;
  adjustments: IncomeTaxAdjustmentLine[];
  special_income_lines: IncomeTaxSpecialIncomeLine[];
}

export interface IncomeTaxComputationListItem {
  id: string;
  name: string;
  assessment_year: string;
  from_date: string;
  to_date: string;
  assessee_mode?: string;
  taxable_income: string;
  total_tax: string;
  tax_payable: string;
  status: string;
  docstatus: number;
  employee_name?: string | null;
}
