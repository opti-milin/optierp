<script setup lang="ts">
// ERPNext-style editable items grid for transaction forms (Quotation, Sales
// Order, Sales Invoice, …). Renders one column per configured field plus a
// computed Amount column, a per-row remove control, "Add row"/"Add multiple",
// and a Total Quantity / Total footer. Two-way bound via v-model; selecting an
// item emits `item-change` so the parent can resolve the rate.

import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import { formatCurrency } from "@/utils/format";
import { rowKey } from "@/utils/rowKey";
import DateField from "@/components/shared/DateField.vue";
import SerialEntryDialog from "@/components/shared/SerialEntryDialog.vue";

export interface GridColumn {
  key: string;
  label: string;
  type: "item" | "select" | "number" | "date" | "text" | "hsn" | "computed" | "serials";
  align?: "left" | "right";
  required?: boolean;
  options?: { value: string; label: string }[]; // for type "select"
  optionsKey?: string; // type "select": read per-row options from row[optionsKey]
  compute?: (row: Record<string, unknown>) => string; // type "computed": read-only display
  showIfKey?: string; // render the control only when row[showIfKey] is truthy
  requiredFor?: (row: Record<string, unknown>) => number; // type "serials": required count
  freeText?: boolean; // type "item": allow typing an ad-hoc item (item_id stays null)
  nameKey?: string; // type "item" + freeText: row key to store the typed name (default "item_name")
}

type Row = Record<string, unknown>;

const props = defineProps<{
  modelValue: Row[];
  columns: GridColumn[];
  itemOptions: { value: string; label: string }[];
  currency: string;
  newRow: () => Row;
  qtyKey?: string;
  rateKey?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [rows: Row[]];
  "item-change": [index: number];
}>();

// Ensure every row carries a stable key so deletes/reorders don't strand child
// state on the wrong row. Backfills rows added without one (import, prefill).
watch(
  () => props.modelValue,
  (rows) => {
    if (rows.some((r) => r._rowKey == null)) {
      emit(
        "update:modelValue",
        rows.map((r) => (r._rowKey == null ? { ...r, _rowKey: rowKey() } : r)),
      );
    }
  },
  { immediate: true },
);

const qtyKey = computed(() => props.qtyKey ?? "qty");
const rateKey = computed(() => props.rateKey ?? "rate");
const itemKey = computed(() => props.columns.find((c) => c.type === "item")?.key ?? null);

// Net per-unit rate after the per-line discount (mirrors the server engine:
// price_list_rate -> discount% / discount_amount -> rate). When no discount and
// no price_list_rate are present, this is just the entered rate.
function netRateOf(row: Row): number {
  const base = Number(row.price_list_rate) || Number(row[rateKey.value]) || 0;
  const pct = Number(row.discount_percentage) || 0;
  const amt = Number(row.discount_amount) || 0;
  if (pct) return base * (1 - pct / 100);
  if (amt) return base - amt;
  return base;
}
function amountOf(row: Row): number {
  return (Number(row[qtyKey.value]) || 0) * netRateOf(row);
}
const totalQty = computed(() =>
  props.modelValue.reduce((sum, r) => sum + (Number(r[qtyKey.value]) || 0), 0),
);
const totalAmount = computed(() =>
  props.modelValue.reduce((sum, r) => sum + amountOf(r), 0),
);

// Options for a "select" column: per-row (optionsKey) or column-level.
function selectOptions(col: GridColumn, row: Row): { value: string; label: string }[] {
  const opts = col.optionsKey ? row[col.optionsKey] : col.options;
  return (opts as { value: string; label: string }[] | undefined) ?? [];
}

// --- "serials" column: a per-row button opening SerialEntryDialog ---
const serialDialog = ref<{ index: number; col: GridColumn } | null>(null);

function showCell(col: GridColumn, row: Row): boolean {
  return !col.showIfKey || !!row[col.showIfKey];
}
function serialCount(row: Row, col: GridColumn): number {
  return String(row[col.key] ?? "").split("\n").map((s) => s.trim()).filter(Boolean).length;
}
function serialRequired(row: Row, col: GridColumn): number {
  return col.requiredFor ? col.requiredFor(row) : 0;
}
const serialDialogRow = computed(() =>
  serialDialog.value ? props.modelValue[serialDialog.value.index] : null,
);

function patch(index: number, key: string, value: unknown): void {
  emit(
    "update:modelValue",
    props.modelValue.map((r, i) => (i === index ? { ...r, [key]: value } : r)),
  );
}
// patch several keys of one row in a SINGLE emit (two patch() calls in one handler
// would race — the second reads the pre-update modelValue and clobbers the first).
function patchMany(index: number, changes: Record<string, unknown>): void {
  emit(
    "update:modelValue",
    props.modelValue.map((r, i) => (i === index ? { ...r, ...changes } : r)),
  );
}

