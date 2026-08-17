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
