<script setup lang="ts">
// Warehouse master: list. Create/view/edit via WarehouseFormView.

import { onMounted } from "vue";
import { useRouter } from "vue-router";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import { useStockStore } from "@/stores/stock";
import type { Warehouse } from "@/types/stock";
import ImportDataButton from "@/components/shared/ImportDataButton.vue";

const router = useRouter();
const store = useStockStore();

const columns: Column[] = [
  { key: "warehouse_name", label: "Warehouse" },
  { key: "warehouse_type", label: "Type" },
  { key: "is_group", label: "Kind" },
];

function open(row: Warehouse): void {
  router.push(`/warehouses/${row.id}`);
}

onMounted(() => store.fetchWarehouses());
</script>

<template>
  <div class="max-w-4xl">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Warehouses</h1>
        <p class="text-sm text-gray-500">{{ store.warehouses.length }} total</p>
      </div>
      <div class="flex items-center gap-2">
        <ImportDataButton module="stock" entity="godown" />
        <button class="btn-primary" @click="router.push('/warehouses/new')">New Warehouse</button>
      </div>
    </div>

    <DataTable :columns="columns" :rows="store.warehouses" :loading="false">
      <template #cell-warehouse_name="{ row }">
        <button class="font-medium text-primary hover:underline" @click="open(row)">
          {{ row.warehouse_name }}
        </button>
      </template>
      <template #cell-warehouse_type="{ value }">
        {{ value ?? "—" }}
      </template>
      <template #cell-is_group="{ value }">
        <span class="rounded-full px-2 py-0.5 text-xs font-medium"
              :class="value ? 'bg-purple-50 text-purple-700' : 'bg-gray-100 text-gray-600'">
          {{ value ? "Group" : "Storage" }}
        </span>
      </template>
    </DataTable>
  </div>
</template>
