<script setup lang="ts">
// The coverage matrix — "what exactly comes across?".
//
// Written in Tally's vocabulary because that is the vocabulary the importer
// thinks in: every source is translated into Tally's reserved groups and voucher
// types before anything is posted (see docs/DATA_MIGRATION.md §4). So a Zoho
// Books "accounts_receivable" account reads here as Sundry Debtors, which is
// exactly what it becomes.
// Rendered straight from the backend catalogue, so this page can never drift
// from what the importer actually does.
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { MigrationCatalogue } from "@/types/migration";

const catalogue = ref<MigrationCatalogue | null>(null);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const tab = ref<"entities" | "groups" | "vouchers">("entities");
const fModule = ref("");

const SUPPORT_LABEL: Record<string, string> = {
  full: "Full",
  partial: "Partial",
  reference: "Reference only",
  none: "Not yet",
};
const SUPPORT_TONE: Record<string, string> = {
  full: "bg-green-100 text-green-800",
  partial: "bg-amber-100 text-amber-800",
  reference: "bg-gray-200 text-gray-700",
  none: "bg-red-100 text-red-800",
};

const entities = computed(() => {
  const rows = catalogue.value?.entities ?? [];
  return fModule.value ? rows.filter((r) => r.module === fModule.value) : rows;
});

const tally = computed(() => Object.entries(catalogue.value?.primary_groups ?? {}));
const vouchers = computed(() => Object.entries(catalogue.value?.voucher_types ?? {}));

onMounted(async () => {
  loading.value = true;
  try {
    catalogue.value = (await api.get<MigrationCatalogue>("/migration/catalogue")).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div>
    <div class="mb-4">
      <RouterLink to="/data-migration/imports" class="text-sm text-blue-600 hover:underline">← Imports</RouterLink>
      <h1 class="text-xl font-semibold text-gray-900">What comes across</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        Every entity the importer understands and the record it becomes here. Anything
        marked <em>Reference only</em> or <em>Not yet</em> is still read and counted, so nothing
        in your file disappears without being reported. The vocabulary below is Tally's,
        because every source is translated into it before anything posts — a Zoho Books
        receivable account reads here as Sundry Debtors, which is what it becomes.
      </p>
    </div>

    <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div class="mb-4 flex gap-1 border-b border-gray-200 text-sm">
      <button
        v-for="t in [
          { key: 'entities', label: 'Entities' },
          { key: 'groups', label: 'Ledger groups → account types' },
          { key: 'vouchers', label: 'Voucher types → documents' },
        ]"
        :key="t.key"
        class="border-b-2 px-3 py-2"
        :class="tab === t.key ? 'border-primary font-medium text-primary' : 'border-transparent text-gray-500'"
        @click="tab = t.key as typeof tab"
      >
        {{ t.label }}
      </button>
    </div>

    <div v-if="tab === 'entities'">
      <div class="mb-3">
        <label class="form-label">Module</label>
        <select v-model="fModule" class="form-input w-56">
          <option value="">All modules</option>
          <option v-for="module in catalogue?.modules ?? []" :key="module" :value="module">
            {{ module }}
          </option>
        </select>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
            <tr>
              <th class="px-4 py-2">In Tally</th>
              <th class="px-4 py-2">XML tag</th>
              <th class="px-4 py-2">Becomes here</th>
              <th class="px-4 py-2">Module</th>
              <th class="px-4 py-2">Coverage</th>
              <th class="px-4 py-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="6" class="px-4 py-6 text-center text-gray-400">Loading…</td>
            </tr>
            <tr v-for="row in entities" :key="row.key" class="border-b border-gray-100 align-top">
              <td class="px-4 py-2 font-medium text-gray-900">{{ row.label }}</td>
              <td class="px-4 py-2 font-mono text-xs text-gray-500">{{ row.tally_tag }}</td>
              <td class="px-4 py-2 text-gray-700">{{ row.target }}</td>
              <td class="px-4 py-2 text-gray-500">{{ row.module }}</td>
              <td class="px-4 py-2">
                <span
                  class="rounded-full px-2 py-0.5 text-xs font-medium"
                  :class="SUPPORT_TONE[row.support]"
                >
                  {{ SUPPORT_LABEL[row.support] }}
                </span>
              </td>
              <td class="max-w-md px-4 py-2 text-xs text-gray-500">{{ row.notes || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-else-if="tab === 'groups'" class="rounded-lg border border-gray-200 bg-white shadow-sm">
      <p class="border-b border-gray-100 px-4 py-3 text-xs text-gray-500">
        Tally ships 28 reserved ledger groups. Where a ledger sits decides what kind of account
        it becomes here — and whether it is also a Customer or a Supplier. Your own sub-groups
        inherit the classification of the reserved group above them.
      </p>
      <table class="min-w-full text-sm">
        <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
          <tr>
            <th class="px-4 py-2">Tally group</th>
            <th class="px-4 py-2">Root type</th>
            <th class="px-4 py-2">Account type</th>
            <th class="px-4 py-2">Also becomes</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="[name, spec] in tally" :key="name" class="border-b border-gray-100">
            <td class="px-4 py-2 font-medium text-gray-900">{{ name }}</td>
            <td class="px-4 py-2 text-gray-700">{{ spec.root_type }}</td>
            <td class="px-4 py-2 text-gray-500">{{ spec.account_type || "—" }}</td>
            <td class="px-4 py-2">
              <span v-if="spec.party_type" class="font-medium text-blue-700">
                {{ spec.party_type }}
              </span>
              <span v-else class="text-gray-300">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="rounded-lg border border-gray-200 bg-white shadow-sm">
      <p class="border-b border-gray-100 px-4 py-3 text-xs text-gray-500">
        Voucher types you created yourself in Tally (say "Cash Sales") inherit the mapping of the
        reserved type they were built on.
      </p>
      <table class="min-w-full text-sm">
        <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
          <tr>
            <th class="px-4 py-2">Tally voucher type</th>
            <th class="px-4 py-2">Becomes</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="[name, doctype] in vouchers" :key="name" class="border-b border-gray-100">
            <td class="px-4 py-2 font-medium text-gray-900">{{ name }}</td>
            <td class="px-4 py-2 text-gray-700">
              <span v-if="doctype !== '—'">{{ doctype }}</span>
              <span v-else class="text-gray-400">not posted (staged for review)</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
