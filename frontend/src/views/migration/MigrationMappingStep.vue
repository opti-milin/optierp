<script setup lang="ts">
// Step 0 of the wizard, and only for spreadsheets: what is each sheet, and which
// column is which field?
//
// A Tally XML export skips this entirely — the format *is* the mapping. A
// workbook cannot tell you which of its 40 sheets is the invoice list, so this
// is where a tester answers that once and, if they want, saves the answer so the
// next file from the same system is recognised on upload.
//
// Everything offered here comes from the backend's own shape catalogue, so the
// wizard can only ever describe a workbook the importer can actually read.
import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type {
  MappingChild,
  MappingField,
  MappingSheet,
  MigrationMapping,
} from "@/types/migration";

const props = defineProps<{ importId: string; status: string }>();
const emit = defineEmits<{ (e: "reparsed"): void }>();

const mapping = ref<MigrationMapping | null>(null);
const loading = ref(false);
const busy = ref("");
const error = ref<ErrorEnvelope | null>(null);
const notice = ref("");
const expanded = ref<string | null>(null);
const profileName = ref("");

/** Local, editable copy — nothing is sent until "Apply". */
const draft = ref<Record<string, MappingSheet["assigned"]>>({});

// Kinds that produce masters or openings need to know which catalogue entity
// they feed; a voucher kind resolves its entity from the voucher type instead.
const ENTITY_KINDS = new Set([
  "ledger_master",
  "group_master",
  "item_master",
  "simple_master",
  "opening_ledger",
  "opening_stock",
]);

const editable = computed(() =>
  ["Draft", "Parsed", "Mapped", "Validated", "Failed", "Rolled Back"].includes(props.status),
);

const sheets = computed(() => mapping.value?.sheets ?? []);
const kinds = computed(() => mapping.value?.kinds ?? []);

const kindByKey = computed(() =>
  Object.fromEntries((mapping.value?.kinds ?? []).map((k) => [k.key, k])),
);

const assignedCount = computed(
  () => sheets.value.filter((s) => draft.value[s.name]?.kind && draft.value[s.name]?.kind !== "reference").length,
);
const blockedSheets = computed(() => sheets.value.filter((s) => s.missing.length > 0));

function fieldsFor(sheetName: string): MappingField[] {
  const kind = draft.value[sheetName]?.kind;
  return kind ? (kindByKey.value[kind]?.fields ?? []) : [];
}

