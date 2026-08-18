<script setup lang="ts">
// The working-entity selector. Persistent and unmissable by design: every
// generative action in this module is scoped to whichever entity is selected, and
// acting on the wrong client is the mistake this category exists to prevent.
import { computed } from "vue";
import { useSecretarialStore } from "@/stores/secretarial";

const store = useSecretarialStore();

// A practice is picking a client; a business is looking at its own company.
// Neither audience should be shown the internal word "entity".
const heading = computed(() => (store.isPractice ? "Working on" : "Company"));

const label = computed(() => store.entity?.entity_name ?? "Nothing selected");

const subtitle = computed(() => {
  const e = store.entity;
  if (!e) return "";
  const id = e.kind === "llp" ? e.llpin : e.cin;
  return id || (e.kind === "llp" ? "LLP" : "Company");
});
</script>

<template>
  <div v-if="store.showSwitcher" class="border-b border-gray-200 px-3 py-2">
    <label class="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-400">
      {{ heading }}
    </label>
    <select
      :value="store.entityId ?? ''"
      class="w-full rounded border border-gray-300 bg-white px-2 py-1.5 text-sm"
      aria-label="Select the client you are working on"
      @change="store.setEntity(($event.target as HTMLSelectElement).value || null)"
    >
      <option v-if="!store.entities.length" value="">No clients yet</option>
      <option v-for="e in store.entities" :key="e.id" :value="e.id">
        {{ e.entity_name }}
      </option>
    </select>
    <p v-if="subtitle" class="mt-1 truncate text-[11px] text-gray-400">{{ subtitle }}</p>
  </div>

  <!-- A business has exactly one company; a picker with one option would be
       noise, but the context still has to be visible. -->
  <div v-else-if="store.entity" class="border-b border-gray-200 px-3 py-2">
    <p class="text-[11px] font-medium uppercase tracking-wide text-gray-400">{{ heading }}</p>
    <p class="truncate text-sm font-medium text-gray-800">{{ label }}</p>
    <p v-if="subtitle" class="truncate text-[11px] text-gray-400">{{ subtitle }}</p>
  </div>
</template>
