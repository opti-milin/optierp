<script setup lang="ts">
// The import wizard. Each step is a separate backend call so a tester can stop,
// check something in the old system, and come back:
//   0 Sheets   — spreadsheets only: which sheet is what, which column is which
//   1 Review   — what was found in the file, per entity
//   2 Map      — what each name in the file will become (least-confident first)
//   3 Dry run  — how many documents, and what is still unresolved
//   4 Run      — create the documents; roll back if the numbers don't match
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import MigrationMappingEditor from "@/views/migration/MigrationMappingEditor.vue";
import MigrationMappingStep from "@/views/migration/MigrationMappingStep.vue";
import MigrationRecordTable from "@/views/migration/MigrationRecordTable.vue";
import type { ErrorEnvelope } from "@/types/core";
import type {
  MigrationAutoMapResult,
  MigrationImportProgress,
  MigrationImportSummary,
  MigrationRollbackResult,
  MigrationValidateResult,
} from "@/types/migration";

const props = defineProps<{ id: string }>();

const summary = ref<MigrationImportSummary | null>(null);
const validation = ref<MigrationValidateResult | null>(null);
const automap = ref<MigrationAutoMapResult | null>(null);
const loading = ref(false);
const busy = ref("");
const error = ref<ErrorEnvelope | null>(null);
const tab = ref<"sheets" | "review" | "map" | "records" | "log">("review");

const session = computed(() => summary.value?.session ?? null);
const entities = computed(() => summary.value?.entities ?? []);

// Only a workbook has sheets to argue about; Tally XML and CSV carry their own
// structure, so the step is hidden rather than shown empty.
const isSpreadsheet = computed(() => session.value?.source_type === "XLSX");
const tabs = computed(() => [
  ...(isSpreadsheet.value ? [{ key: "sheets", label: "Sheets & columns" }] : []),
  { key: "review", label: "What was found" },
  { key: "map", label: "Mappings" },
  { key: "records", label: "Records" },
  { key: "log", label: "Activity" },
]);

const SUPPORT_NOTE: Record<string, string> = {
  full: "Imported into a real record",
  partial: "Imported; some Tally fields have no home here",
  reference: "Kept for reference — never becomes a document",
  none: "No target module yet — counted so the gap is visible",
};
const SUPPORT_TONE: Record<string, string> = {
  full: "bg-green-50 text-green-700",
  partial: "bg-amber-50 text-amber-700",
  reference: "bg-gray-100 text-gray-600",
  none: "bg-red-50 text-red-700",
};

const canRun = computed(
  () => !!session.value && ["Parsed", "Mapped", "Validated"].includes(session.value.status),
);
// "Rolled Back" is included on purpose: rollback is idempotent, and an earlier
// attempt that cancelled nothing must be retryable. Hiding the button there left
// documents stranded with no way to undo them from the UI.
const canRollback = computed(
  () =>
    !!session.value &&
    ["Imported", "Partially Imported", "Failed", "Rolled Back"].includes(
      session.value.status,
    ),
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    summary.value = (
      await api.get<MigrationImportSummary>(`/migration/imports/${props.id}/summary`)
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function act(label: string, run: () => Promise<void>): Promise<void> {
  busy.value = label;
  error.value = null;
  try {
    await run();
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = "";
  }
}

const doAutoMap = () =>
  act("map", async () => {
    automap.value = (
      await api.post<MigrationAutoMapResult>(`/migration/imports/${props.id}/automap`)
    ).data;
    tab.value = "map";
  });

const doValidate = () =>
  act("validate", async () => {
    validation.value = (
      await api.post<MigrationValidateResult>(`/migration/imports/${props.id}/validate`)
    ).data;
  });

// The run happens in the background — a full year of vouchers takes far longer
// than a request should. Start it, then poll until it settles.
const progress = ref<MigrationImportProgress | null>(null);
let pollTimer: ReturnType<typeof setTimeout> | null = null;

// A dropped poll is not a failed import — the run continues on the server
// regardless. Giving up on the first error would strand the page on a stale
// "Importing" until someone refreshed, so tolerate a few before falling back to
// a reload, where the session status is the source of truth.
const POLL_RETRIES = 5;
let pollFailures = 0;

async function pollProgress(): Promise<void> {
  try {
    const { data } = await api.get<MigrationImportProgress>(`/migration/imports/${props.id}/status`);
    pollFailures = 0;
    progress.value = data;
    if (data.is_running) {
      pollTimer = setTimeout(pollProgress, 1000);
      return;
    }
  } catch {
    pollFailures += 1;
    if (pollFailures <= POLL_RETRIES) {
      // Back off a little on each miss rather than hammering a struggling server.
      pollTimer = setTimeout(pollProgress, 1000 * pollFailures);
      return;
    }
  }
  pollTimer = null;
  pollFailures = 0;
  await load();
}

const doRun = () =>
  act("run", async () => {
    await api.post(`/migration/imports/${props.id}/run`);
    tab.value = "records";
    void pollProgress();
  });

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer);
});

