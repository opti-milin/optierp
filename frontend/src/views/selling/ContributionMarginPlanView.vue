<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type {
  CmActualComparison,
  CmCompareResponse,
  CmCostDriver,
  CmPlan,
  CmPlanCost,
  CmCostExplanation,
} from "@/types/cm_planning";
import { formatCurrency } from "@/utils/format";

const route = useRoute();
const router = useRouter();
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const busy = ref(false);
const plan = ref<CmPlan | null>(null);
const compare = ref<CmCompareResponse | null>(null);
const actual = ref<CmActualComparison | null>(null);
const selectedScenarioId = ref<string | null>(null);
const scenarioName = ref("");
const priceOverride = ref("");
const freightOverride = ref("");
const materialFactor = ref("");
const qtyOverride = ref("");
const whyCost = ref<CmPlanCost | null>(null);
const costRules = ref<CmCostDriver[]>([]);

const policy = reactive({
  target_cm1_pct: "",
  target_cm2_pct: "",
  min_cm1_pct: "",
  submit_policy: "warn",
});

const actualFrom = ref("");
const actualTo = ref("");

const METHOD_OPTIONS = [
  { value: "percentage", label: "% of a basis" },
  { value: "fixed_amount", label: "Fixed amount" },
  { value: "rate_times_basis", label: "Rate × basis" },
  { value: "from_source", label: "Auto from BOM / shipping" },
] as const;

const BASIS_OPTIONS = [
  { value: "revenue", label: "Revenue" },
  { value: "quantity", label: "Quantity" },
  { value: "transaction_count", label: "Transaction count" },
  { value: "labor_hours", label: "Labor hours" },
  { value: "machine_hours", label: "Machine hours" },
] as const;

/** Margin waterfall layers — rules are shown as a tree under these folders. */
const RULE_GROUPS = [
  {
    id: "cm1",
    cm_class: "variable_cost",
    group_code: "variable_cost",
    group_label: "Variable Cost",
    title: "CM1 — Variable costs",
    hint: "Costs deducted from revenue to reach CM1 (BOM, freight, commission…).",
    accent: "border-l-emerald-500",
  },
  {
    id: "cm2",
    cm_class: "product_channel_fixed",
    group_code: "product_channel_group",
    group_label: "Product / Channel",
    title: "CM2 — Product / channel",
    hint: "Deducted from CM1 to reach CM2 (channel, SKU, product-line fixed).",
    accent: "border-l-sky-500",
  },
  {
    id: "cm3",
    cm_class: "segment_bu_fixed",
    group_code: "segment_bu_group",
    group_label: "Segment / BU",
    title: "CM3 — Segment / BU",
    hint: "Deducted from CM2 to reach CM3 (business-unit / segment fixed).",
    accent: "border-l-violet-500",
  },
  {
    id: "corp",
    cm_class: "corporate_overhead",
    group_code: "corporate_overhead",
    group_label: "Corporate Overhead",
    title: "After CM3 — Corporate",
    hint: "Deducted from CM3 to reach operating profit (HQ / corporate OH).",
    accent: "border-l-amber-500",
  },
] as const;

type RuleGroup = (typeof RULE_GROUPS)[number];

const baseline = computed(() => plan.value?.scenarios.find((s) => s.is_baseline) ?? null);
const selected = computed(() => {
  if (!plan.value || !selectedScenarioId.value) return baseline.value;
  return plan.value.scenarios.find((s) => s.id === selectedScenarioId.value) ?? baseline.value;
});
const canCompare = computed(() => (plan.value?.scenarios.length ?? 0) >= 2);
const isDraft = computed(() => plan.value?.docstatus === 0);

interface WaterfallLeaf {
  key: string;
  label: string;
  detail: string;
  amount: number;
  cost: CmPlanCost | null;
}

interface WaterfallSection {
  key: string;
  title: string;
  groupLabel: string;
  groupTotal: string;
  marginLabel: string;
  marginAmount: string;
  marginPct: string | null;
  leaves: WaterfallLeaf[];
}

