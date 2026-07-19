<script setup lang="ts">
// Manufacturing reports (tabbed, URL-driven): Production Register, BOM Where-Used, and the
// BOM Stock "can I build N?" report.
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatCurrency, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  BomListItem,
  BomStockReport,
  BomWhereUsedRow,
  MaterialShortageRow,
  ProductionRegisterRow,
} from "@/types/manufacturing";

interface ItemOpt { id: string; item_code: string; item_name: string }

const route = useRoute();
const router = useRouter();

const TABS = [
  { key: "production", label: "Production Register" },
  { key: "shortage", label: "Material Shortage" },
  { key: "where-used", label: "BOM Where-Used" },
  { key: "bom-stock", label: "BOM Stock (Can I build?)" },
];
const tab = ref<string>((route.query.tab as string) || "production");

const error = ref<ErrorEnvelope | null>(null);
const items = ref<ItemOpt[]>([]);
const boms = ref<BomListItem[]>([]);

const register = ref<ProductionRegisterRow[]>([]);
const shortage = ref<MaterialShortageRow[]>([]);
const onlyShort = ref(false);
const whereUsedItem = ref("");
const whereUsed = ref<BomWhereUsedRow[]>([]);
const stockBom = ref("");
const stockQty = ref<number>(1);
const stockReport = ref<BomStockReport | null>(null);

function switchTab(key: string): void {
  tab.value = key;
  void router.replace({ query: { ...route.query, tab: key } });
  if (key === "shortage") void loadShortage();
}
watch(() => route.query.tab, (t) => {
  if (t) {
    tab.value = t as string;
    if (t === "shortage") void loadShortage();
  }
});

