<script setup lang="ts">
// Stock Reconciliation — create (draft) + detail (submit/cancel).

import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import ItemSelect from "@/components/shared/ItemSelect.vue";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import { api } from "@/api/client";
import { useStockStore } from "@/stores/stock";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { formatCurrency, formatDate, formatQty } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type {
  StockBalanceRow,
  StockReconciliationDetail,
  StockReconciliationItemIn,
} from "@/types/stock";

const route = useRoute();
const router = useRouter();
const store = useStockStore();
const companyCurrency = useCompanyCurrency();

const id = computed(() => route.params.id as string | undefined);
const isEdit = computed(() => !!id.value);
const doc = ref<StockReconciliationDetail | null>(null);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const purpose = ref<"Opening Stock" | "Stock Reconciliation">("Stock Reconciliation");
const postingDate = ref(new Date().toISOString().slice(0, 10));
const warehouseId = ref("");
const remarks = ref("");
const rows = ref<StockReconciliationItemIn[]>([{ item_id: "", qty: 0, valuation_rate: null }]);
const balance = ref<Record<string, { qty: number; rate: number }>>({});

function whName(wid: string | null | undefined): string {
  if (!wid) return "—";
  return store.warehouses.find((w) => w.id === wid)?.warehouse_name ?? "—";
}
function currentFor(itemId: string): { qty: number; rate: number } | null {
  if (!itemId || !warehouseId.value) return null;
  return balance.value[`${itemId}|${warehouseId.value}`] ?? { qty: 0, rate: 0 };
}

async function loadBalance(): Promise<void> {
  const resp = await api.get<StockBalanceRow[]>("/reports/stock-balance");
  const map: Record<string, { qty: number; rate: number }> = {};
  for (const r of resp.data) {
    map[`${r.item_id}|${r.warehouse_id}`] = { qty: Number(r.actual_qty), rate: Number(r.valuation_rate) };
  }
  balance.value = map;
}