// --- free-text item combobox: type an ad-hoc item OR pick a master one ---------
// Mirrors the HSN typeahead: the cell shows the picked item's label / the typed
// free-text name; while focused it becomes a search box over itemOptions. Typing
// stores the name and clears item_id (free text); picking sets item_id + prefills.
const itemBox = ref<{ index: number; query: string } | null>(null);

function itemLabelOf(value: string): string {
  return props.itemOptions.find((o) => o.value === value)?.label ?? "";
}
function itemCellValue(index: number, col: GridColumn, row: Row): string {
  if (itemBox.value?.index === index) return itemBox.value.query;
  const id = row[col.key] as string | null;
  if (id) return itemLabelOf(id);
  return (row[col.nameKey ?? "item_name"] as string) ?? "";
}
function itemSuggestions(): { value: string; label: string }[] {
  const q = (itemBox.value?.query ?? "").trim().toLowerCase();
  const opts = props.itemOptions;
  return (q ? opts.filter((o) => o.label.toLowerCase().includes(q)) : opts).slice(0, 12);
}
function onItemFocus(index: number, col: GridColumn, row: Row): void {
  itemBox.value = { index, query: itemCellValue(index, col, row) };
}
function onItemText(index: number, col: GridColumn, event: Event): void {
  const q = (event.target as HTMLInputElement).value;
  itemBox.value = { index, query: q };
  // typing = free text: store the name + clear the master link in ONE emit
  patchMany(index, { [col.nameKey ?? "item_name"]: q, [col.key]: null });
}
function pickItem(index: number, col: GridColumn, opt: { value: string; label: string }): void {
  patch(index, col.key, opt.value);
  itemBox.value = null;
  emit("item-change", index); // parent resolves item_name / rate / uom / hsn
}
function closeItemBox(): void {
  window.setTimeout(() => { itemBox.value = null; }, 150);
}

// --- "hsn" column: per-line HSN/SAC typeahead over the HSN master ------------
// The cell shows the stored 8-digit code; while focused it becomes a search box
// (type a product name or code) whose query is kept separate from the row value
// so a half-typed name is never stored — only a picked code is written back.
interface HsnMatch {
  hsn_code: string;
  description: string;
  gst_rate: string | number;
  gst_treatment: string;
}
const hsnBox = ref<{ index: number; query: string; results: HsnMatch[]; loading: boolean } | null>(null);
let hsnTimer: ReturnType<typeof setTimeout> | undefined;

function hsnCellValue(index: number, key: string, row: Row): string {
  return hsnBox.value?.index === index ? hsnBox.value.query : ((row[key] as string) ?? "");
}
function onHsnInput(index: number, event: Event): void {
  const q = (event.target as HTMLInputElement).value;
  hsnBox.value = { index, query: q, results: hsnBox.value?.index === index ? hsnBox.value.results : [], loading: false };
  clearTimeout(hsnTimer);
  if (q.trim().length < 2) {
    hsnBox.value.results = [];
    return;
  }
  hsnBox.value.loading = true;
  hsnTimer = setTimeout(async () => {
    try {
      const { data } = await api.get<HsnMatch[]>("/hsn-codes", { params: { search: q.trim(), limit: 8 } });
      if (hsnBox.value?.index === index) hsnBox.value = { index, query: q, results: data, loading: false };
    } catch {
      if (hsnBox.value?.index === index) hsnBox.value = { index, query: q, results: [], loading: false };
    }
  }, 300);
}
function pickHsn(index: number, key: string, m: HsnMatch): void {
  patch(index, key, m.hsn_code);
  hsnBox.value = null;
}
function closeHsn(): void {
  // small delay so a mousedown on a result is handled before the box closes
  window.setTimeout(() => { hsnBox.value = null; }, 150);
}

function onCell(index: number, col: GridColumn, event: Event): void {
  const target = event.target as HTMLInputElement | HTMLSelectElement;
  let value: unknown = target.value;
  if (col.type === "number") value = target.value === "" ? null : Number(target.value);
  // an empty optional link (e.g. Warehouse) must be null, not "" (UUID|None on the backend)
  else if (col.type === "select") value = target.value === "" ? null : target.value;
  patch(index, col.key, value);
  if (col.type === "item") emit("item-change", index);
}

function addRow(): void {
  emit("update:modelValue", [...props.modelValue, props.newRow()]);
}
function removeRow(index: number): void {
  emit(
    "update:modelValue",
    props.modelValue.filter((_, i) => i !== index),
  );
}

// --- Add multiple ----------------------------------------------------------
const showMulti = ref(false);
const multiFilter = ref("");
const multiSelected = ref<Set<string>>(new Set());

