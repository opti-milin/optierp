<script setup lang="ts">
/** Read-only statutory catalogue browser (Tier 1 — tenants cannot edit). */
import { onMounted, ref, watch } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";

interface AssessmentYear {
  code: string;
  ay_start: string;
  ay_end: string;
  fy_start: string;
  fy_end: string;
  prev_ay_code: string | null;
}

interface RateBand {
  seq: number;
  lower: string;
  upper: string | null;
  rate_percent: string;
}

interface RateSchedule {
  code: string;
  assessee_class_code: string;
  regime_code: string;
  income_character_code: string | null;
  age_category: string;
  schedule_kind: string;
  condition_expr: string | null;
  remarks: string | null;
  bands: RateBand[];
}

interface SurchargeSchedule {
  code: string;
  assessee_class_code: string;
  regime_code: string | null;
  bands: RateBand[];
}

interface FinanceAct {
  ay_code: string;
  version: string;
  source_ref: string | null;
  is_current: boolean;
}

const years = ref<AssessmentYear[]>([]);
const ayCode = ref("2025-26");
const financeAct = ref<FinanceAct | null>(null);
const rates = ref<RateSchedule[]>([]);
const surcharge = ref<SurchargeSchedule[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

async function loadYears(): Promise<void> {
  years.value = (await api.get<AssessmentYear[]>("/tax/catalogue/assessment-years")).data;
  if (years.value.length && !years.value.some((y) => y.code === ayCode.value)) {
    ayCode.value = years.value[years.value.length - 1]!.code;
  }
}

async function loadAy(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [fa, rs, ss] = await Promise.all([
      api.get<FinanceAct | null>(`/tax/catalogue/finance-act/${ayCode.value}`),
      api.get<RateSchedule[]>("/tax/catalogue/rate-schedules", { params: { ay_code: ayCode.value } }),
      api.get<SurchargeSchedule[]>("/tax/catalogue/surcharge-schedules", {
        params: { ay_code: ayCode.value },
      }),
    ]);
    financeAct.value = fa.data;
    rates.value = rs.data;
    surcharge.value = ss.data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadYears();
    await loadAy();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
});

watch(ayCode, () => {
  void loadAy();
});
</script>

<template>
  <div class="p-6 max-w-6xl mx-auto space-y-6" data-testid="statutory-catalogue">
    <header class="space-y-1">
      <h1 class="text-2xl font-semibold text-slate-900">Statutory Tax Catalogue</h1>
      <p class="text-sm text-slate-600">
        Global Income-tax Act rates — read-only. Tenants cannot edit these rules.
      </p>
    </header>

    <div v-if="error" class="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error.detail }}
    </div>

    <div class="flex flex-wrap items-end gap-4">
      <label class="text-sm">
        <span class="block text-slate-600 mb-1">Assessment year</span>
        <select
          v-model="ayCode"
          data-testid="catalogue-ay-select"
          class="border border-slate-300 rounded px-3 py-2 min-w-[10rem]"
        >
          <option v-for="y in years" :key="y.code" :value="y.code">{{ y.code }}</option>
        </select>
      </label>
      <div v-if="financeAct" class="text-sm text-slate-700" data-testid="catalogue-finance-act">
        Finance Act: <strong>{{ financeAct.version }}</strong>
        <span v-if="financeAct.source_ref" class="text-slate-500"> — {{ financeAct.source_ref }}</span>
      </div>
      <div v-if="loading" class="text-sm text-slate-500">Loading…</div>
    </div>

    <section>
      <h2 class="text-lg font-medium text-slate-800 mb-2">Rate schedules</h2>
      <div class="overflow-x-auto border border-slate-200 rounded">
        <table class="min-w-full text-sm" data-testid="catalogue-rate-table">
          <thead class="bg-slate-50 text-left">
            <tr>
              <th class="px-3 py-2">Code</th>
              <th class="px-3 py-2">Class</th>
              <th class="px-3 py-2">Regime</th>
              <th class="px-3 py-2">Kind</th>
              <th class="px-3 py-2">Bands</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rates" :key="r.code" class="border-t border-slate-100">
              <td class="px-3 py-2 font-mono text-xs">{{ r.code }}</td>
              <td class="px-3 py-2">{{ r.assessee_class_code }}</td>
              <td class="px-3 py-2">{{ r.regime_code }}</td>
              <td class="px-3 py-2">{{ r.schedule_kind }}</td>
              <td class="px-3 py-2 text-xs text-slate-600">
                <span v-for="(b, i) in r.bands" :key="b.seq">
                  <template v-if="i">; </template>
                  {{ b.lower }}–{{ b.upper ?? "∞" }} @ {{ b.rate_percent }}%
                </span>
              </td>
            </tr>
            <tr v-if="!rates.length && !loading">
              <td colspan="5" class="px-3 py-4 text-slate-500">
                No rate schedules for this assessment year.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2 class="text-lg font-medium text-slate-800 mb-2">Surcharge schedules</h2>
      <div class="overflow-x-auto border border-slate-200 rounded">
        <table class="min-w-full text-sm" data-testid="catalogue-surcharge-table">
          <thead class="bg-slate-50 text-left">
            <tr>
              <th class="px-3 py-2">Code</th>
              <th class="px-3 py-2">Class</th>
              <th class="px-3 py-2">Regime</th>
              <th class="px-3 py-2">Bands</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in surcharge" :key="s.code" class="border-t border-slate-100">
              <td class="px-3 py-2 font-mono text-xs">{{ s.code }}</td>
              <td class="px-3 py-2">{{ s.assessee_class_code }}</td>
              <td class="px-3 py-2">{{ s.regime_code ?? "—" }}</td>
              <td class="px-3 py-2 text-xs text-slate-600">
                <span v-for="(b, i) in s.bands" :key="b.seq">
                  <template v-if="i">; </template>
                  {{ b.lower }}–{{ b.upper ?? "∞" }} @ {{ b.rate_percent }}%
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
