<script setup lang="ts">
// Keyboard- and paste-first inline grid for the Income Tax workspace.
//
// Enter moves down the same column (appending a row on the last one), Alt+N
// appends, and pasting a block of tab- or comma-separated text fills rightwards
// and downwards — the "copy a column out of a spreadsheet" path a chartered
// accountant actually uses. Derived columns are read-only and explain themselves.
//
// Prefer this over ItemsGrid for tax data: no item master, no currency amount
// column, per-column derive/explain, statutory references in the header.

import { computed, nextTick, type ComponentPublicInstance } from "vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import SearchSelect from "@/components/shared/SearchSelect.vue";
import DateField from "@/components/shared/DateField.vue";
import { formatNumber } from "@/utils/format";
import type { TaxOption } from "@/types/taxation";

export interface EditableGridColumn {
  key: string;
  label: string;
  short?: string;
  help?: string;
  type: "text" | "number" | "money" | "date" | "select" | "derived" | "checkbox";
  options?: TaxOption[];
  optionsKey?: string;
  width?: string;
  align?: "left" | "right" | "center";
  required?: boolean;
  readonly?: boolean;
  allowFree?: boolean;
  placeholder?: string;
  statutoryRef?: string;
  derive?: (row: Record<string, unknown>) => string;
  explain?: (row: Record<string, unknown>) => string | null;
  onSelect?: (row: Record<string, unknown>, option: TaxOption) => void;
}

type Row = Record<string, unknown>;

const props = withDefaults(defineProps<{
  modelValue: Row[];
  columns: EditableGridColumn[];
  newRow: () => Row;
  disabled?: boolean;
  addLabel?: string;
  emptyText?: string;
  totalKeys?: string[];
  rowOptions?: Record<string, TaxOption[]>;
  showRowNumbers?: boolean;
  testid?: string;
}>(), {
  disabled: false,
  addLabel: "Add row",
  emptyText: "No rows yet.",
  showRowNumbers: true,
});

const emit = defineEmits<{
  "update:modelValue": [rows: Row[]];
  dirty: [];
}>();

// A comma between two digits belongs to 1,20,000 — it is not a cell delimiter.
const CELL_COMMA = /(?<!\d),|,(?!\d)/;

const editableColumns = computed<EditableGridColumn[]>(
  () => props.columns.filter((col) => col.type !== "derived" && !col.readonly),
);

// --- mutation: always a new array, never a mutated prop -----------------------

function commit(rows: Row[]): void {
  emit("update:modelValue", rows);
  emit("dirty");
}

function patch(rowIndex: number, changes: Row): void {
  commit(props.modelValue.map((row, i) => (i === rowIndex ? { ...row, ...changes } : row)));
}

function addRow(): void {
  if (props.disabled) return;
  commit([...props.modelValue, props.newRow()]);
}

function removeRow(rowIndex: number): void {
  if (props.disabled) return;
  commit(props.modelValue.filter((_, i) => i !== rowIndex));
}

// --- cell focus (Enter walks down a column) ----------------------------------

const cellRefs = new Map<string, HTMLInputElement>();

function cellRefKey(rowIndex: number, colKey: string): string {
  return `${rowIndex}:${colKey}`;
}

function setCellRef(rowIndex: number, colKey: string, el: Element | ComponentPublicInstance | null): void {
  const key = cellRefKey(rowIndex, colKey);
  if (el instanceof HTMLInputElement) cellRefs.set(key, el);
  else cellRefs.delete(key);
}

async function focusCell(rowIndex: number, colKey: string): Promise<void> {
  await nextTick();
  const el = cellRefs.get(cellRefKey(rowIndex, colKey));
  el?.focus();
  el?.select();
}

function onCellEnter(rowIndex: number, col: EditableGridColumn): void {
  if (props.disabled) return;
  if (rowIndex >= props.modelValue.length - 1) commit([...props.modelValue, props.newRow()]);
  void focusCell(rowIndex + 1, col.key);
}

function onGridKeydown(event: KeyboardEvent): void {
  if (props.disabled) return;
  if (!event.altKey || event.key.toLowerCase() !== "n") return;
  event.preventDefault();
  const first = editableColumns.value[0];
  addRow();
  if (first) void focusCell(props.modelValue.length, first.key);
}

// --- reading + writing cells --------------------------------------------------

function cellString(row: Row, col: EditableGridColumn): string {
  const value = row[col.key];
  return value == null ? "" : String(value);
}

function optionsFor(col: EditableGridColumn): TaxOption[] {
  if (col.options) return col.options;
  if (col.optionsKey) return props.rowOptions?.[col.optionsKey] ?? [];
  return [];
}

function onCellInput(rowIndex: number, col: EditableGridColumn, event: Event): void {
  patch(rowIndex, { [col.key]: (event.target as HTMLInputElement).value });
}

