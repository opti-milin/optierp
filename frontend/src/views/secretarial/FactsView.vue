<script setup lang="ts">
// Threshold figures for the working entity. Practice tenants type them in for
// managed clients; a business with books in this account can recompute from the
// ledger. Same panel either way.
import FinancialFactsPanel from "@/components/secretarial/FinancialFactsPanel.vue";
import { useSecretarialStore } from "@/stores/secretarial";

const store = useSecretarialStore();
void store.load();
</script>

<template>
  <div class="max-w-4xl">
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">Financial figures</h1>
      <p class="text-sm text-gray-500">
        {{ store.entity?.entity_name ?? "No entity selected" }} — the numbers CSR, XBRL and
        similar thresholds are judged against.
      </p>
    </div>
    <p v-if="!store.entityId" class="rounded border border-gray-200 bg-white p-4 text-sm text-gray-500">
      Pick a company in the sidebar first.
    </p>
    <FinancialFactsPanel
      v-else
      :entity-id="store.entityId"
      :linked="Boolean(store.entity?.linked_company_id)"
    />
  </div>
</template>
