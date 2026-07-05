<script setup lang="ts">
// Stock Entry — create (draft) + detail (read-only with submit/cancel).

import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import PrintButton from "@/components/shared/PrintButton.vue";
import SendEmailButton from "@/components/shared/SendEmailButton.vue";
import { api } from "@/api/client";
import { useStockStore } from "@/stores/stock";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { formatCurrency, formatDate, formatQty } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { StockEntryDetail, StockEntryItemIn } from "@/types/stock";

const route = useRoute();
const router = useRouter();
const store = useStockStore();
const companyCurrency = useCompanyCurrency();

const id = computed(() => route.params.id as string | undefined);
const isEdit = computed(() => !!id.value);
const doc = ref<StockEntryDetail | null>(null);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const purpose = ref<"Material Receipt" | "Material Issue" | "Material Transfer" | "Repack">(
  "Material Receipt",
);
const postingDate = ref(new Date().toISOString().slice(0, 10));
const fromWarehouseId = ref("");
const toWarehouseId = ref("");
const remarks = ref("");
// Repack only: optional flat cost (labour/freight) folded into the finished rows' value
const operatingCost = ref<number>(0);
const operatingCostAccountId = ref("");
const accountOptions = ref<{ value: string; label: string }[]>([]);
const rows = ref<StockEntryItemIn[]>([{ item_id: "", qty: 1, basic_rate: 0 }]);

const isRepack = computed(() => purpose.value === "Repack");
const needsSource = computed(() => purpose.value !== "Material Receipt");
const needsTarget = computed(() => purpose.value !== "Material Issue");
const showRate = computed(() => purpose.value === "Material Receipt");
// Repack additional cost > 0 needs its expense account (backend enforces at create too)
const missingCostAccount = computed(
  () => isRepack.value && (operatingCost.value || 0) > 0 && !operatingCostAccountId.value,
);

// switching purpose changes what basic_rate / Finished MEAN — reset per-row state so a
// Repack weight can't leak into a Receipt rate (or vice versa) invisibly
watch(purpose, () => {
  rows.value.forEach((r) => {
    r.basic_rate = 0;
    r.is_finished_item = false;
  });
  operatingCost.value = 0;
  operatingCostAccountId.value = "";
});

function whName(wid: string | null | undefined): string {
  if (!wid) return "—";
  return store.warehouses.find((w) => w.id === wid)?.warehouse_name ?? "—";
}

