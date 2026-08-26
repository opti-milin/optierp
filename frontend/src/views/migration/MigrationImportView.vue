<script setup lang="ts">
// Data Migration home: upload an export and see every past import.
// The upload is sent base64-encoded so the backend gets the exact bytes: Tally
// writes UTF-16 XML as often as UTF-8 and needs sniffing, and an .xlsx is a
// binary ZIP that would not survive being treated as text at all.
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, downloadFile } from "@/api/client";
import { formatDate } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type {
  MigrationImport,
  MigrationImportListItem,
  MigrationSyncState,
  WorkspaceStats,
} from "@/types/migration";

const router = useRouter();
const route = useRoute();

// Arriving from a module's "Import data" button: pre-select that module's
// entities on the new session so the tester isn't asked to tick 30 boxes.
const focusModule = computed(() => (route.query.module as string) || "");
const focusEntities = computed(() =>
  String(route.query.entities ?? "").split(",").filter(Boolean),
);

const rows = ref<MigrationImportListItem[]>([]);
const stats = ref<WorkspaceStats | null>(null);
const sync = ref<MigrationSyncState | null>(null);
const loading = ref(false);
const uploading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const file = ref<File | null>(null);
const fTitle = ref("");
const fOpeningDate = ref("");
const fSubmit = ref(true);

const canUpload = computed(() => !!file.value && !uploading.value);

async function downloadTemplate(): Promise<void> {
  error.value = null;
  try {
    await downloadFile("/migration/template.xlsx", "OptiERP-import-template.xlsx");
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

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
      await api.post<MigrationImport>("/migration/imports", {
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
      await api.post(`/migration/imports/${created.id}/entities`, { selections });
    }
    void router.push(`/data-migration/imports/${created.id}`);
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
    const [list, workspace, syncState] = await Promise.all([
      api.get<MigrationImportListItem[]>("/migration/imports"),
      api.get<WorkspaceStats>("/migration/workspace"),
      api.get<MigrationSyncState>("/migration/sync-state"),
    ]);
    rows.value = list.data;
    stats.value = workspace.data;
    sync.value = syncState.data;
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
      <h1 class="text-xl font-semibold text-gray-900">Data Migration</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        Bring your books across — masters, opening balances and vouchers — so you can run them
        here and compare against the old system. Nothing is written to your ledgers until you
        review what was found and press Run.
        <RouterLink to="/data-migration/coverage" class="text-blue-600 hover:underline">
          See exactly what maps to what</RouterLink>,
        <RouterLink to="/data-migration/sources" class="text-blue-600 hover:underline">
          which systems are supported</RouterLink>, or
        <RouterLink to="/data-migration/guides/tally" class="text-blue-600 hover:underline">
          how to export from Tally</RouterLink>.
      </p>
    </div>

    <div v-if="stats" class="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
      <div
        v-for="card in stats.cards"
        :key="card.label"
        class="rounded-lg border border-gray-200 bg-white p-4"
      >
        <div class="text-xs uppercase text-gray-400">{{ card.label }}</div>
        <div
          class="text-2xl font-semibold"
          :class="card.label === 'Names Unmapped' && card.value ? 'text-amber-600' : 'text-gray-900'"
        >
          {{ card.value }}
        </div>
      </div>
    </div>

    <!-- What we already hold from Tally, and how to fetch only what's new.
         Tally-only on purpose: ALTERID is Tally's own change counter, and no
         spreadsheet carries anything equivalent. The section stays empty for a
         company that has only ever imported workbooks. -->
    <div
      v-if="sync?.companies.length"
      class="mb-6 rounded-lg border border-gray-200 bg-white p-4 text-sm"
    >
      <h2 class="font-medium text-gray-900">Already imported from Tally</h2>
      <table class="mt-2 w-full text-left">
        <thead class="text-xs uppercase text-gray-400">
          <tr>
            <th class="py-1 font-medium">Tally company</th>
            <th class="py-1 font-medium">Period held</th>
            <th class="py-1 text-right font-medium">Documents</th>
            <th class="py-1 text-right font-medium">Last revision</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in sync.companies" :key="c.source_company_guid ?? c.source_company_name ?? '?'">
            <td class="py-1 text-gray-900">
              {{ c.source_company_name || "Unnamed Tally company" }}
            </td>
            <td class="py-1 text-gray-600">
              <span v-if="c.earliest_voucher_date">
                {{ formatDate(c.earliest_voucher_date) }} →
                {{ formatDate(c.latest_voucher_date) }}
              </span>
              <span v-else class="text-gray-400">—</span>
            </td>
            <td class="py-1 text-right tabular-nums text-gray-900">
              {{ c.documents_imported }}
            </td>
            <td class="py-1 text-right tabular-nums text-gray-900">
              {{ c.last_alter_id ?? "—" }}
            </td>
          </tr>
        </tbody>
      </table>
      <p class="mt-2 text-xs text-gray-500">
        Importing the same period again is safe — vouchers you already have are recognised
        and skipped, never posted twice. "Last revision" is Tally's
        <code>ALTERID</code>: everything above it is what has changed since, so you can
        export a narrower range next time.
      </p>
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
      <p class="mb-4 max-w-4xl text-xs text-gray-500">
        <strong>From Tally:</strong> Gateway of Tally → Export → Format: <strong>XML</strong>.
        Export <em>All Masters</em> first, then the <em>Day Book</em> for the period you want.
        <br />
        <strong>From anything else:</strong> upload the <strong>.xlsx</strong> your system
        exports. Tally workbooks and Zoho Books backups are recognised on sight; anything else
        gets a mapping step where you say which sheet is what — save that once and the next file
        is recognised too. No usable export?
        <button
          type="button"
          class="text-blue-600 hover:underline"
          @click="downloadTemplate"
        >Download the OptiERP template</button>
        and paste your data into it.
      </p>
      <div class="grid grid-cols-1 gap-4 md:grid-cols-4">
        <div>
          <label class="form-label">Export file*</label>
          <input
            type="file"
            accept=".xml,.xlsx,.xlsm,.csv,.txt,.tsv"
            class="form-input"
            @change="onFile"
          />
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
            <th class="px-4 py-2">Source company</th>
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
              No imports yet. Upload an export above to start.
            </td>
          </tr>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="cursor-pointer border-b border-gray-100 hover:bg-gray-50"
            @click="router.push(`/data-migration/imports/${row.id}`)"
          >
            <td class="px-4 py-2">
              <div class="font-medium text-gray-900">{{ row.title || row.name }}</div>
              <div class="text-xs text-gray-400">{{ row.name }} · {{ row.source_type }}</div>
            </td>
            <td class="px-4 py-2 text-gray-700">{{ row.source_company_name || "—" }}</td>
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
