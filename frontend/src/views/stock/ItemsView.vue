<script setup lang="ts">
// Products & Services (Item master): searchable list. Create/view/edit via ItemFormView.

import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import PaginationFooter from "@/components/shared/PaginationFooter.vue";
import { useList } from "@/composables/useList";
import { formatCurrency } from "@/utils/format";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import type { ItemListItem } from "@/types/stock";
import ImportDataButton from "@/components/shared/ImportDataButton.vue";

const router = useRouter();
const companyCurrency = useCompanyCurrency();
const { items, total, page, pageSize, loading, filters, fetchList, goToPage } =
  useList<ItemListItem>("/items");

const search = ref("");

const columns: Column[] = [
  { key: "item_code", label: "Item Code" },
  { key: "item_name", label: "Name" },
  { key: "item_group_name", label: "Group" },
  { key: "stock_uom", label: "UOM" },
  { key: "standard_rate", label: "Selling Rate", class: "text-right" },
  { key: "is_stock_item", label: "Type" },
];

async function applySearch(): Promise<void> {
  filters.value = search.value ? { search: search.value } : {};
  page.value = 1;
  await fetchList();
}

function open(row: ItemListItem): void {
  router.push(`/items/${row.id}`);
}

onMounted(fetchList);
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Products &amp; Services</h1>
        <p class="text-sm text-gray-500">{{ total }} total (goods &amp; services)</p>
      </div>
      <div class="flex items-center gap-3">
        <input v-model="search" class="form-input w-56" placeholder="Search code or name…"
               @keyup.enter="applySearch" />
        <ImportDataButton module="stock" entity="stock_item" />
        <button class="btn-primary" @click="router.push('/items/new')">New Product / Service</button>
      </div>
    </div>

    <DataTable :columns="columns" :rows="items" :loading="loading">
      <template #cell-item_code="{ row }">
        <button class="font-medium text-primary hover:underline" @click="open(row)">
          {{ row.item_code }}
        </button>
      </template>
      <template #cell-item_group_name="{ value }">
        {{ value ?? "—" }}
      </template>
      <template #cell-standard_rate="{ value }">
        {{ formatCurrency(String(value), companyCurrency) }}
      </template>
      <template #cell-is_stock_item="{ value }">
        <span class="rounded-full px-2 py-0.5 text-xs font-medium"
              :class="value ? 'bg-blue-50 text-blue-700' : 'bg-gray-100 text-gray-600'">
          {{ value ? "Stock" : "Service" }}
        </span>
      </template>
    </DataTable>
    <PaginationFooter :page="page" :page-size="pageSize" :total="total" @go-to="goToPage" />
  </div>
</template>
