<script setup lang="ts">
// A value the engine derives — never typed by the user. Carries an "Auto" chip
// (title: Auto-calculated) so nobody hunts for an input that does not exist,
// plus an InfoTip explaining how the figure was reached.

import { computed } from "vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import { formatCurrency, formatDate, formatNumber } from "@/utils/format";

const props = withDefaults(defineProps<{
  label: string;
  value: string | number | null | undefined;
  short?: string;
  explain?: string | null;
  statutoryRef?: string | null;
  kind?: "money" | "number" | "percent" | "text" | "date";
  emphasis?: "normal" | "strong";
  currency?: string;
  testid?: string;
}>(), {
  kind: "money",
  emphasis: "normal",
  currency: "INR",
});

const shown = computed(() => {
  const raw = props.value;
  if (raw == null || raw === "") return "—";
  switch (props.kind) {
    case "money":
      return formatCurrency(raw, props.currency);
    case "number":
      return formatNumber(raw);
    case "percent":
      return `${formatNumber(raw)}%`;
    case "date":
      return formatDate(String(raw));
    default:
      return String(raw);
  }
});

const numeric = computed(() => props.kind === "money" || props.kind === "number" || props.kind === "percent");
</script>

<template>
  <div class="min-w-0" :data-testid="testid ?? 'derived-field'">
    <div class="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
      <span class="form-label mb-0 min-w-0 break-words">{{ short ?? label }}</span>
      <span
        class="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-gray-500"
        title="Auto-calculated"
      >
        Auto
      </span>
      <InfoTip
        v-if="explain || statutoryRef"
        :text="explain ?? label"
        :title="label"
        :statutory-ref="statutoryRef ?? null"
      />
    </div>
    <div
      class="mt-1 break-words rounded-md border border-dashed border-gray-300 bg-gray-50 px-3 py-2 text-sm"
      :class="[
        numeric ? 'text-right tabular-nums' : 'text-left',
        emphasis === 'strong' ? 'font-semibold text-gray-900' : 'text-gray-700',
      ]"
    >
      {{ shown }}
    </div>
  </div>
</template>
