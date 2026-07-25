<script setup lang="ts">
// Production Plan detail — get SO items → get raw materials → submit → create WOs / MRs.
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatDate, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";
import type {
  ProductionPlanCreateResult,
  ProductionPlanDetail,
} from "@/types/manufacturing";

const props = defineProps<{ id: string }>();

const plan = ref<ProductionPlanDetail | null>(null);
const busy = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

const canEdit = computed(() => plan.value?.docstatus === 0);
const canSubmit = computed(
  () => !!plan.value && plan.value.docstatus === 0 && plan.value.items.length > 0,
);
const canCreateWO = computed(
  () =>
    !!plan.value
    && plan.value.docstatus === 1
    && plan.value.items.some((i) => Number(i.planned_qty) - Number(i.ordered_qty) > 0),
);
const canCreateMR = computed(
  () =>
    !!plan.value
    && plan.value.docstatus === 1
    && plan.value.material_requests.some(
      (r) => Number(r.shortfall_qty) > 0 && !r.material_request_id,
    ),
);

async function refresh(): Promise<void> {
  error.value = null;
  try {
    plan.value = (await api.get<ProductionPlanDetail>(`/production-plans/${props.id}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function act(path: string, success: string): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/production-plans/${props.id}/${path}`);
    notice.value = success;
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function createWOs(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (
      await api.post<ProductionPlanCreateResult>(
        `/production-plans/${props.id}/create-work-orders`,
      )
    ).data;
    notice.value =
      res.count > 0
        ? `Created ${res.count} Work Order(s): ${res.created_names.join(", ")}`
        : "No Work Orders to create (all rows already ordered).";
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function createMRs(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (
      await api.post<ProductionPlanCreateResult>(
        `/production-plans/${props.id}/create-material-requests`,
      )
    ).data;
    notice.value = `Created Material Request ${res.created_names.join(", ")}.`;
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div v-if="plan">
    <div class="mb-4 flex items-start justify-between">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-xl font-semibold text-gray-900">{{ plan.name }}</h1>
          <StatusBadge :status="plan.status" />
        </div>
        <p class="text-sm text-gray-500">
          Demand {{ plan.from_date || "—" }} → {{ plan.to_date || "—" }}
          · posted {{ formatDate(plan.posting_date) }}
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <RouterLink
          to="/production-plans"
          class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Back
        </RouterLink>
        <button
          v-if="canEdit"
          class="rounded-md border border-blue-200 px-3 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-50"
          :disabled="busy"
          @click="act('get-items', 'Finished goods pulled from Sales Orders.')"
        >
          Get items from Sales Orders
        </button>
        <button
          v-if="canEdit && plan.items.length"
          class="rounded-md border border-violet-200 px-3 py-1.5 text-sm font-medium text-violet-700 hover:bg-violet-50"
          :disabled="busy"
          @click="act('get-raw-materials', 'Raw materials exploded and netted vs stock.')"
        >
          Get raw materials
        </button>
        <button
          v-if="canSubmit"
          class="btn-primary"
          :disabled="busy"
          @click="act('submit', 'Production Plan submitted.')"
        >
          Submit
        </button>
        <button
          v-if="canCreateWO"
          class="btn-primary"
          :disabled="busy"
          @click="createWOs"
        >
          Create Work Orders
        </button>
        <button
          v-if="canCreateMR"
          class="rounded-md border border-amber-200 px-3 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-50"
          :disabled="busy"
          @click="createMRs"
        >
          Create Material Requests
        </button>
        <button
          v-if="plan.docstatus === 1 && !plan.work_orders_created && !plan.material_requests_created"
          class="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
          :disabled="busy"
          @click="act('cancel', 'Production Plan cancelled.')"
        >
          Cancel
        </button>
      </div>
    </div>

    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div class="border-b border-gray-100 px-4 py-2 text-sm font-medium text-gray-700">
        Finished goods to make
      </div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Item</th>
            <th class="px-4 py-2">BOM</th>
            <th class="px-4 py-2">Sales Order</th>
            <th class="px-4 py-2 text-right">Planned</th>
            <th class="px-4 py-2 text-right">Ordered</th>
            <th class="px-4 py-2">Work Order</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in plan.items" :key="row.id" class="border-t border-gray-100">
            <td class="px-4 py-1.5">
              <div>{{ row.item_name }}</div>
              <div class="text-xs text-gray-400">{{ row.item_code }}</div>
            </td>
            <td class="px-4 py-1.5">{{ row.bom_name ?? "—" }}</td>
            <td class="px-4 py-1.5">{{ row.sales_order_name ?? "—" }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ formatQty(row.planned_qty) }}</td>
            <td class="px-4 py-1.5 text-right text-gray-500">{{ formatQty(row.ordered_qty) }}</td>
            <td class="px-4 py-1.5">
              <RouterLink
                v-if="row.work_order_id"
                :to="`/work-orders/${row.work_order_id}`"
                class="text-blue-600 hover:underline"
              >
                Open
              </RouterLink>
              <span v-else class="text-gray-300">—</span>
            </td>
          </tr>
          <tr v-if="!plan.items.length">
            <td colspan="6" class="px-4 py-6 text-center text-gray-400">
              No finished goods yet — click “Get items from Sales Orders” (or add manual rows via API).
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="plan.material_requests.length"
      class="mt-5 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
    >
      <div class="border-b border-gray-100 px-4 py-2 text-sm font-medium text-gray-700">
        Raw material shortfalls
      </div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Item</th>
            <th class="px-4 py-2 text-right">Required</th>
            <th class="px-4 py-2 text-right">Available</th>
            <th class="px-4 py-2 text-right">Shortfall</th>
            <th class="px-4 py-2">MR</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in plan.material_requests" :key="row.id" class="border-t border-gray-100">
            <td class="px-4 py-1.5">
              <div>{{ row.item_name }}</div>
              <div class="text-xs text-gray-400">{{ row.item_code }}</div>
            </td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.required_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.available_qty) }}</td>
            <td class="px-4 py-1.5 text-right font-medium text-red-600">
              {{ formatQty(row.shortfall_qty) }}
            </td>
            <td class="px-4 py-1.5">
              <RouterLink
                v-if="row.material_request_id"
                :to="`/material-requests/${row.material_request_id}`"
                class="text-blue-600 hover:underline"
              >
                Open
              </RouterLink>
              <span v-else class="text-gray-300">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