function childFields(role: string): MappingField[] {
  return mapping.value?.child_fields[role] ?? [];
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const { data } = await api.get<MigrationMapping>(`/migration/imports/${props.importId}/mapping`);
    mapping.value = data;
    profileName.value = data.profile.saved ? data.profile.label : "";
    draft.value = Object.fromEntries(
      data.sheets.map((s) => [
        s.name,
        s.assigned
          ? { ...s.assigned, columns: { ...s.assigned.columns }, children: s.assigned.children.map((c) => ({ ...c, columns: { ...c.columns } })) }
          : null,
      ]),
    );
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

function setKind(sheetName: string, kind: string): void {
  if (!kind) {
    draft.value[sheetName] = null;
    return;
  }
  const current = draft.value[sheetName];
  draft.value[sheetName] = {
    kind,
    entity: current?.entity ?? null,
    key: current?.key ?? null,
    reason: current?.reason ?? "",
    constants: current?.constants ?? {},
    // Changing the shape changes which fields exist, so a column mapped to a
    // field the new shape does not have would be silently kept and then ignored.
    // Keeping only the ones that still mean something is the honest reset.
    columns: Object.fromEntries(
      Object.entries(current?.columns ?? {}).filter(([field]) =>
        (kindByKey.value[kind]?.fields ?? []).some((f) => f.name === field),
      ),
    ),
    children: current?.children ?? [],
  };
}

function setColumn(sheetName: string, field: string, header: string): void {
  const assigned = draft.value[sheetName];
  if (!assigned) return;
  if (header) assigned.columns[field] = header;
  else delete assigned.columns[field];
}

function setChildColumn(child: MappingChild, field: string, header: string): void {
  if (header) child.columns[field] = header;
  else delete child.columns[field];
}

function columnsOf(sheetName: string): { header: string; label: string; samples: string[] }[] {
  return sheets.value.find((s) => s.name === sheetName)?.columns ?? [];
}

/** Rebuild the wire format: the API takes an alias list per field. */
function definition(): Record<string, unknown> {
  const base = mapping.value;
  if (!base) return {};
  return {
    key: base.profile.key === "custom" ? "custom" : base.profile.key,
    label: base.profile.label,
    app: base.profile.app,
    notes: base.profile.notes,
    defaults: base.defaults,
    sheets: sheets.value
      .filter((s) => draft.value[s.name])
      .map((s) => {
        const a = draft.value[s.name]!;
        return {
          sheet: s.name,
          kind: a.kind,
          entity: a.entity,
          key: a.key,
          reason: a.reason,
          constants: a.constants,
          columns: Object.fromEntries(Object.entries(a.columns).map(([f, h]) => [f, [h]])),
          children: a.children.map((c) => ({
            sheet: c.sheet,
            role: c.role,
            key: c.key,
            constants: c.constants,
            columns: Object.fromEntries(Object.entries(c.columns).map(([f, h]) => [f, [h]])),
          })),
        };
      }),
  };
}

async function act(label: string, run: () => Promise<void>): Promise<void> {
  busy.value = label;
  error.value = null;
  notice.value = "";
  try {
    await run();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = "";
  }
}

const apply = () =>
  act("apply", async () => {
    await api.put(`/migration/imports/${props.importId}/mapping`, { definition: definition() });
    notice.value = "Mapping applied — the file was read again into fresh staging rows.";
    await load();
    emit("reparsed");
  });

const usePreset = (key: string) =>
  act("preset", async () => {
    await api.put(`/migration/imports/${props.importId}/mapping`, { profile: key });
    notice.value = "Switched mapping and re-read the file.";
    await load();
    emit("reparsed");
  });

const saveProfile = () =>
  act("save", async () => {
    await api.post("/migration/sources", {
      label: profileName.value.trim(),
      source_app: mapping.value?.profile.app,
      definition: definition(),
    });
    notice.value = `Saved as "${profileName.value.trim()}". The next workbook of this shape is recognised on upload.`;
  });

watch(() => props.importId, load, { immediate: true });
</script>

<template>
  <div>
    <p v-if="loading" class="text-sm text-gray-500">Reading the workbook…</p>

    <div v-else-if="mapping">
      <!-- What we think this file is -->
      <div class="mb-4 rounded border border-blue-100 bg-blue-50 p-3">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p class="text-sm font-medium text-blue-900">
              Reading this as: {{ mapping.profile.label }}
              <span v-if="mapping.profile.saved" class="ml-1 rounded bg-blue-200 px-1.5 py-0.5 text-xs">
                your saved mapping
              </span>
            </p>
            <p class="mt-0.5 text-xs text-blue-800">
              {{ assignedCount }} of {{ sheets.length }} sheet(s) will be imported.
              <span v-if="blockedSheets.length" class="font-medium">
                {{ blockedSheets.length }} still need a required column.
              </span>
            </p>
            <p v-if="mapping.profile.notes" class="mt-1 max-w-3xl text-xs text-blue-700">
              {{ mapping.profile.notes }}
            </p>
          </div>
          <div v-if="editable" class="flex flex-wrap items-center gap-2">
            <label class="text-xs text-blue-900">Use a different mapping</label>
            <select
              class="rounded border border-blue-200 bg-white px-2 py-1 text-xs"
              :disabled="!!busy"
              @change="usePreset(($event.target as HTMLSelectElement).value)"
            >
              <option value="">Choose…</option>
              <option v-for="c in mapping.candidates" :key="c.key" :value="c.key">
                {{ c.label }} — {{ c.confidence }}% match
              </option>
            </select>
          </div>
        </div>
      </div>

      <div v-if="notice" class="mb-3 rounded border border-green-200 bg-green-50 p-2 text-sm text-green-800">
        {{ notice }}
      </div>
      <div v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800">
        {{ error.detail }}
      </div>

      <div
        v-if="Object.keys(mapping.unreadable_sheets).length"
        class="mb-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800"
      >
        <span class="font-medium">Not read at all:</span>
        <span v-for="(why, name) in mapping.unreadable_sheets" :key="name"> {{ name }} ({{ why }}); </span>
      </div>

      <!-- Sheet by sheet -->
      <div class="space-y-2">
        <div v-for="sheet in sheets" :key="sheet.name" class="rounded border border-gray-200">
          <button
            type="button"
            class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-gray-50"
            @click="expanded = expanded === sheet.name ? null : sheet.name"
          >
            <div class="min-w-0">
              <span class="font-medium text-gray-900">{{ sheet.name }}</span>
              <span class="ml-2 text-xs text-gray-500">
                {{ sheet.rows }} row(s) · {{ sheet.columns.length }} column(s)
                <template v-if="sheet.header_row > 1"> · headings on row {{ sheet.header_row }}</template>
              </span>
              <p
                v-if="draft[sheet.name]?.kind === 'reference' && draft[sheet.name]?.reason"
                class="mt-0.5 max-w-3xl truncate text-xs text-gray-500"
                :title="draft[sheet.name]!.reason"
              >
                {{ draft[sheet.name]!.reason }}
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <span
                v-if="sheet.parent"
                class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600"
                :title="`Read as the ${sheet.parent.role.replace('_', ' ')} of ${sheet.parent.sheet}`"
              >
                lines of {{ sheet.parent.sheet }}
              </span>
              <span
                v-else-if="draft[sheet.name]?.kind === 'reference'"
                class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600"
              >
                not imported
              </span>
              <span
                v-else-if="draft[sheet.name]"
                class="rounded bg-green-50 px-2 py-0.5 text-xs text-green-700"
              >
                {{ kindByKey[draft[sheet.name]!.kind]?.label ?? draft[sheet.name]!.kind }}
              </span>
              <span v-else class="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">unassigned</span>
              <span v-if="sheet.missing.length" class="rounded bg-red-50 px-2 py-0.5 text-xs text-red-700">
                missing {{ sheet.missing.join(", ") }}
              </span>
              <span class="text-gray-400">{{ expanded === sheet.name ? "▾" : "▸" }}</span>
            </div>
          </button>

          <div v-if="expanded === sheet.name" class="border-t border-gray-200 px-3 py-3">
            <p v-if="sheet.parent" class="text-sm text-gray-600">
              These rows are read as the
              <span class="font-medium">{{ sheet.parent.role.replace("_", " ") }}</span>
              of <span class="font-medium">{{ sheet.parent.sheet }}</span>. Change the column
              mapping there.
            </p>

            <template v-else>
              <div
                v-if="draft[sheet.name]?.kind === 'reference' && draft[sheet.name]?.reason"
                class="mb-3 rounded border border-gray-200 bg-gray-50 p-3"
              >
                <p class="text-xs font-semibold uppercase tracking-wide text-gray-500">
                  Why this sheet is not imported
                </p>
                <p class="mt-1 max-w-3xl text-sm text-gray-700">
                  {{ draft[sheet.name]!.reason }}
                </p>
                <p class="mt-2 text-xs text-gray-500">
                  You can still map it yourself below if your file means something
                  different by this sheet — nothing here is locked.
                </p>
              </div>

              <div class="flex flex-wrap items-end gap-3">
                <label class="text-sm">
                  <span class="block text-xs text-gray-500">This sheet is</span>
                  <select
                    class="mt-0.5 rounded border border-gray-300 px-2 py-1 text-sm"
                    :disabled="!editable"
                    :value="draft[sheet.name]?.kind ?? ''"
                    @change="setKind(sheet.name, ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="">— not imported —</option>
                    <option v-for="k in kinds" :key="k.key" :value="k.key">{{ k.label }}</option>
                  </select>
                </label>

                <label v-if="draft[sheet.name] && ENTITY_KINDS.has(draft[sheet.name]!.kind)" class="text-sm">
                  <span class="block text-xs text-gray-500">Becomes</span>
                  <select
                    v-model="draft[sheet.name]!.entity"
                    class="mt-0.5 rounded border border-gray-300 px-2 py-1 text-sm"
                    :disabled="!editable"
                  >
                    <option :value="null">— pick one —</option>
                    <option v-for="e in mapping.entities" :key="e.key" :value="e.key">
                      {{ e.label }} → {{ e.target }}
                    </option>
                  </select>
                </label>
              </div>

              <p v-if="draft[sheet.name] && kindByKey[draft[sheet.name]!.kind]?.notes" class="mt-2 max-w-3xl text-xs text-gray-500">
                {{ kindByKey[draft[sheet.name]!.kind]?.notes }}
              </p>

              <!-- Column mapping -->
              <table v-if="draft[sheet.name]" class="mt-3 w-full text-sm">
                <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-500">
                  <tr>
                    <th class="py-1 pr-3">Field</th>
                    <th class="py-1 pr-3">Column in your file</th>
                    <th class="py-1">Sample values</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-100">
                  <tr v-for="f in fieldsFor(sheet.name)" :key="f.name">
                    <td class="py-1 pr-3 align-top">
                      <span :class="f.required ? 'font-medium text-gray-900' : 'text-gray-700'">
                        {{ f.label }}
                      </span>
                      <span v-if="f.required" class="ml-1 text-xs text-red-600">required</span>
                    </td>
                    <td class="py-1 pr-3 align-top">
                      <select
                        class="w-56 rounded border border-gray-300 px-2 py-1 text-sm"
                        :disabled="!editable"
                        :value="draft[sheet.name]!.columns[f.name] ?? ''"
                        @change="setColumn(sheet.name, f.name, ($event.target as HTMLSelectElement).value)"
                      >
                        <option value="">—</option>
                        <option v-for="c in columnsOf(sheet.name)" :key="c.header" :value="c.header">
                          {{ c.label }}
                        </option>
                      </select>
                    </td>
                    <td class="py-1 align-top text-xs text-gray-500">
                      {{
                        (columnsOf(sheet.name).find(
                          (c) => c.header === draft[sheet.name]!.columns[f.name],
                        )?.samples ?? []).join(" · ")
                      }}
                    </td>
                  </tr>
                </tbody>
              </table>

              <!-- Line sheets -->
              <div
                v-for="child in draft[sheet.name]?.children ?? []"
                :key="child.sheet + child.role"
                class="mt-4 rounded bg-gray-50 p-3"
              >
                <p class="text-sm font-medium text-gray-900">
                  {{ child.sheet }} — {{ child.role.replace("_", " ") }}
                </p>
                <table class="mt-2 w-full text-sm">
                  <tbody class="divide-y divide-gray-200">
                    <tr v-for="f in childFields(child.role)" :key="f.name">
                      <td class="w-56 py-1 pr-3 align-top">
                        <span :class="f.required ? 'font-medium text-gray-900' : 'text-gray-700'">
                          {{ f.label }}
                        </span>
                      </td>
                      <td class="py-1">
                        <select
                          class="w-56 rounded border border-gray-300 px-2 py-1 text-sm"
                          :disabled="!editable"
                          :value="child.columns[f.name] ?? ''"
                          @change="setChildColumn(child, f.name, ($event.target as HTMLSelectElement).value)"
                        >
                          <option value="">—</option>
                          <option v-for="c in columnsOf(child.sheet)" :key="c.header" :value="c.header">
                            {{ c.label }}
                          </option>
                        </select>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- Apply / save -->
      <div v-if="editable" class="mt-4 flex flex-wrap items-end gap-3 border-t border-gray-200 pt-3">
        <button class="btn-primary" :disabled="!!busy" @click="apply">
          {{ busy === "apply" ? "Re-reading…" : "Apply mapping & re-read the file" }}
        </button>
        <div class="flex items-end gap-2">
          <label class="text-sm">
            <span class="block text-xs text-gray-500">Save this mapping as</span>
            <input
              v-model="profileName"
              class="mt-0.5 w-56 rounded border border-gray-300 px-2 py-1 text-sm"
              placeholder="e.g. Busy 21 export"
            />
          </label>
          <button
            class="btn-secondary"
            :disabled="!!busy || !profileName.trim()"
            @click="saveProfile"
          >
            {{ busy === "save" ? "Saving…" : "Save profile" }}
          </button>
        </div>
      </div>
      <p v-else class="mt-4 text-sm text-gray-500">
        This import has already run, so its mapping is fixed. Roll it back to change it.
      </p>
    </div>
  </div>
</template>
