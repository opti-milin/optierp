<script setup lang="ts">
// Searchable select over TaxOption[] — the replacement for free-text typing of
// section codes, block codes, heads, major/minor heads and account links.
//
// Filters on label + value + hint, renders group headers and the statutory
// reference chip, and is fully keyboard driven (arrows, Enter, Escape).

import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import type { TaxOption } from "@/types/taxation";

const MAX_RENDERED = 60;

const props = withDefaults(defineProps<{
  modelValue: string | null | undefined;
  options: TaxOption[];
  placeholder?: string;
  disabled?: boolean;
  allowFree?: boolean;
  clearable?: boolean;
  invalid?: boolean;
  size?: "sm" | "md";
  emptyText?: string;
  testid?: string;
}>(), {
  placeholder: "Search…",
  disabled: false,
  allowFree: false,
  clearable: true,
  invalid: false,
  size: "md",
  emptyText: "No matches",
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
  select: [option: TaxOption | null];
}>();

const root = ref<HTMLElement | null>(null);
const inputEl = ref<HTMLInputElement | null>(null);
const listEl = ref<HTMLElement | null>(null);
const open = ref(false);
const query = ref("");
const highlight = ref(0);

const selected = computed<TaxOption | null>(
  () => props.options.find((opt) => opt.value === props.modelValue) ?? null,
);

const selectedLabel = computed<string>(() => {
  if (selected.value) return selected.value.label;
  // A free-entry value has no option behind it, so show what was typed.
  return props.allowFree ? props.modelValue ?? "" : "";
});

const display = computed<string>(() => (open.value ? query.value : selectedLabel.value));

const shownPlaceholder = computed<string>(() =>
  open.value && selectedLabel.value ? selectedLabel.value : props.placeholder,
);

function haystack(opt: TaxOption): string {
  return `${opt.label} ${opt.value} ${opt.hint ?? ""}`.toLowerCase();
}

const filtered = computed<TaxOption[]>(() => {
  const q = query.value.trim().toLowerCase();
  const base = q ? props.options.filter((opt) => haystack(opt).includes(q)) : props.options;
  return base.slice(0, MAX_RENDERED);
});

interface OptionRow {
  option: TaxOption;
  index: number;
}

const groups = computed<{ title: string | null; items: OptionRow[] }[]>(() => {
  const rows: OptionRow[] = filtered.value.map((option, index) => ({ option, index }));
  if (!rows.some((row) => row.option.group)) return [{ title: null, items: rows }];
  const order: string[] = [];
  const buckets = new Map<string, OptionRow[]>();
  for (const row of rows) {
    const title = row.option.group ?? "Other";
    if (!buckets.has(title)) {
      buckets.set(title, []);
      order.push(title);
    }
    buckets.get(title)?.push(row);
  }
  return order.map((title) => ({ title, items: buckets.get(title) ?? [] }));
});

const exactFreeMatch = computed<boolean>(() =>
  props.options.some((opt) => opt.label.trim().toLowerCase() === query.value.trim().toLowerCase()),
);

const sizeClass = computed(() => (props.size === "sm" ? "py-1 text-sm" : "py-2 text-sm"));

async function scrollHighlightIntoView(): Promise<void> {
  await nextTick();
  const el = listEl.value?.querySelector<HTMLElement>(`[data-option-index="${highlight.value}"]`);
  el?.scrollIntoView({ block: "nearest" });
}

function openList(): void {
  if (props.disabled) return;
  query.value = "";
  open.value = true;
  const pos = filtered.value.findIndex((opt) => opt.value === props.modelValue);
  highlight.value = pos >= 0 ? pos : 0;
  void scrollHighlightIntoView();
}

function close(): void {
  open.value = false;
  query.value = "";
}

function choose(option: TaxOption): void {
  emit("update:modelValue", option.value);
  emit("select", option);
  close();
}

function clear(): void {
  if (props.disabled) return;
  emit("update:modelValue", "");
  emit("select", null);
  close();
  inputEl.value?.focus();
}

function onInput(event: Event): void {
  query.value = (event.target as HTMLInputElement).value;
  open.value = true;
  highlight.value = 0;
}

function move(delta: number): void {
  if (!open.value) {
    openList();
    return;
  }
  const count = filtered.value.length;
  if (count === 0) return;
  highlight.value = (highlight.value + delta + count) % count;
  void scrollHighlightIntoView();
}

function onEnter(): void {
  if (!open.value) {
    openList();
    return;
  }
  const option = filtered.value[highlight.value];
  if (option) {
    choose(option);
    return;
  }
  const typed = query.value.trim();
  if (props.allowFree && typed && !exactFreeMatch.value) {
    emit("update:modelValue", typed);
    emit("select", null);
    close();
  }
}

function onDocumentMousedown(event: MouseEvent): void {
  if (!open.value) return;
  if (root.value && !root.value.contains(event.target as Node)) close();
}

onMounted(() => document.addEventListener("mousedown", onDocumentMousedown));
onBeforeUnmount(() => document.removeEventListener("mousedown", onDocumentMousedown));
</script>

<template>
  <div ref="root" class="relative" :data-testid="testid ?? 'search-select'">
    <input
      ref="inputEl"
      class="form-input"
      :class="[
        sizeClass,
        invalid ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : '',
        clearable && modelValue ? 'pr-7' : '',
      ]"
      role="combobox"
      autocomplete="off"
      :aria-expanded="open"
      :value="display"
      :placeholder="shownPlaceholder"
      :disabled="disabled"
      @focus="openList"
      @input="onInput"
      @keydown.down.prevent="move(1)"
      @keydown.up.prevent="move(-1)"
      @keydown.enter.prevent="onEnter"
      @keydown.esc.prevent="close"
      @keydown.tab="close"
    />
    <button
      v-if="clearable && modelValue && !disabled"
      type="button"
      tabindex="-1"
      class="absolute inset-y-0 right-0 flex items-center px-2 text-gray-400 hover:text-red-600"
      aria-label="Clear selection"
      @mousedown.prevent="clear"
    >
      ✕
    </button>

    <div
      v-if="open"
      ref="listEl"
      class="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded-md border border-gray-200 bg-white text-left shadow-lg"
      role="listbox"
    >
      <p v-if="filtered.length === 0" class="px-3 py-2 text-xs text-gray-400">
        {{ allowFree && query.trim() ? `Press Enter to use “${query.trim()}”` : emptyText }}
      </p>
      <template v-for="group in groups" :key="group.title ?? '_'">
        <p
          v-if="group.title"
          class="sticky top-0 bg-gray-50 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-gray-500"
        >
          {{ group.title }}
        </p>
        <div
          v-for="row in group.items"
          :key="row.option.value"
          :data-option-index="row.index"
          role="option"
          :aria-selected="row.option.value === modelValue"
          class="cursor-pointer border-b border-gray-50 px-3 py-1.5 last:border-0"
          :class="row.index === highlight ? 'bg-primary/10' : 'hover:bg-primary/5'"
          @mousedown.prevent="choose(row.option)"
          @mousemove="highlight = row.index"
        >
          <div class="flex items-start justify-between gap-2">
            <span class="text-sm text-gray-800">{{ row.option.label }}</span>
            <span
              v-if="row.option.statutory_ref"
              class="mt-0.5 shrink-0 rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-600"
            >{{ row.option.statutory_ref }}</span>
          </div>
          <p v-if="row.option.hint" class="text-xs text-gray-500">{{ row.option.hint }}</p>
        </div>
      </template>
    </div>
  </div>
</template>
