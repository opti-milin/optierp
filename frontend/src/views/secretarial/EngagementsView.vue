<script setup lang="ts">
// Delegation, from both sides: engagements this account granted over its own
// entities, and engagements it holds over clients' entities.
//
// The access summary is deliberately shown in plain words before activation.
// `ledger_read` is the default, so a client could otherwise hand over their whole
// book of accounts by clicking Accept on a screen full of enum names.
import { ref } from "vue";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { AccessSummary, Engagement } from "@/types/secretarial";

const store = useSecretarialStore();
const granted = ref<Engagement[]>([]);
const held = ref<Engagement[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const summaries = ref<Record<string, AccessSummary>>({});

const STATUS_TONE: Record<string, string> = {
  pending: "bg-amber-100 text-amber-800",
  active: "bg-green-100 text-green-800",
  suspended: "bg-gray-200 text-gray-700",
  ended: "bg-gray-100 text-gray-500",
};

const ACCESS_LABEL: Record<string, string> = {
  none: "No access to accounts",
  derived_only: "Summary figures only",
  reports_read: "Financial statements (read)",
  ledger_read: "Full books (read-only)",
};

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    granted.value = (
      await api.get<ListResponse<Engagement>>("/secretarial/engagements", {
        params: { direction: "granted", page_size: 100 },
      })
    ).data.items;
    held.value = (
      await api.get<ListResponse<Engagement>>("/secretarial/engagements", {
        params: { direction: "held", page_size: 100 },
      })
    ).data.items;
    for (const e of granted.value) {
      if (e.status !== "ended") await loadSummary(e.id);
    }
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function loadSummary(id: string): Promise<void> {
  summaries.value[id] = (
    await api.get<AccessSummary>(`/secretarial/engagements/${id}/access-summary`)
  ).data;
}

async function activate(e: Engagement): Promise<void> {
  const s = summaries.value[e.id];
  const warn = s?.warnings.length ? `\n\n${s.warnings.join("\n")}` : "";
  if (!window.confirm(`Give ${e.firm_name} access to ${e.entity_name}?${warn}`)) return;
  error.value = null;
  try {
    await api.post(`/secretarial/engagements/${e.id}/activate`);
    await load();
  } catch (err) {
    error.value = (err as { response?: { data: ErrorEnvelope } }).response?.data ?? (err as ErrorEnvelope);
  }
}

async function end(e: Engagement): Promise<void> {
  const reason = window.prompt(`End the engagement with ${e.firm_name}? Reason (recorded permanently):`);
  if (!reason) return;
  error.value = null;
  try {
    await api.post(`/secretarial/engagements/${e.id}/end`, { ended_reason: reason });
    await load();
  } catch (err) {
    error.value = (err as { response?: { data: ErrorEnvelope } }).response?.data ?? (err as ErrorEnvelope);
  }
}

(async () => {
  await store.load();
  await load();
})();
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">Access & engagements</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        Who can work on your statutory records, and whose records you can work on. Access is
        always scoped to one entity, always revocable by the owner, and never includes the
        ability to change your accounts.
      </p>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <h2 class="mb-2 text-sm font-semibold text-gray-900">Access you have granted</h2>
    <p v-if="!granted.length" class="mb-6 rounded border border-gray-200 bg-white p-4 text-sm text-gray-500">
      No firm has access to your records.
    </p>
    <div v-else class="mb-6 space-y-3">
      <div v-for="e in granted" :key="e.id" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p class="font-medium text-gray-900">
              {{ e.firm_name }}
              <span class="ml-1 rounded px-1.5 py-0.5 text-xs" :class="STATUS_TONE[e.status]">
                {{ e.status }}
              </span>
            </p>
            <p class="text-sm text-gray-500">
              on {{ e.entity_name }} · {{ ACCESS_LABEL[e.financial_access] }}
              <span v-if="e.include_banking"> · includes banking</span>
            </p>
          </div>
          <div class="flex gap-2">
            <button
              v-if="e.status === 'pending'"
              class="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-500"
              @click="activate(e)"
            >
              Activate
            </button>
            <button
              v-if="e.status !== 'ended'"
              class="rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50"
              @click="end(e)"
            >
              Revoke
            </button>
          </div>
        </div>

        <div v-if="summaries[e.id]" class="mt-3 rounded bg-gray-50 p-3 text-sm">
          <p class="text-gray-700"><strong>Statutory records:</strong> {{ summaries[e.id].secretarial }}</p>
          <p class="mt-1 text-gray-700"><strong>Accounts:</strong> {{ summaries[e.id].financial }}</p>
          <p class="mt-1 text-gray-700"><strong>Banking:</strong> {{ summaries[e.id].banking }}</p>
          <p
            v-for="w in summaries[e.id].warnings"
            :key="w"
            class="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-amber-900"
          >
            ⚠ {{ w }}
          </p>
        </div>

        <p v-if="e.ended_reason" class="mt-2 text-xs text-gray-500">Ended: {{ e.ended_reason }}</p>
      </div>
    </div>

    <h2 class="mb-2 text-sm font-semibold text-gray-900">Clients who have engaged you</h2>
    <p v-if="!held.length" class="rounded border border-gray-200 bg-white p-4 text-sm text-gray-500">
      No client has granted you access yet.
    </p>
    <div v-else class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Client entity</th>
            <th class="px-3 py-2">Granted by</th>
            <th class="px-3 py-2">Accounts access</th>
            <th class="px-3 py-2">Status</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="e in held" :key="e.id" class="hover:bg-gray-50">
            <td class="px-3 py-2 font-medium text-gray-900">{{ e.entity_name }}</td>
            <td class="px-3 py-2 text-gray-600">{{ e.client_name }}</td>
            <td class="px-3 py-2 text-gray-600">{{ ACCESS_LABEL[e.financial_access] }}</td>
            <td class="px-3 py-2">
              <span class="rounded px-1.5 py-0.5 text-xs" :class="STATUS_TONE[e.status]">
                {{ e.status }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
