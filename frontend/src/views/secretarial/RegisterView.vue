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
    </div>

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
