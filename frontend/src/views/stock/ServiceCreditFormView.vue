<script setup lang="ts">
// Service Credit — buy a block of service units (create) + draw it down (detail).

import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import ItemSelect from "@/components/shared/ItemSelect.vue";
import { useStockStore } from "@/stores/stock";
import { useAccountsStore } from "@/stores/accounts";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { formatCurrency, formatDate, formatQty } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { ServiceCreditDetail } from "@/types/stock";

const route = useRoute();
const router = useRouter();
const store = useStockStore();
const accounts = useAccountsStore();
const companyCurrency = useCompanyCurrency();

const id = computed(() => route.params.id as string | undefined);
const isEdit = computed(() => !!id.value);
const doc = ref<ServiceCreditDetail | null>(null);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const form = reactive({
  item_id: "",
  supplier_id: "",
  purchase_date: new Date().toISOString().slice(0, 10),
  purchased_qty: 0,
  rate: 0,
  valid_upto: "",
  remarks: "",
  purchase_invoice_id: "",
  prepaid_account_id: "",
  expense_account_id: "",
  cost_center_id: "",
});

const purchaseInvoices = ref<Array<{ id: string; name: string; supplier_name?: string | null }>>([]);
const assetAccounts = computed(() =>
  accounts.leafAccounts.filter((a) => a.root_type === "Asset"),
);
const expenseAccounts = computed(() =>
  accounts.leafAccounts.filter((a) => a.root_type === "Expense"),
);
function acctName(aid: string | null): string {
  if (!aid) return "—";
  return accounts.leafAccounts.find((a) => a.id === aid)?.account_name ?? "—";
}
function ccName(cid: string | null): string {
  if (!cid) return "—";
  return accounts.costCenterOptions.find((c) => c.value === cid)?.label ?? "—";
}

// usage entry form (detail page)
const usage = reactive({ usage_date: new Date().toISOString().slice(0, 10), qty: 0, remarks: "" });
const addingUsage = ref(false);

const selectedUom = computed(
  () => store.items.find((i) => i.id === form.item_id)?.stock_uom ?? "",
);

const statusClass = computed(() => {
  const s = doc.value?.status;
  if (s === "Active") return "bg-green-50 text-green-700";
  if (s === "Exhausted") return "bg-gray-100 text-gray-600";
  return "bg-amber-50 text-amber-700"; // Expired
});