async function loadShortage(): Promise<void> {
  error.value = null;
  try {
    shortage.value = (
      await api.get<MaterialShortageRow[]>("/manufacturing-reports/material-shortage", {
        params: { only_short: onlyShort.value },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadRegister(): Promise<void> {
  error.value = null;
  try {
    register.value = (
      await api.get<ProductionRegisterRow[]>("/manufacturing-reports/production-register")
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadWhereUsed(): Promise<void> {
  if (!whereUsedItem.value) { whereUsed.value = []; return; }
  error.value = null;
  try {
    whereUsed.value = (
      await api.get<BomWhereUsedRow[]>("/manufacturing-reports/bom-where-used", {
        params: { item_id: whereUsedItem.value },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadBomStock(): Promise<void> {
  if (!stockBom.value) { stockReport.value = null; return; }
  error.value = null;
  try {
    stockReport.value = (
      await api.get<BomStockReport>("/manufacturing-reports/bom-stock", {
        params: { bom_id: stockBom.value, for_qty: stockQty.value || 1 },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

onMounted(async () => {
  const [it, bm] = await Promise.all([
    api.get<ListResponse<ItemOpt>>("/items", { params: { page_size: 200 } }),
    api.get<ListResponse<BomListItem>>("/boms", { params: { page_size: 200 } }),
  ]);
  items.value = it.data.items;
  boms.value = bm.data.items;
  await loadRegister();
  if (tab.value === "shortage") await loadShortage();
});
</script>

<template>
  <div>
    <h1 class="mb-1 text-xl font-semibold text-gray-900">Manufacturing Reports</h1>
    <div class="mb-4 flex gap-1 border-b border-gray-200">
      <button
        v-for="t in TABS"
        :key="t.key"
        class="border-b-2 px-4 py-2 text-sm font-medium"
        :class="tab === t.key ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
        @click="switchTab(t.key)"
      >
        {{ t.label }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <!-- Production Register -->
    <div v-if="tab === 'production'" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Work Order</th>
            <th class="px-4 py-2">Finished good</th>
            <th class="px-4 py-2 text-right">Qty</th>
            <th class="px-4 py-2 text-right">Produced</th>
            <th class="px-4 py-2 text-right">Pending</th>
            <th class="px-4 py-2 text-right">Est. cost</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in register" :key="r.work_order_id" class="cursor-pointer border-t border-gray-100 hover:bg-gray-50" @click="router.push(`/work-orders/${r.work_order_id}`)">
            <td class="px-4 py-1.5 font-medium text-gray-900">{{ r.name }}</td>
            <td class="px-4 py-1.5">{{ r.production_item_name ?? r.production_item_code }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.produced_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.pending_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(r.estimated_cost) }}</td>
            <td class="px-4 py-1.5"><StatusBadge :status="r.status" /></td>
          </tr>
          <tr v-if="!register.length"><td colspan="7" class="px-4 py-8 text-center text-gray-400">No Work Orders.</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Material Shortage (all open Work Orders) -->
    <div v-else-if="tab === 'shortage'">
      <div class="mb-3 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          Still-to-consume component demand across every open Work Order vs on-hand stock.
        </p>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="onlyShort" type="checkbox" @change="loadShortage" /> Only shortfalls
        </label>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Component</th>
              <th class="px-4 py-2">Warehouse</th>
              <th class="px-4 py-2 text-right">Pending</th>
              <th class="px-4 py-2 text-right">Available</th>
              <th class="px-4 py-2 text-right">Shortfall</th>
              <th class="px-4 py-2">Needed by</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in shortage" :key="`${r.item_id}-${r.warehouse_id ?? 'all'}`" class="border-t border-gray-100">
              <td class="px-4 py-1.5">
                <div>{{ r.item_name ?? r.item_code }}</div>
                <div class="text-xs text-gray-400">{{ r.item_code }}</div>
              </td>
              <td class="px-4 py-1.5 text-gray-500">{{ r.warehouse_name ?? "All warehouses" }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.pending_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.available_qty) }}</td>
              <td class="px-4 py-1.5 text-right">
                <span v-if="Number(r.shortfall_qty) > 0" class="font-medium text-red-600">{{ formatQty(r.shortfall_qty) }}</span>
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="px-4 py-1.5 text-xs text-gray-500">{{ r.work_orders.join(", ") }}</td>
            </tr>
            <tr v-if="!shortage.length">
              <td colspan="6" class="px-4 py-8 text-center text-gray-400">
                {{ onlyShort ? "No shortfalls — every open Work Order is fully stocked." : "No open Work Orders." }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- BOM Where-Used -->
    <div v-else-if="tab === 'where-used'">
      <div class="mb-3 max-w-md">
        <label class="form-label">Component item</label>
        <select v-model="whereUsedItem" class="form-input" @change="loadWhereUsed">
          <option value="" disabled>Select an item…</option>
          <option v-for="i in items" :key="i.id" :value="i.id">{{ i.item_code }} — {{ i.item_name }}</option>
        </select>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">BOM</th>
              <th class="px-4 py-2">Finished good</th>
              <th class="px-4 py-2 text-right">Qty / batch</th>
              <th class="px-4 py-2">Default</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in whereUsed" :key="r.bom_id" class="cursor-pointer border-t border-gray-100 hover:bg-gray-50" @click="router.push(`/bom/${r.bom_id}`)">
              <td class="px-4 py-1.5 font-medium text-gray-900">{{ r.bom_name }}</td>
              <td class="px-4 py-1.5">{{ r.production_item_name ?? r.production_item_code }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.qty_per_batch) }}</td>
              <td class="px-4 py-1.5">{{ r.is_default ? "★" : "" }}</td>
            </tr>
            <tr v-if="whereUsedItem && !whereUsed.length"><td colspan="4" class="px-4 py-8 text-center text-gray-400">No BOMs use this item.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- BOM Stock -->
    <div v-else>
      <div class="mb-3 flex flex-wrap items-end gap-3">
        <div class="min-w-[18rem]">
          <label class="form-label">BOM</label>
          <select v-model="stockBom" class="form-input" @change="loadBomStock">
            <option value="" disabled>Select a BOM…</option>
            <option v-for="b in boms" :key="b.id" :value="b.id">{{ b.name }} — {{ b.production_item_name }}</option>
          </select>
        </div>
        <div class="w-40">
          <label class="form-label">Finished units</label>
          <input v-model.number="stockQty" type="number" min="0.000001" step="any" class="form-input" @change="loadBomStock" />
        </div>
      </div>
      <div v-if="stockReport" class="mb-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <span class="text-sm text-gray-500">Current stock can build</span>
        <span class="ml-2 text-lg font-semibold text-primary">{{ formatQty(stockReport.buildable_qty) }}</span>
        <span class="text-sm text-gray-500"> finished units.</span>
      </div>
      <div v-if="stockReport" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Component</th>
              <th class="px-4 py-2 text-right">Required</th>
              <th class="px-4 py-2 text-right">Available</th>
              <th class="px-4 py-2 text-right">Shortfall</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in stockReport.rows" :key="r.item_id" class="border-t border-gray-100">
              <td class="px-4 py-1.5">{{ r.item_name ?? r.item_code }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.required_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.available_qty) }}</td>
              <td class="px-4 py-1.5 text-right">
                <span v-if="Number(r.shortfall_qty) > 0" class="font-medium text-red-600">{{ formatQty(r.shortfall_qty) }}</span>
                <span v-else class="text-gray-300">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