/** Costs rolled into the CM1 → CM2 → CM3 → operating-profit waterfall, children under parents. */
const waterfall = computed<WaterfallSection[]>(() => {
  const s = selected.value;
  if (!s) return [];

  const byClass = new Map<string, Map<string, WaterfallLeaf>>();
  for (const c of s.costs ?? []) {
    if (c.is_group) continue;
    const cls = c.cm_class;
    if (!byClass.has(cls)) byClass.set(cls, new Map());
    const bucket = byClass.get(cls)!;
    const existing = bucket.get(c.driver);
    if (existing) {
      existing.amount += Number(c.amount ?? 0);
      continue;
    }
    const expl = c.explanation;
    const detail = expl?.basis_label
      ? `${expl.method_label ?? c.source} · ${expl.basis_label}`
      : expl?.method_label ?? c.source ?? "";
    bucket.set(c.driver, {
      key: `${cls}:${c.driver}`,
      label: expl?.driver_label || c.driver,
      detail,
      amount: Number(c.amount ?? 0),
      cost: c,
    });
  }

  const sections: Array<Omit<WaterfallSection, "leaves">> = [
    {
      key: "cm1",
      title: "CM1 — after variable costs",
      groupLabel: "Variable costs",
      groupTotal: s.variable_cost,
      marginLabel: "CM1",
      marginAmount: s.cm1,
      marginPct: s.cm1_pct,
    },
    {
      key: "cm2",
      title: "CM2 — after product / channel",
      groupLabel: "Product / channel fixed",
      groupTotal: s.product_channel_fixed,
      marginLabel: "CM2",
      marginAmount: s.cm2,
      marginPct: s.cm2_pct,
    },
    {
      key: "cm3",
      title: "CM3 — after segment / BU",
      groupLabel: "Segment / BU fixed",
      groupTotal: s.segment_bu_fixed,
      marginLabel: "CM3",
      marginAmount: s.cm3,
      marginPct: s.cm3_pct,
    },
    {
      key: "op",
      title: "Operating profit — after corporate",
      groupLabel: "Corporate overhead",
      groupTotal: s.corporate_overhead,
      marginLabel: "Operating profit",
      marginAmount: s.operating_profit,
      marginPct: s.operating_profit_pct,
    },
  ];

  return sections.map((sec, i) => ({
    ...sec,
    leaves: [...(byClass.get(RULE_GROUPS[i].cm_class)?.values() ?? [])].sort(
      (a, b) => b.amount - a.amount,
    ),
  }));
});

function leavesForGroup(g: RuleGroup): CmCostDriver[] {
  return costRules.value.filter(
    (d) => !d.is_group && d.enabled !== false && d.cm_class === g.cm_class,
  );
}

/** One-line human summary of a rule, e.g. "4% of Revenue" or "₹800 fixed". */
function ruleSummary(d: CmCostDriver): string {
  const basis = BASIS_OPTIONS.find((b) => b.value === d.allocation_basis)?.label ?? "";
  switch (d.allocation_method) {
    case "percentage":
      return `${rulePct(d) || 0}% of ${basis || "revenue"}`;
    case "fixed_amount":
      return `₹${ruleAmount(d) || 0} fixed`;
    case "rate_times_basis":
      return `₹${ruleRate(d) || 0} × ${basis || "quantity"}`;
    case "from_source":
      return `Auto — ${sourceLabel(d.source)}`;
    default:
      return d.allocation_method ?? "";
  }
}

function explainLabel(expl: CmCostExplanation | null | undefined): string {
  return expl?.formula_display ?? "";
}

function sourceLabel(source: string | null | undefined): string {
  const map: Record<string, string> = {
    bom_material: "BOM material",
    bom_operating: "BOM labor / ops",
    shipping_rule: "Shipping rule",
    sales_partner: "Sales partner",
    cost_rate: "Cost rate master",
  };
  return source ? map[source] ?? source : "";
}

/** Deep copy that tolerates Vue reactive proxies (structuredClone rejects them). */
function cloneDrivers(rows: CmCostDriver[]): CmCostDriver[] {
  return JSON.parse(JSON.stringify(rows)) as CmCostDriver[];
}

function snapshotDrivers(p: CmPlan): CmCostDriver[] {
  const s = p.cost_structure_snapshot;
  if (Array.isArray(s)) return cloneDrivers(s as CmCostDriver[]);
  if (s && typeof s === "object" && Array.isArray((s as { drivers?: unknown }).drivers)) {
    return cloneDrivers((s as { drivers: CmCostDriver[] }).drivers);
  }
  return [];
}

function rulePct(d: CmCostDriver): string {
  const pct = d.method_params?.pct;
  return pct != null ? String(pct) : "";
}

function ruleAmount(d: CmCostDriver): string {
  const amt = d.method_params?.amount ?? d.method_params?.fixed_amount;
  return amt != null ? String(amt) : "";
}

function ruleRate(d: CmCostDriver): string {
  const rate = d.method_params?.rate;
  return rate != null ? String(rate) : "";
}

function setRulePct(d: CmCostDriver, value: string): void {
  d.method_params = { ...(d.method_params || {}), pct: value };
}

