<script setup lang="ts">
// Shared list view for Quotations / Sales Orders / Purchase Orders.

import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import PaginationFooter from "@/components/shared/PaginationFooter.vue";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import { useList } from "@/composables/useList";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { formatCurrency, formatDate, formatQty } from "@/utils/format";
import type { OrderListItem } from "@/types/trade";

export type OrderKind = "quotation" | "sales-order" | "purchase-order";

const props = defineProps<{ kind: OrderKind }>();
const router = useRouter();
const route = useRoute();
const companyCurrency = useCompanyCurrency();

const CONFIG = {
  quotation: {
    endpoint: "/quotations", title: "Quotations", party: "Customer",
    statuses: ["Draft", "Open", "Ordered", "Cancelled", "Expired"],
    progressLabel: null as string | null, progressKey: null as keyof OrderListItem | null,
  },
  "sales-order": {
    endpoint: "/sales-orders", title: "Sales Orders", party: "Customer",
    statuses: ["Draft", "To Deliver and Bill", "To Deliver", "To Bill", "Completed", "Cancelled"],
    progressLabel: "Delivered", progressKey: "per_delivered" as keyof OrderListItem,
  },
  "purchase-order": {
    endpoint: "/purchase-orders", title: "Purchase Orders", party: "Supplier",
    statuses: ["Draft", "To Receive and Bill", "To Receive", "To Bill", "Completed", "Cancelled"],
    progressLabel: "Received", progressKey: "per_received" as keyof OrderListItem,
  },
} as const;

const cfg = computed(() => CONFIG[props.kind]);
const { items, total, page, pageSize, loading, filters, fetchList, goToPage, reset } =
  useList<OrderListItem>(() => cfg.value.endpoint);

const statusFilter = ref("");

async function applyStatus(): Promise<void> {
  filters.value = statusFilter.value ? { status: statusFilter.value } : {};
  page.value = 1;
  await fetchList();
}

const columns = computed<Column[]>(() => {
  const cols: Column[] = [
    { key: "name", label: "Document" },
    { key: "party", label: cfg.value.party },
    { key: "posting_date", label: "Date" },
    { key: "grand_total", label: "Grand Total", class: "text-right" },
  ];
  if (cfg.value.progressKey) {
    cols.push({ key: "progress", label: cfg.value.progressLabel ?? "", class: "text-right" });
    cols.push({ key: "per_billed", label: "Billed", class: "text-right" });
  }
  cols.push({ key: "status", label: "Status" });
  return cols;
});

function partyName(row: OrderListItem): string {
  return row.customer_name ?? row.supplier_name ?? "—";
}

// Deep link support: /sales-orders?status=Overdue. Applied reactively because the
// sidebar's bare list link only drops the query without remounting the view, and
// must clear the filter that link set.
function applyStatusFromRoute(): void {
  const queryStatus = route.query.status;
  const next =
    typeof queryStatus === "string" &&
    (cfg.value.statuses as readonly string[]).includes(queryStatus)
      ? queryStatus
      : "";
  statusFilter.value = next;
  filters.value = next ? { status: next } : {};
  page.value = 1;
}

watch(() => route.query.status, () => {
  applyStatusFromRoute();
  void fetchList();
});

onMounted(() => {
  applyStatusFromRoute();
  void fetchList();
});

watch(
  () => props.kind,
  async () => {
    statusFilter.value = "";
    await reset();
  },
);
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">{{ cfg.title }}</h1>
        <p class="text-sm text-gray-500">{{ total }} total</p>
      </div>
      <div class="flex items-center gap-3">
        <select v-model="statusFilter" class="form-input w-48" @change="applyStatus">
          <option value="">All statuses</option>
          <option v-for="s in cfg.statuses" :key="s" :value="s">{{ s }}</option>
        </select>
        <button class="btn-primary" @click="router.push(`${cfg.endpoint}/new`)">
          New {{ cfg.title.slice(0, -1) }}
        </button>
      </div>
    </div>
    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      @row-click="(row) => router.push(`${cfg.endpoint}/${row.id}`)"
    >
      <template #cell-name="{ row }">
        <span class="font-medium text-gray-900">{{ row.name }}</span>
      </template>
      <template #cell-party="{ row }">
        {{ partyName(row) }}
      </template>
      <template #cell-posting_date="{ value }">
        {{ formatDate(String(value)) }}
      </template>
      <template #cell-grand_total="{ row }">
        {{ formatCurrency(row.grand_total, row.currency ?? companyCurrency) }}
      </template>
      <template #cell-progress="{ row }">
        {{ formatQty(String(row[cfg.progressKey!] ?? 0)) }}%
      </template>
      <template #cell-per_billed="{ row }">
        {{ formatQty(row.per_billed ?? "0") }}%
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="String(value)" />
      </template>
    </DataTable>
    <PaginationFooter :page="page" :page-size="pageSize" :total="total" @go-to="goToPage" />
  </div>
</template>
