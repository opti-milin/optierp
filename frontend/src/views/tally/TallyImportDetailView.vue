<script setup lang="ts">
// The import wizard. Four steps, each a separate backend call so a tester can
// stop, check something in Tally, and come back:
//   1 Review   — what was found in the file, per entity
//   2 Map      — what each Tally name will become (least-confident first)
//   3 Dry run  — how many documents, and what is still unresolved
//   4 Run      — create the documents; roll back if the numbers don't match
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import TallyMappingEditor from "@/views/tally/TallyMappingEditor.vue";
import TallyRecordTable from "@/views/tally/TallyRecordTable.vue";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TallyAutoMapResult,
  TallyImportSummary,
  TallyRollbackResult,
  TallyValidateResult,
} from "@/types/tally";

const props = defineProps<{ id: string }>();

const summary = ref<TallyImportSummary | null>(null);
const validation = ref<TallyValidateResult | null>(null);
const automap = ref<TallyAutoMapResult | null>(null);
const loading = ref(false);
const busy = ref("");
const error = ref<ErrorEnvelope | null>(null);
const tab = ref<"review" | "map" | "records" | "log">("review");

const session = computed(() => summary.value?.session ?? null);
const entities = computed(() => summary.value?.entities ?? []);

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
const canRollback = computed(
  () =>
    !!session.value &&
    ["Imported", "Partially Imported", "Failed"].includes(session.value.status),
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    summary.value = (
      await api.get<TallyImportSummary>(`/tally/imports/${props.id}/summary`)
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
      await api.post<TallyAutoMapResult>(`/tally/imports/${props.id}/automap`)
    ).data;
    tab.value = "map";
  });

const doValidate = () =>
  act("validate", async () => {
    validation.value = (
      await api.post<TallyValidateResult>(`/tally/imports/${props.id}/validate`)
    ).data;
  });

const doRun = () =>
  act("run", async () => {
    await api.post(`/tally/imports/${props.id}/run`);
    tab.value = "records";
  });

const doRollback = () =>
  act("rollback", async () => {
    const result = (
      await api.post<TallyRollbackResult>(`/tally/imports/${props.id}/rollback`)
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
    await api.post(`/tally/imports/${props.id}/entities`, {
      selections: { [key]: selected },
    });
  });
}

onMounted(load);
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <RouterLink to="/tally" class="text-sm text-blue-600 hover:underline">
          ← All imports
        </RouterLink>
        <h1 class="text-xl font-semibold text-gray-900">
          {{ session?.title || session?.name || "Import" }}
        </h1>
        <p v-if="session" class="text-sm text-gray-500">
          {{ session.tally_company_name || "Unnamed Tally company" }} ·
          {{ session.source_type }} ·
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
    </div>

    <div class="mb-4 flex gap-1 border-b border-gray-200 text-sm">
      <button
        v-for="t in [
          { key: 'review', label: 'What was found' },
          { key: 'map', label: 'Mappings' },
          { key: 'records', label: 'Records' },
          { key: 'log', label: 'Activity' },
        ]"
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

    <TallyMappingEditor v-else-if="tab === 'map'" :automap="automap" @changed="load" />

    <TallyRecordTable v-else-if="tab === 'records'" :import-id="props.id" :entities="entities" />

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
