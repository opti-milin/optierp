// Shared types for the /tax/* API surface.
//
// Mirrors backend/app/schemas/taxation.py one-for-one: same field names,
// every Decimal as a string (the API serialises money to 2dp strings),
// date/datetime as an ISO string, dict as Record<string, unknown>.

// --- Tier 1: statutory catalogue -----------------------------------------------

export interface AssessmentYear {
  code: string;
  ay_start: string;
  ay_end: string;
  fy_start: string;
  fy_end: string;
}

export interface AssesseeClass {
  code: string;
  title: string;
  default_itr_form?: string | null;
}

// --- Tier 2: tenant tax configuration -----------------------------------------

/** Mirrors `TaxRegistrationOut`. */
export interface TaxRegistration {
  id: string;
  pan: string | null;
  tan: string | null;
  cin: string | null;
  assessee_class_code: string;
  residential_status: string;
  incorporation_date: string | null;
  nature_of_business_codes: string[];
  jurisdiction: string | null;
  default_assessment_year: string | null;
  itr_efile_provider: string | null;
  remarks: string | null;
}

/** Mirrors `TaxRegimeElectionOut`. */
export interface TaxRegimeElection {
  id: string;
  ay_code: string;
  regime_code: string;
  assessee_class_code: string;
  elected_on: string | null;
  form_ack_no: string | null;
  irrevocable: boolean;
  remarks: string | null;
  docstatus: number;
}

// --- Tier 3: computations ------------------------------------------------------

export interface TaxIncomeLine {
  id?: string;
  seq: number;
  head: string;
  income_character_code: string;
  sub_ref?: string | null;
  gross: string;
  deductions: string;
  net: string;
  source_doc_type?: string | null;
  source_doc_id?: string | null;
}

export interface TaxAdjustmentLine {
  id?: string;
  run_id?: string | null;
  provision_section_code?: string | null;
  rule_code?: string | null;
  section_code: string;
  stage: string;
  description?: string | null;
  direction: string;
  amount: string;
  override_amount?: string | null;
  final_amount?: string;
  status: string;
  explanation?: Record<string, unknown>;
}

/** Mirrors `TaxComputationOut`. */
export interface TaxComputation {
  id: string;
  name: string;
  ay_code: string;
  assessee_class_code: string;
  regime_election_id?: string | null;
  regime_code: string;
  filing_type: string;
  revises_computation_id?: string | null;
  from_date: string;
  to_date: string;
  finance_act_version_id: string;
  status: string;
  remarks?: string | null;
  current_run_id: string | null;
  book_profit_115jb?: string | null;
  return_filed_date?: string | null;
  audit_applicable?: boolean;
  itr_due_date_override?: string | null;
  provision_expense_account_id?: string | null;
  provision_liability_account_id?: string | null;
  provision_gl_posted?: boolean;
  docstatus: number;
  income_lines: TaxIncomeLine[];
  adjustment_lines: TaxAdjustmentLine[];
}

/** Mirrors `TaxComputationResultOut` (`id`/`run_id` absent on the nested run payload). */
export interface TaxRunResult {
  id?: string;
  run_id?: string;
  taxable_income: string;
  tax_normal: string;
  tax_mat: string | null;
  tax_applied_basis: string;
  tax_before_rebate: string;
  rebate_amount: string;
  surcharge_before_relief: string;
  marginal_relief_amount: string;
  surcharge_amount: string;
  cess_amount: string;
  interest_234a: string;
  interest_234b: string;
  interest_234c: string;
  credits_total: string;
  total_tax: string;
  net_payable: string;
  breakdown: Record<string, unknown>;
}

/** Mirrors `TaxComputationRunOut`. */
export interface TaxRun {
  id: string;
  run_no: number;
  trigger: string;
  engine_version: string;
  finance_act_version_id: string;
  ruleset_hash: string;
  input_hash: string;
  duration_ms?: number | null;
  user_id?: string | null;
  superseded_at: string | null;
  creation?: string | null;
  result: TaxRunResult | null;
}

// --- Tier 3: depreciation / losses / Minimum Alternate Tax credit ---------------

