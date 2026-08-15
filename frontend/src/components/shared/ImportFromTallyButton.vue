<script setup lang="ts">
// "Import from Tally" — drop this on any module screen.
//
// The import itself is one pipeline (a Tally file is not per-module: a sales
// voucher needs its customer, its item and its tax ledger), so this does not
// fork the flow. It sends the tester to the wizard with this module's entities
// pre-selected, which is the honest version of "import Customers from here".
import { computed } from "vue";
import { useRouter } from "vue-router";
import { MODULE_ENTITY_HINTS } from "@/types/tally";

const props = withDefaults(
  defineProps<{
    /** Module key: accounting | selling | buying | stock | manufacturing */
    module: string;
    /** Optional single entity to focus, e.g. "customer". Overrides the module hint. */
    entity?: string;
    variant?: "button" | "link";
    label?: string;
  }>(),
  { variant: "button", label: "Import from Tally" },
);

const router = useRouter();

const entities = computed(() =>
  props.entity ? [props.entity] : (MODULE_ENTITY_HINTS[props.module] ?? []),
);

const hint = computed(() =>
  entities.value.length
    ? `Opens the Tally import wizard with ${entities.value.length} matching entity type(s) pre-selected`
    : "Opens the Tally import wizard",
);

function open(): void {
  void router.push({
    path: "/tally",
    query: { module: props.module, entities: entities.value.join(",") || undefined },
  });
}
</script>

<template>
  <button
    v-if="props.variant === 'button'"
    type="button"
    class="btn-secondary inline-flex items-center gap-1.5"
    :title="hint"
    @click="open"
  >
    <span aria-hidden="true">⇥</span>
    {{ props.label }}
  </button>
  <button
    v-else
    type="button"
    class="text-sm text-blue-600 hover:underline"
    :title="hint"
    @click="open"
  >
    {{ props.label }}
  </button>
</template>
