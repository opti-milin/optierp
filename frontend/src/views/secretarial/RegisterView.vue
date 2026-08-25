<script setup lang="ts">
// One page for every statutory register. The backend serves them through a single
// slug-keyed router, so the UI follows: the column set is derived from the rows,
// and the filter → search → export controls are identical everywhere. That
// sameness is the point — an inspector asks for "the register", and each one
// should behave the same way.
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { RegisterMeta } from "@/types/secretarial";

const route = useRoute();
const store = useSecretarialStore();

const slug = computed(() => String(route.params.slug || "members"));
const registers = ref<RegisterMeta[]>([]);
const rows = ref<Record<string, unknown>[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const search = ref("");
const fy = ref("");
const activeOnly = ref(false);
const syncResult = ref<string | null>(null);
const adding = ref(false);
const saving = ref(false);
const form = ref<Record<string, string>>({});

interface FieldSpec {
  key: string;
  label: string;
  type: "text" | "number" | "date" | "select";
  required?: boolean;
  options?: string[];
}

const ADD_FIELDS: Record<string, FieldSpec[]> = {
  members: [
    { key: "member_name", label: "Member name", type: "text", required: true },
    { key: "folio_no", label: "Folio", type: "text", required: true },
    {
      key: "member_type",
      label: "Type",
      type: "select",
      options: ["individual", "body_corporate", "trust", "huf", "nominee", "government"],
    },
    { key: "shares_held", label: "Shares held", type: "number" },
    { key: "share_class", label: "Share class", type: "text" },
    { key: "joined_on", label: "Joined on", type: "date" },
    { key: "pan", label: "PAN", type: "text" },
  ],
  committees: [
    { key: "committee_name", label: "Committee name", type: "text", required: true },
    { key: "committee_type", label: "Type", type: "text" },
    { key: "constituted_on", label: "Constituted on", type: "date" },
    { key: "quorum", label: "Quorum", type: "number" },
  ],
  "group-links": [
    { key: "related_entity_name", label: "Related entity", type: "text", required: true },
    { key: "related_cin", label: "CIN", type: "text" },
    {
      key: "relation",
      label: "Relation",
      type: "select",
      required: true,
      options: ["holding", "subsidiary", "associate", "joint_venture", "fellow_subsidiary"],
    },
    { key: "shareholding_pct", label: "Shareholding %", type: "number" },
    { key: "valid_from", label: "Valid from", type: "date", required: true },
  ],
  "related-parties": [
    { key: "party_name", label: "Party name", type: "text", required: true },
    {
      key: "basis",
      label: "Basis",
      type: "select",
      required: true,
      options: ["director", "kmp", "member", "group", "relative", "manual"],
    },
    { key: "relationship_note", label: "Note", type: "text" },
    { key: "valid_from", label: "Valid from", type: "date" },
  ],
  "beneficial-owners": [
    { key: "person_name", label: "Name", type: "text", required: true },
    {
      key: "classification",
      label: "Classification",
      type: "select",
      required: true,
      options: ["bo", "sbo", "ubo"],
    },
    { key: "holding_pct", label: "Holding %", type: "number" },
    { key: "valid_from", label: "Valid from", type: "date", required: true },
    { key: "declared_on", label: "Declared on", type: "date" },
  ],
  auditors: [
    { key: "firm_name", label: "Firm name", type: "text", required: true },
    { key: "registration_no", label: "Registration no.", type: "text" },
    {
      key: "auditor_type",
      label: "Type",
      type: "select",
      options: ["statutory", "internal", "secretarial", "cost", "tax"],
    },
    { key: "appointed_on", label: "Appointed on", type: "date" },
    { key: "email", label: "Email", type: "text" },
  ],
  charges: [
    { key: "holder_name", label: "Charge holder", type: "text", required: true },
    { key: "charge_type", label: "Type", type: "text" },
    { key: "amount_secured", label: "Amount secured", type: "number" },
    { key: "created_on", label: "Created on", type: "date" },
    { key: "srn", label: "SRN", type: "text" },
  ],
  dscs: [
    { key: "holder_name", label: "Holder", type: "text", required: true },
    { key: "serial_no", label: "Serial no.", type: "text" },
    { key: "issuing_authority", label: "Issuing authority", type: "text" },
    { key: "issued_on", label: "Issued on", type: "date" },
    { key: "expires_on", label: "Expires on", type: "date" },
  ],
};

const addFields = computed(() => ADD_FIELDS[slug.value] ?? []);

const label = computed(
  () => registers.value.find((r) => r.slug === slug.value)?.label ?? slug.value,
);

// Columns that carry plumbing rather than meaning — hidden from every register.
const HIDDEN = new Set([
  "id",
  "company_id",
  "entity_id",
  "owner",
  "modified_by",
  "docstatus",
  "creation",
  "modified",
  "person_id",
  "shareholder_id",
  "related_entity_id",
]);

const columns = computed(() => {
  if (!rows.value.length) return [];
  return Object.keys(rows.value[0]).filter((k) => !HIDDEN.has(k));
});

function humanise(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function render(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function loadRegisters(): Promise<void> {
  registers.value = (await api.get<RegisterMeta[]>("/secretarial/registers")).data;
}

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    const resp = await api.get<ListResponse<Record<string, unknown>>>(
      `/secretarial/registers/${slug.value}`,
      {
        params: {
          entity_id: store.entityId,
          search: search.value || undefined,
          fy: fy.value || undefined,
          active_only: activeOnly.value || undefined,
          page_size: 100,
        },
      },
    );
    rows.value = resp.data.items;
    total.value = resp.data.total;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

function exportUrl(): string {
  const params = new URLSearchParams({ entity_id: store.entityId ?? "" });
  if (fy.value) params.set("fy", fy.value);
  if (activeOnly.value) params.set("active_only", "true");
  return `/api/v1/secretarial/registers/${slug.value}/export?${params}`;
}

function openAdd(): void {
  adding.value = true;
  form.value = {};
}

async function saveRow(): Promise<void> {
  if (!store.entityId) return;
  saving.value = true;
  error.value = null;
  try {
    const body: Record<string, unknown> = { entity_id: store.entityId };
    for (const field of addFields.value) {
      const raw = form.value[field.key];
      if (raw === undefined || raw === "") {
        if (field.required) {
          error.value = { detail: `${field.label} is required.`, code: "required", field: field.key };
          saving.value = false;
          return;
        }
        continue;
      }
      body[field.key] = field.type === "number" ? Number(raw) : raw;
    }
    await api.post(`/secretarial/registers/${slug.value}`, body);
    adding.value = false;
    form.value = {};
    await load();
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    saving.value = false;
  }
}

async function syncRelatedParties(): Promise<void> {
  if (!store.entityId) return;
  const resp = await api.post<{ added: number; updated: number; removed: number; kept_manual: number }>(
    "/secretarial/registers/related-parties/sync",
    null,
    { params: { entity_id: store.entityId } },
  );
  const r = resp.data;
  syncResult.value = `${r.added} added, ${r.updated} refreshed, ${r.removed} removed, ${r.kept_manual} manual rows left alone.`;
  await load();
}

watch(
  () => [slug.value, store.entityId],
  async () => {
    syncResult.value = null;
    adding.value = false;
    await load();
  },
);

(async () => {
  await store.load();
  await loadRegisters();
  await load();
})();
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">{{ label }}</h1>
      <p class="text-sm text-gray-500">
        {{ store.entity?.entity_name ?? "No entity selected" }} · {{ total }} row{{
          total === 1 ? "" : "s"
        }}
      </p>
    </div>

    <div class="mb-3 flex flex-wrap items-end gap-2">
      <div>
        <label class="block text-xs text-gray-500">Register</label>
        <select
          :value="slug"
          class="rounded border border-gray-300 px-2 py-1.5 text-sm"
          @change="$router.push(`/secretarial/registers/${($event.target as HTMLSelectElement).value}`)"
        >
          <option v-for="r in registers" :key="r.slug" :value="r.slug">{{ r.label }}</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-500">Search</label>
        <input
          v-model="search"
          class="rounded border border-gray-300 px-2 py-1.5 text-sm"
          placeholder="Name, folio, number…"
          @keyup.enter="load"
        />
      </div>
      <div>
        <label class="block text-xs text-gray-500">Financial year</label>
        <input
          v-model="fy"
          class="w-28 rounded border border-gray-300 px-2 py-1.5 text-sm"
          placeholder="2025-26"
          @keyup.enter="load"
        />
      </div>
      <label class="flex items-center gap-1.5 pb-1.5 text-sm text-gray-600">
        <input v-model="activeOnly" type="checkbox" @change="load" />
        Current only
      </label>
      <button
        class="rounded bg-gray-800 px-3 py-1.5 text-sm text-white hover:bg-gray-700"
        @click="load"
      >
        Apply
      </button>
      <a
        :href="exportUrl()"
        class="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
      >
        Export CSV
      </a>
      <button
        v-if="slug === 'related-parties'"
        class="rounded border border-blue-300 px-3 py-1.5 text-sm text-blue-700 hover:bg-blue-50"
        title="Rebuild from directors, group links and 20%+ members. Rows you added by hand are kept."
        @click="syncRelatedParties"
      >
        Sync from master data
      </button>
      <button
        v-if="addFields.length"
        class="rounded bg-blue-700 px-3 py-1.5 text-sm text-white hover:bg-blue-600"
        @click="openAdd"
      >
        Add row
      </button>
    </div>

    <form
      v-if="adding"
      class="mb-3 grid grid-cols-1 gap-2 rounded-lg border border-blue-200 bg-blue-50 p-3 sm:grid-cols-2 lg:grid-cols-3"
      @submit.prevent="saveRow"
    >
      <div v-for="field in addFields" :key="field.key">
        <label class="block text-xs text-gray-600">{{ field.label }}</label>
        <select
          v-if="field.type === 'select'"
          v-model="form[field.key]"
          class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
        >
          <option value="">—</option>
          <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
        </select>
        <input
          v-else
          v-model="form[field.key]"
          :type="field.type"
          class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
          :required="field.required"
        />
      </div>
      <div class="flex items-end gap-2 sm:col-span-2">
        <button type="submit" class="rounded bg-gray-800 px-3 py-1.5 text-sm text-white" :disabled="saving">
          {{ saving ? "Saving…" : "Save" }}
        </button>
        <button type="button" class="rounded border border-gray-300 px-3 py-1.5 text-sm" @click="adding = false">
          Cancel
        </button>
      </div>
    </form>

    <p v-if="syncResult" class="mb-3 rounded border border-blue-200 bg-blue-50 p-2 text-sm text-blue-800">
      {{ syncResult }}
    </p>
    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th v-for="c in columns" :key="c" class="whitespace-nowrap px-3 py-2">
              {{ humanise(c) }}
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="loading">
            <td :colspan="Math.max(columns.length, 1)" class="px-3 py-6 text-center text-gray-400">
              Loading…
            </td>
          </tr>
          <tr v-else-if="!rows.length">
            <td :colspan="Math.max(columns.length, 1)" class="px-3 py-6 text-center text-gray-400">
              This register is empty.
            </td>
          </tr>
          <tr v-for="(row, i) in rows" :key="i" class="hover:bg-gray-50">
            <td v-for="c in columns" :key="c" class="whitespace-nowrap px-3 py-2 text-gray-700">
              {{ render(row[c]) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
