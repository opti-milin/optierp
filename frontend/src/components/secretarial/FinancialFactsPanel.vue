<script setup lang="ts">
// The figures that decide which ROC obligations apply. Derived from the ledger
// when this company's books live here; typed in when they do not. `source` is
// shown on purpose — a computed number and a typed-in number deserve different
// trust on screen.
import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { ApplicabilityCheck, FinancialFacts } from "@/types/secretarial";

const props = defineProps<{
  entityId: string;
  linked: boolean;
}>();

const FACT_FIELDS: { key: keyof FinancialFacts; label: string }[] = [
  { key: "turnover", label: "Turnover" },
  { key: "net_profit", label: "Net profit" },
  { key: "net_worth", label: "Net worth" },
  { key: "paid_up_capital", label: "Paid-up capital" },
  { key: "free_reserves", label: "Free reserves" },
  { key: "securities_premium", label: "Securities premium" },
  { key: "borrowings", label: "Borrowings" },
  { key: "deposits", label: "Deposits" },
];

const fy = ref("2025-26");
const facts = ref<FinancialFacts | null>(null);
const form = ref<Record<string, string>>({});
const checks = ref<ApplicabilityCheck[]>([]);
const loading = ref(false);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const showNotApplicable = ref(false);

const SOURCE_LABEL: Record<string, string> = {
  auto: "Computed from the general ledger",
  manual: "Entered by hand — books are kept elsewhere",
  missing: "No figures yet — threshold rules stay unknown, not exempt",
};

const applies = computed(() => checks.value.filter((c) => c.verdict === "applies"));
const unknown = computed(() => checks.value.filter((c) => c.verdict === "unknown"));
const notApplicable = computed(() => checks.value.filter((c) => c.verdict === "not_applicable"));

function toForm(row: FinancialFacts): Record<string, string> {
  const next: Record<string, string> = {};
  for (const field of FACT_FIELDS) {
    const value = row[field.key];
    next[field.key] = typeof value === "number" ? String(value) : "";
  }
  return next;
}

async function load(): Promise<void> {
  if (!props.entityId) return;
  loading.value = true;
  error.value = null;
  notice.value = null;
  try {
    const row = (
      await api.get<FinancialFacts>(`/secretarial/entities/${props.entityId}/facts`, {
        params: { fy: fy.value },
      })
    ).data;
    facts.value = row;
    form.value = toForm(row);
    checks.value = (
      await api.get<ApplicabilityCheck[]>(`/secretarial/entities/${props.entityId}/applicability`, {
        params: { fy: fy.value },
      })
    ).data;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function refreshLedger(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const row = (
      await api.get<FinancialFacts>(`/secretarial/entities/${props.entityId}/facts`, {
        params: { fy: fy.value, refresh: true },
      })
    ).data;
    facts.value = row;
    form.value = toForm(row);
    await load();
    notice.value = "Recomputed from the ledger.";
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  notice.value = null;
  try {
    const body: Record<string, unknown> = { fy: fy.value };
    for (const field of FACT_FIELDS) {
      const raw = form.value[field.key];
      body[field.key] = raw === "" || raw === undefined ? null : Number(raw);
    }
    facts.value = (await api.put<FinancialFacts>(`/secretarial/entities/${props.entityId}/facts`, body)).data;
    form.value = toForm(facts.value);
    await load();
    notice.value = "Saved as a manual row. Ledger refresh will not overwrite these figures.";
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    saving.value = false;
  }
}

watch(
  () => [props.entityId, fy.value],
  () => {
    void load();
  },
  { immediate: true },
);
</script>

<template>
  <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
    <div class="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 class="text-sm font-semibold text-gray-900">Financial figures</h2>
        <p class="text-xs text-gray-500">
          {{ SOURCE_LABEL[facts?.source ?? "missing"] }}
        </p>
      </div>
      <label class="text-xs text-gray-500">
        Financial year
        <input v-model="fy" class="ml-1 w-24 rounded border border-gray-300 px-2 py-1 text-sm" placeholder="2025-26" />
      </label>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p v-if="notice" class="mb-3 rounded border border-green-200 bg-green-50 p-2 text-sm text-green-800">
      {{ notice }}
    </p>
    <p v-if="loading && !facts" class="text-sm text-gray-500">Loading…</p>

    <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      <label v-for="field in FACT_FIELDS" :key="field.key" class="text-xs text-gray-500">
        {{ field.label }}
        <input
          v-model="form[field.key]"
          type="number"
          class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
      </label>
    </div>

    <div class="mt-3 flex flex-wrap gap-2">
      <button
        class="rounded bg-gray-800 px-3 py-1.5 text-sm text-white hover:bg-gray-700"
        :disabled="saving"
        @click="save"
      >
        {{ saving ? "Saving…" : "Save figures" }}
      </button>
      <button
        v-if="linked"
        class="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
        :disabled="loading"
        @click="refreshLedger"
      >
        Recompute from ledger
      </button>
    </div>

    <div v-if="checks.length" class="mt-5">
      <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        What applies this year
      </h3>
      <ul class="space-y-1 text-sm">
        <li v-for="c in applies" :key="c.rule_code" class="rounded bg-green-50 px-2 py-1 text-green-900">
          <span class="font-medium">{{ c.rule_code }}</span> — {{ c.title }}
        </li>
        <li v-for="c in unknown" :key="c.rule_code" class="rounded bg-amber-50 px-2 py-1 text-amber-900">
          <span class="font-medium">{{ c.rule_code }}</span> — {{ c.title }}
          <span class="block text-xs">Cannot tell: {{ c.reasons.join("; ") }}</span>
        </li>
      </ul>
      <button
        v-if="notApplicable.length"
        class="mt-2 text-xs text-gray-500 hover:underline"
        @click="showNotApplicable = !showNotApplicable"
      >
        {{ showNotApplicable ? "Hide" : `Show ${notApplicable.length} not applicable` }}
      </button>
      <ul v-if="showNotApplicable" class="mt-1 space-y-1 text-xs text-gray-500">
        <li v-for="c in notApplicable" :key="c.rule_code">{{ c.rule_code }} — {{ c.title }}</li>
      </ul>
    </div>
  </section>
</template>