async function load(): Promise<void> {
  if (!id.value) return;
  try {
    doc.value = (await api.get<StockReconciliationDetail>(`/stock-reconciliations/${id.value}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const { data } = await api.post<{ id: string }>("/stock-reconciliations", {
      purpose: purpose.value,
      posting_date: postingDate.value,
      set_warehouse_id: warehouseId.value || null,
      remarks: remarks.value || null,
      items: rows.value.filter((r) => r.item_id).map((r) => ({
        item_id: r.item_id, qty: r.qty, valuation_rate: r.valuation_rate || null,
      })),
    });
    router.push(`/stock-reconciliations/${data.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function docAction(action: "submit" | "cancel"): Promise<void> {
  error.value = null;
  try {
    await api.post(`/stock-reconciliations/${id.value}/${action}`);
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

onMounted(async () => {
  await Promise.all([store.fetchItems(), store.fetchWarehouses()]);
  await (isEdit.value ? load() : loadBalance());
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
            <router-link to="/stock-reconciliations" class="text-primary hover:underline">Stock Reconciliation</router-link>
            · {{ doc.purpose }}
          </p>
        </div>
        <div class="flex items-center gap-3">
          <StatusBadge :status="doc.docstatus" />
          <button v-if="doc.docstatus === 0" class="btn-primary" @click="docAction('submit')">Submit</button>
          <button v-if="doc.docstatus === 1" class="text-sm text-red-600 hover:underline" @click="docAction('cancel')">Cancel</button>
        </div>
      </div>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <section class="mb-4 grid grid-cols-4 gap-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm text-sm">
        <div><div class="text-xs uppercase text-gray-400">Date</div>{{ formatDate(doc.posting_date) }}</div>
        <div><div class="text-xs uppercase text-gray-400">Warehouse</div>{{ whName(doc.set_warehouse_id) }}</div>
        <div><div class="text-xs uppercase text-gray-400">Difference Posted</div>{{ formatCurrency(doc.difference_amount, companyCurrency) }}</div>
        <div v-if="doc.remarks" class="col-span-4"><div class="text-xs uppercase text-gray-400">Remarks</div>{{ doc.remarks }}</div>
      </section>

      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Item</th>
              <th class="px-4 py-2 text-right">Book Qty</th>
              <th class="px-4 py-2 text-right">Counted Qty</th>
              <th class="px-4 py-2 text-right">Valuation Rate</th>
              <th class="px-4 py-2 text-right">Difference</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in doc.items" :key="r.idx" class="border-t border-gray-100">
              <td class="px-4 py-2 font-medium text-gray-900">{{ r.item_code }} — {{ r.item_name }}</td>
              <td class="px-4 py-2 text-right text-gray-500">{{ formatQty(r.current_qty) }}</td>
              <td class="px-4 py-2 text-right">{{ formatQty(r.qty) }}</td>
              <td class="px-4 py-2 text-right">{{ formatCurrency(r.valuation_rate, companyCurrency) }}</td>
              <td class="px-4 py-2 text-right" :class="Number(r.amount_difference) < 0 ? 'text-red-700' : 'text-green-700'">
                {{ formatCurrency(r.amount_difference, companyCurrency) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- CREATE -->
    <template v-else-if="!isEdit">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold text-gray-900">New Stock Reconciliation</h1>
          <p class="text-sm text-gray-500">
            <router-link to="/stock-reconciliations" class="text-primary hover:underline">Stock Reconciliation</router-link>
          </p>
        </div>
        <button class="btn-primary" :disabled="saving || !warehouseId" @click="save">{{ saving ? "Saving…" : "Save (Draft)" }}</button>
      </div>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <section class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div class="grid grid-cols-3 gap-4">
          <div>
            <label class="form-label">Purpose*</label>
            <select v-model="purpose" class="form-input">
              <option>Stock Reconciliation</option><option>Opening Stock</option>
            </select>
          </div>
          <div>
            <label class="form-label">Posting Date*</label>
            <input v-model="postingDate" type="date" class="form-input" />
          </div>
          <div>
            <label class="form-label">Warehouse*</label>
            <select v-model="warehouseId" class="form-input">
              <option value="" disabled>Select…</option>
              <option v-for="w in store.leafWarehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
            </select>
          </div>
        </div>
        <div class="mb-1 mt-4 grid grid-cols-12 gap-2 text-xs font-medium text-gray-500">
          <div class="col-span-5">Item</div>
          <div class="col-span-2 text-right">Book Qty</div>
          <div class="col-span-2 text-right">Counted Qty*</div>
          <div class="col-span-3 text-right">Valuation Rate</div>
        </div>
        <div v-for="(row, i) in rows" :key="i" class="mb-2 grid grid-cols-12 items-center gap-2">
          <ItemSelect v-model="row.item_id" class="col-span-5" :options="store.itemOptions" placeholder="Select item…" />
          <div class="col-span-2 pr-2 text-right text-sm text-gray-500">
            {{ currentFor(row.item_id) ? currentFor(row.item_id)!.qty : "—" }}
          </div>
          <input v-model.number="row.qty" type="number" min="0" step="any" placeholder="Counted" class="form-input col-span-2 text-right" />
          <input v-model.number="row.valuation_rate" type="number" min="0" step="any"
                 :placeholder="currentFor(row.item_id) && currentFor(row.item_id)!.rate ? String(currentFor(row.item_id)!.rate) : 'auto'"
                 class="form-input col-span-3 text-right" />
        </div>
        <div class="mt-2 flex items-center justify-between">
          <button type="button" class="btn-secondary" @click="rows.push({ item_id: '', qty: 0, valuation_rate: null })">Add Row</button>
          <input v-model="remarks" class="form-input w-72" placeholder="Remarks (optional)" />
        </div>
        <p class="mt-2 text-xs text-gray-400">
          Counted Qty is the absolute new balance (not a delta). Leave Valuation Rate empty to keep the current cost.
        </p>
      </section>
    </template>
  </div>
</template>