/** Mirrors `TaxDepreciationMovementOut`. */
export interface TaxDepreciationMovement {
  id: string;
  movement_type: string;
  amount: string;
  asset_id: string | null;
  put_to_use_date: string | null;
  half_rate: boolean;
  remarks: string | null;
}

/** Mirrors `TaxDepreciationRegisterOut`. */
export interface TaxDepreciationRegister {
  id: string;
  ay_code: string;
  block_code: string;
  rate_percent: string;
  opening_wdv: string;
  additions_full: string;
  additions_half: string;
  deletions: string;
  depreciation_amount: string;
  additional_depreciation_amount: string;
  closing_wdv: string;
  remarks: string | null;
  movements: TaxDepreciationMovement[];
}

/** Mirrors `TaxLossCarryForwardOut`. */
export interface TaxLossCarryForward {
  id: string;
  origin_ay_code: string;
  expires_after_ay: string | null;
  setoff_group: string;
  loss_kind: string;
  entry_kind: string;
  amount: string;
  amount_remaining: string;
  computation_id: string | null;
  remarks: string | null;
}

/** Mirrors `TaxLossSetoffEntryOut`. */
export interface TaxLossSetoffEntry {
  id: string;
  ay_code: string;
  computation_id: string | null;
  run_id: string | null;
  ledger_id: string;
  against_character: string;
  amount_set_off: string;
  sequence: number;
  explanation: Record<string, unknown>;
}

/** Mirrors `MatCreditLedgerOut` — Section 115JAA credit ledger. */
export interface TaxMatCredit {
  id: string;
  ay_code: string;
  entry_kind: string;
  amount: string;
  tax_mat: string | null;
  tax_normal: string | null;
  expires_after_ay: string | null;
  computation_id: string | null;
  run_id: string | null;
  remarks: string | null;
}

// --- Tier 3: credits, challans, Form 26AS reconciliation ------------------------

/** Mirrors `TaxCreditEntryOut`. */
export interface TaxCreditEntry {
  id: string;
  ay_code: string;
  credit_kind: string;
  computation_id: string | null;
  deductor_tan: string | null;
  deductor_name: string | null;
  section_code: string | null;
  amount_credited: string;
  amount_claimed: string;
  challan_id: string | null;
  reconciliation_status: string;
  portal_amount: string | null;
  source_refs: Record<string, unknown>;
  remarks: string | null;
  docstatus: number;
}

/** Mirrors `TaxChallanOut`. */
export interface TaxChallan {
  id: string;
  name: string;
  ay_code: string;
  challan_type: string;
  bsr_code: string;
  challan_serial: string;
  cin: string | null;
  deposit_date: string;
  major_head: string;
  minor_head: string;
  amount: string;
  bank_account_id: string;
  tax_payable_account_id: string;
  computation_id: string | null;
  remarks: string | null;
  docstatus: number;
}

/** Mirrors `Tax26asReconRowOut` (both amounts are already strings server-side). */
export interface Tax26asReconRow {
  bucket: string;
  deductor_tan: string | null;
  section_code: string | null;
  books_amount: string;
  portal_amount: string;
  credit_id: string | null;
}

/** Mirrors `Tax26asReconOut`. */
export interface Tax26asRecon {
  id: string;
  ay_code: string;
  computation_id: string | null;
  status: string;
  books_total: string;
  portal_total: string;
  difference: string;
  payload_hash: string;
  matched: number;
  mismatch: number;
  only_in_books: number;
  only_in_26as: number;
  rows: Tax26asReconRow[];
  creation: string | null;
}

// --- Advance tax + interest ----------------------------------------------------

/** Mirrors `AdvanceTaxInstalmentOut`. */
export interface AdvanceTaxInstalment {
  seq: number;
  code: string;
  label: string;
  due_date: string;
  cumulative_percent: string;
  required_cumulative: string;
  paid_to_date: string;
  shortfall: string;
  suggested_payment: string;
  status: string;
}

/** Mirrors `AdvanceTaxCalendarOut`. */
export interface AdvanceTaxCalendar {
  ay_code: string;
  estimated_tax_net: string;
  instalments: AdvanceTaxInstalment[];
}

