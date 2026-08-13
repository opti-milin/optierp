<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatDate, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";
import type { QualityInspectionDetail } from "@/types/manufacturing";

const props = defineProps<{ id: string }>();
const router = useRouter();
const qi = ref<QualityInspectionDetail | null>(null);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const busy = ref(false);

const canSubmit = computed(() => qi.value?.docstatus === 0);
const canCancel = computed(() => qi.value?.docstatus === 1);

async function load(): Promise<void> {
  error.value = null;
  try {
    qi.value = (await api.get<QualityInspectionDetail>(`/quality-inspections/${props.id}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function accept(): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    qi.value = (await api.post<QualityInspectionDetail>(`/quality-inspections/${props.id}/accept`)).data;
    notice.value = "Accepted — you can now Finish / Receive the linked document.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function reject(): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    qi.value = (await api.post<QualityInspectionDetail>(`/quality-inspections/${props.id}/reject`)).data;
    notice.value = "Rejected.";
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
    qi.value = (await api.post<QualityInspectionDetail>(`/quality-inspections/${props.id}/cancel`)).data;
    notice.value = "Cancelled.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="qi" class="mx-auto max-w-2xl">
    <button
      type="button"
      class="mb-3 text-sm text-indigo-600 hover:underline"
      @click="router.push('/quality-inspections')"
    >
      ← Quality Inspections
    </button>
    <div class="mb-4 flex items-start justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">{{ qi.name }}</h1>
        <p class="text-sm text-gray-500">
          {{ qi.reference_type }} {{ qi.reference_name }} · {{ qi.item_code }}
        </p>
      </div>
      <StatusBadge :status="qi.status" />
    </div>
    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>
    <div class="mb-4 flex gap-2">
      <button v-if="canSubmit" type="button" class="btn-primary" :disabled="busy" @click="accept">Accept</button>
      <button v-if="canSubmit" type="button" class="btn-secondary" :disabled="busy" @click="reject">Reject</button>
      <button v-if="canCancel" type="button" class="btn-secondary" :disabled="busy" @click="cancel">Cancel</button>
    </div>
    <dl class="grid grid-cols-2 gap-3 rounded-lg border border-gray-200 bg-white p-4 text-sm">
      <div><dt class="text-gray-500">Qty</dt><dd class="font-medium">{{ formatQty(qi.qty) }}</dd></div>
      <div><dt class="text-gray-500">Date</dt><dd class="font-medium">{{ formatDate(qi.inspection_date) }}</dd></div>
      <div class="col-span-2"><dt class="text-gray-500">Remarks</dt><dd>{{ qi.remarks || "—" }}</dd></div>
    </dl>
  </div>
  <p v-else-if="error" class="text-red-600">{{ error.detail }}</p>
  <p v-else class="text-gray-400">Loading…</p>
</template>
