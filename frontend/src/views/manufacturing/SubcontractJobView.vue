<script setup lang="ts">
// Subcontract Job list — send materials to a job-work vendor, receive FG back.
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatDate, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { BomListItem, ManufacturingSettings, SubcontractJobListItem } from "@/types/manufacturing";

interface WarehouseOpt { id: string; warehouse_name: string; is_group?: boolean }
interface SupplierOpt { id: string; supplier_name: string }
interface AccountOpt { value: string; label: string }

const router = useRouter();
const rows = ref<SubcontractJobListItem[]>([]);
const boms = ref<BomListItem[]>([]);
const warehouses = ref<WarehouseOpt[]>([]);
const suppliers = ref<SupplierOpt[]>([]);
const expenseAccounts = ref<AccountOpt[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const showForm = ref(false);
const saving = ref(false);

const today = new Date().toISOString().slice(0, 10);
const fBom = ref("");
const fSupplier = ref("");
const fQty = ref(1);
const fPosting = ref(today);
const fSource = ref("");
const fSupplierWh = ref("");
const fFg = ref("");
const fServiceCost = ref(0);
const fServiceAccount = ref("");

async function fetchList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rows.value = (
      await api.get<ListResponse<SubcontractJobListItem>>("/subcontract-jobs", {
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
  const [b, w, s, a, settings] = await Promise.all([
    api.get<ListResponse<BomListItem>>("/boms", { params: { page_size: 200 } }),
    api.get<WarehouseOpt[]>("/warehouses"),
    api.get<ListResponse<SupplierOpt>>("/suppliers", { params: { page_size: 200 } }),
    api.get<AccountOpt[]>("/registry/account/options").catch(() => ({ data: [] as AccountOpt[] })),
    api.get<ManufacturingSettings>("/manufacturing/settings").catch(() => ({ data: null })),
  ]);
  boms.value = b.data.items.filter((x) => x.docstatus === 1 && x.is_active);
  warehouses.value = w.data.filter((x) => !x.is_group);
  suppliers.value = s.data.items;
  expenseAccounts.value = a.data;
  if (settings.data) {
    fSource.value = settings.data.default_source_warehouse_id ?? "";
    fFg.value = settings.data.default_fg_warehouse_id ?? "";
  }
}

async function save(): Promise<void> {
  if (!fBom.value || !fSupplier.value || !fSupplierWh.value) {
    error.value = { detail: "BOM, supplier, and supplier warehouse are required", code: "ERR_VALIDATION", field: null };
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<{ id: string }>("/subcontract-jobs", {
        bom_id: fBom.value,
        supplier_id: fSupplier.value,
        qty: fQty.value,
        posting_date: fPosting.value,
        source_warehouse_id: fSource.value || null,
        supplier_warehouse_id: fSupplierWh.value,
        fg_warehouse_id: fFg.value || null,
        service_cost: fServiceCost.value || 0,
        service_cost_account_id: fServiceAccount.value || null,
      })
    ).data;
    void router.push(`/subcontract-jobs/${created.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  await Promise.all([fetchOptions(), fetchList()]);
});
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Subcontract Jobs</h1>
        <p class="text-sm text-gray-500">Send materials to a vendor and receive finished goods back.</p>
      </div>
      <button type="button" class="btn-primary" @click="showForm = !showForm">
        {{ showForm ? "Cancel" : "New Subcontract Job" }}
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
          <label class="form-label">BOM *</label>
          <select v-model="fBom" class="form-input" required>
            <option value="">—</option>
            <option v-for="b in boms" :key="b.id" :value="b.id">
              {{ b.name }} — {{ b.production_item_code }}
            </option>
          </select>
        </div>
        <div>
          <label class="form-label">Supplier *</label>
          <select v-model="fSupplier" class="form-input" required>
            <option value="">—</option>
            <option v-for="s in suppliers" :key="s.id" :value="s.id">{{ s.supplier_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Qty to make *</label>
          <input v-model.number="fQty" type="number" min="0.000001" step="any" class="form-input" required />
        </div>
        <div>
          <label class="form-label">Posting date</label>
          <input v-model="fPosting" type="date" class="form-input" />
        </div>
        <div>
          <label class="form-label">Source warehouse</label>
          <select v-model="fSource" class="form-input">
            <option value="">—</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Supplier warehouse *</label>
          <select v-model="fSupplierWh" class="form-input" required>
            <option value="">—</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Finished-goods warehouse</label>
          <select v-model="fFg" class="form-input">
            <option value="">—</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Service cost (whole job)</label>
          <input v-model.number="fServiceCost" type="number" min="0" step="any" class="form-input" />
        </div>
        <div>
          <label class="form-label">Service cost account</label>
          <select v-model="fServiceAccount" class="form-input">
            <option value="">—</option>
            <option v-for="a in expenseAccounts" :key="a.value" :value="a.value">{{ a.label }}</option>
          </select>
        </div>
      </div>
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
            <th class="px-4 py-3">Supplier</th>
            <th class="px-4 py-3">Item</th>
            <th class="px-4 py-3">Qty</th>
            <th class="px-4 py-3">Sent / Recv</th>
            <th class="px-4 py-3">Status</th>
            <th class="px-4 py-3">Date</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="loading">
            <td colspan="7" class="px-4 py-6 text-center text-gray-400">Loading…</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="7" class="px-4 py-6 text-center text-gray-400">No subcontract jobs yet.</td>
          </tr>
          <tr
            v-for="r in rows"
            :key="r.id"
            class="cursor-pointer hover:bg-gray-50"
            @click="router.push(`/subcontract-jobs/${r.id}`)"
          >
            <td class="px-4 py-3 font-medium text-indigo-700">{{ r.name }}</td>
            <td class="px-4 py-3">{{ r.supplier_name }}</td>
            <td class="px-4 py-3">{{ r.production_item_code }}</td>
            <td class="px-4 py-3">{{ formatQty(r.qty) }}</td>
            <td class="px-4 py-3">{{ formatQty(r.sent_qty) }} / {{ formatQty(r.received_qty) }}</td>
            <td class="px-4 py-3"><StatusBadge :status="r.status" /></td>
            <td class="px-4 py-3">{{ formatDate(r.posting_date) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
