<script setup lang="ts">
// The compliance calendar. Generation is safe to re-run — it inserts what is
// missing and corrects due dates on rows nobody has started, never overwriting
// work in progress — so the button carries no warning.
import { computed, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { ComplianceItem, GenerateCalendarResult } from "@/types/secretarial";

const store = useSecretarialStore();
const items = ref<ComplianceItem[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const generateResult = ref<GenerateCalendarResult | null>(null);

const fy = ref(defaultFy());
const statusFilter = ref("");
const openOnly = ref(true);
const scopeAll = ref(false);

function defaultFy(): string {
  const now = new Date();
  // Indian FY: April to March.
  const start = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  return `${start}-${String(start + 1).slice(-2)}`;
}

const STATUS_TONE: Record<string, string> = {
  overdue: "bg-red-100 text-red-800",
  due: "bg-amber-100 text-amber-800",
  upcoming: "bg-gray-100 text-gray-700",
  in_progress: "bg-blue-100 text-blue-800",
  pending_review: "bg-purple-100 text-purple-800",
  filed: "bg-green-100 text-green-800",
  completed: "bg-green-100 text-green-800",
  waived: "bg-gray-200 text-gray-600",
  not_applicable: "bg-gray-100 text-gray-400",
};

const grouped = computed(() => {
  const out: Record<string, ComplianceItem[]> = {};
  for (const item of items.value) {
    const key = item.due_on.slice(0, 7);
    (out[key] ??= []).push(item);
  }
  return Object.entries(out).sort(([a], [b]) => a.localeCompare(b));
});

function monthLabel(ym: string): string {
  const [y, m] = ym.split("-");
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const resp = await api.get<ListResponse<ComplianceItem>>("/secretarial/compliance/items", {
      params: {
        entity_id: scopeAll.value ? undefined : store.entityId,
        fy: fy.value || undefined,
        status: statusFilter.value || undefined,
        open_only: openOnly.value || undefined,
        page_size: 200,
      },
    });
    items.value = resp.data.items;
    total.value = resp.data.total;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function generate(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    generateResult.value = (
      await api.post<GenerateCalendarResult>("/secretarial/compliance/generate", {
        fy: fy.value,
        entity_id: scopeAll.value ? null : store.entityId,
      })
    ).data;
    await load();
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function setStatus(item: ComplianceItem, status: string): Promise<void> {
  const body: Record<string, unknown> = { status };
  if (status === "waived") {
    const reason = window.prompt("Why is this obligation being waived? (recorded permanently)");
    if (!reason) return;
    body.waived_reason = reason;
  }
  await api.patch(`/secretarial/compliance/items/${item.id}`, body);
  await load();
}

(async () => {
  await store.load();
  await load();
})();
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">Compliance calendar</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        Every ROC obligation for the selected financial year, with its statutory due date
        computed from that company's own year end.
      </p>
    </div>

    <div class="mb-3 flex flex-wrap items-end gap-2">
      <div>
        <label class="block text-xs text-gray-500">Financial year</label>
        <input v-model="fy" class="w-28 rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </div>
      <div>
        <label class="block text-xs text-gray-500">Status</label>
        <select v-model="statusFilter" class="rounded border border-gray-300 px-2 py-1.5 text-sm">
          <option value="">All</option>
          <option v-for="s in Object.keys(STATUS_TONE)" :key="s" :value="s">
            {{ s.replace("_", " ") }}
          </option>
        </select>
      </div>
      <label class="flex items-center gap-1.5 pb-1.5 text-sm text-gray-600">
        <input v-model="openOnly" type="checkbox" @change="load" /> Open only
      </label>
      <label class="flex items-center gap-1.5 pb-1.5 text-sm text-gray-600">
        <input v-model="scopeAll" type="checkbox" @change="load" /> {{ store.isPractice ? "All clients" : "All companies" }}
      </label>
      <button
        class="rounded bg-gray-800 px-3 py-1.5 text-sm text-white hover:bg-gray-700"
        @click="load"
      >
        Apply
      </button>
      <button
        class="rounded border border-blue-300 px-3 py-1.5 text-sm text-blue-700 hover:bg-blue-50"
        title="Safe to re-run: adds what's missing, corrects untouched due dates, never overwrites work in progress."
        @click="generate"
      >
        Generate for {{ fy }}
      </button>
    </div>

    <div
      v-if="generateResult"
      class="mb-3 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900"
    >
      {{ generateResult.items_created }} created, {{ generateResult.items_refreshed }} refreshed,
      {{ generateResult.items_skipped_not_applicable }} not applicable here.
      <span v-if="generateResult.unpublished_rules_ignored" class="font-medium">
        {{ generateResult.unpublished_rules_ignored }} rule(s) skipped — not yet reviewed and
        published by a qualified professional
        (<RouterLink to="/secretarial/rules" class="underline">review</RouterLink>).
      </span>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <p v-if="!loading && !items.length" class="rounded border border-gray-200 bg-white p-6 text-center text-sm text-gray-500">
      Nothing on the calendar for {{ fy }}. Press <strong>Generate</strong> to build it from the
      published rule catalogue.
    </p>

    <div v-for="[month, rows] in grouped" :key="month" class="mb-5">
      <h2 class="mb-2 text-sm font-semibold text-gray-700">{{ monthLabel(month) }}</h2>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full divide-y divide-gray-200 text-sm">
          <tbody class="divide-y divide-gray-100">
            <tr v-for="item in rows" :key="item.id" class="hover:bg-gray-50">
              <td class="w-24 px-3 py-2 text-gray-500">{{ item.due_on }}</td>
              <td class="px-3 py-2">
                <p class="font-medium text-gray-900">{{ item.title }}</p>
                <p class="text-xs text-gray-400">
                  <span v-if="scopeAll">{{ item.entity_name }} · </span>{{ item.act_section || "—" }}
                </p>
              </td>
              <td class="w-24 px-3 py-2 text-xs text-gray-500">{{ item.form_code || "—" }}</td>
              <td class="w-28 px-3 py-2">
                <span
                  class="rounded px-1.5 py-0.5 text-xs"
                  :class="STATUS_TONE[item.status] ?? 'bg-gray-100 text-gray-700'"
                >
                  {{ item.status.replace("_", " ") }}
                </span>
              </td>
              <td class="w-52 px-3 py-2 text-right">
                <select
                  class="rounded border border-gray-300 px-1.5 py-1 text-xs"
                  :value="item.status"
                  @change="setStatus(item, ($event.target as HTMLSelectElement).value)"
                >
                  <!-- `due` and `overdue` are computed from the date rather than
                       chosen, but they must still appear here or the select renders
                       blank for exactly the rows that need attention most. -->
                  <option value="upcoming">Upcoming</option>
                  <option value="due">Due</option>
                  <option value="overdue">Overdue</option>
                  <option value="in_progress">In progress</option>
                  <option value="pending_review">Pending review</option>
                  <option value="filed">Filed</option>
                  <option value="completed">Completed</option>
                  <option value="waived">Waive…</option>
                  <option value="not_applicable">Not applicable</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
