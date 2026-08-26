// Module 12 — Data Migration (Tally import) types. Mirrors app/schemas/tally.py.

export type MigrationSupport = "full" | "partial" | "reference" | "none";

export type MigrationImportStatus =
  | "Draft"
  | "Parsed"
  | "Mapped"
  | "Validated"
  | "Importing"
  | "Imported"
  | "Partially Imported"
  | "Failed"
  | "Rolled Back";

export type MigrationRecordStatus =
  | "Pending"
  | "Ready"
  | "Warning"
  | "Error"
  | "Imported"
  | "Skipped"
  | "Rolled Back";

export interface MigrationEntityCoverage {
  key: string;
  label: string;
  tally_tag: string;
  target: string;
  module: string;
  stage: number;
  support: MigrationSupport;
  notes: string;
}

export interface MigrationCatalogue {
  entities: MigrationEntityCoverage[];
  primary_groups: Record<
    string,
    { root_type: string; account_type: string | null; party_type: string | null }
  >;
  voucher_types: Record<string, string>;
  modules: string[];
}

export interface MigrationImportEntity {
  id: string;
  entity_key: string;
  label: string;
  target_doctype: string;
  module: string;
  stage: number;
  support: MigrationSupport;
  selected: boolean;
  total: number;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
}

export interface MigrationImportListItem {
  id: string;
  name: string;
  title: string | null;
  source_type: string;
  file_name: string | null;
  source_company_name: string | null;
  from_date: string | null;
  to_date: string | null;
  status: MigrationImportStatus;
  total_records: number;
  imported_count: number;
  skipped_count: number;
  error_count: number;
  creation: string;
  finished_at: string | null;
}

export interface MigrationImport extends MigrationImportListItem {
  company_id: string;
  file_size: number;
  /** "Tally" | "Zoho Books" | "OptiERP Template" | "Custom" — null for older sessions. */
  source_app: string | null;
  /** Key of the workbook mapping that parsed it. Null for XML/CSV. */
  source_profile: string | null;
  opening_date: string | null;
  options: Record<string, unknown> | null;
  started_at: string | null;
  error_message: string | null;
  entities: MigrationImportEntity[];
}

export interface MigrationImportLog {
  id: string;
  creation: string;
  phase: string;
  entity_key: string | null;
  level: "info" | "warning" | "error";
  message: string;
  context: Record<string, unknown> | null;
}

export interface MigrationImportSummary {
  session: MigrationImport;
  entities: MigrationImportEntity[];
  logs: MigrationImportLog[];
  status_counts: Record<string, number>;
  unmapped_count: number;
  low_confidence_count: number;
}

export interface MigrationStagingMessage {
  level: "info" | "warning" | "error";
  message: string;
  field: string | null;
}

export interface MigrationStagingRecord {
  id: string;
  entity_key: string;
  sequence: number;
  source_guid: string | null;
  source_name: string | null;
  source_parent: string | null;
  source_voucher_type: string | null;
  voucher_number: string | null;
  posting_date: string | null;
  amount: string | null;
  target_doctype: string | null;
  target_id: string | null;
  target_name: string | null;
  status: MigrationRecordStatus;
  messages: MigrationStagingMessage[] | null;
}