function onCheckbox(rowIndex: number, col: EditableGridColumn, event: Event): void {
  patch(rowIndex, { [col.key]: (event.target as HTMLInputElement).checked });
}

function onSelectOption(rowIndex: number, col: EditableGridColumn, option: TaxOption | null): void {
  if (!option || !col.onSelect) return;
  // Picking a deductor fills its name, picking a block fills its rate — the
  // column's own callback decides, in one emit so nothing races.
  const next: Row = { ...props.modelValue[rowIndex], [col.key]: option.value };
  col.onSelect(next, option);
  commit(props.modelValue.map((row, i) => (i === rowIndex ? next : row)));
}

// --- paste: fill rightwards and downwards ------------------------------------

function toIsoDate(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const match = value.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/);
  if (!match) return value;
  const [, d, m, y] = match;
  return `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`;
}

function coerce(col: EditableGridColumn, raw: string): unknown {
  const value = raw.trim();
  switch (col.type) {
    case "money":
    case "number":
      return value.replace(/[^\d.-]/g, "");
    case "checkbox":
      return /^(1|y|yes|true)$/i.test(value);
    case "date":
      return toIsoDate(value);
    default:
      return value;
  }
}

function matchOption(col: EditableGridColumn, raw: string): TaxOption | null {
  const wanted = raw.trim().toLowerCase();
  if (!wanted) return null;
  const options = optionsFor(col);
  return (
    options.find((opt) => opt.value.toLowerCase() === wanted) ??
    options.find((opt) => opt.label.trim().toLowerCase() === wanted) ??
    null
  );
}

function parseClipboard(text: string): string[][] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  while (lines.length > 0 && lines[lines.length - 1].trim() === "") lines.pop();
  const tabbed = lines.some((line) => line.includes("\t"));
  return lines.map((line) =>
    (tabbed ? line.split("\t") : line.split(CELL_COMMA)).map((cell) => cell.trim().replace(/^"|"$/g, "")),
  );
}

function onPaste(rowIndex: number, col: EditableGridColumn, event: ClipboardEvent): void {
  if (props.disabled) return;
  const text = event.clipboardData?.getData("text/plain") ?? "";
  if (!text) return;
  const matrix = parseClipboard(text);
  // A single value is an ordinary paste — let the browser handle it.
  if (matrix.length === 0 || (matrix.length === 1 && matrix[0].length <= 1)) return;
  event.preventDefault();

  const targets = editableColumns.value;
  const startPos = Math.max(0, targets.findIndex((candidate) => candidate.key === col.key));
  const rows: Row[] = props.modelValue.map((row) => ({ ...row }));

  matrix.forEach((cells, r) => {
    const target = rowIndex + r;
    while (rows.length <= target) rows.push(props.newRow());
    const row = rows[target];
    cells.forEach((raw, c) => {
      const column = targets[startPos + c];
      if (!column) return;
      if (column.type === "select") {
        const option = matchOption(column, raw);
        row[column.key] = option ? option.value : column.allowFree ? raw.trim() : "";
        if (option) column.onSelect?.(row, option);
        return;
      }
      row[column.key] = coerce(column, raw);
    });
  });

  commit(rows);
}

// --- presentation ------------------------------------------------------------

function headerText(col: EditableGridColumn): string {
  return col.short ?? col.label;
}

function needsTip(col: EditableGridColumn): boolean {
  return Boolean(col.help || col.statutoryRef || (col.short && col.short !== col.label));
}

function alignClass(col: EditableGridColumn): string {
  const align = col.align ?? (col.type === "money" || col.type === "number" || col.type === "derived" ? "right" : "left");
  if (align === "right") return "text-right";
  if (align === "center") return "text-center";
  return "text-left";
}

function derived(row: Row, col: EditableGridColumn): string {
  return col.derive ? col.derive(row) : "";
}

function explanation(row: Row, col: EditableGridColumn): string | null {
  return col.explain ? col.explain(row) : null;
}

function rowKeyOf(row: Row, index: number): string {
  const key = row._rowKey;
  return typeof key === "string" ? key : `row-${index}`;
}

const totals = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {};
  for (const key of props.totalKeys ?? []) {
    const col = props.columns.find((candidate) => candidate.key === key);
    const sum = props.modelValue.reduce((acc, row) => {
      const raw = col?.type === "derived" ? derived(row, col) : row[key];
      const value = Number(String(raw ?? "").replace(/,/g, ""));
      return acc + (Number.isNaN(value) ? 0 : value);
    }, 0);
    out[key] = formatNumber(sum);
  }
  return out;
});

const hasTotals = computed(() => (props.totalKeys ?? []).length > 0);
const columnSpan = computed(() => props.columns.length + (props.showRowNumbers ? 2 : 1));
</script>

