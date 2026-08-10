<script setup lang="ts">
// Ctrl/⌘ K action list. Every workspace action — jump to a section, recompute,
// copy last year, populate from books, run validation — is reachable by name
// without hunting for the button that triggers it.

import { computed, nextTick, ref, watch } from "vue";

export interface CommandAction {
  id: string;
  label: string;
  hint?: string;
  group?: string;
  keywords?: string;
  shortcut?: string;
  run: () => void | Promise<void>;
}

const props = defineProps<{
  open: boolean;
  actions: CommandAction[];
  placeholder?: string;
}>();

const emit = defineEmits<{
  close: [];
}>();

const inputEl = ref<HTMLInputElement | null>(null);
const listEl = ref<HTMLElement | null>(null);
const query = ref("");
const highlight = ref(0);

function haystack(action: CommandAction): string {
  return `${action.label} ${action.hint ?? ""} ${action.keywords ?? ""}`.toLowerCase();
}

const filtered = computed<CommandAction[]>(() => {
  const terms = query.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return props.actions;
  return props.actions.filter((action) => {
    const text = haystack(action);
    return terms.every((term) => text.includes(term));
  });
});

interface ActionRow {
  action: CommandAction;
  index: number;
}

const groups = computed<{ title: string | null; items: ActionRow[] }[]>(() => {
  const rows: ActionRow[] = filtered.value.map((action, index) => ({ action, index }));
  if (!rows.some((row) => row.action.group)) return [{ title: null, items: rows }];
  const order: string[] = [];
  const buckets = new Map<string, ActionRow[]>();
  for (const row of rows) {
    const title = row.action.group ?? "Other";
    if (!buckets.has(title)) {
      buckets.set(title, []);
      order.push(title);
    }
    buckets.get(title)?.push(row);
  }
  return order.map((title) => ({ title, items: buckets.get(title) ?? [] }));
});

watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    query.value = "";
    highlight.value = 0;
    await nextTick();
    inputEl.value?.focus();
  },
);

async function scrollHighlightIntoView(): Promise<void> {
  await nextTick();
  const el = listEl.value?.querySelector<HTMLElement>(`[data-action-index="${highlight.value}"]`);
  el?.scrollIntoView({ block: "nearest" });
}

function move(delta: number): void {
  const count = filtered.value.length;
  if (count === 0) return;
  highlight.value = (highlight.value + delta + count) % count;
  void scrollHighlightIntoView();
}

async function run(action: CommandAction): Promise<void> {
  try {
    await action.run();
  } finally {
    emit("close");
  }
}

function onEnter(): void {
  const action = filtered.value[highlight.value];
  if (action) void run(action);
}

function onInput(event: Event): void {
  query.value = (event.target as HTMLInputElement).value;
  highlight.value = 0;
}
</script>

<template>
  <div
    v-if="open"
    data-testid="command-palette"
    class="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-24"
    role="dialog"
    aria-modal="true"
    aria-label="Command palette"
    @click.self="emit('close')"
    @keydown.esc.prevent="emit('close')"
  >
    <div class="w-full max-w-xl overflow-hidden rounded-lg border border-gray-200 bg-white shadow-2xl">
      <div class="border-b border-gray-100 p-2">
        <input
          ref="inputEl"
          class="form-input border-0 shadow-none focus:ring-0"
          autocomplete="off"
          :placeholder="placeholder ?? 'Type a command…'"
          :value="query"
          @input="onInput"
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.enter.prevent="onEnter"
          @keydown.esc.prevent="emit('close')"
        />
      </div>
      <div ref="listEl" class="max-h-80 overflow-y-auto py-1">
        <p v-if="filtered.length === 0" class="px-4 py-6 text-center text-sm text-gray-400">
          No matching action
        </p>
        <template v-for="group in groups" :key="group.title ?? '_'">
          <p
            v-if="group.title"
            class="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400"
          >
            {{ group.title }}
          </p>
          <button
            v-for="row in group.items"
            :key="row.action.id"
            type="button"
            :data-action-index="row.index"
            class="flex w-full items-center justify-between gap-3 px-4 py-2 text-left"
            :class="row.index === highlight ? 'bg-primary/10' : 'hover:bg-gray-50'"
            @mousemove="highlight = row.index"
            @click="run(row.action)"
          >
            <span class="min-w-0">
              <span class="block truncate text-sm text-gray-800">{{ row.action.label }}</span>
              <span v-if="row.action.hint" class="block truncate text-xs text-gray-500">{{ row.action.hint }}</span>
            </span>
            <span
              v-if="row.action.shortcut"
              class="shrink-0 rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 font-mono text-[10px] text-gray-500"
            >{{ row.action.shortcut }}</span>
          </button>
        </template>
      </div>
    </div>
  </div>
</template>
