<script setup lang="ts">
// Status-aware left navigation for the Income Tax workspace. Section status is
// computed server-side from the same facts the validator uses, so this list and
// the Review panel can never disagree.

import { computed, ref } from "vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import type { TaxSectionStatusInfo } from "@/types/taxation";

const props = defineProps<{
  sections: TaxSectionStatusInfo[];
  active: string;
  groups?: { title: string; keys: string[] }[];
}>();

const emit = defineEmits<{
  select: [key: string];
}>();

const buttons = ref(new Map<string, HTMLButtonElement>());

function setButtonRef(key: string, el: Element | null): void {
  if (el instanceof HTMLButtonElement) buttons.value.set(key, el);
  else buttons.value.delete(key);
}

const renderedGroups = computed<{ title: string | null; items: TaxSectionStatusInfo[] }[]>(() => {
  if (!props.groups || props.groups.length === 0) {
    return [{ title: null, items: props.sections }];
  }
  const byKey = new Map(props.sections.map((section) => [section.key, section]));
  const grouped = props.groups.map((group) => ({
    title: group.title,
    items: group.keys.map((key) => byKey.get(key)).filter((s): s is TaxSectionStatusInfo => Boolean(s)),
  }));
  const claimed = new Set(props.groups.flatMap((group) => group.keys));
  const rest = props.sections.filter((section) => !claimed.has(section.key));
  if (rest.length > 0) grouped.push({ title: "Other", items: rest });
  return grouped.filter((group) => group.items.length > 0);
});

const order = computed<string[]>(() =>
  renderedGroups.value.flatMap((group) => group.items.map((item) => item.key)),
);

function onKeydown(event: KeyboardEvent, key: string): void {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault();
  const keys = order.value;
  const at = keys.indexOf(key);
  if (at < 0) return;
  const delta = event.key === "ArrowDown" ? 1 : -1;
  const next = keys[(at + delta + keys.length) % keys.length];
  buttons.value.get(next)?.focus();
}
</script>

<template>
  <nav data-testid="section-nav" aria-label="Computation sections" class="space-y-4">
    <div v-for="group in renderedGroups" :key="group.title ?? '_'">
      <p
        v-if="group.title"
        class="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400"
      >
        {{ group.title }}
      </p>
      <ul class="space-y-0.5">
        <li v-for="section in group.items" :key="section.key">
          <button
            :ref="(el) => setButtonRef(section.key, el as Element | null)"
            type="button"
            :data-section-key="section.key"
            :aria-current="section.key === active ? 'page' : undefined"
            class="w-full rounded-md px-2 py-1.5 text-left transition focus:outline-none focus:ring-1 focus:ring-primary"
            :class="section.key === active ? 'bg-primary/10 ring-1 ring-primary/30' : 'hover:bg-gray-50'"
            @click="emit('select', section.key)"
            @keydown="onKeydown($event, section.key)"
          >
            <span class="flex min-w-0 items-start justify-between gap-2">
              <span
                class="min-w-0 flex-1 break-words text-sm leading-snug"
                :class="section.key === active ? 'font-semibold text-gray-900' : 'text-gray-700'"
                :title="section.label"
              >{{ section.label }}</span>
              <span class="flex shrink-0 items-center gap-1 pt-0.5">
                <span
                  v-if="section.blocking_count > 0"
                  class="rounded-full bg-red-600 px-1.5 text-[10px] font-semibold text-white tabular-nums"
                  :title="`${section.blocking_count} issue(s) to resolve`"
                >{{ section.blocking_count }}</span>
                <StatusPill :status="section.status" />
              </span>
            </span>
            <span v-if="section.summary" class="mt-0.5 block truncate text-xs text-gray-400">
              {{ section.summary }}
            </span>
          </button>
        </li>
      </ul>
    </div>
  </nav>
</template>