function setRuleAmount(d: CmCostDriver, value: string): void {
  d.method_params = { ...(d.method_params || {}), amount: value };
}

function setRuleRate(d: CmCostDriver, value: string): void {
  d.method_params = { ...(d.method_params || {}), rate: value };
}

function onMethodChange(d: CmCostDriver): void {
  if (d.allocation_method === "percentage") {
    d.allocation_basis = d.allocation_basis || "revenue";
    if (d.method_params?.pct == null) setRulePct(d, "1");
  } else if (d.allocation_method === "fixed_amount") {
    d.allocation_basis = null;
    if (d.method_params?.amount == null) setRuleAmount(d, "0");
  } else if (d.allocation_method === "rate_times_basis") {
    d.allocation_basis = d.allocation_basis || "quantity";
    if (d.method_params?.rate == null) setRuleRate(d, "0");
  }
}

function ensureGroupFolder(g: RuleGroup): void {
  if (costRules.value.some((d) => d.code === g.group_code && d.is_group)) return;
  costRules.value = [
    ...costRules.value,
    {
      code: g.group_code,
      label: g.group_label,
      cm_class: g.cm_class,
      is_group: true,
      parent_code: null,
      allocation_method: null,
      allocation_basis: null,
      method_params: {},
      basis_params: {},
      scope: "header",
      sort_order: RULE_GROUPS.findIndex((x) => x.id === g.id),
      enabled: true,
      is_system: true,
    },
  ];
}

function addCostRule(g: RuleGroup): void {
  ensureGroupFolder(g);
  const n = costRules.value.length + 1;
  const defaults: Record<string, { label: string; pct?: string; amount?: string }> = {
    variable_cost: { label: "Extra variable cost", pct: "1" },
    product_channel_fixed: { label: "Channel / product fixed", pct: "3" },
    segment_bu_fixed: { label: "Segment / BU fixed", pct: "2" },
    corporate_overhead: { label: "Corporate overhead", pct: "5" },
  };
  const def = defaults[g.cm_class] ?? { label: "New cost rule", pct: "1" };
  costRules.value = [
    ...costRules.value,
    {
      code: `custom_${g.id}_${Date.now().toString(36)}`,
      label: def.label,
      cm_class: g.cm_class,
      is_group: false,
      parent_code: g.group_code,
      allocation_method: "percentage",
      allocation_basis: "revenue",
      method_params: { pct: def.pct ?? "1" },
      basis_params: {},
      scope: "header",
      sort_order: n,
      enabled: true,
      is_system: false,
    },
  ];
}

function removeCostRule(code: string): void {
  const row = costRules.value.find((d) => d.code === code);
  if (!row || row.is_system || row.allocation_method === "from_source") return;
  costRules.value = costRules.value.filter((d) => d.code !== code);
}

function canRemove(d: CmCostDriver): boolean {
  return !d.is_system && d.allocation_method !== "from_source";
}

function syncPolicyFromPlan(p: CmPlan): void {
  policy.target_cm1_pct = String(p.target_cm1_pct ?? "");
  policy.target_cm2_pct = p.target_cm2_pct != null ? String(p.target_cm2_pct) : "";
  policy.min_cm1_pct = String(p.min_cm1_pct ?? "");
  policy.submit_policy = p.submit_policy ?? "warn";
  costRules.value = snapshotDrivers(p);
}

function defaultActualDates(): void {
  const to = new Date();
  const from = new Date(to.getFullYear(), to.getMonth(), 1);
  actualTo.value = to.toISOString().slice(0, 10);
  actualFrom.value = from.toISOString().slice(0, 10);
}

