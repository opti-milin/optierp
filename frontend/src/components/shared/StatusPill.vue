<script setup lang="ts">
// Section completion status / document status pill for the Income Tax module.
// Labels resolve through TAX_TERMS so "has-errors" reads "Needs attention".

import { computed } from "vue";
import { taxLabel } from "@/config/taxTerminology";

const props = withDefaults(defineProps<{
  status: string;
  label?: string;
  count?: number | null;
  size?: "sm" | "md";
}>(), {
  size: "sm",
});

const TONES: Record<string, string> = {
  complete: "bg-green-100 text-green-800",
  Submitted: "bg-green-100 text-green-800",
  Filed: "bg-green-100 text-green-800",
  "in-progress": "bg-amber-100 text-amber-800",
  Draft: "bg-sky-100 text-sky-800",
  Computed: "bg-sky-100 text-sky-800",
  "has-errors": "bg-red-100 text-red-700",
  Cancelled: "bg-red-100 text-red-700",
  "not-started": "bg-slate-100 text-slate-600",
};

const tone = computed(() => TONES[props.status] ?? "bg-slate-100 text-slate-600");

const text = computed(() => props.label ?? taxLabel(`status.${props.status}`, props.status));

const sizing = computed(() =>
  props.size === "md" ? "px-2.5 py-1 text-xs" : "px-2 py-0.5 text-[11px]",
);
</script>

<template>
  <span
    data-testid="status-pill"
    class="inline-flex items-center gap-1 whitespace-nowrap rounded-full font-medium"
    :class="[tone, sizing]"
  >
    {{ text }}
    <span v-if="count != null" class="rounded-full bg-white/70 px-1 font-semibold tabular-nums">{{ count }}</span>
  </span>
</template>
