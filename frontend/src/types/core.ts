// TypeScript interfaces mirroring the backend Pydantic schemas (Module 01).

export interface ListResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ErrorEnvelope {
  detail: string;
  code: string;
  field: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user_id: string;
  email: string;
  full_name: string;
  company_id: string | null;
  roles: string[];
}

export interface DocumentMeta {
  id: string;
  creation: string;
  modified: string;
  docstatus: number;
  owner: string | null;
  modified_by: string | null;
}

export interface Company extends DocumentMeta {
  company_name: string;
  abbr: string;
  country_code: string | null;
  default_currency: string;
  tax_id: string | null;
  pan: string | null;
  tan: string | null;
  domain: string | null;
  is_group: boolean;
  parent_company_id: string | null;
  chart_of_accounts_template: string | null;
  default_receivable_account_id: string | null;
  default_payable_account_id: string | null;
  default_cost_center_id: string | null;
  enabled: boolean;
}

export interface CompanyListItem {
  id: string;
  company_name: string;
  abbr: string;
  default_currency: string;
  country_code: string | null;
  enabled: boolean;
}

export interface User extends DocumentMeta {
  email: string;
  first_name: string;
  last_name: string | null;
  full_name: string;
  is_active: boolean;
  language: string;
  time_zone: string | null;
  default_company_id: string | null;
  role_names: string[];
}

export interface UserListItem {
  id: string;
  email: string;
  first_name: string;
  last_name: string | null;
  is_active: boolean;
}

export interface Role extends DocumentMeta {
  name: string;
  description: string | null;
  is_system: boolean;
  disabled: boolean;
}

export interface Currency extends DocumentMeta {
  code: string;
  currency_name: string;
  symbol: string | null;
  enabled: boolean;
}

export interface UOM extends DocumentMeta {
  uom_name: string;
  must_be_whole_number: boolean;
  enabled: boolean;
}

export interface AccountNode {
  id: string;
  account_name: string;
  account_number: string | null;
  parent_account_id: string | null;
  root_type: string;
  report_type: string;
  account_type: string | null;
  cm_class?: string | null;
  is_group: boolean;
  account_currency: string | null;
  freeze_account?: boolean;
  disabled?: boolean;
  path: string;
}

export interface BrandConfig {
  product_name: string;
  tagline: string;
  logo_url: string;
  favicon_url: string;
  primary_color: string;
  secondary_color: string;
  support_email: string;
  docs_url: string;
}