// --- Return filing -------------------------------------------------------------

/** Mirrors `TaxFilingOut`. */
export interface TaxFiling {
  id: string;
  name: string;
  computation_id: string;
  run_id: string | null;
  ay_code: string;
  form_code: string;
  schema_version: string;
  filing_type: string;
  revises_filing_id: string | null;
  payload: Record<string, unknown>;
  payload_sha256: string;
  canonical_snapshot: Record<string, unknown>;
  status: string;
  ack_no: string | null;
  filed_on: string | null;
  verification_mode: string | null;
  provider: string | null;
  provider_response: Record<string, unknown>;
  remarks: string | null;
  docstatus: number;
  creation: string | null;
  modified: string | null;
}

// =============================================================================
// Unified workspace — BFF aggregate, non-persisting preview, lookups,
// templates, structured validation and the Statement of Total Income.
// =============================================================================

/** Server-computed per-section completion state (`SectionStatus`). */
export type TaxSectionStatus = "not-started" | "in-progress" | "complete" | "has-errors";

/** `IssueSeverity` — blocking issues gate submission, advisory ones do not. */
export type TaxIssueSeverity = "blocking" | "advisory";

/** Mirrors `TaxOptionOut`. `label` is always plain business English. */
export interface TaxOption {
  value: string;
  label: string;
  group?: string | null;
  hint?: string | null;
  statutory_ref?: string | null;
  meta?: Record<string, string>;
}

/** Mirrors `TaxLookupsOut` — every dropdown in the module, one round trip. */
export interface TaxLookups {
  ay_code: string | null;
  assessment_years: TaxOption[];
  entity_classes: TaxOption[];
  tax_regimes: TaxOption[];
  filing_types: TaxOption[];
  income_heads: TaxOption[];
  income_characters: TaxOption[];
  adjustment_provisions: TaxOption[];
  adjustment_stages: TaxOption[];
  adjustment_directions: TaxOption[];
  depreciation_blocks: TaxOption[];
  loss_kinds: TaxOption[];
  setoff_groups: TaxOption[];
  challan_types: TaxOption[];
  major_heads: TaxOption[];
  minor_heads: TaxOption[];
  bank_branch_codes: TaxOption[];
  credit_kinds: TaxOption[];
  deduction_sections: TaxOption[];
  deductors: TaxOption[];
  bank_accounts: TaxOption[];
  tax_payable_accounts: TaxOption[];
  tax_expense_accounts: TaxOption[];
  itr_forms: TaxOption[];
  verification_modes: TaxOption[];
}

/** Mirrors `TaxWorkspaceContextOut` — the only input to field visibility rules. */
export interface TaxWorkspaceContext {
  ay_code: string;
  ay_label: string;
  previous_ay_code: string | null;
  financial_year_label: string;
  fy_start: string;
  fy_end: string;
  entity_class_code: string;
  entity_class_label: string;
  regime_code: string;
  regime_label: string;
  regime_statutory_ref: string | null;
  regime_irrevocable: boolean;
  concessional_regime: boolean;
  forfeited_incentives: string[];
  mat_applicable: boolean;
  presumptive: boolean;
  audit_applicable: boolean;
  filing_type: string;
  itr_form_code: string | null;
  return_due_date: string | null;
  is_draft: boolean;
  is_submitted: boolean;
  is_cancelled: boolean;
  currency: string;
}

/** Mirrors `TaxValidationIssueOut`. `section` is a `TaxSectionKey` so the UI can jump to it. */
export interface TaxValidationIssue {
  severity: TaxIssueSeverity;
  code: string;
  message: string;
  section: string;
  field: string | null;
  hint: string | null;
  row_index: number | null;
}

/** Mirrors `TaxValidationOut`. */
export interface TaxValidation {
  ok: boolean;
  blocking_count: number;
  advisory_count: number;
  issues: TaxValidationIssue[];
}

/** Mirrors `TaxSectionStatusOut`. */
export interface TaxSectionStatusInfo {
  key: string;
  label: string;
  status: TaxSectionStatus;
  summary: string | null;
  row_count: number;
  blocking_count: number;
  advisory_count: number;
}

