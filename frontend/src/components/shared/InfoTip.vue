<script setup lang="ts">
// The full business term behind a short grid header, a derived value or a
// statutory reference. Opens on hover and on keyboard focus, closes on Escape.
// No native `title` — that black browser tooltip duplicated the white tip.

import { computed, ref } from "vue";

const props = withDefaults(defineProps<{
  text: string;
  title?: string;
  statutoryRef?: string | null;
  size?: "sm" | "md";
}>(), {
  size: "sm",
});

const open = ref(false);

const buttonSize = computed(() =>
  props.size === "md" ? "h-5 w-5 text-[11px]" : "h-4 w-4 text-[10px]",
);
</script>

<template>
  <span class="relative inline-flex align-middle">
    <button
      type="button"
      tabindex="0"
      data-testid="info-tip"
      class="inline-flex items-center justify-center rounded-full border border-gray-300 font-semibold leading-none text-gray-500 hover:border-primary hover:text-primary focus:outline-none focus:ring-1 focus:ring-primary"
      :class="buttonSize"
      :aria-label="title ? `${title}: ${text}` : text"
      :aria-expanded="open"
      @mouseenter="open = true"
      @mouseleave="open = false"
      @focus="open = true"
      @blur="open = false"
      @keydown.escape.stop="open = false"
      @click.prevent.stop="open = !open"
    >
      i
    </button>
    <span
      v-if="open"
      role="tooltip"
      class="absolute left-1/2 top-full z-40 mt-1 w-64 -translate-x-1/2 rounded-md border border-gray-200 bg-white p-2.5 text-left text-xs font-normal normal-case tracking-normal text-gray-700 shadow-lg"
    >
      <span v-if="title" class="mb-1 block font-semibold text-gray-900">{{ title }}</span>
      <span class="block">{{ text }}</span>
      <span
        v-if="statutoryRef"
        class="mt-1.5 inline-block rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-600"
      >{{ statutoryRef }}</span>
    </span>
  </span>
</template>
