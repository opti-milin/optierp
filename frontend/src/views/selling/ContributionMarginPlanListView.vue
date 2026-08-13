<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { CmPlan } from "@/types/cm_planning";

interface ListResponse {
  items: CmPlan[];
  total: number;
  page: number;
  page_size: number;
}

const router = useRouter();
const error = ref<ErrorEnvelope | null>(null);
const rows = ref<CmPlan[]>([]);

onMounted(async () => {
  try {
    rows.value = (await api.get<ListResponse>("/cm-plans", { params: { page_size: 50 } })).data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
});
</script>

<template>
  <div class="mx-auto max-w-5xl p-6">
    <h1 class="mb-4 text-xl font-semibold text-gray-900">Contribution Margin Plans</h1>
    <p class="mb-4 text-sm text-gray-600">
      Create a plan from a Quotation via <strong>Estimate CM</strong> on the quotation form, then add
      scenarios (price, freight, material %, qty) and compare.
    </p>
    <div v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error.detail }}
    </div>
    <table class="min-w-full overflow-hidden rounded border border-gray-200 bg-white text-sm">
      <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
        <tr>
          <th class="px-3 py-2">Name</th>
          <th class="px-3 py-2">Status</th>
          <th class="px-3 py-2">Target CM1%</th>
          <th class="px-3 py-2">Min CM1%</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="r in rows"
          :key="r.id"
          class="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
          @click="router.push(`/cm-plans/${r.id}`)"
        >
          <td class="px-3 py-2 font-medium">{{ r.name }}</td>
          <td class="px-3 py-2">{{ r.docstatus === 0 ? "Draft" : r.docstatus === 1 ? "Submitted" : "Cancelled" }}</td>
          <td class="px-3 py-2">{{ r.target_cm1_pct }}</td>
          <td class="px-3 py-2">{{ r.min_cm1_pct }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="4" class="px-3 py-6 text-center text-gray-400">No plans yet</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
