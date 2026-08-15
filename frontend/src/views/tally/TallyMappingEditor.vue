<script setup lang="ts">
// The mapping table: every Tally name and what it will become.
// Sorted least-confident first — the whole point is that a tester only has to
// check the handful the matcher was unsure about, not all 400 ledgers.
import { computed, onMounted, ref, watch } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { TallyAutoMapResult, TallyMapping, TallyMappingTarget } from "@/types/tally";

const props = defineProps<{ automap: TallyAutoMapResult | null }>();
const emit = defineEmits<{ changed: [] }>();

const rows = ref<TallyMapping[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const fEntity = ref("");
const fSearch = ref("");
const fUnresolvedOnly = ref(false);

const editing = ref<TallyMapping | null>(null);
const targets = ref<TallyMappingTarget[]>([]);
const targetSearch = ref("");
const chosenTarget = ref("");
const saving = ref(false);

const ENTITY_LABELS: Record<string, string> = {
  group: "Ledger Group",
  ledger: "Ledger",
  customer: "Customer",
  supplier: "Supplier",
  stock_group: "Stock Group",
  stock_item: "Stock Item",
  godown: "Godown",
  cost_centre: "Cost Centre",
  unit: "Unit",
  currency: "Currency",
  price_list: "Price Level",
};

const METHOD_NOTE: Record<string, string> = {
  guid: "Same Tally GUID as a mapping you made before — the safest match there is",
  exact: "The names match character for character",
  normalised: "Match after ignoring punctuation and 'Pvt Ltd' style suffixes",
  fuzzy: "Close but not identical — worth a look",
  created: "Nothing matched; a new record will be created on import",
  manual: "You set this by hand",
};

const entityOptions = computed(() => {
  const keys = new Set(rows.value.map((r) => r.entity_key));
  return [...keys].sort();
});

function tone(row: TallyMapping): string {
  if (row.is_locked) return "bg-purple-50 text-purple-700";
  if (!row.target_id) return "bg-amber-50 text-amber-700";
  if (row.confidence >= 95) return "bg-green-50 text-green-700";
  if (row.confidence >= 85) return "bg-blue-50 text-blue-700";
  return "bg-orange-50 text-orange-700";
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rows.value = (
      await api.get<TallyMapping[]>("/tally/mappings", {
        params: {
          entity_key: fEntity.value || undefined,
          search: fSearch.value || undefined,
          unresolved_only: fUnresolvedOnly.value || undefined,
        },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function openEditor(row: TallyMapping): Promise<void> {
  editing.value = row;
  chosenTarget.value = row.target_id ?? "";
  targetSearch.value = "";
  await loadTargets();
}

async function loadTargets(): Promise<void> {
  if (!editing.value) return;
  targets.value = (
    await api.get<TallyMappingTarget[]>("/tally/mappings/targets", {
      params: {
        target_doctype: editing.value.target_doctype,
        search: targetSearch.value || undefined,
      },
    })
  ).data;
}

async function save(): Promise<void> {
  if (!editing.value) return;
  saving.value = true;
  error.value = null;
  try {
    await api.put(`/tally/mappings/${editing.value.id}`, {
      target_doctype: editing.value.target_doctype,
      target_id: chosenTarget.value || null,
    });
    editing.value = null;
    await load();
    emit("changed");
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

watch([fEntity, fUnresolvedOnly], load);
onMounted(load);
</script>

<template>
  <div>
    <div
      v-if="props.automap"
      class="mb-4 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900"
    >
      Auto-map finished.
      <span v-for="(result, key) in props.automap.summary" :key="key" class="mr-3">
        <strong>{{ ENTITY_LABELS[key] ?? key }}:</strong>
        {{ result.matched }} matched, {{ result.to_create }} to create<span
          v-if="result.low_confidence"
          class="text-orange-700"
        >
          , {{ result.low_confidence }} unsure</span
        >.
      </span>
    </div>

    <div class="mb-3 flex flex-wrap items-end gap-3">
      <div>
        <label class="form-label">Entity</label>
        <select v-model="fEntity" class="form-input">
          <option value="">All</option>
          <option v-for="key in entityOptions" :key="key" :value="key">
            {{ ENTITY_LABELS[key] ?? key }}
          </option>
        </select>
      </div>
      <div>
        <label class="form-label">Search Tally name</label>
        <input
          v-model="fSearch"
          type="search"
          class="form-input"
          placeholder="e.g. Bharat Steel"
          @keyup.enter="load"
        />
      </div>
      <label class="flex items-center gap-2 pb-2 text-sm text-gray-700">
        <input v-model="fUnresolvedOnly" type="checkbox" />
        Only names with no target
      </label>
      <button class="btn-secondary" @click="load">Refresh</button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div class="rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
          <tr>
            <th class="px-4 py-2">Tally name</th>
            <th class="px-4 py-2">Under</th>
            <th class="px-4 py-2">Becomes</th>
            <th class="px-4 py-2">Matched to</th>
            <th class="px-4 py-2">How</th>
            <th class="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="6" class="px-4 py-6 text-center text-gray-400">Loading…</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="6" class="px-4 py-6 text-center text-gray-400">
              No mappings yet — run "Auto-map names" on the import first.
            </td>
          </tr>
          <tr v-for="row in rows" :key="row.id" class="border-b border-gray-100">
            <td class="px-4 py-2 font-medium text-gray-900">{{ row.tally_name }}</td>
            <td class="px-4 py-2 text-gray-500">{{ row.tally_parent || "—" }}</td>
            <td class="px-4 py-2 text-gray-700">
              {{ ENTITY_LABELS[row.entity_key] ?? row.entity_key }} → {{ row.target_doctype }}
            </td>
            <td class="px-4 py-2">
              <span v-if="row.target_id" class="text-gray-800">{{ row.target_name }}</span>
              <span v-else class="text-amber-700">will be created</span>
            </td>
            <td class="px-4 py-2">
              <span
                class="rounded px-2 py-0.5 text-xs font-medium"
                :class="tone(row)"
                :title="METHOD_NOTE[row.match_method] ?? row.match_method"
              >
                {{ row.is_locked ? "locked" : row.match_method }}
                <span v-if="!row.is_locked && row.target_id"> · {{ row.confidence }}%</span>
              </span>
            </td>
            <td class="px-4 py-2 text-right">
              <button class="text-sm text-blue-600 hover:underline" @click="openEditor(row)">
                Change
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="editing"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      @click.self="editing = null"
    >
      <div class="w-full max-w-lg rounded-lg bg-white p-5 shadow-lg">
        <h3 class="text-sm font-semibold text-gray-900">
          Map "{{ editing.tally_name }}" to a {{ editing.target_doctype }}
        </h3>
        <p class="mb-3 text-xs text-gray-500">
          Your choice is locked: later auto-map runs, and later imports from the same Tally
          company, will keep it.
        </p>
        <input
          v-model="targetSearch"
          type="search"
          class="form-input mb-2"
          placeholder="Search existing records…"
          @keyup.enter="loadTargets"
        />
        <select v-model="chosenTarget" class="form-input" size="10">
          <option value="">— Create a new record on import —</option>
          <option v-for="target in targets" :key="target.id" :value="target.id">
            {{ target.name }}
          </option>
        </select>
        <div class="mt-4 flex justify-end gap-2">
          <button class="btn-secondary" @click="editing = null">Cancel</button>
          <button class="btn-primary" :disabled="saving" @click="save">
            {{ saving ? "Saving…" : "Save mapping" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
