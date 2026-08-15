<script setup lang="ts">
// Data Migration home: upload a Tally export and see every past import.
// The upload is sent base64-encoded so the backend gets the exact bytes and can
// sniff Tally's encoding itself (Tally writes UTF-16 as often as UTF-8).
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { TallyImport, TallyImportListItem, TallyWorkspaceStats } from "@/types/tally";

const router = useRouter();
const route = useRoute();

// Arriving from a module's "Import from Tally" button: pre-select that module's
// entities on the new session so the tester isn't asked to tick 30 boxes.
const focusModule = computed(() => (route.query.module as string) || "");
const focusEntities = computed(() =>
  String(route.query.entities ?? "").split(",").filter(Boolean),
);

const rows = ref<TallyImportListItem[]>([]);
const stats = ref<TallyWorkspaceStats | null>(null);
const loading = ref(false);
const uploading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const file = ref<File | null>(null);
const fTitle = ref("");
const fOpeningDate = ref("");
const fSubmit = ref(true);

const canUpload = computed(() => !!file.value && !uploading.value);

const STATUS_TONE: Record<string, string> = {
  Imported: "bg-green-100 text-green-800",
  "Partially Imported": "bg-amber-100 text-amber-800",
  Failed: "bg-red-100 text-red-800",
  "Rolled Back": "bg-gray-200 text-gray-700",
  Importing: "bg-blue-100 text-blue-800",
};
function tone(status: string): string {
  return STATUS_TONE[status] ?? "bg-blue-50 text-blue-700";
}

function onFile(event: Event): void {
  const input = event.target as HTMLInputElement;
  file.value = input.files?.[0] ?? null;
  if (file.value && !fTitle.value) fTitle.value = file.value.name;
}

/** Read the picked file as base64 without assuming a text encoding. */
function readBase64(target: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error("Could not read the file"));
    reader.readAsDataURL(target);
  });
}

async function upload(): Promise<void> {
  if (!file.value) return;
  uploading.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<TallyImport>("/tally/imports", {
        file_name: file.value.name,
        content_base64: await readBase64(file.value),
        title: fTitle.value || file.value.name,
        opening_date: fOpeningDate.value || null,
        options: { submit_vouchers: fSubmit.value },
      })
    ).data;
    if (focusEntities.value.length) {
      // Everything the file contained is on by default; narrow it to the module
      // the tester came from, keeping the masters those documents depend on.
      const selections: Record<string, boolean> = {};
      for (const entity of created.entities) {
        selections[entity.entity_key] =
          focusEntities.value.includes(entity.entity_key) || entity.stage < 50;
      }
      await api.post(`/tally/imports/${created.id}/entities`, { selections });
    }
    void router.push(`/tally/imports/${created.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    uploading.value = false;
  }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [list, workspace] = await Promise.all([
      api.get<TallyImportListItem[]>("/tally/imports"),
      api.get<TallyWorkspaceStats>("/tally/workspace"),
    ]);
    rows.value = list.data;
    stats.value = workspace.data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">Import from Tally</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        Bring a Tally company across — masters, opening balances and vouchers — so you can run
        the same books here and compare. Nothing is written to your ledgers until you review
        what was found and press Run.
        <RouterLink to="/tally/coverage" class="text-blue-600 hover:underline">
          See exactly what maps to what
        </RouterLink>.
      </p>
    </div>

    <div v-if="stats" class="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Imports</div>
        <div class="text-2xl font-semibold text-gray-900">{{ stats.total_imports }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Documents imported</div>
        <div class="text-2xl font-semibold text-gray-900">{{ stats.documents_imported }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Names still unmapped</div>
        <div class="text-2xl font-semibold" :class="stats.unmapped_names ? 'text-amber-600' : 'text-gray-900'">
          {{ stats.unmapped_names }}
        </div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Entities supported</div>
        <div class="text-2xl font-semibold text-gray-900">{{ stats.supported_entities }}</div>
      </div>
    </div>

    <p
      v-if="focusModule"
      class="mb-4 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900"
    >
      Importing for <strong>{{ focusModule }}</strong>. The masters every document depends on
      (ledgers, items, godowns) come across too — a sales voucher cannot post without its
      customer and its tax ledger. You can change the selection on the next screen.
    </p>

    <form class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="upload">
      <h2 class="mb-1 text-sm font-semibold text-gray-900">New import</h2>
      <p class="mb-4 text-xs text-gray-500">
        In Tally: <strong>Gateway of Tally → Export → Format: XML</strong>. Export
        <em>All Masters</em> first, then the <em>Day Book</em> for the period you want.
        A Day Book CSV also works, but carries no bill references or godown detail.
      </p>
      <div class="grid grid-cols-1 gap-4 md:grid-cols-4">
        <div>
          <label class="form-label">Tally export file*</label>
          <input type="file" accept=".xml,.csv,.txt,.tsv" class="form-input" @change="onFile" />
        </div>
        <div>
          <label class="form-label">Label for this import</label>
          <input v-model="fTitle" type="text" class="form-input" placeholder="FY 2025-26 masters" />
        </div>
        <div>
          <label class="form-label" title="Opening balances are booked on this date">
            Opening date
          </label>
          <input v-model="fOpeningDate" type="date" class="form-input" />
        </div>
        <div class="flex items-end">
          <label class="flex items-center gap-2 text-sm text-gray-700">
            <input v-model="fSubmit" type="checkbox" />
            Submit vouchers on import (posts GL &amp; stock)
          </label>
        </div>
      </div>
      <div class="mt-4">
        <button class="btn-primary" type="submit" :disabled="!canUpload">
          {{ uploading ? "Reading file…" : "Upload &amp; parse" }}
        </button>
      </div>
    </form>

    <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div class="rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
          <tr>
            <th class="px-4 py-2">Import</th>
            <th class="px-4 py-2">Tally company</th>
            <th class="px-4 py-2">Period</th>
            <th class="px-4 py-2 text-right">Records</th>
            <th class="px-4 py-2 text-right">Imported</th>
            <th class="px-4 py-2 text-right">Failed</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="7" class="px-4 py-6 text-center text-gray-400">Loading…</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="7" class="px-4 py-6 text-center text-gray-400">
              No imports yet. Upload a Tally export above to start.
            </td>
          </tr>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="cursor-pointer border-b border-gray-100 hover:bg-gray-50"
            @click="router.push(`/tally/imports/${row.id}`)"
          >
            <td class="px-4 py-2">
              <div class="font-medium text-gray-900">{{ row.title || row.name }}</div>
              <div class="text-xs text-gray-400">{{ row.name }} · {{ row.source_type }}</div>
            </td>
            <td class="px-4 py-2 text-gray-700">{{ row.tally_company_name || "—" }}</td>
            <td class="px-4 py-2 text-gray-700">
              <span v-if="row.from_date">{{ formatDate(row.from_date) }} → {{ formatDate(row.to_date) }}</span>
              <span v-else>—</span>
            </td>
            <td class="px-4 py-2 text-right tabular-nums">{{ row.total_records }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-green-700">{{ row.imported_count }}</td>
            <td class="px-4 py-2 text-right tabular-nums" :class="row.error_count ? 'text-red-600' : 'text-gray-400'">
              {{ row.error_count }}
            </td>
            <td class="px-4 py-2">
              <span class="rounded-full px-2 py-0.5 text-xs font-medium" :class="tone(row.status)">
                {{ row.status }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
