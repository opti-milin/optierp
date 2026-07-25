<script setup lang="ts">
// Job Card list — shop-floor tracking created from Work Order operations on submit.
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { JobCardListItem } from "@/types/manufacturing";

const route = useRoute();
const router = useRouter();
const rows = ref<JobCardListItem[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

async function fetchList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const workOrderId = typeof route.query.work_order_id === "string"
      ? route.query.work_order_id
      : undefined;
    rows.value = (
      await api.get<ListResponse<JobCardListItem>>("/job-cards", {
        params: { page_size: 100, work_order_id: workOrderId },
      })
    ).data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

onMounted(fetchList);
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Job Cards</h1>
        <p class="text-sm text-gray-500">
          One card per Work Order operation. Start/stop time and record completed qty on the detail page.
        </p>
      </div>
      <RouterLink
        to="/work-orders"
        class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        Work Orders
      </RouterLink>
    </div>

    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Job Card</th>
            <th class="px-4 py-2">Work Order</th>
            <th class="px-4 py-2">Operation</th>
            <th class="px-4 py-2">Workstation</th>
            <th class="px-4 py-2 text-right">For qty</th>
            <th class="px-4 py-2 text-right">Completed</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
            @click="router.push(`/job-cards/${row.id}`)"
          >
            <td class="px-4 py-1.5 font-medium text-gray-900">{{ row.name }}</td>
            <td class="px-4 py-1.5">{{ row.work_order_name ?? "—" }}</td>
            <td class="px-4 py-1.5">{{ row.operation_name ?? "—" }}</td>
            <td class="px-4 py-1.5">{{ row.workstation_name ?? "—" }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.for_quantity) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.total_completed_qty) }}</td>
            <td class="px-4 py-1.5"><StatusBadge :status="row.status" /></td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="7" class="px-4 py-8 text-center text-gray-400">
              No Job Cards yet. Submit a Work Order whose BOM has operations.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
