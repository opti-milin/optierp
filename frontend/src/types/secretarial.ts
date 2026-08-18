// Module 13 — Company Secretarial & Governance.
// "Entity" here is the company or LLP whose statutory record we keep — never the
// tenant Company, which is the account the data belongs to.

export type EntityKind = "company" | "llp";
export type TenantProfile = "practice" | "business";
export type FinancialAccess = "none" | "derived_only" | "reports_read" | "ledger_read";
export type RoleType =
  | "director"
  | "designated_partner"
  | "partner"
  | "kmp"
  | "auditor"
  | "secretary";

export interface SecretarialSettings {
  id: string;
  company_id: string;
  profile: TenantProfile;
  practice_name: string | null;
  practice_registration_no: string | null;
  default_financial_access: FinancialAccess;
  reminder_offsets: number[] | null;
}

export interface SecretarialEntity {
  id: string;
  entity_name: string;
  kind: EntityKind;
  entity_class: string;
  cin: string | null;
  llpin: string | null;
  status: string;
  is_listed: boolean;
  incorporated_on: string | null;
  linked_company_id: string | null;
}

export interface EntityDetail extends SecretarialEntity {
  company_id: string;
  pan: string | null;
  tan: string | null;
  gstin: string | null;
  fy_end_mmdd: string;
  registered_office: Record<string, unknown> | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  notes: string | null;
}

export interface Person {
  id: string;
  full_name: string;
  din: string | null;
  pan: string | null;
  email: string | null;
  mobile: string | null;
  kyc_status: string;
  is_disqualified: boolean;
  is_body_corporate: boolean;
}

export interface Appointment {
  id: string;
  entity_id: string;
  person_id: string;
  person_name: string | null;
  role_type: RoleType;
  designation: string | null;
  appointed_on: string;
  ceased_on: string | null;
  cessation_reason: string | null;
  is_signing: boolean;
  is_chairperson: boolean;
}

export interface PersonEntityLink {
  entity_id: string;
  entity_name: string;
  role_type: string;
  designation: string | null;
  appointed_on: string;
  ceased_on: string | null;
}

export interface ComplianceItem {
  id: string;
  entity_id: string;
  entity_name: string | null;
  rule_code: string;
  title: string;
  form_code: string | null;
  act_section: string | null;
  fy: string;
  due_on: string;
  status: string;
  assigned_to_user_id: string | null;
  completed_on: string | null;
  srn: string | null;
  filed_on: string | null;
  days_to_due: number | null;
}

export interface ComplianceRule {
  id: string;
  code: string;
  version: number;
  title: string;
  description: string | null;
  act: string | null;
  section: string | null;
  form_code: string | null;
  authority: string;
  basis: string;
  source_ref: string | null;
  review_status: string;
  reviewer_name: string | null;
  reviewed_on: string | null;
  is_active: boolean;
  is_system: boolean;
}

export interface GenerateCalendarResult {
  fy: string;
  entities_processed: number;
  rules_evaluated: number;
  items_created: number;
  items_refreshed: number;
  items_skipped_not_applicable: number;
  unpublished_rules_ignored: number;
}

export interface Engagement {
  id: string;
  client_company_id: string;
  firm_company_id: string;
  entity_id: string;
  entity_name: string | null;
  firm_name: string | null;
  client_name: string | null;
  secretarial_access: string;
  financial_access: FinancialAccess;
  include_banking: boolean;
  status: string;
  starts_on: string | null;
  ends_on: string | null;
  ended_reason: string | null;
  projected_roles: string[] | null;
  granted_user_ids: string[] | null;
}

export interface AccessSummary {
  secretarial: string;
  financial: string;
  banking: string;
  warnings: string[];
}

export interface PracticeClient {
  id: string;
  entity_id: string;
  owner_company_id: string;
  engagement_id: string | null;
  relationship_type: "own" | "managed" | "delegated";
  entity_name: string;
  entity_kind: string;
  registration_no: string | null;
  onboarding_state: string;
  billable: boolean;
  assigned_to_user_id: string | null;
  next_due_on: string | null;
  overdue_count: number;
  open_item_count: number;
  last_activity_at: string | null;
}

export interface RegisterMeta {
  slug: string;
  label: string;
}

export interface SecretarialWorkspaceStats {
  profile: TenantProfile;
  entity_count: number;
  cards: { label: string; value: number; format: string }[];
  chart_title: string;
  trend_format: string;
  trend: { label: string; value: number }[];
  currency: string;
}

// --- Phase 2: document engine -----------------------------------------------------

export interface ContentPack {
  id: string;
  code: string;
  version: number;
  title: string;
  event_type: string;
  applies_to_kinds: string[] | null;
  variables: { name: string; source?: string; required?: boolean; label?: string; type?: string }[] | null;
  compliance_meta: Record<string, unknown> | null;
  source_ref: string | null;
  review_status: string;
  reviewer_name: string | null;
  reviewed_on: string | null;
  is_system: boolean;
  fragments: Record<string, unknown> | null;
}

export interface SecretarialDocument {
  id: string;
  entity_id: string;
  title: string;
  document_type: string;
  fragment: string;
  pack_code: string | null;
  pack_version: number | null;
  status: string;
  document_date: string | null;
  version: number;
  is_current: boolean;
  change_summary: string | null;
  root_id: string | null;
  creation: string;
}

