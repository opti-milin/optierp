<script setup lang="ts">
// Material Request — create (draft) + detail (submit/cancel, order → PO).

import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import ItemSelect from "@/components/shared/ItemSelect.vue";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import PrintButton from "@/components/shared/PrintButton.vue";
import SendEmailButton from "@/components/shared/SendEmailButton.vue";
import { api } from "@/api/client";
import { useStockStore } from "@/stores/stock";
import { formatDate, formatQty } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { MaterialRequestDetail } from "@/types/stock";

const MR_TYPES = ["Purchase", "Material Transfer", "Material Issue", "Manufacture"] as const;
type MrRow = { item_id: string; qty: number; warehouse_id: string; schedule_date: string };

const route = useRoute();
const router = useRouter();
const store = useStockStore();

const id = computed(() => route.params.id as string | undefined);
const isEdit = computed(() => !!id.value);
const doc = ref<MaterialRequestDetail | null>(null);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const materialRequestType = ref<(typeof MR_TYPES)[number]>("Purchase");
const postingDate = ref(new Date().toISOString().slice(0, 10));
const scheduleDate = ref("");
const remarks = ref("");
const newRow = (): MrRow => ({ item_id: "", qty: 1, warehouse_id: "", schedule_date: "" });
const rows = ref<MrRow[]>([newRow()]);

function whName(wid: string | null | undefined): string {
  if (!wid) return "—";
  return store.warehouses.find((w) => w.id === wid)?.warehouse_name ?? "—";
}

async function load(): Promise<void> {
  if (!id.value) return;
  try {
    doc.value = (await api.get<MaterialRequestDetail>(`/material-requests/${id.value}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const { data } = await api.post<{ id: string }>("/material-requests", {
      material_request_type: materialRequestType.value,
      posting_date: postingDate.value,
      schedule_date: scheduleDate.value || null,
      remarks: remarks.value || null,
      items: rows.value.filter((r) => r.item_id).map((r) => ({
        item_id: r.item_id,
        qty: r.qty,
        warehouse_id: r.warehouse_id || null,
        schedule_date: r.schedule_date || null,
      })),
    });
    router.push(`/material-requests/${data.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function docAction(action: "submit" | "cancel"): Promise<void> {
  error.value = null;
  try {
    await api.post(`/material-requests/${id.value}/${action}`);
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

function orderIt(): void {
  router.push({ name: "purchase-order-new", query: { material_request_id: id.value } });
}

onMounted(async () => {
  await Promise.all([store.fetchItems(), store.fetchWarehouses()]);
  await load();
});
</script>

<template>
  <div class="mx-auto max-w-4xl">
    <!-- DETAIL -->
    <template v-if="isEdit && doc">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold text-gray-900">{{ doc.name }}</h1>
          <p class="text-sm text-gray-500">
            <router-link to="/material-requests" class="text-primary hover:underline">Material Requests</router-link>
            · {{ doc.material_request_type }}
          </p>
        </div>
        <div class="flex items-center gap-3">
          <StatusBadge :status="doc.status" />
          <PrintButton :path="`/print/Material%20Request/${doc.id}`" :title="`${doc.name} — Preview`" />
          <SendEmailButton doctype="Material Request" :doc-id="doc.id" :doc-name="doc.name" />
          <button v-if="doc.docstatus === 0" class="btn-primary" @click="docAction('submit')">Submit</button>
          <button v-if="doc.docstatus === 1 && doc.status !== 'Ordered'" class="btn-primary" @click="orderIt">Order → PO</button>
          <button v-if="doc.docstatus === 1" class="text-sm text-red-600 hover:underline" @click="docAction('cancel')">Cancel</button>
        </div>
      </div>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <section class="mb-4 grid grid-cols-4 gap-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm text-sm">
        <div><div class="text-xs uppercase text-gray-400">Date</div>{{ formatDate(doc.posting_date) }}</div>
        <div><div class="text-xs uppercase text-gray-400">Required By</div>{{ doc.schedule_date ? formatDate(doc.schedule_date) : "—" }}</div>
        <div><div class="text-xs uppercase text-gray-400">Ordered</div>{{ formatQty(doc.per_ordered) }}%</div>
        <div v-if="doc.remarks" class="col-span-4"><div class="text-xs uppercase text-gray-400">Remarks</div>{{ doc.remarks }}</div>
      </section>

      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Item</th><th class="px-4 py-2">Warehouse</th>
              <th class="px-4 py-2 text-right">Qty</th><th class="px-4 py-2 text-right">Ordered</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in doc.items" :key="r.idx" class="border-t border-gray-100">
              <td class="px-4 py-2 font-medium text-gray-900">{{ r.item_code }} — {{ r.item_name }}</td>
              <td class="px-4 py-2 text-gray-500">{{ whName(r.warehouse_id) }}</td>
              <td class="px-4 py-2 text-right">{{ formatQty(r.qty) }}</td>
              <td class="px-4 py-2 text-right">{{ formatQty(r.ordered_qty) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- CREATE -->
    <template v-else-if="!isEdit">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold text-gray-900">New Material Request</h1>
          <p class="text-sm text-gray-500">
            <router-link to="/material-requests" class="text-primary hover:underline">Material Requests</router-link>
          </p>
        </div>
        <button class="btn-primary" :disabled="saving" @click="save">{{ saving ? "Saving…" : "Save (Draft)" }}</button>
      </div>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <section class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div class="grid grid-cols-3 gap-4">
          <div>
            <label class="form-label">Type</label>
            <select v-model="materialRequestType" class="form-input">
              <option v-for="t in MR_TYPES" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
          <div>
            <label class="form-label">Date*</label>
            <input v-model="postingDate" type="date" class="form-input" />
          </div>
          <div>
            <label class="form-label">Required By</label>
            <input v-model="scheduleDate" type="date" class="form-input" />
          </div>
        </div>
        <div class="mb-1 mt-4 grid grid-cols-12 gap-2 text-xs font-medium text-gray-500">
          <div class="col-span-4">Item</div><div class="col-span-2 text-right">Qty</div>
          <div class="col-span-3">Warehouse</div><div class="col-span-3">Required By</div>
        </div>
        <div v-for="(row, i) in rows" :key="i" class="mb-2 grid grid-cols-12 gap-2">
          <ItemSelect v-model="row.item_id" class="col-span-4" :options="store.itemOptions" placeholder="Select item…" />
          <input v-model.number="row.qty" type="number" min="0" step="any" placeholder="Qty" class="form-input col-span-2 text-right" />
          <select v-model="row.warehouse_id" class="form-input col-span-3">
            <option value="">Warehouse…</option>
            <option v-for="w in store.leafWarehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
          <input v-model="row.schedule_date" type="date" class="form-input col-span-3" />
        </div>
        <div class="mt-2 flex items-center justify-between">
          <button type="button" class="btn-secondary" @click="rows.push(newRow())">Add Row</button>
          <input v-model="remarks" class="form-input w-72" placeholder="Remarks (optional)" />
        </div>
      </section>
    </template>
  </div>
</template>
