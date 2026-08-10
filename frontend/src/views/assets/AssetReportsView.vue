<script setup lang="ts">
// Asset reports: the Fixed Asset Register (net block — gross, accumulated depreciation,
// book value per asset as of a date) and the Depreciation Ledger (every posted
// depreciation entry with its Journal Entry).
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/client";
import { formatCurrency, formatDate } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";

interface RegisterRow {
  asset_id: string;
  name: string;
  asset_name: string;
  category_name: string | null;
  location_name: string | null;
  gross_purchase_amount: string;
  accumulated_depreciation: string;
  book_value: string;
  status: string;
}
interface LedgerRow {
  asset_no: string;
  asset_name: string;
  category_name: string | null;
  schedule_date: string;
  depreciation_amount: string;
  accumulated_depreciation: string;
  journal_entry_no: string | null;
}
interface MaintenanceRow {
  id: string;
  name: string;
  asset_name: string | null;
  maintenance_type: string;
  periodicity: string;
  next_due_date: string;
  assigned_to: string | null;
  status: string;
  days_overdue: number;
}

const route = useRoute();
type Tab = "register" | "ledger" | "maintenance";
function tabFromQuery(value: unknown): Tab {
  return value === "ledger" || value === "maintenance" ? value : "register";
}
const tab = ref<Tab>(tabFromQuery(route.query.tab));
const today = new Date().toISOString().slice(0, 10);
const asOf = ref(today);
const includeDisposed = ref(false);
const fromDate = ref("");
const toDate = ref(today);