export interface MigrationStagingList {
  items: MigrationStagingRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface MigrationMapping {
  id: string;
  entity_key: string;
  source_name: string;
  source_guid: string | null;
  source_parent: string | null;
  target_doctype: string;
  target_id: string | null;
  target_name: string | null;
  match_method: "guid" | "exact" | "normalised" | "fuzzy" | "created" | "manual" | "auto";
  confidence: number;
  is_locked: boolean;
  attributes: Record<string, unknown> | null;
  notes: string | null;
}

export interface MigrationMappingTarget {
  id: string;
  name: string;
  [key: string]: string;
}

export interface MigrationAutoMapResult {
  summary: Record<
    string,
    { matched: number; to_create: number; locked: number; low_confidence: number }
  >;
  session: MigrationImport;
}

/** A record in this file that this company already imported. */
export interface MigrationDuplicate {
  entity_key: string;
  voucher_number: string | null;
  posting_date: string | null;
  target_doctype: string;
  target_name: string | null;
  imported_by: string | null;
}

export interface MigrationValidateResult {
  planned: Record<string, number>;
  total_planned: number;
  unresolved: Record<string, string[]>;
  unresolved_count: number;
  blockers: string[];
  /** Not blockers — the run skips these. Shown so the count isn't a surprise. */
  duplicates: MigrationDuplicate[];
  duplicate_count: number;
  /** Imported here, then edited in Tally. The two copies disagree. */
  amendments: MigrationAmendment[];
  amendment_count: number;
  /** Cancelled/optional vouchers in the file — staged and reported, never posted. */
  not_posting_count: number;
  session: MigrationImport;
}

/** A voucher Tally has edited since we imported it (same GUID, higher ALTERID). */
export interface MigrationAmendment extends MigrationDuplicate {
  imported_alter_id: number | null;
  file_alter_id: number | null;
}

/** How far this company has synced one Tally company. */
export interface MigrationSyncCompany {
  source_company_guid: string | null;
  source_company_name: string | null;
  /** High-water ALTERID — export "changes above this" from Tally next time. */
  last_alter_id: number | null;
  documents_imported: number;
  last_imported_at: string | null;
  earliest_voucher_date: string | null;
  latest_voucher_date: string | null;
}

export interface MigrationSyncState {
  companies: MigrationSyncCompany[];
  total_documents_imported: number;
}

/** Progress of a run in flight. Polled while `is_running`. */
export interface MigrationImportProgress {
  id: string;
  status: MigrationImportStatus;
  is_running: boolean;
  total_records: number;
  processed: number;
  imported_count: number;
  skipped_count: number;
  error_count: number;
  percent: number;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  current_entity: string | null;
}

export interface MigrationRollbackResult {
  cancelled: number;
  failed: string[];
  session: MigrationImport;
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
export const SLUG_TO_MIGRATION_ENTITY: Record<string, { entity: string; module: string }> = {
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


// --- Spreadsheet mapping -------------------------------------------------------------
// A Tally XML export needs no mapping step: the format is the mapping. A
// spreadsheet does, because no file can tell you which of its sheets is the
// invoice list. These types back that step.

/** One field a sheet shape can read off a row. */
export interface MappingField {
  name: string;
  label: string;
  required: boolean;
  kind: "text" | "date" | "amount" | "qty" | "bool" | "choice";
}

/** One structural shape a sheet may be assigned. */
export interface MappingKind {
  key: string;
  label: string;
  notes: string;
  child_roles: string[];
  fields: MappingField[];
}

/** A line sheet hanging off a header sheet. */
export interface MappingChild {
  sheet: string;
  role: string;
  key: string;
  constants: Record<string, string>;
  columns: Record<string, string>;
}

/** What the current mapping claims one sheet is. */
export interface MappingAssignment {
  kind: string;
  entity: string | null;
  key: string | null;
  reason: string;
  constants: Record<string, string>;
  /** canonical field name -> the column header it was found in */
  columns: Record<string, string>;
  children: MappingChild[];
}

export interface MappingColumn {
  /** Folded header the mapping matches on. */
  header: string;
  /** Header as written in the file, for display. */
  label: string;
  samples: string[];
}

export interface MappingSheet {
  name: string;
  rows: number;
  header_row: number;
  columns: MappingColumn[];
  assigned: MappingAssignment | null;
  /** Set when this sheet is read as another sheet's line sheet. */
  parent: { sheet: string; role: string } | null;
  /** Required fields with no column — the sheet will not import until these are set. */
  missing: string[];
}

export interface MappingCandidate {
  key: string;
  label: string;
  app: string;
  saved: boolean;
  confidence: number;
  importable_sheets: number;
}

export interface MigrationMapping {
  source_type: string;
  source_app: string | null;
  profile: { key: string; label: string; app: string; notes: string; saved: boolean };
  candidates: MappingCandidate[];
  kinds: MappingKind[];
  child_fields: Record<string, MappingField[]>;
  entities: { key: string; label: string; target: string; module: string; stage: number }[];
  defaults: Record<string, string>;
  sheets: MappingSheet[];
  unreadable_sheets: Record<string, string>;
}

/** A saved, named workbook shape belonging to this company. */
export interface SavedSourceProfile {
  id: string;
  key: string;
  label: string;
  app: string;
  notes: string;
  saved: true;
  use_count: number;
}

export interface MigrationSourceCatalogue {
  profiles: {
    key: string;
    label: string;
    app: string;
    notes: string;
    saved: boolean;
    sheets: {
      sheet: string;
      kind: string;
      entity: string | null;
      reason: string;
      children: { sheet: string; role: string }[];
    }[];
  }[];
  kinds: MappingKind[];
  saved: SavedSourceProfile[];
  template_version: string;
}
