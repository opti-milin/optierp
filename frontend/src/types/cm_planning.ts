/** Contribution Margin Planning types (pre-sales). */

export interface CmCostExplanation {
  driver_code?: string;
  driver_label?: string;
  parent_code?: string | null;
  cm_class?: string;
  method?: string;
  method_label?: string;
  basis?: string | null;
  basis_label?: string | null;
  basis_value?: string | null;
  basis_total?: string | null;
  pool_ref?: string | null;
  pool_label?: string | null;
  pool_amount?: string | null;
  rate_or_pct?: string | null;
  allocated_amount?: string;
  formula_display?: string;
  inputs?: Record<string, unknown>;
  status?: string;
  warning?: string | null;
  source?: string | null;
}

export interface CmPlanCost {
  id: string;
  idx: number;
  driver: string;
  cm_class: string;
  amount: string;
  source: string;
  explanation?: CmCostExplanation | null;
  parent_driver?: string | null;
  is_group?: boolean;
  cm_plan_item_id?: string | null;
}

export interface CmCostDriver {
  code: string;
  label: string;
  cm_class: string;
  is_group?: boolean;
  parent_code?: string | null;
  allocation_method?: string | null;
  allocation_basis?: string | null;
  method_params?: Record<string, unknown>;
  basis_params?: Record<string, unknown>;
  source?: string | null;
  scope?: string;
  sort_order?: number;
  enabled?: boolean;
  is_system?: boolean;
}

export interface CmCostStructure {
  id: string;
  name: string;
  template_id?: string | null;
  effective_from?: string | null;
  is_active: boolean;
  drivers: CmCostDriver[];
  allocations: Record<string, string>;
}

export interface CmPlanScenario {
  id: string;
  name: string;
  is_baseline: boolean;
  sort_order: number;
  overrides?: Record<string, unknown> | null;
  customer_id?: string | null;
  sales_partner_id?: string | null;
  shipping_rule_id?: string | null;
  revenue: string;
  material: string;
  labor: string;
  freight: string;
  commission: string;
  packaging: string;
  variable_cost: string;
  product_channel_fixed: string;
  segment_bu_fixed: string;
  corporate_overhead: string;
  cm1: string;
  cm2: string;
  cm3: string;
  operating_profit: string;
  cm1_pct: string | null;
  cm2_pct: string | null;
  cm3_pct: string | null;
  operating_profit_pct: string | null;
  min_selling_total: string | null;
  applied_at?: string | null;
  items?: {
    line_key: string;
    item_name: string;
    qty: string;
    selling_rate: string;
    selling_amount: string;
    cm1: string;
    min_selling_rate: string | null;
  }[];
  costs?: CmPlanCost[];
}

export interface CmPlanAllocations {
  product_channel_fixed_pct_of_revenue?: string;
  segment_bu_fixed_pct_of_revenue?: string;
  corporate_overhead_pct_of_revenue?: string;
  [key: string]: string | undefined;
}

export interface CmPlan {
  id: string;
  name: string;
  docstatus: number;
  quotation_id?: string | null;
  sales_order_id?: string | null;
  template_id?: string | null;
  target_cm1_pct: string;
  target_cm2_pct?: string | null;
  min_cm1_pct: string;
  submit_policy: string;
  allocations?: CmPlanAllocations | null;
  cost_structure_snapshot?: CmCostDriver[] | Record<string, unknown> | null;
  warnings?: string[] | Record<string, unknown> | null;
  scenarios: CmPlanScenario[];
}

export interface CmPlanningSettings {
  target_cm1_pct: string;
  target_cm2_pct?: string | null;
  min_cm1_pct: string;
  submit_policy: string;
  default_template?: string | null;
  max_scenarios_per_plan: number;
  allocations: CmPlanAllocations;
}

export interface CmPlanningTemplateInfo {
  id: string;
  label: string;
  description?: string | null;
  target_cm1_pct?: string | null;
}

export interface CmCompareColumn {
  scenario_id: string;
  name: string;
  is_baseline: boolean;
  revenue: string;
  variable_cost: string;
  cm1: string;
  cm2: string;
  cm3: string;
  operating_profit: string;
  cm1_pct: string | null;
  min_selling_total: string | null;
}

export interface CmCompareResponse {
  plan_id: string;
  columns: CmCompareColumn[];
}

export interface CmActualComparisonRow {
  field: string;
  label: string;
  estimated: string;
  actual: string;
  delta: string;
  delta_pct: string | null;
}

export interface CmActualComparison {
  plan_id: string;
  scenario_id: string;
  scenario_name: string;
  from_date: string;
  to_date: string;
  rows: CmActualComparisonRow[];
  warnings: string[];
}