const doRollback = () =>
  act("rollback", async () => {
    const result = (
      await api.post<MigrationRollbackResult>(`/migration/imports/${props.id}/rollback`)
    ).data;
    if (result.failed.length) {
      error.value = {
        detail: `${result.cancelled} cancelled; these could not be: ${result.failed.join(", ")}`,
        code: "ROLLBACK_PARTIAL",
        field: null,
      };
    }
  });

async function toggleEntity(key: string, selected: boolean): Promise<void> {
  await act("entities", async () => {
    await api.post(`/migration/imports/${props.id}/entities`, {
      selections: { [key]: selected },
    });
  });
}

onMounted(async () => {
  await load();
  // A freshly parsed workbook opens on its mapping: whether we read the sheets
  // right is the first thing worth checking, and the only thing that is still
  // free to change.
  if (isSpreadsheet.value && session.value?.status === "Parsed") tab.value = "sheets";
  // Opening the page while a run is already going (a refresh, another tab, or
  // a colleague started it) must still show it moving.
  if (session.value?.status === "Importing") void pollProgress();
});
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <RouterLink to="/data-migration/imports" class="text-sm text-blue-600 hover:underline">
          ← All imports
        </RouterLink>
        <h1 class="text-xl font-semibold text-gray-900">
          {{ session?.title || session?.name || "Import" }}
        </h1>
        <p v-if="session" class="text-sm text-gray-500">
          {{ session.source_company_name || session.source_app || "Unnamed source" }} ·
          {{ session.source_type }}<template v-if="session.source_app && session.source_company_name">
            · {{ session.source_app }}</template> ·
          <span v-if="session.from_date">
            {{ formatDate(session.from_date) }} → {{ formatDate(session.to_date) }} ·
          </span>
          {{ session.total_records }} record(s) found
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn-secondary" :disabled="!!busy" @click="doAutoMap">
          {{ busy === "map" ? "Mapping…" : "1 · Auto-map names" }}
        </button>
        <button class="btn-secondary" :disabled="!!busy" @click="doValidate">
          {{ busy === "validate" ? "Checking…" : "2 · Dry run" }}
        </button>
        <button class="btn-primary" :disabled="!!busy || !canRun" @click="doRun">
          {{ busy === "run" ? "Importing…" : "3 · Run import" }}
        </button>
        <button
          v-if="canRollback"
          class="rounded border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50"
          :disabled="!!busy"
          @click="doRollback"
        >
          {{ busy === "rollback" ? "Rolling back…" : "Roll back" }}
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p
      v-if="session?.error_message"
      class="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700"
    >
      The run stopped: {{ session.error_message }}
    </p>

    <div v-if="session" class="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Status</div>
        <div class="text-lg font-semibold text-gray-900">{{ session.status }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Imported</div>
        <div class="text-lg font-semibold text-green-700">{{ session.imported_count }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Skipped</div>
        <div class="text-lg font-semibold text-gray-500">{{ session.skipped_count }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Failed</div>
        <div class="text-lg font-semibold" :class="session.error_count ? 'text-red-600' : 'text-gray-400'">
          {{ session.error_count }}
        </div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4">
        <div class="text-xs uppercase text-gray-400">Unmapped names</div>
        <div
          class="text-lg font-semibold"
          :class="summary?.unmapped_count ? 'text-amber-600' : 'text-gray-400'"
        >
          {{ summary?.unmapped_count ?? 0 }}
        </div>
      </div>
    </div>

    <div
      v-if="validation"
      class="mb-6 rounded-lg border p-4 text-sm"
      :class="validation.blockers.length ? 'border-red-200 bg-red-50' : 'border-blue-200 bg-blue-50'"
    >
      <div class="font-medium">
        Dry run: {{ validation.total_planned }} document(s) would be created.
      </div>
      <p v-if="validation.not_posting_count" class="mt-1 text-xs text-gray-600">
        {{ validation.not_posting_count }} voucher(s) in this file are cancelled or
        marked optional in Tally. They are reported but never posted, so they are not
        counted above.
      </p>
      <ul v-if="validation.blockers.length" class="mt-1 list-disc pl-5 text-red-700">
        <li v-for="blocker in validation.blockers" :key="blocker">{{ blocker }}</li>
      </ul>
      <div v-if="validation.unresolved_count" class="mt-2 text-amber-800">
        {{ validation.unresolved_count }} Tally name(s) referenced by vouchers are not mapped.
        Vouchers naming them will fail — fix them on the Mappings tab first.
        <div class="mt-1 text-xs">
          <span v-for="(names, kind) in validation.unresolved" :key="kind">
            <strong>{{ kind }}:</strong> {{ names.slice(0, 8).join(", ")
            }}{{ names.length > 8 ? ` +${names.length - 8} more` : "" }};
          </span>
        </div>
      </div>
      <div v-if="validation.duplicate_count" class="mt-2 text-amber-800">
        {{ validation.duplicate_count }} record(s) in this file were already imported into
        this company and will be skipped, so they are not counted above.
        <div class="mt-1 text-xs">
          <span v-for="(dup, i) in validation.duplicates.slice(0, 8)" :key="i">
            {{ dup.voucher_number || dup.target_name }} ({{ dup.target_doctype }}<span
              v-if="dup.imported_by"
            >
              · {{ dup.imported_by }}</span
            >);
          </span>
          <span v-if="validation.duplicate_count > 8">
            +{{ validation.duplicate_count - 8 }} more
          </span>
        </div>
      </div>
      <div
        v-if="validation.amendment_count"
        class="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-amber-900"
      >
        <div class="font-medium">
          {{ validation.amendment_count }} voucher(s) were edited in Tally after they were
          imported here.
        </div>
        <p class="mt-1 text-xs">
          These are not ordinary repeats — the two copies disagree. The documents here are
          left untouched; open each one, compare it with Tally, and if the Tally version is
          right, amend or cancel and re-enter it.
        </p>
        <ul class="mt-1 list-disc pl-5 text-xs">
          <li v-for="(a, i) in validation.amendments.slice(0, 8)" :key="i">
            {{ a.voucher_number || a.target_name }} ({{ a.target_doctype }}) — Tally revision
            {{ a.file_alter_id }}, imported at revision {{ a.imported_alter_id }}
          </li>
        </ul>
        <p v-if="validation.amendment_count > 8" class="mt-1 text-xs">
          +{{ validation.amendment_count - 8 }} more.
        </p>
      </div>
    </div>

    <!-- Live progress while the run works in the background. -->
    <div
      v-if="progress?.is_running"
      class="mb-6 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm"
    >
      <div class="flex items-baseline justify-between gap-3">
        <span class="font-medium text-blue-900">
          Importing{{ progress.current_entity ? ` — ${progress.current_entity}` : "" }}…
        </span>
        <span class="tabular-nums text-blue-800">
          {{ progress.processed }} / {{ progress.total_records }} ({{ progress.percent }}%)
        </span>
      </div>
      <div class="mt-2 h-2 overflow-hidden rounded bg-blue-100">
        <div
          class="h-full rounded bg-blue-600 transition-all duration-500"
          :style="{ width: `${progress.percent}%` }"
        ></div>
      </div>
      <p class="mt-2 text-xs text-blue-800">
        This runs on the server — you can leave this page and come back.
      </p>
    </div>

    <div class="mb-4 flex gap-1 border-b border-gray-200 text-sm">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="border-b-2 px-3 py-2"
        :class="tab === t.key ? 'border-primary font-medium text-primary' : 'border-transparent text-gray-500'"
        @click="tab = t.key as typeof tab"
      >
        {{ t.label }}
      </button>
    </div>

    <div v-if="tab === 'review'" class="rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
          <tr>
            <th class="w-10 px-4 py-2">Use</th>
            <th class="px-4 py-2">Tally entity</th>
            <th class="px-4 py-2">Becomes</th>
            <th class="px-4 py-2">Module</th>
            <th class="px-4 py-2 text-right">Found</th>
            <th class="px-4 py-2 text-right">Created</th>
            <th class="px-4 py-2 text-right">Matched</th>
            <th class="px-4 py-2 text-right">Skipped</th>
            <th class="px-4 py-2 text-right">Failed</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="9" class="px-4 py-6 text-center text-gray-400">Loading…</td>
          </tr>
          <tr v-for="entity in entities" :key="entity.id" class="border-b border-gray-100">
            <td class="px-4 py-2">
              <input
                type="checkbox"
                :checked="entity.selected"
                :disabled="!canRun || !!busy"
                @change="toggleEntity(entity.entity_key, ($event.target as HTMLInputElement).checked)"
              />
            </td>
            <td class="px-4 py-2 font-medium text-gray-900">{{ entity.label }}</td>
            <td class="px-4 py-2 text-gray-700">
              {{ entity.target_doctype }}
              <span
                class="ml-1 rounded px-1.5 py-0.5 text-[11px]"
                :class="SUPPORT_TONE[entity.support]"
                :title="SUPPORT_NOTE[entity.support]"
              >
                {{ entity.support }}
              </span>
            </td>
            <td class="px-4 py-2 text-gray-500">{{ entity.module }}</td>
            <td class="px-4 py-2 text-right tabular-nums">{{ entity.total }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-green-700">{{ entity.created }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-blue-700">{{ entity.updated }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-gray-400">{{ entity.skipped }}</td>
            <td
              class="px-4 py-2 text-right tabular-nums"
              :class="entity.failed ? 'text-red-600' : 'text-gray-400'"
            >
              {{ entity.failed }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <MigrationMappingStep
      v-else-if="tab === 'sheets'"
      :import-id="props.id"
      :status="session?.status ?? ''"
      @reparsed="load"
    />

    <MigrationMappingEditor v-else-if="tab === 'map'" :automap="automap" @changed="load" />

    <MigrationRecordTable v-else-if="tab === 'records'" :import-id="props.id" :entities="entities" />

    <div v-else class="rounded-lg border border-gray-200 bg-white shadow-sm">
      <ul class="divide-y divide-gray-100 text-sm">
        <li v-if="!summary?.logs.length" class="px-4 py-6 text-center text-gray-400">
          Nothing logged yet.
        </li>
        <li v-for="entry in summary?.logs ?? []" :key="entry.id" class="flex gap-3 px-4 py-2">
          <span
            class="mt-0.5 h-2 w-2 shrink-0 rounded-full"
            :class="{
              'bg-red-500': entry.level === 'error',
              'bg-amber-500': entry.level === 'warning',
              'bg-gray-300': entry.level === 'info',
            }"
          />
          <div>
            <div class="text-gray-800">{{ entry.message }}</div>
            <div class="text-xs text-gray-400">
              {{ entry.phase }}<span v-if="entry.entity_key"> · {{ entry.entity_key }}</span> ·
              {{ formatDate(entry.creation) }}
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