async function load(): Promise<void> {
  if (!id.value) return;
  try {
    doc.value = (await api.get<ServiceCreditDetail>(`/service-credits/${id.value}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const { data } = await api.post<{ id: string }>("/service-credits", {
      item_id: form.item_id,
      supplier_id: form.supplier_id || null,
      purchase_date: form.purchase_date,
      purchased_qty: form.purchased_qty,
      rate: form.rate || 0,
      valid_upto: form.valid_upto || null,
      remarks: form.remarks || null,
      purchase_invoice_id: form.purchase_invoice_id || null,
      prepaid_account_id: form.prepaid_account_id || null,
      expense_account_id: form.expense_account_id || null,
      cost_center_id: form.cost_center_id || null,
    });
    router.push(`/service-credits/${data.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function addUsage(): Promise<void> {
  if (!usage.qty) return;
  addingUsage.value = true;
  error.value = null;
  try {
    doc.value = (
      await api.post<ServiceCreditDetail>(`/service-credits/${id.value}/usage`, {
        usage_date: usage.usage_date,
        qty: usage.qty,
        remarks: usage.remarks || null,
      })
    ).data;
    usage.qty = 0;
    usage.remarks = "";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    addingUsage.value = false;
  }
}

onMounted(async () => {
  await Promise.all([
    store.fetchItems(),
    accounts.fetchSuppliers(),
    accounts.fetchAccounts(),
    accounts.fetchCostCenters().catch(() => undefined),
    api.get<{ items: Array<{ id: string; name: string; supplier_name?: string | null }> }>(
      "/purchase-invoices", { params: { page_size: 200 } },
    ).then((r) => { purchaseInvoices.value = r.data.items; }).catch(() => undefined),
  ]);
  if (!isEdit.value) {
    // pre-fill from a Purchase Invoice's "Create Service Credit" button
    const q = route.query;
    if (q.purchase_invoice_id) form.purchase_invoice_id = String(q.purchase_invoice_id);
    if (q.supplier_id) form.supplier_id = String(q.supplier_id);
    if (q.item_id) form.item_id = String(q.item_id);
    if (q.qty) form.purchased_qty = Number(q.qty);
    if (q.rate) form.rate = Number(q.rate);
  }
  await load();
});
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <!-- DETAIL -->
    <template v-if="isEdit && doc">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold text-gray-900">{{ doc.name }}</h1>
          <p class="text-sm text-gray-500">
            <router-link to="/service-credits" class="text-primary hover:underline">Service Credits</router-link>
            · {{ doc.item_code }} — {{ doc.item_name }}
          </p>
        </div>
        <span class="rounded-full px-3 py-1 text-sm font-medium" :class="statusClass">{{ doc.status }}</span>
      </div>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <!-- balance cards -->
      <div class="mb-4 grid grid-cols-3 gap-4">
        <div class="rounded-lg border border-gray-200 bg-white p-5 text-center shadow-sm">
          <div class="text-xs uppercase text-gray-400">Purchased</div>
          <div class="text-2xl font-semibold text-gray-900">{{ formatQty(doc.purchased_qty) }}</div>
          <div class="text-xs text-gray-400">{{ doc.uom }}</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-5 text-center shadow-sm">
          <div class="text-xs uppercase text-gray-400">Used</div>
          <div class="text-2xl font-semibold text-amber-700">{{ formatQty(doc.consumed_qty) }}</div>
          <div class="text-xs text-gray-400">{{ doc.uom }}</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-5 text-center shadow-sm">
          <div class="text-xs uppercase text-gray-400">Remaining (credit)</div>
          <div class="text-2xl font-semibold text-green-700">{{ formatQty(doc.balance_qty) }}</div>
          <div class="text-xs text-gray-400">{{ doc.uom }}</div>
        </div>
      </div>

      <section class="mb-4 grid grid-cols-4 gap-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm text-sm">
        <div><div class="text-xs uppercase text-gray-400">Purchased On</div>{{ formatDate(doc.purchase_date) }}</div>
        <div><div class="text-xs uppercase text-gray-400">Supplier</div>{{ doc.supplier_name ?? "—" }}</div>
        <div><div class="text-xs uppercase text-gray-400">Rate</div>{{ formatCurrency(doc.rate, companyCurrency) }} / {{ doc.uom }}</div>
        <div><div class="text-xs uppercase text-gray-400">Valid Upto</div>{{ doc.valid_upto ? formatDate(doc.valid_upto) : "—" }}</div>
        <div v-if="doc.remarks" class="col-span-4"><div class="text-xs uppercase text-gray-400">Remarks</div>{{ doc.remarks }}</div>
      </section>

      <section v-if="doc.purchase_invoice_id || doc.prepaid_account_id || doc.expense_account_id"
               class="mb-4 grid grid-cols-4 gap-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm text-sm">
        <div>
          <div class="text-xs uppercase text-gray-400">Purchase Invoice</div>
          <router-link v-if="doc.purchase_invoice_id" :to="`/purchase-invoices/${doc.purchase_invoice_id}`"
                       class="text-primary hover:underline">{{ doc.purchase_invoice_name ?? "view" }}</router-link>
          <span v-else>—</span>
        </div>
        <div><div class="text-xs uppercase text-gray-400">Prepaid Account</div>{{ acctName(doc.prepaid_account_id) }}</div>
        <div><div class="text-xs uppercase text-gray-400">Expense Account</div>{{ acctName(doc.expense_account_id) }}</div>
        <div><div class="text-xs uppercase text-gray-400">Cost Center</div>{{ ccName(doc.cost_center_id) }}</div>
        <div class="col-span-4 text-xs text-gray-400">
          Each usage posts <strong>Dr Expense / Cr Prepaid</strong> for qty × rate (prepaid asset booked by the linked invoice).
        </div>
      </section>

      <!-- log usage -->
      <section v-if="doc.status !== 'Exhausted'" class="mb-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <h2 class="mb-3 text-sm font-semibold text-gray-900">Log Usage</h2>
        <div class="grid grid-cols-12 items-end gap-3">
          <div class="col-span-3">
            <label class="form-label">Date</label>
            <input v-model="usage.usage_date" type="date" class="form-input" />
          </div>
          <div class="col-span-3">
            <label class="form-label">Qty ({{ doc.uom }})</label>
            <input v-model.number="usage.qty" type="number" min="0" step="any" class="form-input text-right" />
          </div>
          <div class="col-span-4">
            <label class="form-label">Remarks</label>
            <input v-model="usage.remarks" class="form-input" placeholder="optional" />
          </div>
          <div class="col-span-2">
            <button class="btn-primary w-full" :disabled="addingUsage || !usage.qty" @click="addUsage">
              {{ addingUsage ? "…" : "Add" }}
            </button>
          </div>
        </div>
      </section>

      <!-- usage log -->
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="border-b border-gray-100 px-5 py-3 text-sm font-semibold text-gray-900">Usage Log</div>
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr><th class="px-5 py-2">#</th><th class="px-5 py-2">Date</th><th class="px-5 py-2 text-right">Qty</th><th class="px-5 py-2">Remarks</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in doc.usages" :key="u.idx" class="border-t border-gray-100">
              <td class="px-5 py-2 text-gray-400">{{ u.idx }}</td>
              <td class="px-5 py-2 text-gray-500">{{ formatDate(u.usage_date) }}</td>
              <td class="px-5 py-2 text-right">{{ formatQty(u.qty) }}</td>
              <td class="px-5 py-2 text-gray-500">{{ u.remarks ?? "—" }}</td>
            </tr>
            <tr v-if="!doc.usages.length"><td colspan="4" class="px-5 py-8 text-center text-gray-400">No usage logged yet.</td></tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- CREATE -->
    <template v-else-if="!isEdit">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold text-gray-900">New Service Credit</h1>
          <p class="text-sm text-gray-500">
            <router-link to="/service-credits" class="text-primary hover:underline">Service Credits</router-link>
            · a prepaid block of a service (e.g. support hours)
          </p>
        </div>
        <button class="btn-primary" :disabled="saving || !form.item_id || !form.purchased_qty" @click="save">
          {{ saving ? "Saving…" : "Create" }}
        </button>
      </div>
      <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

      <section class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">Service (item)*</label>
            <ItemSelect v-model="form.item_id" :options="store.itemOptions" placeholder="Select a service…" />
            <p v-if="selectedUom" class="mt-1 text-xs text-gray-400">Measured in <strong>{{ selectedUom }}</strong> (the item's UOM)</p>
          </div>
          <div>
            <label class="form-label">Supplier</label>
            <select v-model="form.supplier_id" class="form-input">
              <option value="">—</option>
              <option v-for="s in accounts.suppliers" :key="s.id" :value="s.id">{{ s.supplier_name }}</option>
            </select>
          </div>
          <div>
            <label class="form-label">Purchase Date*</label>
            <input v-model="form.purchase_date" type="date" class="form-input" />
          </div>
          <div>
            <label class="form-label">Valid Upto</label>
            <input v-model="form.valid_upto" type="date" class="form-input" />
          </div>
          <div>
            <label class="form-label">Purchased Qty* ({{ selectedUom || "units" }})</label>
            <input v-model.number="form.purchased_qty" type="number" min="0" step="any" class="form-input text-right" />
          </div>
          <div>
            <label class="form-label">Rate (per {{ selectedUom || "unit" }})</label>
            <input v-model.number="form.rate" type="number" min="0" step="any" class="form-input text-right" />
          </div>
          <div class="col-span-2">
            <label class="form-label">Remarks</label>
            <input v-model="form.remarks" class="form-input" placeholder="optional" />
          </div>
        </div>
      </section>

      <section class="mt-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <h2 class="mb-1 text-sm font-semibold text-gray-900">Accounting (optional)</h2>
        <p class="mb-3 text-xs text-gray-400">
          Buy via a Purchase Invoice booked to your Prepaid (asset) account and link it here; each
          usage then posts <strong>Dr Expense / Cr Prepaid</strong>. Leave blank to track quantity only.
        </p>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">Purchase Invoice</label>
            <select v-model="form.purchase_invoice_id" class="form-input">
              <option value="">—</option>
              <option v-for="pi in purchaseInvoices" :key="pi.id" :value="pi.id">
                {{ pi.name }}<template v-if="pi.supplier_name"> · {{ pi.supplier_name }}</template>
              </option>
            </select>
          </div>
          <div>
            <label class="form-label">Cost Center</label>
            <select v-model="form.cost_center_id" class="form-input">
              <option value="">—</option>
              <option v-for="c in accounts.costCenterOptions" :key="c.value" :value="c.value">{{ c.label }}</option>
            </select>
          </div>
          <div>
            <label class="form-label">Prepaid Account (asset)</label>
            <select v-model="form.prepaid_account_id" class="form-input">
              <option value="">—</option>
              <option v-for="a in assetAccounts" :key="a.id" :value="a.id">{{ a.account_name }}</option>
            </select>
          </div>
          <div>
            <label class="form-label">Expense Account</label>
            <select v-model="form.expense_account_id" class="form-input">
              <option value="">— defaults from item —</option>
              <option v-for="a in expenseAccounts" :key="a.id" :value="a.id">{{ a.account_name }}</option>
            </select>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
