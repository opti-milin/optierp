<script setup lang="ts">
// Quality Inspection list — Accepted/Rejected gate for manufacturing finishes.
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatDate, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  QualityInspectionListItem,
  SubcontractJobListItem,
  WorkOrderListItem,
} from "@/types/manufacturing";

interface ItemOpt { id: string; item_code: string; item_name: string }

const route = useRoute();
const router = useRouter();
const rows = ref<QualityInspectionListItem[]>([]);
const items = ref<ItemOpt[]>([]);
const workOrders = ref<WorkOrderListItem[]>([]);
const subcontractJobs = ref<SubcontractJobListItem[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const showForm = ref(false);
const saving = ref(false);

const today = new Date().toISOString().slice(0, 10);
const fRefType = ref((route.query.reference_type as string) || "Work Order");
const fRefId = ref((route.query.reference_id as string) || "");
const fItem = ref((route.query.item_id as string) || "");
const fQty = ref(Number(route.query.qty) || 1);
const fDate = ref(today);

const openWorkOrders = computed(() =>
  workOrders.value.filter((w) => w.docstatus === 1 && w.status !== "Cancelled"),
);
const openJobs = computed(() =>
  subcontractJobs.value.filter((j) => j.docstatus === 1 && j.status !== "Cancelled"),
);

async function fetchList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rows.value = (
      await api.get<ListResponse<QualityInspectionListItem>>("/quality-inspections", {
        params: { page_size: 100 },
      })
    ).data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function fetchOptions(): Promise<void> {
  error.value = null;
  try {
    // /items page_size max is 200 (API validation) — 500 was silently failing the load.
    const [it, wo, sc] = await Promise.all([
      api.get<ListResponse<ItemOpt>>("/items", { params: { page_size: 200 } }),
      api.get<ListResponse<WorkOrderListItem>>("/work-orders", { params: { page_size: 100 } }),
      api.get<ListResponse<SubcontractJobListItem>>("/subcontract-jobs", { params: { page_size: 100 } })
        .catch(() => ({ data: { items: [] as SubcontractJobListItem[] } })),
    ]);
    items.value = it.data.items;
    workOrders.value = wo.data.items;
    subcontractJobs.value = sc.data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

function applyReferenceDefaults(): void {
  if (fRefType.value === "Work Order") {
    const wo = workOrders.value.find((w) => w.id === fRefId.value);
    if (!wo) return;
    // Prefer matching item by code from the WO list fields.
    const match = items.value.find((i) => i.item_code === wo.production_item_code);
    if (match) fItem.value = match.id;
    const pending = Number(wo.qty) - Number(wo.produced_qty);
    if (pending > 0) fQty.value = pending;
  } else if (fRefType.value === "Subcontract Job") {
    const job = subcontractJobs.value.find((j) => j.id === fRefId.value);
    if (!job) return;
    const match = items.value.find((i) => i.item_code === job.production_item_code);
    if (match) fItem.value = match.id;
    const pending = Number(job.qty) - Number(job.received_qty);
    if (pending > 0) fQty.value = pending;
  }
}

watch(fRefType, () => {
  fRefId.value = "";
  fItem.value = "";
});
watch(fRefId, () => applyReferenceDefaults());

async function save(): Promise<void> {
  if (!fRefId.value || !fItem.value) {
    error.value = {
      detail: "Reference and finished item are required",
      code: "ERR_VALIDATION",
      field: null,
    };
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<{ id: string }>("/quality-inspections", {
        reference_type: fRefType.value,
        reference_id: fRefId.value,
        item_id: fItem.value,
        qty: fQty.value,
        inspection_date: fDate.value,
      })
    ).data;
    void router.push(`/quality-inspections/${created.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  if (route.query.reference_id) showForm.value = true;
  await Promise.all([fetchOptions(), fetchList()]);
  if (fRefId.value) applyReferenceDefaults();
});
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Quality Inspections</h1>
        <p class="text-sm text-gray-500">
          Accept before finishing a Work Order or receiving a Subcontract Job when the FG requires inspection.
        </p>
      </div>
      <button type="button" class="btn-primary" @click="showForm = !showForm">
        {{ showForm ? "Cancel" : "New Inspection" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <form
      v-if="showForm"
      class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
      @submit.prevent="save"
    >
      <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">Reference type *</label>
          <select v-model="fRefType" class="form-input">
            <option>Work Order</option>
            <option>Subcontract Job</option>
          </select>
        </div>
        <div>
          <label class="form-label">
            {{ fRefType === "Work Order" ? "Work Order *" : "Subcontract Job *" }}
          </label>
          <select v-if="fRefType === 'Work Order'" v-model="fRefId" class="form-input" required>
            <option value="">—</option>
            <option v-for="w in openWorkOrders" :key="w.id" :value="w.id">
              {{ w.name }} — {{ w.production_item_code }} ({{ w.status }})
            </option>
          </select>
          <select v-else v-model="fRefId" class="form-input" required>
            <option value="">—</option>
            <option v-for="j in openJobs" :key="j.id" :value="j.id">
              {{ j.name }} — {{ j.production_item_code }} ({{ j.status }})
            </option>
          </select>
        </div>
        <div>
          <label class="form-label">Finished item *</label>
          <select v-model="fItem" class="form-input" required>
            <option value="">—</option>
            <option v-for="i in items" :key="i.id" :value="i.id">
              {{ i.item_code }} — {{ i.item_name }}
            </option>
          </select>
          <p v-if="!items.length" class="mt-1 text-xs text-amber-600">
            No items loaded — check Item read permission or refresh.
          </p>
        </div>
        <div>
          <label class="form-label">Qty *</label>
          <input v-model.number="fQty" type="number" min="0.000001" step="any" class="form-input" required />
        </div>
        <div>
          <label class="form-label">Inspection date</label>
          <input v-model="fDate" type="date" class="form-input" />
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Pick the Work Order (or Subcontract Job) by name — finished item and pending qty fill in automatically.
      </p>
      <div class="mt-4">
        <button type="submit" class="btn-primary" :disabled="saving">
          {{ saving ? "Creating…" : "Create draft" }}
        </button>
      </div>
    </form>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
          <tr>
            <th class="px-4 py-3">Name</th>
            <th class="px-4 py-3">Reference</th>
            <th class="px-4 py-3">Item</th>
            <th class="px-4 py-3">Qty</th>
            <th class="px-4 py-3">Status</th>
            <th class="px-4 py-3">Date</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="loading">
            <td colspan="6" class="px-4 py-6 text-center text-gray-400">Loading…</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="6" class="px-4 py-6 text-center text-gray-400">No inspections yet.</td>
          </tr>
          <tr
            v-for="r in rows"
            :key="r.id"
            class="cursor-pointer hover:bg-gray-50"
            @click="router.push(`/quality-inspections/${r.id}`)"
          >
            <td class="px-4 py-3 font-medium text-indigo-700">{{ r.name }}</td>
            <td class="px-4 py-3">{{ r.reference_type }} {{ r.reference_name }}</td>
            <td class="px-4 py-3">{{ r.item_code }}</td>
            <td class="px-4 py-3">{{ formatQty(r.qty) }}</td>
            <td class="px-4 py-3"><StatusBadge :status="r.status" /></td>
            <td class="px-4 py-3">{{ formatDate(r.inspection_date) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
