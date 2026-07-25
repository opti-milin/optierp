<script setup lang="ts">
// Subcontract Job detail — submit → send materials → receive FG.
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatCurrency, formatDate, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";
import type { SubcontractJobDetail } from "@/types/manufacturing";

const props = defineProps<{ id: string }>();
const router = useRouter();

const job = ref<SubcontractJobDetail | null>(null);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const busy = ref(false);

const today = new Date().toISOString().slice(0, 10);
const sendQty = ref(0);
const sendDate = ref(today);
const recvQty = ref(0);
const recvDate = ref(today);
const showSend = ref(false);
const showRecv = ref(false);

const canSubmit = computed(() => job.value?.docstatus === 0);
const canCancel = computed(
  () =>
    job.value?.docstatus === 1 &&
    Number(job.value.sent_qty) === 0 &&
    Number(job.value.received_qty) === 0,
);
const canSend = computed(
  () => job.value?.docstatus === 1 && Number(job.value.sent_qty) < Number(job.value.qty),
);
const canRecv = computed(
  () =>
    job.value?.docstatus === 1 &&
    Number(job.value.sent_qty) > Number(job.value.received_qty),
);

async function load(): Promise<void> {
  error.value = null;
  try {
    job.value = (await api.get<SubcontractJobDetail>(`/subcontract-jobs/${props.id}`)).data;
    const pendingSend = Number(job.value.qty) - Number(job.value.sent_qty);
    const pendingRecv = Math.min(
      Number(job.value.sent_qty) - Number(job.value.received_qty),
      Number(job.value.qty) - Number(job.value.received_qty),
    );
    sendQty.value = pendingSend;
    recvQty.value = pendingRecv;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function submit(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    job.value = (await api.post<SubcontractJobDetail>(`/subcontract-jobs/${props.id}/submit`)).data;
    notice.value = "Submitted.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function cancel(): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    job.value = (await api.post<SubcontractJobDetail>(`/subcontract-jobs/${props.id}/cancel`)).data;
    notice.value = "Cancelled.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function doSend(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (
      await api.post<{ job: SubcontractJobDetail; stock_entry_name: string }>(
        `/subcontract-jobs/${props.id}/send`,
        { qty: sendQty.value, posting_date: sendDate.value },
      )
    ).data;
    job.value = res.job;
    showSend.value = false;
    notice.value = `Materials sent — Stock Entry ${res.stock_entry_name}`;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function doRecv(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (
      await api.post<{ job: SubcontractJobDetail; stock_entry_name: string }>(
        `/subcontract-jobs/${props.id}/receive`,
        { qty: recvQty.value, posting_date: recvDate.value },
      )
    ).data;
    job.value = res.job;
    showRecv.value = false;
    notice.value = `Received — Stock Entry ${res.stock_entry_name}`;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="job" class="mx-auto max-w-5xl">
    <button type="button" class="mb-3 text-sm text-indigo-600 hover:underline" @click="router.push('/subcontract-jobs')">
      ← Subcontract Jobs
    </button>

    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">{{ job.name }}</h1>
        <p class="text-sm text-gray-500">
          {{ job.production_item_code }} · {{ job.supplier_name }} · BOM {{ job.bom_name }}
        </p>
      </div>
      <StatusBadge :status="job.status" />
    </div>

    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <div class="mb-4 flex flex-wrap gap-2">
      <button v-if="canSubmit" type="button" class="btn-primary" :disabled="busy" @click="submit">Submit</button>
      <button v-if="canSend" type="button" class="btn-secondary" :disabled="busy" @click="showSend = !showSend">
        Send materials…
      </button>
      <button v-if="canRecv" type="button" class="btn-secondary" :disabled="busy" @click="showRecv = !showRecv">
        Receive FG…
      </button>
      <button v-if="canCancel" type="button" class="btn-secondary" :disabled="busy" @click="cancel">Cancel</button>
    </div>

    <form v-if="showSend" class="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4" @submit.prevent="doSend">
      <h3 class="mb-2 text-sm font-semibold">Send materials to supplier warehouse</h3>
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label class="form-label">FG qty cover</label>
          <input v-model.number="sendQty" type="number" min="0.000001" step="any" class="form-input" />
        </div>
        <div>
          <label class="form-label">Date</label>
          <input v-model="sendDate" type="date" class="form-input" />
        </div>
        <button type="submit" class="btn-primary" :disabled="busy">Confirm send</button>
      </div>
    </form>

    <form v-if="showRecv" class="mb-4 rounded-lg border border-green-200 bg-green-50 p-4" @submit.prevent="doRecv">
      <h3 class="mb-2 text-sm font-semibold">Receive finished goods</h3>
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label class="form-label">Qty to receive</label>
          <input v-model.number="recvQty" type="number" min="0.000001" step="any" class="form-input" />
        </div>
        <div>
          <label class="form-label">Date</label>
          <input v-model="recvDate" type="date" class="form-input" />
        </div>
        <button type="submit" class="btn-primary" :disabled="busy">Confirm receive</button>
      </div>
      <p class="mt-2 text-xs text-gray-600">
        If the FG item requires inspection, Accept a Quality Inspection against this job first.
      </p>
    </form>

    <dl class="mb-6 grid grid-cols-2 gap-3 rounded-lg border border-gray-200 bg-white p-4 text-sm md:grid-cols-4">
      <div><dt class="text-gray-500">Qty</dt><dd class="font-medium">{{ formatQty(job.qty) }}</dd></div>
      <div><dt class="text-gray-500">Sent</dt><dd class="font-medium">{{ formatQty(job.sent_qty) }}</dd></div>
      <div><dt class="text-gray-500">Received</dt><dd class="font-medium">{{ formatQty(job.received_qty) }}</dd></div>
      <div><dt class="text-gray-500">Service cost</dt><dd class="font-medium">{{ formatCurrency(job.service_cost) }}</dd></div>
      <div><dt class="text-gray-500">Posting date</dt><dd class="font-medium">{{ formatDate(job.posting_date) }}</dd></div>
    </dl>

    <h2 class="mb-2 text-sm font-semibold text-gray-900">Required materials</h2>
    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
          <tr>
            <th class="px-4 py-3">Item</th>
            <th class="px-4 py-3">Required</th>
            <th class="px-4 py-3">Sent</th>
            <th class="px-4 py-3">Consumed</th>
            <th class="px-4 py-3">Amount</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="row in job.items" :key="row.id">
            <td class="px-4 py-3">{{ row.item_code }} — {{ row.item_name }}</td>
            <td class="px-4 py-3">{{ formatQty(row.required_qty) }}</td>
            <td class="px-4 py-3">{{ formatQty(row.sent_qty) }}</td>
            <td class="px-4 py-3">{{ formatQty(row.consumed_qty) }}</td>
            <td class="px-4 py-3">{{ formatCurrency(row.amount) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <p v-else-if="error" class="text-red-600">{{ error.detail }}</p>
  <p v-else class="text-gray-400">Loading…</p>
</template>