async function load(): Promise<void> {
  if (!id.value) return;
  try {
    doc.value = (await api.get<StockEntryDetail>(`/stock-entries/${id.value}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const { data } = await api.post<{ id: string }>("/stock-entries", {
      purpose: purpose.value,
      posting_date: postingDate.value,
      from_warehouse_id: needsSource.value ? fromWarehouseId.value || null : null,
      to_warehouse_id: needsTarget.value ? toWarehouseId.value || null : null,
      operating_cost: isRepack.value ? operatingCost.value || 0 : 0,
      operating_cost_account_id: isRepack.value ? operatingCostAccountId.value || null : null,
      remarks: remarks.value || null,
      items: rows.value.filter((r) => r.item_id),
    });
    router.push(`/stock-entries/${data.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function docAction(action: "submit" | "cancel"): Promise<void> {
  error.value = null;
  try {
    await api.post(`/stock-entries/${id.value}/${action}`);
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

onMounted(async () => {
  await Promise.all([store.fetchItems(), store.fetchWarehouses()]);
  await load();
  // account options for the Repack additional-cost credit (best-effort)
  try {
    accountOptions.value = (
      await api.get<{ value: string; label: string }[]>("/registry/account/options")
    ).data;
  } catch {
    accountOptions.value = [];
  }
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
            <router-link to="/stock-entries" class="text-primary hover:underline">Stock Entries</router-link>
            · {{ doc.purpose }}
          </p>
        </div>
        <div class="flex items-center gap-3">
          <StatusBadge :status="doc.docstatus" />
          <PrintButton :path="`/print/Stock%20Entry/${doc.id}`" :title="`${doc.name} — Preview`" />
          <SendEmailButton doctype="Stock Entry" :doc-id="doc.id" :doc-name="doc.name" />
          <button v-if="doc.docstatus === 0" class="btn-primary" @click="docAction('submit')">Submit</button>
          <button v-if="doc.docstatus === 1" class="text-sm text-red-600 hover:underline" @click="docAction('cancel')">Cancel</button>
        </div>
      </div>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <section class="mb-4 grid grid-cols-4 gap-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm text-sm">
        <div><div class="text-xs uppercase text-gray-400">Date</div>{{ formatDate(doc.posting_date) }}</div>
        <div><div class="text-xs uppercase text-gray-400">From</div>{{ whName(doc.from_warehouse_id) }}</div>
        <div><div class="text-xs uppercase text-gray-400">To</div>{{ whName(doc.to_warehouse_id) }}</div>
        <div><div class="text-xs uppercase text-gray-400">Total</div>{{ formatCurrency(doc.total_amount, companyCurrency) }}</div>
        <div v-if="Number(doc.operating_cost) > 0">
          <div class="text-xs uppercase text-gray-400">Additional cost</div>
          {{ formatCurrency(doc.operating_cost, companyCurrency) }}
        </div>
        <div v-if="doc.remarks" class="col-span-4"><div class="text-xs uppercase text-gray-400">Remarks</div>{{ doc.remarks }}</div>
      </section>

      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Item</th><th class="px-4 py-2">Source</th><th class="px-4 py-2">Target</th>
              <th class="px-4 py-2 text-right">Qty</th><th class="px-4 py-2 text-right">Rate</th>
              <th class="px-4 py-2 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in doc.items" :key="r.idx" class="border-t border-gray-100">
              <td class="px-4 py-2 font-medium text-gray-900">{{ r.item_code }} — {{ r.item_name }}</td>
              <td class="px-4 py-2 text-gray-500">{{ whName((r as any).source_warehouse_id) }}</td>
              <td class="px-4 py-2 text-gray-500">{{ whName((r as any).target_warehouse_id) }}</td>
              <td class="px-4 py-2 text-right">{{ formatQty(r.qty) }}</td>
              <td class="px-4 py-2 text-right">{{ formatCurrency(r.basic_rate, companyCurrency) }}</td>
              <td class="px-4 py-2 text-right">{{ formatCurrency(r.amount, companyCurrency) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- CREATE -->
    <template v-else-if="!isEdit">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold text-gray-900">New Stock Entry</h1>
          <p class="text-sm text-gray-500">
            <router-link to="/stock-entries" class="text-primary hover:underline">Stock Entries</router-link>
          </p>
        </div>
        <button class="btn-primary" :disabled="saving || missingCostAccount" @click="save">{{ saving ? "Saving…" : "Save (Draft)" }}</button>
      </div>
      <p v-if="missingCostAccount" class="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700">
        An additional cost needs an expense account to credit — pick one (e.g. "Expenses Included In Valuation").
      </p>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <section class="mb-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div class="grid grid-cols-4 gap-4">
          <div>
            <label class="form-label">Purpose*</label>
            <select v-model="purpose" class="form-input">
              <option>Material Receipt</option><option>Material Issue</option>
              <option>Material Transfer</option><option>Repack</option>
            </select>
          </div>
          <div>
            <label class="form-label">Posting Date*</label>
            <input v-model="postingDate" type="date" class="form-input" />
          </div>
          <div v-if="needsSource">
            <label class="form-label">{{ isRepack ? "Consume From*" : "From Warehouse*" }}</label>
            <select v-model="fromWarehouseId" class="form-input">
              <option value="" disabled>Select…</option>
              <option v-for="w in store.leafWarehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
            </select>
          </div>
          <div v-if="needsTarget">
            <label class="form-label">{{ isRepack ? "Produce Into*" : "To Warehouse*" }}</label>
            <select v-model="toWarehouseId" class="form-input">
              <option value="" disabled>Select…</option>
              <option v-for="w in store.leafWarehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
            </select>
          </div>
        </div>
        <div v-if="isRepack" class="mt-3 grid grid-cols-4 gap-4">
          <div>
            <label class="form-label">Additional cost (labour / freight)</label>
            <input v-model.number="operatingCost" type="number" min="0" step="any" class="form-input" />
          </div>
          <div class="col-span-2">
            <label class="form-label">Additional cost account</label>
            <select v-model="operatingCostAccountId" class="form-input">
              <option value="">— (needed only if cost &gt; 0)</option>
              <option v-for="a in accountOptions" :key="a.value" :value="a.value">{{ a.label }}</option>
            </select>
          </div>
        </div>
        <p v-if="isRepack" class="mt-2 text-xs text-gray-500">
          Tick <strong>Finished</strong> on the rows this repack <em>produces</em>; the other rows are
          consumed. Finished rows are valued at consumed cost (+ additional cost) — with several finished
          rows the value is split by the optional per-row weight (else per unit).
        </p>
        <div class="mb-1 mt-4 grid grid-cols-12 gap-2 text-xs font-medium text-gray-500">
          <div :class="isRepack ? 'col-span-5' : 'col-span-6'">Item</div>
          <div v-if="isRepack" class="col-span-1 text-center">Finished</div>
          <div class="col-span-3 text-right">Qty</div>
          <div v-if="showRate" class="col-span-3 text-right">Rate</div>
          <div v-else-if="isRepack" class="col-span-3 text-right">Value weight</div>
        </div>
        <div v-for="(row, i) in rows" :key="i" class="mb-2 grid grid-cols-12 gap-2">
          <select v-model="row.item_id" class="form-input" :class="isRepack ? 'col-span-5' : 'col-span-6'">
            <option value="" disabled>Item…</option>
            <option v-for="opt in store.itemOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
          <div v-if="isRepack" class="col-span-1 flex items-center justify-center">
            <input v-model="row.is_finished_item" type="checkbox" />
          </div>
          <input v-model.number="row.qty" type="number" min="0" step="any" placeholder="Qty" class="form-input col-span-3 text-right" />
          <input
            v-if="showRate || (isRepack && row.is_finished_item)"
            v-model.number="row.basic_rate" type="number" min="0" step="any"
            :placeholder="isRepack ? 'weight (optional)' : 'Rate'" class="form-input col-span-3 text-right"
          />
          <div v-else class="col-span-3 flex items-center justify-end pr-2 text-xs text-gray-400">at current valuation</div>
        </div>
        <div class="mt-2 flex items-center justify-between">
          <button type="button" class="btn-secondary" @click="rows.push({ item_id: '', qty: 1, basic_rate: 0, is_finished_item: false })">Add Row</button>
          <input v-model="remarks" class="form-input w-72" placeholder="Remarks (optional)" />
        </div>
      </section>
    </template>
  </div>
</template>