/** Mirrors `TaxPreviewRequest` — the unsaved editor overlay for a live recompute. */
export interface TaxPreviewRequest {
  income_lines?: TaxIncomeLine[] | null;
  adjustment_lines?: TaxAdjustmentLine[] | null;
  book_profit_115jb?: string | null;
  apply_book_profit?: boolean;
  return_filed_date?: string | null;
  audit_applicable?: boolean | null;
  itr_due_date_override?: string | null;
  tax_depreciation_total?: string | null;
  as_of_date?: string | null;
}

/** Mirrors `TaxPreviewLineOut` — one drill-down row on the live result rail. */
export interface TaxPreviewLine {
  key: string;
  label: string;
  amount: string;
  kind: string;
  statutory_ref: string | null;
  explain: string | null;
  emphasis: string;
}

/** Mirrors `TaxPreviewOut`. `persisted` is always false, by contract. */
export interface TaxPreview {
  ay_code: string;
  gross_total_income: string;
  total_income: string;
  losses_set_off: string;
  tax_depreciation_claimed: string;
  tax_on_total_income: string;
  rebate_amount: string;
  surcharge_before_relief: string;
  marginal_relief_amount: string;
  surcharge_amount: string;
  cess_amount: string;
  tax_normal: string;
  tax_mat: string | null;
  tax_applied_basis: string;
  mat_credit_created: string;
  mat_credit_utilised: string;
  interest_234a: string;
  interest_234b: string;
  interest_234c: string;
  total_interest: string;
  credits_total: string;
  total_tax: string;
  net_payable: string;
  refund_due: string;
  lines: TaxPreviewLine[];
  breakdown: Record<string, unknown>;
  engine_version: string;
  ruleset_hash: string;
  input_hash: string;
  persisted: boolean;
  matches_current_run: boolean;
  notes: string[];
}

/** Mirrors `TaxStatementRowOut`. */
export interface TaxStatementRow {
  key: string;
  label: string;
  amount: string | null;
  statutory_ref: string | null;
  indent: number;
  emphasis: string;
  note: string | null;
}

/** Mirrors `TaxStatementSectionOut`. */
export interface TaxStatementSection {
  key: string;
  title: string;
  rows: TaxStatementRow[];
}

/** Mirrors `TaxStatementOut` — Statement of Total Income + Tax Computation. */
export interface TaxStatement {
  ay_code: string;
  previous_ay_code: string | null;
  entity_label: string;
  pan: string | null;
  regime_label: string;
  basis_applied: string;
  sections: TaxStatementSection[];
  variance_previous_year: TaxStatementRow[];
  variance_books: TaxStatementRow[];
  notes: string[];
}

/** Mirrors `TaxTemplateAdjustmentOut`. */
export interface TaxTemplateAdjustment {
  section_code: string;
  label: string;
  stage: string;
  direction: string;
  hint: string | null;
}

/** Mirrors `TaxTemplateOut` — a system-supplied starting preset. */
export interface TaxTemplate {
  code: string;
  title: string;
  description: string;
  entity_class_code: string;
  regime_code: string;
  regime_statutory_ref: string | null;
  audit_applicable: boolean;
  mat_applicable: boolean;
  requires_book_profit: boolean;
  forfeited_incentives: string[];
  income_heads: string[];
  suggested_adjustments: TaxTemplateAdjustment[];
  notes: string[];
  available: boolean;
  unavailable_reason: string | null;
  recommended: boolean;
}

/** Mirrors `TaxTemplateApplyIn`. */
export interface TaxTemplateApplyIn {
  template_code: string;
  ay_code: string;
  book_profit_115jb?: string | null;
  copy_from_previous_year?: boolean;
  populate_from_books?: boolean;
  filing_type?: string;
  remarks?: string | null;
}

/** Mirrors `TaxCopyPreviousYearIn`. */
export interface TaxCopyPreviousYearIn {
  source_ay_code?: string | null;
  copy_income_heads?: boolean;
  copy_adjustments?: boolean;
  carry_depreciation_blocks?: boolean;
  overwrite_existing?: boolean;
}

