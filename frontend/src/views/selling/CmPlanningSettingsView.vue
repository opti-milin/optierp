<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { CmPlanningSettings, CmPlanningTemplateInfo } from "@/types/cm_planning";

const form = reactive<CmPlanningSettings>({
  target_cm1_pct: "35",
  target_cm2_pct: "25",
  min_cm1_pct: "15",
  submit_policy: "warn",
  default_template: "manufacturing",
  max_scenarios_per_plan: 10,
  allocations: {
    product_channel_fixed_pct_of_revenue: "3",
    segment_bu_fixed_pct_of_revenue: "2",
    corporate_overhead_pct_of_revenue: "5",
  },
});
const templates = ref<CmPlanningTemplateInfo[]>([]);
const loading = ref(false);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const applyId = ref("");

function assignSettings(data: CmPlanningSettings): void {
  form.target_cm1_pct = String(data.target_cm1_pct ?? "35");
  form.target_cm2_pct = data.target_cm2_pct != null ? String(data.target_cm2_pct) : null;
  form.min_cm1_pct = String(data.min_cm1_pct ?? "15");
  form.submit_policy = data.submit_policy ?? "warn";
  form.default_template = data.default_template ?? null;
  form.max_scenarios_per_plan = data.max_scenarios_per_plan ?? 10;
  const a = data.allocations ?? {};
  form.allocations = {
    product_channel_fixed_pct_of_revenue: String(
      a.product_channel_fixed_pct_of_revenue ?? "3",
    ),
    segment_bu_fixed_pct_of_revenue: String(a.segment_bu_fixed_pct_of_revenue ?? "2"),
    corporate_overhead_pct_of_revenue: String(a.corporate_overhead_pct_of_revenue ?? "5"),
  };
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [settingsRes, tplRes] = await Promise.all([
      api.get<CmPlanningSettings>("/cm-planning/settings"),
      api.get<CmPlanningTemplateInfo[]>("/cm-planning/templates"),
    ]);
    assignSettings(settingsRes.data);
    templates.value = tplRes.data;
    applyId.value = form.default_template ?? templates.value[0]?.id ?? "";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  notice.value = null;
  try {
    const payload: CmPlanningSettings = {
      target_cm1_pct: form.target_cm1_pct,
      target_cm2_pct: form.target_cm2_pct,
      min_cm1_pct: form.min_cm1_pct,
      submit_policy: form.submit_policy,
      default_template: form.default_template,
      max_scenarios_per_plan: Number(form.max_scenarios_per_plan) || 10,
      allocations: {
        product_channel_fixed_pct_of_revenue:
          form.allocations.product_channel_fixed_pct_of_revenue,
        segment_bu_fixed_pct_of_revenue: form.allocations.segment_bu_fixed_pct_of_revenue,
        corporate_overhead_pct_of_revenue: form.allocations.corporate_overhead_pct_of_revenue,
      },
    };
    assignSettings((await api.put<CmPlanningSettings>("/cm-planning/settings", payload)).data);
    notice.value = "Defaults saved. New plans copy these; edit rules on each plan.";
    setTimeout(() => (notice.value = null), 3000);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function applyTemplate(): Promise<void> {
  if (!applyId.value) return;
  saving.value = true;
  error.value = null;
  notice.value = null;
  try {
    assignSettings(
      (await api.post<CmPlanningSettings>(`/cm-planning/apply-template?template_id=${applyId.value}`))
        .data,
    );
    notice.value = `Applied template “${applyId.value}”. Open a CM Plan to tune rules for that deal.`;
    setTimeout(() => (notice.value = null), 4000);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="mx-auto max-w-2xl space-y-6 p-6">
    <div>
      <h1 class="text-xl font-semibold text-gray-900">CM Planning Settings</h1>
      <p class="text-sm text-gray-500">
        Company defaults for new contribution-margin plans. To add different rules and analyze a
        deal, open that
        <RouterLink to="/cm-plans" class="text-blue-600 hover:underline">CM Plan</RouterLink>
        and edit <span class="font-medium text-gray-700">Cost rules</span> there.
      </p>
    </div>

    <p v-if="notice" class="rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>
    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Margin policy defaults</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="form-label">Target CM1 %</label>
          <input v-model="form.target_cm1_pct" type="number" step="0.01" class="form-input" />
        </div>
        <div>
          <label class="form-label">Target CM2 % (optional)</label>
          <input v-model="form.target_cm2_pct" type="number" step="0.01" class="form-input" />
        </div>
        <div>
          <label class="form-label">Minimum CM1 %</label>
          <input v-model="form.min_cm1_pct" type="number" step="0.01" class="form-input" />
        </div>
        <div>
          <label class="form-label">Submit policy</label>
          <select v-model="form.submit_policy" class="form-input">
            <option value="off">Off</option>
            <option value="warn">Warn</option>
            <option value="block">Block</option>
          </select>
        </div>
        <div>
          <label class="form-label">Max scenarios / plan</label>
          <input
            v-model.number="form.max_scenarios_per_plan"
            type="number"
            min="2"
            max="50"
            class="form-input"
          />
        </div>
        <div>
          <label class="form-label">Default template</label>
          <input
            v-model="form.default_template"
            type="text"
            class="form-input"
            placeholder="manufacturing"
          />
        </div>
      </div>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Default fixed costs (% of revenue)</h2>
      <p class="mb-3 text-xs text-gray-500">
        Starting values for new plans. Override per deal under Cost rules on the plan.
      </p>
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <label class="form-label">Product / channel %</label>
          <input
            v-model="form.allocations.product_channel_fixed_pct_of_revenue"
            type="number"
            step="0.01"
            class="form-input"
          />
        </div>
        <div>
          <label class="form-label">Segment / BU %</label>
          <input
            v-model="form.allocations.segment_bu_fixed_pct_of_revenue"
            type="number"
            step="0.01"
            class="form-input"
          />
        </div>
        <div>
          <label class="form-label">Corporate overhead %</label>
          <input
            v-model="form.allocations.corporate_overhead_pct_of_revenue"
            type="number"
            step="0.01"
            class="form-input"
          />
        </div>
      </div>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Industry template</h2>
      <div class="flex flex-wrap items-end gap-3">
        <div class="min-w-[12rem] flex-1">
          <label class="form-label">Template</label>
          <select v-model="applyId" class="form-input">
            <option v-for="t in templates" :key="t.id" :value="t.id">
              {{ t.label }}{{ t.target_cm1_pct != null ? ` (CM1 ${t.target_cm1_pct}%)` : "" }}
            </option>
          </select>
        </div>
        <button class="btn-secondary" :disabled="saving || !applyId" @click="applyTemplate">
          Apply template
        </button>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Sets company defaults and the default cost structure for new plans.
      </p>
    </section>

    <div class="flex gap-2">
      <button class="btn-primary" :disabled="saving || loading" @click="save">Save defaults</button>
      <RouterLink to="/cm-plans" class="btn-secondary">CM Plans</RouterLink>
      <RouterLink to="/reports?tab=contribution-margin" class="btn-secondary">GL CM report</RouterLink>
    </div>
  </div>
</template>
