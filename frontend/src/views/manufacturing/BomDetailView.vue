<script setup lang="ts">
// BOM detail: components + live cost, with submit / cancel / refresh-cost / (de)activate.
import { onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatCurrency, formatQty, formatDate } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";
import type { BomDetail } from "@/types/manufacturing";

const props = defineProps<{ id: string }>();

const bom = ref<BomDetail | null>(null);
const loading = ref(false);
const busy = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

async function fetchBom(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    bom.value = (await api.get<BomDetail>(`/boms/${props.id}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function act(action: "submit" | "cancel" | "update-cost" | "activate" | "deactivate"): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/boms/${props.id}/${action}`);
    await fetchBom();
    notice.value = action === "update-cost" ? "Cost refreshed from current valuation." : `BOM ${action}d.`;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

onMounted(fetchBom);
</script>

<template>
  <div v-if="bom">
    <div class="mb-4 flex items-start justify-between">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-xl font-semibold text-gray-900">{{ bom.name }}</h1>
          <StatusBadge :status="bom.docstatus" />
          <StatusBadge v-if="bom.docstatus === 1" :status="bom.is_active ? 'Active' : 'Disabled'" />
          <span v-if="bom.is_default" class="text-sm text-amber-600">★ default</span>
        </div>
        <p class="text-sm text-gray-500">
          {{ bom.production_item_name }} <span class="text-gray-400">({{ bom.production_item_code }})</span>
          — yields {{ formatQty(bom.quantity) }} {{ bom.uom }}
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <RouterLink to="/bom" class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50">
          Back
        </RouterLink>
        <button v-if="bom.docstatus === 1" class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50" :disabled="busy" @click="act('update-cost')">
          Refresh cost
        </button>
        <button v-if="bom.docstatus === 0" class="btn-primary" :disabled="busy" @click="act('submit')">Submit</button>
        <button v-if="bom.docstatus === 1 && bom.is_active" class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50" :disabled="busy" @click="act('deactivate')">
          Deactivate
        </button>
        <button v-if="bom.docstatus === 1 && !bom.is_active" class="btn-primary" :disabled="busy" @click="act('activate')">Activate</button>
        <button v-if="bom.docstatus === 1" class="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50" :disabled="busy" @click="act('cancel')">
          Cancel
        </button>
      </div>
    </div>

    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <div class="mb-5 grid grid-cols-2 gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Raw material cost</div>
        <div class="mt-1 text-lg font-semibold">{{ formatCurrency(bom.raw_material_cost) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Operating cost</div>
        <div class="mt-1 text-lg font-semibold">{{ formatCurrency(bom.operating_cost) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Total cost / batch</div>
        <div class="mt-1 text-lg font-semibold">{{ formatCurrency(bom.total_cost) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Cost per unit</div>
        <div class="mt-1 text-lg font-semibold text-primary">{{ formatCurrency(bom.cost_per_unit) }}</div>
      </div>
    </div>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div class="border-b border-gray-100 px-4 py-2 text-sm font-medium text-gray-700">Components</div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">#</th>
            <th class="px-4 py-2">Item</th>
            <th class="px-4 py-2 text-right">Qty</th>
            <th class="px-4 py-2">UOM</th>
            <th class="px-4 py-2 text-right">Rate</th>
            <th class="px-4 py-2 text-right">Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in bom.items" :key="row.id" class="border-t border-gray-100">
            <td class="px-4 py-1.5 text-gray-400">{{ row.idx }}</td>
            <td class="px-4 py-1.5">
              <div>{{ row.item_name }}</div>
              <div class="text-xs text-gray-400">{{ row.item_code }}</div>
            </td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.qty) }}</td>
            <td class="px-4 py-1.5">{{ row.uom }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(row.rate) }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ formatCurrency(row.amount) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="mt-3 text-xs text-gray-400">
      Created {{ formatDate(bom.creation) }} · currency {{ bom.currency }}
    </p>
  </div>
</template>
