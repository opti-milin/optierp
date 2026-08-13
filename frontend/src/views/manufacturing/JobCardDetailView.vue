<script setup lang="ts">
// Job Card detail — start/stop time logs and record completed qty.
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatDate, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";
import type { JobCardDetail } from "@/types/manufacturing";

const props = defineProps<{ id: string }>();

const jc = ref<JobCardDetail | null>(null);
const busy = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const completeQty = ref<number>(0);

const canStart = computed(
  () => !!jc.value && jc.value.status !== "Completed" && jc.value.status !== "Cancelled"
    && jc.value.docstatus !== 2
    && !jc.value.time_logs.some((t) => !t.to_time),
);
const canComplete = computed(
  () => !!jc.value && jc.value.status !== "Completed" && jc.value.status !== "Cancelled"
    && jc.value.docstatus !== 2,
);

async function refresh(): Promise<void> {
  error.value = null;
  try {
    jc.value = (await api.get<JobCardDetail>(`/job-cards/${props.id}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function start(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/job-cards/${props.id}/start`);
    notice.value = "Time log started.";
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function complete(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/job-cards/${props.id}/complete`, {
      completed_qty: completeQty.value || 0,
    });
    notice.value = "Job Card updated.";
    completeQty.value = 0;
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function cancel(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/job-cards/${props.id}/cancel`);
    notice.value = "Job Card cancelled.";
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
  <div v-if="jc">
    <div class="mb-4 flex items-start justify-between">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-xl font-semibold text-gray-900">{{ jc.name }}</h1>
          <StatusBadge :status="jc.status" />
        </div>
        <p class="text-sm text-gray-500">
          {{ jc.operation_name }}
          <span v-if="jc.workstation_name"> · {{ jc.workstation_name }}</span>
          —
          <RouterLink :to="`/work-orders/${jc.work_order_id}`" class="text-blue-600 hover:underline">
            {{ jc.work_order_name }}
          </RouterLink>
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <RouterLink
          to="/job-cards"
          class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Back
        </RouterLink>
        <button v-if="canStart" class="btn-primary" :disabled="busy" @click="start">Start</button>
        <button
          v-if="jc.docstatus !== 2 && jc.status !== 'Cancelled' && Number(jc.total_completed_qty) === 0"
          class="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
          :disabled="busy"
          @click="cancel"
        >
          Cancel
        </button>
      </div>
    </div>

    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <div class="mb-5 grid grid-cols-2 gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">For qty</div>
        <div class="mt-1 text-lg font-semibold">{{ formatQty(jc.for_quantity) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Completed</div>
        <div class="mt-1 text-lg font-semibold text-primary">{{ formatQty(jc.total_completed_qty) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Time (mins)</div>
        <div class="mt-1 text-lg font-semibold">{{ formatQty(jc.time_in_mins) }}</div>
      </div>
    </div>

    <form
      v-if="canComplete"
      class="mb-5 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
      @submit.prevent="complete"
    >
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">Completed qty this stop</label>
          <input v-model.number="completeQty" type="number" min="0" step="any" class="form-input" />
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Stops a running time log (if any) and accrues completed quantity toward the Work Order operation.
      </p>
      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy">Stop / record qty</button>
      </div>
    </form>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div class="border-b border-gray-100 px-4 py-2 text-sm font-medium text-gray-700">Time logs</div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">#</th>
            <th class="px-4 py-2">From</th>
            <th class="px-4 py-2">To</th>
            <th class="px-4 py-2 text-right">Mins</th>
            <th class="px-4 py-2 text-right">Qty</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in jc.time_logs" :key="row.id" class="border-t border-gray-100">
            <td class="px-4 py-1.5 text-gray-400">{{ row.idx }}</td>
            <td class="px-4 py-1.5">{{ formatDate(row.from_time) }}</td>
            <td class="px-4 py-1.5">{{ row.to_time ? formatDate(row.to_time) : "running…" }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.time_in_mins) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.completed_qty) }}</td>
          </tr>
          <tr v-if="!jc.time_logs.length">
            <td colspan="5" class="px-4 py-6 text-center text-gray-400">No time logs yet — click Start.</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