async function load(id: string): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    plan.value = (await api.get<CmPlan>(`/cm-plans/${id}`)).data;
    selectedScenarioId.value = plan.value.scenarios.find((s) => s.is_baseline)?.id ?? null;
    syncPolicyFromPlan(plan.value);
    if (!costRules.value.length) {
      try {
        const struct = (await api.get<{ drivers: CmCostDriver[] }>("/cm-planning/cost-structure")).data;
        if (struct.drivers?.length) costRules.value = cloneDrivers(struct.drivers);
      } catch {
        /* keep empty — user can Add rule */
      }
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function savePolicy(): Promise<void> {
  if (!plan.value || !isDraft.value) return;
  busy.value = true;
  error.value = null;
  try {
    plan.value = (
      await api.put<CmPlan>(`/cm-plans/${plan.value.id}`, {
        target_cm1_pct: Number(policy.target_cm1_pct),
        target_cm2_pct: policy.target_cm2_pct === "" ? null : Number(policy.target_cm2_pct),
        min_cm1_pct: Number(policy.min_cm1_pct),
        submit_policy: policy.submit_policy,
        cost_drivers: costRules.value,
        recompute: true,
      })
    ).data;
    syncPolicyFromPlan(plan.value);
    notice.value = "Cost rules saved — waterfall recomputed.";
    setTimeout(() => (notice.value = null), 3000);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function runActualComparison(): Promise<void> {
  if (!plan.value || !actualFrom.value || !actualTo.value) return;
  busy.value = true;
  error.value = null;
  try {
    const params = new URLSearchParams({
      from_date: actualFrom.value,
      to_date: actualTo.value,
    });
    if (selectedScenarioId.value) params.set("scenario_id", selectedScenarioId.value);
    actual.value = (
      await api.get<CmActualComparison>(
        `/cm-plans/${plan.value.id}/actual-comparison?${params.toString()}`,
      )
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function runCompare(): Promise<void> {
  if (!plan.value) return;
  const ids = plan.value.scenarios.slice(0, 5).map((s) => s.id);
  if (ids.length < 2) {
    error.value = {
      detail: "Add at least one more scenario before comparing.",
      code: "ERR_VALIDATION",
      field: "scenario_ids",
    } as ErrorEnvelope;
    return;
  }
  error.value = null;
  compare.value = (
    await api.post<CmCompareResponse>(`/cm-plans/${plan.value.id}/compare`, { scenario_ids: ids })
  ).data;
}

async function exportCsv(): Promise<void> {
  if (!plan.value || !canCompare.value) return;
  busy.value = true;
  error.value = null;
  try {
    const ids = plan.value.scenarios.slice(0, 5).map((s) => s.id);
    const res = await api.post<string>(
      `/cm-plans/${plan.value.id}/compare.csv`,
      { scenario_ids: ids },
      { responseType: "blob" },
    );
    const blob = new Blob([res.data], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${plan.value.name}-compare.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function addScenario(): Promise<void> {
  if (!plan.value || !baseline.value) return;
  busy.value = true;
  error.value = null;
  try {
    const overrides: Record<string, unknown> = {};
    if (materialFactor.value.trim() !== "") {
      const f = Number(materialFactor.value);
      if (Number.isFinite(f) && f > 0) overrides.material_cost_factor = f;
    }
    if (freightOverride.value.trim() !== "") {
      overrides.freight_amount = Number(freightOverride.value);
    }
    if (priceOverride.value.trim() !== "") {
      overrides.selling_rate = Number(priceOverride.value);
    }
    if (qtyOverride.value.trim() !== "") {
      overrides.qty = Number(qtyOverride.value);
    }
    plan.value = (
      await api.post<CmPlan>(`/cm-plans/${plan.value.id}/scenarios`, {
        name: scenarioName.value || "Scenario",
        clone_from_scenario_id: baseline.value.id,
        overrides,
      })
    ).data;
    const neu = plan.value.scenarios
      .filter((s) => !s.is_baseline)
      .sort((a, b) => b.sort_order - a.sort_order)[0];
    selectedScenarioId.value = neu?.id ?? selectedScenarioId.value;
    scenarioName.value = "";
    priceOverride.value = "";
    freightOverride.value = "";
    materialFactor.value = "";
    qtyOverride.value = "";
    await runCompare();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function deleteSelectedScenario(): Promise<void> {
  if (!plan.value || !selectedScenarioId.value || !selected.value || selected.value.is_baseline) {
    return;
  }
  busy.value = true;
  error.value = null;
  try {
    plan.value = (
      await api.delete<CmPlan>(`/cm-plans/${plan.value.id}/scenarios/${selectedScenarioId.value}`)
    ).data;
    selectedScenarioId.value = plan.value.scenarios.find((s) => s.is_baseline)?.id ?? null;
    compare.value = null;
    if (canCompare.value) await runCompare();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function applyToQuotation(): Promise<void> {
  if (!plan.value || !selectedScenarioId.value) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    plan.value = (
      await api.post<CmPlan>(
        `/cm-plans/${plan.value.id}/scenarios/${selectedScenarioId.value}/apply-to-quotation`,
      )
    ).data;
    const qid = plan.value.quotation_id;
    notice.value = qid
      ? "Rates applied to Quotation (totals recalculated)."
      : "Scenario applied.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  defaultActualDates();
  const id = route.params.id;
  if (typeof id === "string") await load(id);
});

function money(v: string | null | undefined): string {
  if (v == null) return "—";
  return formatCurrency(v);
}
</script>

<template>
  <div class="mx-auto max-w-6xl p-6">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div>
        <button class="text-sm text-gray-500 hover:text-gray-800" @click="router.push('/cm-plans')">← Plans</button>
        <h1 class="text-xl font-semibold text-gray-900">
          {{ plan?.name ?? "Contribution Margin Plan" }}
        </h1>
        <p v-if="plan" class="text-xs text-gray-500">
          Policy {{ plan.submit_policy }} · min CM1 {{ plan.min_cm1_pct }}% · target {{ plan.target_cm1_pct }}%
          ·
          <RouterLink to="/cm-planning-settings" class="text-blue-600 hover:underline">Company settings</RouterLink>
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn-secondary" :disabled="busy || !canCompare" @click="runCompare">Compare</button>
        <button class="btn-secondary" :disabled="busy || !canCompare" @click="exportCsv">Export CSV</button>
        <button
          v-if="selected && !selected.is_baseline"
          class="btn-secondary"
          :disabled="busy"
          @click="deleteSelectedScenario"
        >
          Delete scenario
        </button>
        <button
          v-if="plan?.quotation_id"
          class="btn-secondary"
          :disabled="busy || !selectedScenarioId"
          @click="applyToQuotation"
        >
          Apply to Quotation
        </button>
      </div>
    </div>

    <div v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error.detail }}
    </div>
    <div
      v-if="notice"
      class="mb-4 rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800"
    >
      {{ notice }}
      <RouterLink
        v-if="plan?.quotation_id"
        class="ml-2 font-medium text-blue-700 hover:underline"
        :to="`/quotations/${plan.quotation_id}`"
      >
        Open Quotation →
      </RouterLink>
    </div>

    <div v-if="plan && isDraft" class="mb-6 space-y-4">
      <div class="rounded border border-gray-200 bg-white p-4">
        <h2 class="mb-1 text-sm font-semibold">Margin targets (this plan)</h2>
        <p class="mb-3 text-xs text-gray-500">
          Gates and targets apply when you submit the linked Quotation / Sales Order.
        </p>
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label class="form-label">Target CM1 %</label>
            <input v-model="policy.target_cm1_pct" type="number" step="0.01" class="form-input" />
          </div>
          <div>
            <label class="form-label">Min CM1 %</label>
            <input v-model="policy.min_cm1_pct" type="number" step="0.01" class="form-input" />
          </div>
          <div>
            <label class="form-label">Submit policy</label>
            <select v-model="policy.submit_policy" class="form-input">
              <option value="off">Off</option>
              <option value="warn">Warn</option>
              <option value="block">Block</option>
            </select>
          </div>
          <div>
            <label class="form-label">Target CM2 % (optional)</label>
            <input v-model="policy.target_cm2_pct" type="number" step="0.01" class="form-input" />
          </div>
        </div>
      </div>

      <div class="rounded border border-gray-200 bg-white p-4">
        <div class="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 class="text-sm font-semibold">Cost rules (this plan)</h2>
            <p class="mt-0.5 text-xs text-gray-500">
              Grouped by margin layer. Auto rules pull from BOM / shipping; add % or fixed rules
              under each folder to model the deal.
            </p>
          </div>
          <button class="btn-primary text-xs" :disabled="busy" @click="savePolicy">
            Save rules &amp; recompute
          </button>
        </div>

        <div class="mt-4 space-y-4">
          <section
            v-for="g in RULE_GROUPS"
            :key="g.id"
            class="rounded-lg border border-gray-200 border-l-4 bg-white"
            :class="g.accent"
          >
            <div class="flex flex-wrap items-start justify-between gap-2 border-b border-gray-100 px-3 py-2.5">
              <div>
                <h3 class="text-sm font-semibold text-gray-900">
                  {{ g.title }}
                  <span class="ml-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-normal text-gray-600">
                    {{ leavesForGroup(g).length }}
                  </span>
                </h3>
                <p class="text-xs text-gray-500">{{ g.hint }}</p>
              </div>
              <button
                type="button"
                class="btn-secondary text-xs"
                :disabled="busy"
                @click="addCostRule(g)"
              >
                + Add rule
              </button>
            </div>

            <ul v-if="leavesForGroup(g).length" class="divide-y divide-gray-100">
              <li
                v-for="d in leavesForGroup(g)"
                :key="d.code"
                class="px-3 py-3"
              >
                <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div class="flex min-w-0 flex-1 items-center gap-2">
                    <span class="text-gray-300">↳</span>
                    <input
                      v-model="d.label"
                      class="form-input max-w-xs font-medium"
                      :disabled="d.allocation_method === 'from_source'"
                      placeholder="Rule name"
                    />
                    <span class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                      {{ ruleSummary(d) }}
                    </span>
                  </div>
                  <button
                    v-if="canRemove(d)"
                    type="button"
                    class="shrink-0 text-xs text-red-600 hover:underline"
                    @click="removeCostRule(d.code)"
                  >
                    Remove
                  </button>
                </div>

                <div
                  v-if="d.allocation_method === 'from_source'"
                  class="ml-6 rounded border border-emerald-100 bg-emerald-50/70 px-3 py-2 text-sm text-emerald-900"
                >
                  Auto from {{ sourceLabel(d.source) || "source" }}
                  <span class="text-emerald-700/80"> — value comes from the document</span>
                </div>

                <div v-else class="ml-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <div>
                    <label class="form-label">How calculated</label>
                    <select
                      v-model="d.allocation_method"
                      class="form-input"
                      @change="onMethodChange(d)"
                    >
                      <option
                        v-for="o in METHOD_OPTIONS.filter((m) => m.value !== 'from_source')"
                        :key="o.value"
                        :value="o.value"
                      >
                        {{ o.label }}
                      </option>
                    </select>
                  </div>
                  <div v-if="d.allocation_method === 'percentage'">
                    <label class="form-label">Percent</label>
                    <div class="flex items-center gap-2">
                      <input
                        :value="rulePct(d)"
                        type="number"
                        step="0.01"
                        class="form-input"
                        @input="setRulePct(d, ($event.target as HTMLInputElement).value)"
                      />
                      <span class="text-sm text-gray-500">%</span>
                    </div>
                  </div>
                  <div v-else-if="d.allocation_method === 'fixed_amount'">
                    <label class="form-label">Amount (₹)</label>
                    <input
                      :value="ruleAmount(d)"
                      type="number"
                      step="0.01"
                      class="form-input"
                      @input="setRuleAmount(d, ($event.target as HTMLInputElement).value)"
                    />
                  </div>
                  <div v-else-if="d.allocation_method === 'rate_times_basis'">
                    <label class="form-label">Rate (₹)</label>
                    <input
                      :value="ruleRate(d)"
                      type="number"
                      step="0.01"
                      class="form-input"
                      @input="setRuleRate(d, ($event.target as HTMLInputElement).value)"
                    />
                  </div>
                  <div
                    v-if="
                      d.allocation_method === 'percentage' ||
                      d.allocation_method === 'rate_times_basis'
                    "
                  >
                    <label class="form-label">Of / basis</label>
                    <select v-model="d.allocation_basis" class="form-input">
                      <option v-for="o in BASIS_OPTIONS" :key="o.value" :value="o.value">
                        {{ o.label }}
                      </option>
                    </select>
                  </div>
                </div>
              </li>
            </ul>

            <p
              v-else
              class="px-3 py-4 text-center text-xs text-gray-400"
            >
              No rules in this layer yet — click Add above.
            </p>
          </section>
        </div>
      </div>
    </div>

    <div v-if="plan" class="mb-4 flex flex-wrap gap-2">
      <button
        v-for="s in plan.scenarios"
        :key="s.id"
        class="rounded border px-3 py-1 text-sm"
        :class="selectedScenarioId === s.id ? 'border-primary bg-primary/10' : 'border-gray-200'"
        @click="selectedScenarioId = s.id"
      >
        {{ s.name }}{{ s.is_baseline ? " ★" : "" }}
      </button>
    </div>

    <div v-if="selected" class="mb-6 grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <div class="rounded border border-gray-200 bg-white p-3">
        <div class="text-xs uppercase tracking-wide text-gray-400">Revenue</div>
        <div class="text-lg font-semibold">{{ money(selected.revenue) }}</div>
        <div class="text-xs text-gray-400">Min sell {{ money(selected.min_selling_total) }}</div>
      </div>
      <div class="rounded border border-gray-200 border-l-4 border-l-emerald-500 bg-white p-3">
        <div class="text-xs uppercase tracking-wide text-gray-400">CM1</div>
        <div class="text-lg font-semibold">{{ money(selected.cm1) }}</div>
        <div class="text-xs text-gray-500">{{ selected.cm1_pct ?? "—" }}% of revenue</div>
      </div>
      <div class="rounded border border-gray-200 border-l-4 border-l-sky-500 bg-white p-3">
        <div class="text-xs uppercase tracking-wide text-gray-400">CM2</div>
        <div class="text-lg font-semibold">{{ money(selected.cm2) }}</div>
        <div class="text-xs text-gray-500">{{ selected.cm2_pct ?? "—" }}% of revenue</div>
      </div>
      <div class="rounded border border-gray-200 border-l-4 border-l-violet-500 bg-white p-3">
        <div class="text-xs uppercase tracking-wide text-gray-400">CM3</div>
        <div class="text-lg font-semibold">{{ money(selected.cm3) }}</div>
        <div class="text-xs text-gray-500">{{ selected.cm3_pct ?? "—" }}% of revenue</div>
      </div>
      <div class="rounded border border-gray-200 border-l-4 border-l-amber-500 bg-white p-3">
        <div class="text-xs uppercase tracking-wide text-gray-400">Operating profit</div>
        <div class="text-lg font-semibold">{{ money(selected.operating_profit) }}</div>
        <div class="text-xs text-gray-500">
          {{ selected.operating_profit_pct ?? "—" }}% of revenue
        </div>
      </div>
    </div>

    <div v-if="selected?.items?.length" class="mb-6 overflow-x-auto rounded border border-gray-200 bg-white">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-3 py-2">Line</th>
            <th class="px-3 py-2 text-right">Qty</th>
            <th class="px-3 py-2 text-right">Selling rate</th>
            <th class="px-3 py-2 text-right">Amount</th>
            <th class="px-3 py-2 text-right">Line CM1</th>
            <th class="px-3 py-2 text-right">Min rate</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in selected.items" :key="it.line_key" class="border-t">
            <td class="px-3 py-1.5">{{ it.item_name }}</td>
            <td class="px-3 py-1.5 text-right">{{ it.qty }}</td>
            <td class="px-3 py-1.5 text-right">{{ money(it.selling_rate) }}</td>
            <td class="px-3 py-1.5 text-right">{{ money(it.selling_amount) }}</td>
            <td class="px-3 py-1.5 text-right">{{ money(it.cm1) }}</td>
            <td class="px-3 py-1.5 text-right">{{ money(it.min_selling_rate) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="selected" class="mb-6 overflow-x-auto rounded border border-gray-200 bg-white">
      <div class="border-b border-gray-100 px-3 py-2.5">
        <h2 class="text-sm font-semibold text-gray-900">Margin waterfall</h2>
        <p class="text-xs text-gray-500">
          Revenue less each cost layer. Cost rules roll up under their layer; click Why? for the
          calculation.
        </p>
      </div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-3 py-2">Line</th>
            <th class="px-3 py-2">How calculated</th>
            <th class="px-3 py-2 text-right">Amount</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          <tr class="border-t bg-slate-50 font-semibold text-gray-900">
            <td class="px-3 py-2">Revenue</td>
            <td class="px-3 py-2 text-xs font-normal text-gray-500">Selling price × qty</td>
            <td class="px-3 py-2 text-right">{{ money(selected.revenue) }}</td>
            <td></td>
          </tr>

          <template v-for="sec in waterfall" :key="sec.key">
            <tr class="border-t bg-gray-50/70">
              <td class="px-3 py-1.5 font-medium text-gray-800">− {{ sec.groupLabel }}</td>
              <td class="px-3 py-1.5 text-xs text-gray-500">
                {{ sec.leaves.length }} rule{{ sec.leaves.length === 1 ? "" : "s" }}
              </td>
              <td class="px-3 py-1.5 text-right font-medium">{{ money(sec.groupTotal) }}</td>
              <td></td>
            </tr>
            <tr v-for="leaf in sec.leaves" :key="leaf.key" class="border-t border-gray-100">
              <td class="py-1.5 pl-8 pr-3 text-gray-700">
                <span class="text-gray-300">↳</span> {{ leaf.label }}
              </td>
              <td class="px-3 py-1.5 text-xs text-gray-500">{{ leaf.detail }}</td>
              <td class="px-3 py-1.5 text-right text-gray-700">
                {{ formatCurrency(String(leaf.amount)) }}
              </td>
              <td class="px-3 py-1.5 text-right">
                <button
                  v-if="leaf.cost?.explanation"
                  type="button"
                  class="text-xs text-blue-600 hover:underline"
                  @click="whyCost = leaf.cost"
                >
                  Why?
                </button>
              </td>
            </tr>
            <tr v-if="!sec.leaves.length" class="border-t border-gray-100">
              <td class="py-1.5 pl-8 pr-3 text-xs text-gray-400" colspan="4">
                No rules in this layer.
              </td>
            </tr>
            <tr class="border-t-2 border-gray-200 bg-white font-semibold text-gray-900">
              <td class="px-3 py-2">= {{ sec.marginLabel }}</td>
              <td class="px-3 py-2 text-xs font-normal text-gray-500">{{ sec.title }}</td>
              <td class="px-3 py-2 text-right">
                {{ money(sec.marginAmount) }}
                <span v-if="sec.marginPct" class="ml-1 text-xs font-normal text-gray-500">
                  ({{ sec.marginPct }}%)
                </span>
              </td>
              <td></td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-if="whyCost?.explanation" class="border-t bg-slate-50 px-3 py-3 text-sm text-gray-700">
        <span class="font-semibold">Why {{ money(whyCost.amount) }}?</span>
        {{ explainLabel(whyCost.explanation) }}
        <button type="button" class="ml-2 text-xs text-gray-500 underline" @click="whyCost = null">
          Dismiss
        </button>
      </p>
    </div>

    <div class="mb-6 rounded border border-gray-200 bg-white p-4">
      <h2 class="mb-3 text-sm font-semibold">New scenario</h2>
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <input v-model="scenarioName" class="form-input" placeholder="Name (e.g. SP 1500)" />
        <input v-model="priceOverride" class="form-input" placeholder="Selling rate (all lines)" />
        <input v-model="freightOverride" class="form-input" placeholder="Freight amount" />
        <input v-model="materialFactor" class="form-input" placeholder="Material factor (optional, e.g. 1.10)" />
        <input v-model="qtyOverride" class="form-input" placeholder="Qty (all lines)" />
      </div>
      <button class="btn-primary mt-3" :disabled="busy || !plan" @click="addScenario">Add scenario</button>
    </div>

    <div v-if="compare" class="mb-6 overflow-x-auto rounded border border-gray-200 bg-white">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-3 py-2">Metric</th>
            <th v-for="c in compare.columns" :key="c.scenario_id" class="px-3 py-2">{{ c.name }}</th>
          </tr>
        </thead>
        <tbody>
          <tr class="border-t">
            <td class="px-3 py-1.5">Revenue</td>
            <td v-for="c in compare.columns" :key="c.scenario_id + 'r'" class="px-3 py-1.5">{{ money(c.revenue) }}</td>
          </tr>
          <tr class="border-t">
            <td class="px-3 py-1.5">CM1</td>
            <td v-for="c in compare.columns" :key="c.scenario_id + 'c1'" class="px-3 py-1.5">
              {{ money(c.cm1) }}
              <span v-if="c.cm1_pct" class="text-gray-500">({{ c.cm1_pct }}%)</span>
            </td>
          </tr>
          <tr class="border-t">
            <td class="px-3 py-1.5">Op. profit</td>
            <td v-for="c in compare.columns" :key="c.scenario_id + 'op'" class="px-3 py-1.5">
              {{ money(c.operating_profit) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="mb-6 rounded border border-gray-200 bg-white p-4">
      <h2 class="mb-3 text-sm font-semibold">Estimated vs Actual (GL CM)</h2>
      <p class="mb-3 text-xs text-gray-500">
        Selected scenario vs company GL Contribution Margin for the period (deal estimate vs company
        actuals — scale may differ).
      </p>
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label class="form-label">From</label>
          <input v-model="actualFrom" type="date" class="form-input" />
        </div>
        <div>
          <label class="form-label">To</label>
          <input v-model="actualTo" type="date" class="form-input" />
        </div>
        <button class="btn-secondary" :disabled="busy || !plan" @click="runActualComparison">
          Compare to GL
        </button>
      </div>
      <div v-if="actual" class="mt-4 overflow-x-auto">
        <p class="mb-2 text-xs text-gray-500">
          Scenario: {{ actual.scenario_name }} · {{ actual.from_date }} → {{ actual.to_date }}
        </p>
        <ul v-if="actual.warnings.length" class="mb-2 list-disc pl-5 text-xs text-amber-700">
          <li v-for="(w, i) in actual.warnings" :key="i">{{ w }}</li>
        </ul>
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-3 py-2">Metric</th>
              <th class="px-3 py-2 text-right">Estimated</th>
              <th class="px-3 py-2 text-right">Actual (GL)</th>
              <th class="px-3 py-2 text-right">Δ (Act − Est)</th>
              <th class="px-3 py-2 text-right">Δ %</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in actual.rows" :key="r.field" class="border-t">
              <td class="px-3 py-1.5">{{ r.label }}</td>
              <td class="px-3 py-1.5 text-right">{{ money(r.estimated) }}</td>
              <td class="px-3 py-1.5 text-right">{{ money(r.actual) }}</td>
              <td class="px-3 py-1.5 text-right">{{ money(r.delta) }}</td>
              <td class="px-3 py-1.5 text-right">{{ r.delta_pct != null ? `${r.delta_pct}%` : "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