<template>
  <div :data-testid="testid ?? 'editable-grid'" @keydown="onGridKeydown">
    <div class="overflow-x-auto overflow-y-visible rounded-lg border border-gray-200">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50">
          <tr>
            <th v-if="showRowNumbers" class="w-10 px-2 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              No.
            </th>
            <th
              v-for="col in columns"
              :key="col.key"
              class="px-2 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500"
              :class="alignClass(col)"
              :style="col.width ? { width: col.width } : undefined"
            >
              <span class="inline-flex items-center gap-1">
                {{ headerText(col) }}<span v-if="col.required" class="text-red-500">*</span>
                <InfoTip
                  v-if="needsTip(col)"
                  :text="col.help ?? col.label"
                  :title="col.label"
                  :statutory-ref="col.statutoryRef ?? null"
                />
              </span>
            </th>
            <th class="w-8 px-2 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="modelValue.length === 0">
            <td :colspan="columnSpan" class="px-3 py-6 text-center text-sm text-gray-400">{{ emptyText }}</td>
          </tr>
          <tr v-for="(row, i) in modelValue" :key="rowKeyOf(row, i)" class="align-top hover:bg-gray-50/60">
            <td v-if="showRowNumbers" class="px-2 py-1.5 text-xs text-gray-400 tabular-nums">{{ i + 1 }}</td>
            <td v-for="col in columns" :key="col.key" class="px-2 py-1.5" :class="alignClass(col)">
              <!-- derived: engine output, never typed -->
              <div v-if="col.type === 'derived'" class="flex items-center justify-end gap-1 py-1.5">
                <span class="tabular-nums text-gray-700">{{ derived(row, col) }}</span>
                <span class="rounded bg-gray-100 px-1 text-[9px] font-medium uppercase tracking-wide text-gray-500">
                  Auto
                </span>
                <InfoTip
                  v-if="explanation(row, col)"
                  :text="explanation(row, col) ?? ''"
                  :title="col.label"
                  :statutory-ref="col.statutoryRef ?? null"
                />
              </div>

              <SearchSelect
                v-else-if="col.type === 'select'"
                :model-value="cellString(row, col)"
                :options="optionsFor(col)"
                :disabled="disabled || col.readonly"
                :allow-free="col.allowFree ?? false"
                :placeholder="col.placeholder ?? 'Search…'"
                size="sm"
                @update:model-value="patch(i, { [col.key]: $event })"
                @select="onSelectOption(i, col, $event)"
              />

              <input
                v-else-if="col.type === 'checkbox'"
                type="checkbox"
                class="h-4 w-4 rounded border-gray-300 text-primary"
                :checked="Boolean(row[col.key])"
                :disabled="disabled || col.readonly"
                @change="onCheckbox(i, col, $event)"
              />

              <DateField
                v-else-if="col.type === 'date'"
                :model-value="cellString(row, col)"
                @update:model-value="patch(i, { [col.key]: $event })"
              />

              <input
                v-else
                :ref="(el) => setCellRef(i, col.key, el)"
                type="text"
                class="form-input py-1.5"
                :class="[
                  alignClass(col),
                  col.type === 'money' || col.type === 'number' ? 'tabular-nums' : '',
                ]"
                :inputmode="col.type === 'money' || col.type === 'number' ? 'decimal' : undefined"
                :placeholder="col.placeholder"
                :readonly="col.readonly"
                :disabled="disabled"
                :value="cellString(row, col)"
                @input="onCellInput(i, col, $event)"
                @paste="onPaste(i, col, $event)"
                @keydown.enter.prevent="onCellEnter(i, col)"
              />
            </td>
            <td class="px-2 py-1.5 text-right">
              <button
                type="button"
                class="text-gray-400 hover:text-red-600 disabled:opacity-40"
                :disabled="disabled"
                :title="`Remove row ${i + 1}`"
                :aria-label="`Remove row ${i + 1}`"
                @click="removeRow(i)"
              >
                ✕
              </button>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="hasTotals" class="border-t border-gray-200 bg-gray-50">
          <tr>
            <td v-if="showRowNumbers" class="px-2 py-2"></td>
            <td
              v-for="col in columns"
              :key="col.key"
              class="px-2 py-2 text-xs font-semibold text-gray-700"
              :class="alignClass(col)"
            >
              <span v-if="totals[col.key] !== undefined" class="tabular-nums">{{ totals[col.key] }}</span>
              <span v-else-if="col === columns[0]" class="uppercase tracking-wide text-gray-500">Total</span>
            </td>
            <td class="px-2 py-2"></td>
          </tr>
        </tfoot>
      </table>
    </div>

    <div class="mt-2 flex items-center gap-3">
      <button type="button" class="btn-secondary" :disabled="disabled" @click="addRow">{{ addLabel }}</button>
      <span class="text-xs text-gray-400">
        Enter moves down a column · Alt+N adds a row · paste a block from a spreadsheet to fill many rows
      </span>
    </div>
  </div>
</template>