export interface DocumentDetail extends SecretarialDocument {
  resolved_blocks: Record<string, unknown>[] | null;
  generation_inputs: Record<string, unknown> | null;
  compliance_meta: Record<string, unknown> | null;
  source_doctype: string | null;
  source_id: string | null;
}

// --- Phase 3: meetings ------------------------------------------------------------

export type MeetingType = "board" | "agm" | "egm" | "committee" | "partners";

export interface Meeting {
  id: string;
  entity_id: string;
  meeting_type: string;
  serial_no: number | null;
  title: string | null;
  fy: string;
  scheduled_at: string;
  held_at: string | null;
  venue: string | null;
  status: string;
  notice_due_on: string | null;
  notice_sent_on: string | null;
  minutes_signed_on: string | null;
  minutes_entry_no: number | null;
  minutes_page_from: number | null;
  minutes_page_to: number | null;
  quorum_met: boolean | null;
  entity_name: string | null;
}

export interface AgendaItem {
  id: string;
  meeting_id: string;
  seq: number;
  title: string;
  body: string | null;
  resolution_text: string | null;
  resolution_kind: string | null;
  source: string;
  circular_id: string | null;
  is_passed: boolean | null;
}

export interface Attendance {
  id: string;
  person_id: string;
  person_name: string | null;
  status: string;
  joined_via: string | null;
  is_chairperson: boolean;
  remarks: string | null;
}

export interface CirculationRecipient {
  id: string;
  person_id: string;
  person_name: string | null;
  email: string | null;
  status: string;
  sent_at: string | null;
  viewed_at: string | null;
  acknowledged_at: string | null;
  resend_count: number;
}

export interface Circulation {
  id: string;
  meeting_id: string | null;
  entity_id: string;
  subject: string;
  message: string | null;
  document_ids: string[] | null;
  sent_at: string | null;
}

// --- Phase 3: circular resolutions ------------------------------------------------

export interface Circular {
  id: string;
  entity_id: string;
  title: string;
  reference_no: string | null;
  consent_rule: string;
  status: string;
  fy: string | null;
  circulated_at: string | null;
  expires_at: string | null;
  decided_at: string | null;
  ratified_meeting_id: string | null;
}

export interface CircularDetail extends Circular {
  description: string | null;
  resolution_text: string;
  eligibility_result: Record<string, unknown> | null;
}

export interface ConsentResponse {
  id: string;
  person_id: string;
  person_name: string | null;
  status: string;
  is_interested: boolean;
  sent_at: string | null;
  viewed_at: string | null;
  responded_at: string | null;
  comments: string | null;
}

export interface CircularTally {
  rule: string;
  entitled: number;
  interested_excluded: number;
  consented: number;
  declined: number;
  abstained: number;
  pending: number;
  needed: number;
  reached: boolean;
  impossible: boolean;
  expires_at: string | null;
  status: string;
}

export interface Eligibility {
  eligible: boolean;
  blocked_matters: { matter: string; reference: string; matched: string }[];
  message: string;
  overridden: boolean;
  override_reason: string | null;
}

// --- Phase 3: certified true copies -----------------------------------------------

export interface CtcSignatory {
  name: string;
  designation: string | null;
  din: string | null;
}

export interface Ctc {
  id: string;
  entity_id: string;
  issuance_no: number;
  passage_mode: string;
  resolution_text: string;
  passed_on: string | null;
  certified_on: string;
  place: string | null;
  issued_to: string | null;
  purpose: string | null;
  signatories: CtcSignatory[] | null;
  document_id: string | null;
  issued_at: string;
  supersedes_id: string | null;
  superseded_reason: string | null;
}

// --- Phase 4: filings -------------------------------------------------------------

export interface Filing {
  id: string;
  entity_id: string;
  compliance_item_id: string | null;
  form_code: string;
  fy: string | null;
  srn: string | null;
  filed_on: string | null;
  status: string;
  filing_fee: string | null;
  additional_fee: string | null;
  meeting_id: string | null;
  circular_id: string | null;
  document_id: string | null;
  challan_file_id: string | null;
  filed_by: string | null;
  notes: string | null;
}

export interface FinancialFacts {
  fy: string;
  source: string;
  computed_at: string | null;
  turnover: number | null;
  net_profit: number | null;
  net_worth: number | null;
  paid_up_capital: number | null;
  free_reserves: number | null;
  securities_premium: number | null;
  borrowings: number | null;
  deposits: number | null;
}

export interface ApplicabilityCheck {
  rule_code: string;
  title: string;
  verdict: "applies" | "not_applicable" | "unknown";
  reasons: string[];
}

// --- Director portal (no login) ---------------------------------------------------

export interface PortalDocument {
  id: string;
  title: string;
  document_type: string;
}

export interface PortalCirculation {
  entity_name: string;
  subject: string;
  message: string | null;
  recipient_name: string;
  status: string;
  sent_at: string | null;
  acknowledged_at: string | null;
  documents: PortalDocument[];
}

export interface PortalConsent {
  entity_name: string;
  title: string;
  reference_no: string | null;
  resolution_text: string;
  description: string | null;
  consent_rule: string;
  expires_at: string | null;
  recipient_name: string;
  status: string;
  responded_at: string | null;
  circular_status: string;
}