const filteredOptions = computed(() => {
  const q = multiFilter.value.trim().toLowerCase();
  if (!q) return props.itemOptions;
  return props.itemOptions.filter((o) => o.label.toLowerCase().includes(q));
});

function openMulti(): void {
  multiSelected.value = new Set();
  multiFilter.value = "";
  showMulti.value = true;
}
function toggleMulti(value: string): void {
  const next = new Set(multiSelected.value);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  multiSelected.value = next;
}
function confirmMulti(): void {
  const key = itemKey.value;
  if (!key || multiSelected.value.size === 0) {
    showMulti.value = false;
    return;
  }
  const base = props.modelValue.length;
  const ids = [...multiSelected.value];
  const additions = ids.map((id) => ({ ...props.newRow(), [key]: id }));
  emit("update:modelValue", [...props.modelValue, ...additions]);
  ids.forEach((_, i) => emit("item-change", base + i));
  showMulti.value = false;
}
</script>

<template>
  <div>
    <div class="overflow-x-auto rounded-lg border border-gray-200">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50">
          <tr>
            <th class="w-12 px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">No.</th>
            <th
              v-for="col in columns"
              :key="col.key"
              class="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500"
              :class="col.align === 'right' ? 'text-right' : 'text-left'"
            >
              {{ col.label }}<span v-if="col.required" class="text-red-500">&nbsp;*</span>
            </th>
            <th class="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500">Amount</th>
            <th class="w-10 px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="modelValue.length === 0">
            <td :colspan="columns.length + 3" class="px-3 py-6 text-center text-gray-400">No rows</td>
          </tr>
          <tr v-for="(row, i) in modelValue" :key="(row._rowKey as string) ?? i" class="hover:bg-gray-50/60">
            <td class="px-3 py-1.5 text-gray-500">{{ i + 1 }}</td>
            <td v-for="col in columns" :key="col.key" class="px-3 py-1.5">
              <span v-if="!showCell(col, row)" class="text-gray-300">—</span>
              <button
                v-else-if="col.type === 'serials'"
                type="button"
                class="text-xs underline decoration-dotted"
                :class="serialCount(row, col) === serialRequired(row, col) ? 'text-green-600' : 'text-amber-600'"
                @click="serialDialog = { index: i, col }"
              >
                Serials ({{ serialCount(row, col) }}/{{ serialRequired(row, col) }})
              </button>
              <template v-else-if="col.type === 'item'">
                <!-- master-only picker (default) -->
                <select
                  v-if="!col.freeText"
                  class="form-input py-1.5"
                  :value="(row[col.key] as string) ?? ''"
                  @change="onCell(i, col, $event)"
                >
                  <option value="" disabled>Select item…</option>
                  <option v-for="o in itemOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
                <!-- free-text combobox: type an ad-hoc item OR pick a master one -->
                <div v-else class="relative min-w-[12rem]">
                  <input
                    :value="itemCellValue(i, col, row)"
                    class="form-input py-1.5"
                    placeholder="Type or pick an item…"
                    @focus="onItemFocus(i, col, row)"
                    @input="onItemText(i, col, $event)"
                    @blur="closeItemBox"
                  />
                  <ul
                    v-if="itemBox && itemBox.index === i && itemSuggestions().length"
                    class="absolute z-20 mt-1 max-h-64 w-72 overflow-auto rounded-md border border-gray-200 bg-white text-left shadow-lg"
                  >
                    <li
                      v-for="o in itemSuggestions()"
                      :key="o.value"
                      class="cursor-pointer border-b border-gray-50 px-3 py-1.5 text-xs last:border-0 hover:bg-primary/5"
                      @mousedown.prevent="pickItem(i, col, o)"
                    >
                      {{ o.label }}
                    </li>
                  </ul>
                </div>
              </template>
              <DateField
                v-else-if="col.type === 'date'"
                :model-value="(row[col.key] as string) ?? ''"
                @update:model-value="patch(i, col.key, $event)"
              />
              <select
                v-else-if="col.type === 'select'"
                class="form-input py-1.5"
                :value="(row[col.key] as string) ?? ''"
                @change="onCell(i, col, $event)"
              >
                <option value="">—</option>
                <option v-for="o in selectOptions(col, row)" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
              <span
                v-else-if="col.type === 'computed'"
                class="block py-1.5 tabular-nums text-gray-600"
                :class="col.align === 'right' ? 'text-right' : ''"
              >{{ col.compute ? col.compute(row) : "" }}</span>
              <div v-else-if="col.type === 'hsn'" class="relative min-w-[7rem]">
                <input
                  :value="hsnCellValue(i, col.key, row)"
                  class="form-input py-1.5"
                  placeholder="code / name"
                  @input="onHsnInput(i, $event)"
                  @focus="onHsnInput(i, $event)"
                  @blur="closeHsn"
                />
                <ul
                  v-if="hsnBox && hsnBox.index === i && (hsnBox.results.length || hsnBox.loading)"
                  class="absolute z-20 mt-1 max-h-64 w-72 overflow-auto rounded-md border border-gray-200 bg-white text-left shadow-lg"
                >
                  <li v-if="hsnBox.loading && !hsnBox.results.length" class="px-3 py-2 text-xs text-gray-400">
                    Searching…
                  </li>
                  <li
                    v-for="m in hsnBox.results"
                    :key="m.hsn_code + m.description"
                    class="cursor-pointer border-b border-gray-50 px-3 py-1.5 text-xs last:border-0 hover:bg-primary/5"
                    @mousedown.prevent="pickHsn(i, col.key, m)"
                  >
                    <div class="flex justify-between gap-2">
                      <span class="font-mono text-gray-500">{{ m.hsn_code }}</span>
                      <span
                        class="font-medium"
                        :class="Number(m.gst_rate) === 0 ? 'text-gray-500' : 'text-emerald-700'"
                      >{{ Number(m.gst_rate) }}%</span>
                    </div>
                    <div class="text-gray-700">{{ m.description }}</div>
                  </li>
                </ul>
              </div>
              <input
                v-else
                :type="col.type === 'number' ? 'number' : 'text'"
                :step="col.type === 'number' ? 'any' : undefined"
                :min="col.type === 'number' ? '0' : undefined"
                class="form-input py-1.5"
                :class="col.align === 'right' ? 'text-right' : ''"
                :value="(row[col.key] as string | number | null) ?? ''"
                @input="onCell(i, col, $event)"
              />
            </td>
            <td class="px-3 py-1.5 text-right tabular-nums text-gray-700">
              {{ formatCurrency(amountOf(row), currency) }}
            </td>
            <td class="px-3 py-1.5 text-right">
              <button
                type="button"
                class="text-gray-400 hover:text-red-600"
                title="Remove row"
                @click="removeRow(i)"
              >
                ✕
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="mt-2 flex items-center gap-2">
      <button type="button" class="btn-secondary" @click="addRow">Add row</button>
      <button type="button" class="btn-secondary" @click="openMulti">Add multiple</button>
    </div>

    <!-- Totals -->
    <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <div class="form-label">Total Quantity</div>
        <div class="form-input bg-gray-50 tabular-nums text-gray-700">{{ totalQty }}</div>
      </div>
      <div>
        <div class="form-label">Total ({{ currency }})</div>
        <div class="form-input bg-gray-50 tabular-nums text-gray-700">{{ formatCurrency(totalAmount, currency) }}</div>
      </div>
    </div>

    <!-- Add multiple dialog -->
    <div
      v-if="showMulti"
      class="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4"
      @click.self="showMulti = false"
    >
      <div class="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl">
        <h3 class="mb-3 text-sm font-semibold text-gray-900">Add multiple items</h3>
        <input v-model="multiFilter" class="form-input mb-3" placeholder="Search items…" />
        <div class="max-h-72 overflow-y-auto rounded-md border border-gray-200">
          <label
            v-for="o in filteredOptions"
            :key="o.value"
            class="flex cursor-pointer items-center gap-2 border-b border-gray-100 px-3 py-2 text-sm last:border-b-0 hover:bg-gray-50"
          >
            <input
              type="checkbox"
              class="h-4 w-4 rounded border-gray-300 text-primary"
              :checked="multiSelected.has(o.value)"
              @change="toggleMulti(o.value)"
            />
            <span class="text-gray-700">{{ o.label }}</span>
          </label>
          <p v-if="filteredOptions.length === 0" class="px-3 py-4 text-center text-sm text-gray-400">No items</p>
        </div>
        <div class="mt-4 flex items-center justify-between">
          <span class="text-xs text-gray-500">{{ multiSelected.size }} selected</span>
          <div class="flex gap-2">
            <button type="button" class="btn-secondary" @click="showMulti = false">Cancel</button>
            <button type="button" class="btn-primary" :disabled="multiSelected.size === 0" @click="confirmMulti">
              Add {{ multiSelected.size || "" }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <SerialEntryDialog
      v-if="serialDialog && serialDialogRow"
      :model-value="(serialDialogRow[serialDialog.col.key] as string) ?? ''"
      :required="serialRequired(serialDialogRow, serialDialog.col)"
      :title="`Serial numbers — line ${serialDialog.index + 1}`"
      @update:model-value="patch(serialDialog.index, serialDialog.col.key, $event)"
      @close="serialDialog = null"
    />
  </div>
</template>
