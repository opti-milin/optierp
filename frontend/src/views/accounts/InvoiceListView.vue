<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import PaginationFooter from "@/components/shared/PaginationFooter.vue";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import { useList } from "@/composables/useList";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { formatCurrency, formatDate } from "@/utils/format";
import type { InvoiceListItem } from "@/types/accounts";

const props = defineProps<{ kind: "sales" | "purchase" }>();
const router = useRouter();
const route = useRoute();
const companyCurrency = useCompanyCurrency();

const endpoint = computed(() => (props.kind === "sales" ? "/sales-invoices" : "/purchase-invoices"));
const title = computed(() => (props.kind === "sales" ? "Sales Invoices" : "Purchase Invoices"));
const partyLabel = computed(() => (props.kind === "sales" ? "Customer" : "Supplier"));

const { items, total, page, pageSize, loading, filters, fetchList, goToPage, reset } =
  useList<InvoiceListItem>(() => endpoint.value);

const STATUSES = ["Draft", "Unpaid", "Overdue", "Partly Paid", "Paid", "Cancelled", "Return"];
const statusFilter = ref("");

async function applyStatus(): Promise<void> {
  filters.value = statusFilter.value ? { status: statusFilter.value } : {};
  page.value = 1;
  await fetchList();
}

const columns = computed<Column[]>(() => [
  { key: "name", label: "Invoice" },
  { key: "party", label: partyLabel.value },
  { key: "posting_date", label: "Date" },
  { key: "due_date", label: "Due Date" },
  { key: "grand_total", label: "Grand Total", class: "text-right" },
  { key: "outstanding_amount", label: "Outstanding", class: "text-right" },
  { key: "status", label: "Status" },
]);

function partyName(row: InvoiceListItem): string {
  return (props.kind === "sales" ? row.customer_name : row.supplier_name) ?? "—";
}

// Deep link support: /sales-invoices?status=Overdue (e.g. from the dashboard).
// Applied reactively because the sidebar's bare list link only drops the query
// without remounting the view, and must clear the filter that link set.
function applyStatusFromRoute(): void {
  const queryStatus = route.query.status;
  const next =
    typeof queryStatus === "string" && STATUSES.includes(queryStatus) ? queryStatus : "";
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
        <h1 class="text-xl font-semibold text-gray-900">{{ title }}</h1>
        <p class="text-sm text-gray-500">{{ total }} total</p>
      </div>
      <div class="flex items-center gap-3">
        <select v-model="statusFilter" class="form-input w-40" @change="applyStatus">
          <option value="">All statuses</option>
          <option v-for="s in STATUSES" :key="s" :value="s">{{ s }}</option>
        </select>
        <button class="btn-primary" @click="router.push(`${endpoint}/new`)">New Invoice</button>
      </div>
    </div>
    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      @row-click="(row) => router.push(`${endpoint}/${row.id}`)"
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
      <template #cell-due_date="{ value }">
        {{ formatDate(value ? String(value) : null) }}
      </template>
      <template #cell-grand_total="{ row }">
        {{ formatCurrency(row.grand_total, row.currency ?? companyCurrency) }}
      </template>
      <template #cell-outstanding_amount="{ row }">
        <!-- outstanding is tracked in company (base) currency -->
        <span :class="Number(row.outstanding_amount) > 0 ? 'font-medium text-gray-900' : 'text-gray-400'">
          {{ formatCurrency(row.outstanding_amount, companyCurrency) }}
        </span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="String(value)" />
      </template>
    </DataTable>
    <PaginationFooter :page="page" :page-size="pageSize" :total="total" @go-to="goToPage" />
  </div>
</template>