const register = ref<RegisterRow[]>([]);
const ledger = ref<LedgerRow[]>([]);
const maintenance = ref<MaintenanceRow[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const totals = computed(() => ({
  gross: register.value.reduce((s, r) => s + Number(r.gross_purchase_amount), 0),
  accum: register.value.reduce((s, r) => s + Number(r.accumulated_depreciation), 0),
  book: register.value.reduce((s, r) => s + Number(r.book_value), 0),
}));

async function loadRegister(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    register.value = (
      await api.get<RegisterRow[]>("/asset-reports/fixed-asset-register", {
        params: { as_of: asOf.value, include_disposed: includeDisposed.value },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function loadLedger(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    ledger.value = (
      await api.get<LedgerRow[]>("/asset-reports/depreciation-ledger", {
        params: { from_date: fromDate.value || undefined, to_date: toDate.value || undefined },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function loadMaintenance(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    maintenance.value = (await api.get<MaintenanceRow[]>("/asset-reports/maintenance-due")).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

function reload(): void {
  if (tab.value === "register") void loadRegister();
  else if (tab.value === "ledger") void loadLedger();
  else void loadMaintenance();
}

watch(tab, reload);
// The sidebar links to /asset-reports, ?tab=ledger and ?tab=maintenance — same path,
// different query — so the view is not remounted when moving between them.
watch(
  () => route.query.tab,
  (value) => {
    tab.value = tabFromQuery(value);
  },
);
onMounted(reload);
</script>

<template>
  <div>
    <h1 class="mb-1 text-xl font-semibold text-gray-900">Asset Reports</h1>
    <p class="mb-4 text-sm text-gray-500">Net block and posted depreciation across your fixed assets.</p>

    <div class="mb-4 flex gap-2 border-b border-gray-200">
      <button
        class="px-3 py-2 text-sm font-medium"
        :class="tab === 'register' ? 'border-b-2 border-primary text-primary' : 'text-gray-500'"
        @click="tab = 'register'"
      >
        Fixed Asset Register
      </button>
      <button
        class="px-3 py-2 text-sm font-medium"
        :class="tab === 'ledger' ? 'border-b-2 border-primary text-primary' : 'text-gray-500'"
        @click="tab = 'ledger'"
      >
        Depreciation Ledger
      </button>
      <button
        class="px-3 py-2 text-sm font-medium"
        :class="tab === 'maintenance' ? 'border-b-2 border-primary text-primary' : 'text-gray-500'"
        @click="tab = 'maintenance'"
      >
        Maintenance Due
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <!-- Fixed Asset Register -->
    <template v-if="tab === 'register'">
      <div class="mb-3 flex flex-wrap items-end gap-4">
        <div>
          <label class="form-label">As of</label>
          <input v-model="asOf" type="date" class="form-input" @change="loadRegister" />
        </div>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="includeDisposed" type="checkbox" class="rounded border-gray-300" @change="loadRegister" />
          Include disposed
        </label>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Asset</th><th class="px-4 py-2">Category</th>
              <th class="px-4 py-2">Location</th>
              <th class="px-4 py-2 text-right">Cost</th>
              <th class="px-4 py-2 text-right">Accumulated Dep.</th>
              <th class="px-4 py-2 text-right">Book Value</th>
              <th class="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in register" :key="row.asset_id" class="border-t border-gray-100">
              <td class="px-4 py-1.5">
                <div class="font-medium text-gray-900">{{ row.asset_name }}</div>
                <div class="text-xs text-gray-400">{{ row.name }}</div>
              </td>
              <td class="px-4 py-1.5">{{ row.category_name ?? "—" }}</td>
              <td class="px-4 py-1.5 text-gray-500">{{ row.location_name ?? "—" }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatCurrency(row.gross_purchase_amount) }}</td>
              <td class="px-4 py-1.5 text-right text-gray-500">{{ formatCurrency(row.accumulated_depreciation) }}</td>
              <td class="px-4 py-1.5 text-right font-medium">{{ formatCurrency(row.book_value) }}</td>
              <td class="px-4 py-1.5"><StatusBadge :status="row.status" /></td>
            </tr>
            <tr v-if="register.length" class="border-t-2 border-gray-300 bg-gray-50 font-semibold">
              <td class="px-4 py-2" colspan="3">Total ({{ register.length }} assets)</td>
              <td class="px-4 py-2 text-right">{{ formatCurrency(totals.gross) }}</td>
              <td class="px-4 py-2 text-right">{{ formatCurrency(totals.accum) }}</td>
              <td class="px-4 py-2 text-right">{{ formatCurrency(totals.book) }}</td>
              <td></td>
            </tr>
            <tr v-if="!register.length && !loading">
              <td colspan="7" class="px-4 py-8 text-center text-gray-400">No assets on the books.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- Maintenance Due -->
    <template v-else-if="tab === 'maintenance'">
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Asset</th><th class="px-4 py-2">Type</th>
              <th class="px-4 py-2">Recurs</th><th class="px-4 py-2">Next Due</th>
              <th class="px-4 py-2">Assigned To</th><th class="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in maintenance" :key="m.id" class="border-t border-gray-100" :class="{ 'bg-red-50/40': m.days_overdue > 0 }">
              <td class="px-4 py-1.5 font-medium text-gray-900">{{ m.asset_name ?? "—" }}</td>
              <td class="px-4 py-1.5">{{ m.maintenance_type }}</td>
              <td class="px-4 py-1.5 text-gray-500">{{ m.periodicity }}</td>
              <td class="px-4 py-1.5">
                {{ formatDate(m.next_due_date) }}
                <span v-if="m.days_overdue > 0" class="ml-1 text-xs font-medium text-red-600">
                  ({{ m.days_overdue }}d overdue)
                </span>
                <span v-else class="ml-1 text-xs text-gray-400">(in {{ -m.days_overdue }}d)</span>
              </td>
              <td class="px-4 py-1.5 text-gray-500">{{ m.assigned_to ?? "—" }}</td>
              <td class="px-4 py-1.5">{{ m.status }}</td>
            </tr>
            <tr v-if="!maintenance.length && !loading">
              <td colspan="6" class="px-4 py-8 text-center text-gray-400">
                No scheduled maintenance due. Set a "Next Due" date on a Maintenance record.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- Depreciation Ledger -->
    <template v-else-if="tab === 'ledger'">
      <div class="mb-3 flex flex-wrap items-end gap-4">
        <div>
          <label class="form-label">From</label>
          <input v-model="fromDate" type="date" class="form-input" @change="loadLedger" />
        </div>
        <div>
          <label class="form-label">To</label>
          <input v-model="toDate" type="date" class="form-input" @change="loadLedger" />
        </div>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Date</th><th class="px-4 py-2">Asset</th>
              <th class="px-4 py-2">Category</th>
              <th class="px-4 py-2 text-right">Depreciation</th>
              <th class="px-4 py-2 text-right">Accumulated</th>
              <th class="px-4 py-2">Journal Entry</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in ledger" :key="i" class="border-t border-gray-100">
              <td class="px-4 py-1.5">{{ formatDate(row.schedule_date) }}</td>
              <td class="px-4 py-1.5">
                <div class="font-medium text-gray-900">{{ row.asset_name }}</div>
                <div class="text-xs text-gray-400">{{ row.asset_no }}</div>
              </td>
              <td class="px-4 py-1.5 text-gray-500">{{ row.category_name ?? "—" }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatCurrency(row.depreciation_amount) }}</td>
              <td class="px-4 py-1.5 text-right text-gray-500">{{ formatCurrency(row.accumulated_depreciation) }}</td>
              <td class="px-4 py-1.5 text-xs text-gray-500">{{ row.journal_entry_no ?? "—" }}</td>
            </tr>
            <tr v-if="!ledger.length && !loading">
              <td colspan="6" class="px-4 py-8 text-center text-gray-400">
                No depreciation posted in this period.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
