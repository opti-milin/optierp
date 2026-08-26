<script setup lang="ts">
// "Can I bring my books over from X?" — answered honestly.
//
// Three tiers, and the page is laid out as them: shapes we recognise on sight,
// shapes this company has taught us, and the template for everything else. The
// last one is what makes the answer "yes" for a system nobody has profiled,
// which is most of them.
import { computed, onMounted, ref } from "vue";
import { api, downloadFile } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { MigrationSourceCatalogue } from "@/types/migration";

const data = ref<MigrationSourceCatalogue | null>(null);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const busy = ref("");
const expanded = ref<string | null>(null);

const builtins = computed(() => data.value?.profiles ?? []);
const saved = computed(() => data.value?.saved ?? []);
const kindLabel = computed(() =>
  Object.fromEntries((data.value?.kinds ?? []).map((k) => [k.key, k.label])),
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    data.value = (await api.get<MigrationSourceCatalogue>("/migration/sources")).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function remove(id: string, label: string): Promise<void> {
  if (!window.confirm(`Delete the saved mapping "${label}"? Imports already run keep theirs.`))
    return;
  busy.value = id;
  error.value = null;
  try {
    await api.delete(`/migration/sources/${id}`);
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = "";
  }
}

async function download(entities?: string): Promise<void> {
  error.value = null;
  busy.value = "template";
  try {
    await downloadFile(
      `/migration/template.xlsx${entities ? `?entities=${entities}` : ""}`,
      "OptiERP-import-template.xlsx",
    );
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = "";
  }
}

onMounted(load);
</script>

<template>
  <div>
    <div class="mb-4">
      <RouterLink to="/data-migration/imports" class="text-sm text-blue-600 hover:underline">
        ← Imports
      </RouterLink>
      <h1 class="text-xl font-semibold text-gray-900">Where your books can come from</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        Tally's own XML export needs nothing set up — the format carries its own structure.
        A spreadsheet needs to be told which sheet is what. These are the shapes already
        known; anything else is mapped once, saved, and then recognised like the rest.
      </p>
    </div>

    <div v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      {{ error.detail }}
    </div>
    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <template v-else-if="data">
      <!-- The template: the answer for everything unprofiled -->
      <div class="mb-6 rounded-lg border border-blue-200 bg-blue-50 p-4">
        <h2 class="text-sm font-semibold text-blue-900">
          Your system isn't listed? Use the OptiERP template
        </h2>
        <p class="mt-1 max-w-3xl text-sm text-blue-800">
          One sheet per thing OptiERP can import, the exact headings the importer reads, a note
          on every column, and a worked example for each. Export whatever your system gives you,
          paste it in, and upload — a filled template skips the mapping step entirely.
        </p>
        <div class="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" class="btn-primary" :disabled="busy === 'template'" @click="download()">
            {{ busy === "template" ? "Preparing…" : "Download template (.xlsx)" }}
          </button>
          <button
            type="button"
            class="text-sm text-blue-700 hover:underline"
            @click="download('ledger,customer,supplier,payment_terms,address,contact')"
          >
            Just the accounts &amp; parties
          </button>
          <button
            type="button"
            class="text-sm text-blue-700 hover:underline"
            @click="download('stock_item,godown,price_list')"
          >
            Just items &amp; warehouses
          </button>
          <span class="text-xs text-blue-700">version {{ data.template_version }}</span>
        </div>
      </div>

      <!-- Shapes this company has saved -->
      <div v-if="saved.length" class="mb-6">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Your saved mappings</h2>
        <div class="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <table class="min-w-full text-sm">
            <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
              <tr>
                <th class="px-4 py-2 font-medium">Mapping</th>
                <th class="px-4 py-2 font-medium">System</th>
                <th class="px-4 py-2 text-right font-medium">Used</th>
                <th class="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr v-for="row in saved" :key="row.id">
                <td class="px-4 py-2">
                  <div class="font-medium text-gray-900">{{ row.label }}</div>
                  <div v-if="row.notes" class="text-xs text-gray-500">{{ row.notes }}</div>
                </td>
                <td class="px-4 py-2 text-gray-600">{{ row.app }}</td>
                <td class="px-4 py-2 text-right text-gray-600">{{ row.use_count }}</td>
                <td class="px-4 py-2 text-right">
                  <button
                    class="text-xs text-red-600 hover:underline"
                    :disabled="busy === row.id"
                    @click="remove(row.id, row.label)"
                  >
                    {{ busy === row.id ? "Deleting…" : "Delete" }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Built-in shapes -->
      <h2 class="mb-2 text-sm font-semibold text-gray-900">Recognised automatically</h2>
      <div class="space-y-2">
        <div v-for="p in builtins" :key="p.key" class="rounded-lg border border-gray-200 bg-white">
          <button
            type="button"
            class="flex w-full items-start justify-between gap-4 px-4 py-3 text-left hover:bg-gray-50"
            @click="expanded = expanded === p.key ? null : p.key"
          >
            <div class="min-w-0">
              <div class="font-medium text-gray-900">{{ p.label }}</div>
              <p class="mt-0.5 max-w-3xl text-sm text-gray-500">{{ p.notes }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-3">
              <span class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">{{ p.app }}</span>
              <span class="text-gray-400">{{ expanded === p.key ? "▾" : "▸" }}</span>
            </div>
          </button>

          <div v-if="expanded === p.key" class="border-t border-gray-200 px-4 py-3">
            <table class="min-w-full text-sm">
              <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
                <tr>
                  <th class="py-1 pr-4 font-medium">Sheet</th>
                  <th class="py-1 pr-4 font-medium">Read as</th>
                  <th class="py-1 font-medium">Notes</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100">
                <tr v-for="sheet in p.sheets" :key="sheet.sheet">
                  <td class="py-1 pr-4 font-mono text-xs text-gray-700">{{ sheet.sheet }}</td>
                  <td class="py-1 pr-4">
                    <span
                      v-if="sheet.kind === 'reference'"
                      class="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600"
                    >
                      not imported
                    </span>
                    <span v-else class="text-gray-800">
                      {{ kindLabel[sheet.kind] ?? sheet.kind }}
                    </span>
                    <span v-if="sheet.children.length" class="text-xs text-gray-500">
                      + {{ sheet.children.map((c) => c.sheet).join(", ") }}
                    </span>
                  </td>
                  <td class="py-1 text-xs text-gray-500">{{ sheet.reason }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
