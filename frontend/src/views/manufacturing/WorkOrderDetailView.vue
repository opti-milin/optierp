<script setup lang="ts">
// Work Order detail: required items, produced progress, material availability, and the
// Finish action (posts a Manufacture Stock Entry). Also submit / cancel / stop / resume and
// a one-click Material Request for shortfalls.
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatCurrency, formatQty, formatDate } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";
import type { MaterialAvailability, WorkOrderDetail } from "@/types/manufacturing";

const props = defineProps<{ id: string }>();

const wo = ref<WorkOrderDetail | null>(null);
const avail = ref<MaterialAvailability | null>(null);
const busy = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

const showFinish = ref(false);
const today = new Date().toISOString().slice(0, 10);
const finQty = ref<number | null>(null);
const finDate = ref(today);
const finAccount = ref("");

const remaining = computed(() =>
  wo.value ? Number(wo.value.qty) - Number(wo.value.produced_qty) : 0,
);

async function fetchWo(): Promise<void> {
  wo.value = (await api.get<WorkOrderDetail>(`/work-orders/${props.id}`)).data;
  finQty.value = Number(wo.value.qty) - Number(wo.value.produced_qty);
  finAccount.value = wo.value.operating_cost_account_id ?? "";
}

async function fetchAvailability(): Promise<void> {
  try {
    avail.value = (
      await api.get<MaterialAvailability>(`/work-orders/${props.id}/material-availability`)
    ).data;
  } catch {
    avail.value = null;
  }
}

async function refresh(): Promise<void> {
  await Promise.all([fetchWo(), fetchAvailability()]);
}

async function act(action: "submit" | "cancel" | "stop" | "resume"): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/work-orders/${props.id}/${action}`);
    await refresh();
    const past: Record<string, string> = {
      submit: "submitted", cancel: "cancelled", stop: "stopped", resume: "resumed",
    };
    notice.value = `Work Order ${past[action]}.`;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function finish(): Promise<void> {
  if (!finQty.value || finQty.value <= 0) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (
      await api.post<{ stock_entry_no: string; produced_qty: string; status: string }>(
        `/work-orders/${props.id}/finish`,
        { qty: finQty.value, posting_date: finDate.value, operating_cost_account_id: finAccount.value || null },
      )
    ).data;
    notice.value = `Manufactured ${finQty.value} — posted ${res.stock_entry_no}.`;
    showFinish.value = false;
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function raiseMaterialRequest(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const mr = (await api.post<{ name: string }>(`/work-orders/${props.id}/material-request`)).data;
    notice.value = `Raised Material Request ${mr.name} for the shortfall.`;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

const hasShortfall = computed(() => avail.value?.rows.some((r) => Number(r.shortfall_qty) > 0) ?? false);

onMounted(refresh);
</script>

<template>
  <div v-if="wo">
    <div class="mb-4 flex items-start justify-between">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-xl font-semibold text-gray-900">{{ wo.name }}</h1>
          <StatusBadge :status="wo.status" />
        </div>
        <p class="text-sm text-gray-500">
          {{ wo.production_item_name }} <span class="text-gray-400">({{ wo.production_item_code }})</span>
          — BOM {{ wo.bom_name }}
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <RouterLink to="/work-orders" class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50">Back</RouterLink>
        <button v-if="wo.docstatus === 0" class="btn-primary" :disabled="busy" @click="act('submit')">Submit</button>
        <button
          v-if="wo.docstatus === 1 && ['Not Started', 'In Process'].includes(wo.status)"
          class="btn-primary" :disabled="busy || remaining <= 0" @click="showFinish = !showFinish"
        >
          Finish…
        </button>
        <button
          v-if="wo.docstatus === 1 && ['Not Started', 'In Process'].includes(wo.status)"
          class="rounded-md border border-amber-200 px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-50" :disabled="busy" @click="act('stop')"
        >
          Stop
        </button>
        <button v-if="wo.status === 'Stopped'" class="btn-primary" :disabled="busy" @click="act('resume')">Resume</button>
        <button
          v-if="wo.docstatus === 1 && Number(wo.produced_qty) === 0 && wo.status !== 'Completed'"
          class="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50" :disabled="busy" @click="act('cancel')"
        >
          Cancel
        </button>
      </div>
    </div>

    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <form v-if="showFinish" class="mb-5 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="finish">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">Quantity to produce* (≤ {{ formatQty(remaining) }})</label>
          <input v-model.number="finQty" type="number" min="0.000001" step="any" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Posting date*</label>
          <input v-model="finDate" type="date" required class="form-input" />
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        This consumes the required components and produces the finished good into stock, valued at
        (consumed materials + operating cost) ÷ produced qty.
      </p>
      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy || !finQty">Confirm &amp; manufacture</button>
      </div>
    </form>

    <div class="mb-5 grid grid-cols-2 gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">To make</div>
        <div class="mt-1 text-lg font-semibold">{{ formatQty(wo.qty) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Produced</div>
        <div class="mt-1 text-lg font-semibold text-primary">{{ formatQty(wo.produced_qty) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Operating cost</div>
        <div class="mt-1 text-lg font-semibold">{{ formatCurrency(wo.operating_cost) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Can finish now</div>
        <div class="mt-1 text-lg font-semibold">{{ avail ? formatQty(avail.can_finish_qty) : "—" }}</div>
      </div>
    </div>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div class="flex items-center justify-between border-b border-gray-100 px-4 py-2">
        <span class="text-sm font-medium text-gray-700">Required materials</span>
        <button
          v-if="hasShortfall && wo.docstatus === 1"
          class="text-sm text-blue-600 hover:underline" :disabled="busy" @click="raiseMaterialRequest"
        >
          Raise Material Request for shortfall
        </button>
      </div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Item</th>
            <th class="px-4 py-2 text-right">Required</th>
            <th class="px-4 py-2 text-right">Consumed</th>
            <th class="px-4 py-2 text-right">Available</th>
            <th class="px-4 py-2 text-right">Shortfall</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in wo.items" :key="row.id" class="border-t border-gray-100">
            <td class="px-4 py-1.5">
              <div>{{ row.item_name }}</div>
              <div class="text-xs text-gray-400">{{ row.item_code }}</div>
            </td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.required_qty) }}</td>
            <td class="px-4 py-1.5 text-right text-gray-500">{{ formatQty(row.consumed_qty) }}</td>
            <td class="px-4 py-1.5 text-right">
              {{ avail ? formatQty(avail.rows.find((a) => a.item_id === row.item_id)?.available_qty ?? "0") : "—" }}
            </td>
            <td class="px-4 py-1.5 text-right">
              <span
                v-if="avail && Number(avail.rows.find((a) => a.item_id === row.item_id)?.shortfall_qty ?? 0) > 0"
                class="font-medium text-red-600"
              >
                {{ formatQty(avail.rows.find((a) => a.item_id === row.item_id)?.shortfall_qty ?? "0") }}
              </span>
              <span v-else class="text-gray-300">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="mt-3 text-xs text-gray-400">
      Created {{ formatDate(wo.creation) }}
      <span v-if="wo.actual_start_date"> · started {{ wo.actual_start_date }}</span>
      <span v-if="wo.actual_end_date"> · completed {{ wo.actual_end_date }}</span>
    </p>
  </div>
</template>
