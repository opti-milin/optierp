<script setup lang="ts">
// Material Request: list. Create/view via MaterialRequestFormView.

import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import PaginationFooter from "@/components/shared/PaginationFooter.vue";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import { useList } from "@/composables/useList";
import { api } from "@/api/client";
import { formatDate, formatQty } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { MaterialRequestListItem } from "@/types/stock";

const router = useRouter();
const route = useRoute();
const { items, total, page, pageSize, loading, fetchList, goToPage } =
  useList<MaterialRequestListItem>("/material-requests");
const error = ref<ErrorEnvelope | null>(null);
// Confirmation banner when redirected from Reorder. Kept in sync with the query
// because the sidebar's bare list link only drops it without remounting the view
// (a ref rather than a computed so Dismiss can clear the banner).
const createdNotice = ref<string | null>(route.query.created ? String(route.query.created) : null);
watch(
  () => route.query.created,
  (created) => {
    createdNotice.value = created ? String(created) : null;
  },
);

const columns: Column[] = [
  { key: "name", label: "Request" },
  { key: "posting_date", label: "Date" },
  { key: "material_request_type", label: "Type" },
  { key: "per_ordered", label: "Ordered", class: "text-right" },
  { key: "status", label: "Status" },
];

function open(row: MaterialRequestListItem): void {
  router.push(`/material-requests/${row.id}`);
}

async function rowAction(row: MaterialRequestListItem, action: "submit"): Promise<void> {
  error.value = null;
  try {
    await api.post(`/material-requests/${row.id}/${action}`);
    await fetchList();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

function orderIt(row: MaterialRequestListItem): void {
  router.push({ name: "purchase-order-new", query: { material_request_id: row.id } });
}

onMounted(fetchList);
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Material Requests</h1>
        <p class="text-sm text-gray-500">{{ total }} total</p>
      </div>
      <button class="btn-primary" @click="router.push('/material-requests/new')">New Request</button>
    </div>

    <div v-if="createdNotice"
         class="mb-4 flex items-center justify-between rounded-lg border border-green-200 bg-green-50 px-4 py-2 text-sm text-green-800">
      <span>Drafted <strong>{{ createdNotice }}</strong> from reorder levels — open it to review and submit.</span>
      <button class="text-green-700 hover:underline" @click="createdNotice = null">Dismiss</button>
    </div>
    <p v-if="error" class="mb-2 text-sm text-red-600">{{ error.detail }}</p>

    <DataTable :columns="columns" :rows="items" :loading="loading">
      <template #cell-name="{ row }">
        <button class="font-medium text-primary hover:underline" @click="open(row)">{{ row.name }}</button>
      </template>
      <template #cell-posting_date="{ value }">{{ formatDate(String(value)) }}</template>
      <template #cell-per_ordered="{ value }">{{ formatQty(value as string) }}%</template>
      <template #cell-status="{ value, row }">
        <div class="flex items-center gap-2">
          <StatusBadge :status="String(value)" />
          <button v-if="row.docstatus === 0" class="text-xs text-primary hover:underline" @click.stop="rowAction(row, 'submit')">submit</button>
          <button v-if="row.docstatus === 1 && row.status !== 'Ordered'" class="text-xs text-primary hover:underline" @click.stop="orderIt(row)">order</button>
        </div>
      </template>
    </DataTable>
    <PaginationFooter :page="page" :page-size="pageSize" :total="total" @go-to="goToPage" />
  </div>
</template>
