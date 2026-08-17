// Module 12 — Data Migration (Tally import) types. Mirrors app/schemas/tally.py.

export type TallySupport = "full" | "partial" | "reference" | "none";

export type TallyImportStatus =
  | "Draft"
  | "Parsed"
  | "Mapped"
  | "Validated"
  | "Importing"
  | "Imported"
  | "Partially Imported"
  | "Failed"
  | "Rolled Back";

export type TallyRecordStatus =
  | "Pending"
  | "Ready"
  | "Warning"
  | "Error"
  | "Imported"
  | "Skipped"
  | "Rolled Back";

export interface TallyEntityCoverage {
  key: string;
  label: string;
  tally_tag: string;
  target: string;
  module: string;
  stage: number;
  support: TallySupport;
  notes: string;
}

export interface TallyCatalogue {
  entities: TallyEntityCoverage[];
  primary_groups: Record<
    string,
    { root_type: string; account_type: string | null; party_type: string | null }
  >;
  voucher_types: Record<string, string>;
  modules: string[];
}

export interface TallyImportEntity {
  id: string;
  entity_key: string;
  label: string;
  target_doctype: string;
  module: string;
  stage: number;
  support: TallySupport;
  selected: boolean;
  total: number;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
}

export interface TallyImportListItem {
  id: string;
  name: string;
  title: string | null;
  source_type: string;
  file_name: string | null;
  tally_company_name: string | null;
  from_date: string | null;
  to_date: string | null;
  status: TallyImportStatus;
  total_records: number;
  imported_count: number;
  skipped_count: number;
  error_count: number;
  creation: string;
  finished_at: string | null;
}

export interface TallyImport extends TallyImportListItem {
  company_id: string;
  file_size: number;
  opening_date: string | null;
  options: Record<string, unknown> | null;
  started_at: string | null;
  error_message: string | null;
  entities: TallyImportEntity[];
}

export interface TallyImportLog {
  id: string;
  creation: string;
  phase: string;
  entity_key: string | null;
  level: "info" | "warning" | "error";
  message: string;
  context: Record<string, unknown> | null;
}

export interface TallyImportSummary {
  session: TallyImport;
  entities: TallyImportEntity[];
  logs: TallyImportLog[];
  status_counts: Record<string, number>;
  unmapped_count: number;
  low_confidence_count: number;
}

export interface TallyStagingMessage {
  level: "info" | "warning" | "error";
  message: string;
  field: string | null;
}

export interface TallyStagingRecord {
  id: string;
  entity_key: string;
  sequence: number;
  tally_guid: string | null;
  tally_name: string | null;
  tally_parent: string | null;
  tally_voucher_type: string | null;
  voucher_number: string | null;
  posting_date: string | null;
  amount: string | null;
  target_doctype: string | null;
  target_id: string | null;
  target_name: string | null;
  status: TallyRecordStatus;
  messages: TallyStagingMessage[] | null;
}

export interface TallyStagingList {
  items: TallyStagingRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface TallyMapping {
  id: string;
  entity_key: string;
  tally_name: string;
  tally_guid: string | null;
  tally_parent: string | null;
  target_doctype: string;
  target_id: string | null;
  target_name: string | null;
  match_method: "guid" | "exact" | "normalised" | "fuzzy" | "created" | "manual" | "auto";
  confidence: number;
  is_locked: boolean;
  attributes: Record<string, unknown> | null;
  notes: string | null;
}

export interface TallyMappingTarget {
  id: string;
  name: string;
  [key: string]: string;
}

export interface TallyAutoMapResult {
  summary: Record<
    string,
    { matched: number; to_create: number; locked: number; low_confidence: number }
  >;
  session: TallyImport;
}

export interface TallyValidateResult {
  planned: Record<string, number>;
  total_planned: number;
  unresolved: Record<string, string[]>;
  unresolved_count: number;
  blockers: string[];
  session: TallyImport;
}

export interface TallyRollbackResult {
  cancelled: number;
  failed: string[];
  session: TallyImport;
}

/** Shape every module workspace endpoint returns (see ModuleWorkspace.vue). */
export interface WorkspaceStats {
  currency: string;
  chart_title: string;
  trend_format?: "int" | "currency";
  cards: { label: string; value: number; format: "int" | "currency" }[];
  trend: { label: string; value: number }[];
}

/**
 * Metadata-engine slug (`/m/<slug>`) -> the Tally entity that fills it.
 * Drives the "Import from Tally" button on every generic master list, so a new
 * master screen gets the option without touching its view.
 */
export const SLUG_TO_TALLY_ENTITY: Record<string, { entity: string; module: string }> = {
  customer: { entity: "customer", module: "selling" },
  supplier: { entity: "supplier", module: "buying" },
  "cost-center": { entity: "cost_centre", module: "accounting" },
  "item-group": { entity: "stock_group", module: "stock" },
  address: { entity: "customer", module: "selling" },
  contact: { entity: "customer", module: "selling" },
};

/** Which catalogue entities each module's "Import from Tally" button pre-selects. */
export const MODULE_ENTITY_HINTS: Record<string, string[]> = {
  accounting: [
    "group",
    "ledger",
    "cost_centre",
    "opening_ledger",
    "voucher_sales",
    "voucher_purchase",
    "voucher_credit_note",
    "voucher_debit_note",
    "voucher_journal",
    "voucher_receipt",
    "voucher_payment",
    "voucher_contra",
  ],
  selling: ["customer", "voucher_sales", "voucher_credit_note", "voucher_sales_order"],
  buying: ["supplier", "voucher_purchase", "voucher_debit_note", "voucher_purchase_order"],
  stock: [
    "unit",
    "stock_group",
    "godown",
    "stock_item",
    "price_list",
    "opening_stock",
    "voucher_delivery_note",
    "voucher_receipt_note",
    "voucher_stock_journal",
    "voucher_physical_stock",
  ],
  manufacturing: ["stock_item", "voucher_manufacturing_journal", "voucher_stock_journal"],
};