/** Mirrors `TaxCopyPreviousYearOut`. */
export interface TaxCopyPreviousYearResult {
  source_ay_code: string | null;
  source_computation_id: string | null;
  income_lines_copied: number;
  adjustment_lines_copied: number;
  depreciation_blocks_carried: number;
  brought_forward_losses_available: number;
  notes: string[];
  computation: TaxComputation;
}

/** Mirrors `TaxPopulateFromBooksIn`. */
export interface TaxPopulateFromBooksIn {
  set_book_profit?: boolean;
  replace_income_heads?: boolean;
  add_accounting_depreciation_addback?: boolean;
}

/** Mirrors `TaxPopulateFromBooksOut`. */
export interface TaxPopulateFromBooksResult {
  ay_code: string;
  fy_start: string;
  fy_end: string;
  book_profit: string;
  accounting_depreciation: string;
  income_lines_written: number;
  adjustment_lines_written: number;
  source: string;
  notes: string[];
  computation: TaxComputation;
}

/** Mirrors `TaxYearSummaryOut` — one row on the Income Tax landing screen. */
export interface TaxYearSummary {
  ay_code: string;
  ay_label: string;
  financial_year_label: string;
  computation_id: string | null;
  computation_name: string | null;
  status: string;
  docstatus: number | null;
  regime_code: string | null;
  regime_label: string | null;
  total_income: string | null;
  net_payable: string | null;
  taxes_paid: string;
  return_due_date: string | null;
  next_due_label: string | null;
  next_due_date: string | null;
  next_action: string;
  next_action_section: string;
  blocking_count: number;
  advisory_count: number;
  filing_status: string | null;
  ack_no: string | null;
  is_current: boolean;
}

/** Mirrors `TaxWorkspaceBootstrapOut` — landing + workspace cold start. */
export interface TaxWorkspaceBootstrap {
  current_ay_code: string;
  default_ay_code: string;
  registration: TaxRegistration | null;
  registration_complete: boolean;
  years: TaxYearSummary[];
  templates: TaxTemplate[];
  lookups: TaxLookups;
}

/** Mirrors `TaxWorkspaceOut` — everything the unified workspace renders. */
export interface TaxWorkspace {
  computation: TaxComputation;
  context: TaxWorkspaceContext;
  registration: TaxRegistration | null;
  election: TaxRegimeElection | null;
  result: TaxRunResult | null;
  runs: TaxRun[];
  depreciation_registers: TaxDepreciationRegister[];
  depreciation_total: string;
  losses: TaxLossCarryForward[];
  setoff_entries: TaxLossSetoffEntry[];
  mat_credits: TaxMatCredit[];
  mat_credit_available: string;
  credits: TaxCreditEntry[];
  challans: TaxChallan[];
  reconciliations: Tax26asRecon[];
  advance_tax: AdvanceTaxCalendar | null;
  filings: TaxFiling[];
  sections: TaxSectionStatusInfo[];
  validation: TaxValidation;
  lookups: TaxLookups;
}

// --- Section contract every workspace panel implements -------------------------

export type TaxSectionKey =
  | "overview" | "income" | "adjustments" | "depreciation" | "losses" | "mat"
  | "credits" | "challans" | "reconciliation" | "interest" | "summary"
  | "review" | "filing" | "audit";

export interface TaxSectionProps {
  workspace: TaxWorkspace;
  lookups: TaxLookups;
  disabled: boolean;
  preview: TaxPreview | null;
  statement: TaxStatement | null;
  incomeLines: TaxIncomeLine[];
  adjustmentLines: TaxAdjustmentLine[];
  bookProfit: string;
}

export type TaxSectionEmits = {
  changed: [];
  dirty: [];
  navigate: [section: TaxSectionKey, field?: string];
  "update:incomeLines": [lines: TaxIncomeLine[]];
  "update:adjustmentLines": [lines: TaxAdjustmentLine[]];
  "update:bookProfit": [value: string];
  "load-statement": [];
};
