<script setup lang="ts">
// Manufacturing reports (tabbed, URL-driven): Production Register, BOM Where-Used, and the
// BOM Stock "can I build N?" report.
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatCurrency, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  BomExplorerReport,
  BomListItem,
  BomStockReport,
  BomWhereUsedRow,
  CapacityBoard,
  DemandForecast,
  LeadTimeEstimate,
  MaterialShortageRow,
  PeggingTimeline,
  ProductionAnalyticsRow,
  ProductionRegisterRow,
  ReverseSchedule,
  WorkOrderSummaryRow,
} from "@/types/manufacturing";

interface ItemOpt { id: string; item_code: string; item_name: string }

const route = useRoute();
const router = useRouter();

const TABS = [
  { key: "production", label: "Production Register" },
  { key: "wo-summary", label: "Work Order Summary" },
  { key: "analytics", label: "Production Analytics" },
  { key: "shortage", label: "Material Shortage" },
  { key: "where-used", label: "BOM Where-Used" },
  { key: "bom-stock", label: "BOM Stock (Can I build?)" },
  { key: "explorer", label: "BOM Explorer" },
];
const tab = ref<string>((route.query.tab as string) || "production");

// Planning tools moved to /manufacturing-planning
if (route.query.tab === "planning") {
  void router.replace({ path: "/manufacturing-planning" });
}

const error = ref<ErrorEnvelope | null>(null);
const items = ref<ItemOpt[]>([]);
const boms = ref<BomListItem[]>([]);

const register = ref<ProductionRegisterRow[]>([]);
const woSummary = ref<WorkOrderSummaryRow[]>([]);
const analytics = ref<ProductionAnalyticsRow[]>([]);
const shortage = ref<MaterialShortageRow[]>([]);
const onlyShort = ref(false);
const whereUsedItem = ref("");
const whereUsed = ref<BomWhereUsedRow[]>([]);
const stockBom = ref("");
const stockQty = ref<number>(1);
const stockReport = ref<BomStockReport | null>(null);
const explorerBom = ref("");
const explorerQty = ref<number>(1);
const explorerFlattenAll = ref(true);
const explorerReport = ref<BomExplorerReport | null>(null);

const planItem = ref("");
const planQty = ref<number>(1);
const planAsOf = ref(new Date().toISOString().slice(0, 10));
const planDelivery = ref("");
const ctpResult = ref<LeadTimeEstimate | null>(null);
const reverseResult = ref<ReverseSchedule | null>(null);
const peggingResult = ref<PeggingTimeline | null>(null);
const forecastResult = ref<DemandForecast | null>(null);
const capacityBoard = ref<CapacityBoard | null>(null);
const planBusy = ref(false);
const whatIfExtraItem = ref("");
const whatIfExtraQty = ref<number>(0);
const whatIfLeadItem = ref("");
const whatIfLeadDays = ref<number>(0);
const forecastLookback = ref(6);
const forecastHorizon = ref(3);

function switchTab(key: string): void {
  tab.value = key;
  void router.replace({ query: { ...route.query, tab: key } });
  if (key === "shortage") void loadShortage();
  if (key === "wo-summary") void loadWoSummary();
  if (key === "analytics") void loadAnalytics();
  if (key === "planning") void loadCapacityBoard();
}
watch(() => route.query.tab, (t) => {
  if (t) {
    tab.value = t as string;
    if (t === "shortage") void loadShortage();
    if (t === "wo-summary") void loadWoSummary();
    if (t === "analytics") void loadAnalytics();
    if (t === "planning") void loadCapacityBoard();
  }
});

async function loadShortage(): Promise<void> {
  error.value = null;
  try {
    shortage.value = (
      await api.get<MaterialShortageRow[]>("/manufacturing-reports/material-shortage", {
        params: { only_short: onlyShort.value },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadRegister(): Promise<void> {
  error.value = null;
  try {
    register.value = (
      await api.get<ProductionRegisterRow[]>("/manufacturing-reports/production-register")
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadWoSummary(): Promise<void> {
  error.value = null;
  try {
    woSummary.value = (
      await api.get<WorkOrderSummaryRow[]>("/manufacturing-reports/work-order-summary")
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadAnalytics(): Promise<void> {
  error.value = null;
  try {
    analytics.value = (
      await api.get<ProductionAnalyticsRow[]>("/manufacturing-reports/production-analytics")
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function runCapableToPromise(): Promise<void> {
  if (!planItem.value) return;
  error.value = null;
  planBusy.value = true;
  reverseResult.value = null;
  peggingResult.value = null;
  forecastResult.value = null;
  try {
    ctpResult.value = (
      await api.get<LeadTimeEstimate>("/manufacturing-reports/capable-to-promise", {
        params: {
          item_id: planItem.value,
          qty: planQty.value || 1,
          as_of: planAsOf.value || undefined,
        },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
    ctpResult.value = null;
  } finally {
    planBusy.value = false;
  }
}

async function runReverseSchedule(): Promise<void> {
  if (!planItem.value || !planDelivery.value) return;
  error.value = null;
  planBusy.value = true;
  ctpResult.value = null;
  peggingResult.value = null;
  forecastResult.value = null;
  try {
    reverseResult.value = (
      await api.get<ReverseSchedule>("/manufacturing-reports/reverse-schedule", {
        params: {
          item_id: planItem.value,
          qty: planQty.value || 1,
          as_of: planAsOf.value || undefined,
          delivery_date: planDelivery.value,
        },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
    reverseResult.value = null;
  } finally {
    planBusy.value = false;
  }
}

async function runPegging(): Promise<void> {
  if (!planItem.value) return;
  error.value = null;
  planBusy.value = true;
  ctpResult.value = null;
  reverseResult.value = null;
  forecastResult.value = null;
  try {
    peggingResult.value = (
      await api.get<PeggingTimeline>("/manufacturing-reports/pegging", {
        params: {
          item_id: planItem.value,
          as_of: planAsOf.value || undefined,
        },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
    peggingResult.value = null;
  } finally {
    planBusy.value = false;
  }
}

async function runForecast(): Promise<void> {
  if (!planItem.value) return;
  error.value = null;
  planBusy.value = true;
  ctpResult.value = null;
  reverseResult.value = null;
  peggingResult.value = null;
  try {
    forecastResult.value = (
      await api.get<DemandForecast>("/manufacturing-reports/demand-forecast", {
        params: {
          item_id: planItem.value,
          lookback_months: forecastLookback.value,
          horizon_months: forecastHorizon.value,
          as_of: planAsOf.value || undefined,
        },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
    forecastResult.value = null;
  } finally {
    planBusy.value = false;
  }
}

async function runWhatIfCtp(): Promise<void> {
  if (!planItem.value) return;
  error.value = null;
  planBusy.value = true;
  reverseResult.value = null;
  peggingResult.value = null;
  forecastResult.value = null;
  try {
    const extra_stock =
      whatIfExtraItem.value && whatIfExtraQty.value > 0
        ? [{ item_id: whatIfExtraItem.value, extra_qty: whatIfExtraQty.value }]
        : [];
    const lead_time_overrides =
      whatIfLeadItem.value && whatIfLeadDays.value >= 0
        ? [{ item_id: whatIfLeadItem.value, lead_time_days: whatIfLeadDays.value }]
        : [];
    ctpResult.value = (
      await api.post<LeadTimeEstimate>("/manufacturing-reports/what-if-ctp", {
        item_id: planItem.value,
        qty: planQty.value || 1,
        as_of: planAsOf.value || null,
        extra_stock,
        lead_time_overrides,
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
    ctpResult.value = null;
  } finally {
    planBusy.value = false;
  }
}

async function loadCapacityBoard(): Promise<void> {
  error.value = null;
  try {
    capacityBoard.value = (
      await api.get<CapacityBoard>("/manufacturing-reports/capacity-board", {
        params: { as_of: planAsOf.value || undefined },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
    capacityBoard.value = null;
  }
}

async function loadWhereUsed(): Promise<void> {
  if (!whereUsedItem.value) { whereUsed.value = []; return; }
  error.value = null;
  try {
    whereUsed.value = (
      await api.get<BomWhereUsedRow[]>("/manufacturing-reports/bom-where-used", {
        params: { item_id: whereUsedItem.value },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadBomStock(): Promise<void> {
  if (!stockBom.value) { stockReport.value = null; return; }
  error.value = null;
  try {
    stockReport.value = (
      await api.get<BomStockReport>("/manufacturing-reports/bom-stock", {
        params: { bom_id: stockBom.value, for_qty: stockQty.value || 1 },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function loadExplorer(): Promise<void> {
  if (!explorerBom.value) { explorerReport.value = null; return; }
  error.value = null;
  try {
    explorerReport.value = (
      await api.get<BomExplorerReport>("/manufacturing-reports/bom-explorer", {
        params: {
          bom_id: explorerBom.value,
          for_qty: explorerQty.value || 1,
          flatten_all: explorerFlattenAll.value,
        },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

onMounted(async () => {
  const [it, bm] = await Promise.all([
    api.get<ListResponse<ItemOpt>>("/items", { params: { page_size: 200 } }),
    api.get<ListResponse<BomListItem>>("/boms", { params: { page_size: 200 } }),
  ]);
  items.value = it.data.items;
  boms.value = bm.data.items;
  await loadRegister();
  if (tab.value === "shortage") await loadShortage();
  if (tab.value === "wo-summary") await loadWoSummary();
  if (tab.value === "analytics") await loadAnalytics();
  if (tab.value === "planning") await loadCapacityBoard();
});
</script>

<template>
  <div>
    <h1 class="mb-1 text-xl font-semibold text-gray-900">Manufacturing Reports</h1>
    <div class="mb-4 flex gap-1 border-b border-gray-200">
      <button
        v-for="t in TABS"
        :key="t.key"
        class="border-b-2 px-4 py-2 text-sm font-medium"
        :class="tab === t.key ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
        @click="switchTab(t.key)"
      >
        {{ t.label }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <!-- Production Register -->
    <div v-if="tab === 'production'" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Work Order</th>
            <th class="px-4 py-2">Finished good</th>
            <th class="px-4 py-2 text-right">Qty</th>
            <th class="px-4 py-2 text-right">Produced</th>
            <th class="px-4 py-2 text-right">Pending</th>
            <th class="px-4 py-2 text-right">Est. cost</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in register" :key="r.work_order_id" class="cursor-pointer border-t border-gray-100 hover:bg-gray-50" @click="router.push(`/work-orders/${r.work_order_id}`)">
            <td class="px-4 py-1.5 font-medium text-gray-900">{{ r.name }}</td>
            <td class="px-4 py-1.5">{{ r.production_item_name ?? r.production_item_code }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.produced_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.pending_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(r.estimated_cost) }}</td>
            <td class="px-4 py-1.5"><StatusBadge :status="r.status" /></td>
          </tr>
          <tr v-if="!register.length"><td colspan="7" class="px-4 py-8 text-center text-gray-400">No Work Orders.</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Work Order Summary -->
    <div v-else-if="tab === 'wo-summary'" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Status</th>
            <th class="px-4 py-2 text-right">Count</th>
            <th class="px-4 py-2 text-right">Qty</th>
            <th class="px-4 py-2 text-right">Produced</th>
            <th class="px-4 py-2 text-right">Pending</th>
            <th class="px-4 py-2 text-right">Est. cost</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in woSummary" :key="r.status" class="border-t border-gray-100">
            <td class="px-4 py-1.5"><StatusBadge :status="r.status" /></td>
            <td class="px-4 py-1.5 text-right">{{ r.count }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.total_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.total_produced_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.total_pending_qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(r.total_estimated_cost) }}</td>
          </tr>
          <tr v-if="!woSummary.length"><td colspan="6" class="px-4 py-8 text-center text-gray-400">No data.</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Production Analytics -->
    <div v-else-if="tab === 'analytics'" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Period</th>
            <th class="px-4 py-2 text-right">WOs completed</th>
            <th class="px-4 py-2 text-right">Qty produced</th>
            <th class="px-4 py-2 text-right">Est. cost</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in analytics" :key="r.period" class="border-t border-gray-100">
            <td class="px-4 py-1.5 font-medium">{{ r.period }}</td>
            <td class="px-4 py-1.5 text-right">{{ r.work_orders_completed }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(r.qty_produced) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(r.estimated_cost) }}</td>
          </tr>
          <tr v-if="!analytics.length"><td colspan="4" class="px-4 py-8 text-center text-gray-400">No completed production yet.</td></tr>
        </tbody>
      </table>
    </div>

    <!-- CTP / Reverse / Forecast / Capacity (Phase 7) -->
    <div v-else-if="tab === 'planning'" class="space-y-4">
      <p class="text-sm text-gray-500">
        Soft planning (not finite capacity). Prefer seeded <strong>FG-GEARBOX</strong> —
        partial RAW-ALUM stock + open Sales Order make CTP / reverse / pegging / what-if interesting.
      </p>
      <div class="grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label class="form-label">Finished item</label>
          <select v-model="planItem" class="form-input">
            <option value="" disabled>Select…</option>
            <option v-for="i in items" :key="i.id" :value="i.id">{{ i.item_code }} — {{ i.item_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Qty (CTP / reverse / what-if)</label>
          <input v-model.number="planQty" type="number" min="0.000001" step="any" class="form-input" />
        </div>
        <div>
          <label class="form-label">As of</label>
          <input v-model="planAsOf" type="date" class="form-input" />
        </div>
        <div>
          <label class="form-label">Delivery date (reverse)</label>
          <input v-model="planDelivery" type="date" class="form-input" />
        </div>
      </div>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="btn-primary" :disabled="!planItem || planBusy" @click="runCapableToPromise">
          Capable to promise
        </button>
        <button type="button" class="btn-secondary" :disabled="!planItem || !planDelivery || planBusy" @click="runReverseSchedule">
          Reverse schedule
        </button>
        <button type="button" class="btn-secondary" :disabled="!planItem || planBusy" @click="runPegging">
          Pegging timeline
        </button>
        <button type="button" class="btn-secondary" :disabled="!planItem || planBusy" @click="runForecast">
          Demand forecast
        </button>
        <button type="button" class="btn-secondary" :disabled="planBusy" @click="loadCapacityBoard">
          Refresh capacity
        </button>
      </div>

      <div class="grid gap-3 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <label class="form-label">What-if extra stock item</label>
          <select v-model="whatIfExtraItem" class="form-input">
            <option value="">(none)</option>
            <option v-for="i in items" :key="i.id" :value="i.id">{{ i.item_code }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Extra qty</label>
          <input v-model.number="whatIfExtraQty" type="number" min="0" step="any" class="form-input" />
        </div>
        <div>
          <label class="form-label">Lead override item</label>
          <select v-model="whatIfLeadItem" class="form-input">
            <option value="">(none)</option>
            <option v-for="i in items" :key="i.id" :value="i.id">{{ i.item_code }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Lead days</label>
          <input v-model.number="whatIfLeadDays" type="number" min="0" step="1" class="form-input" />
        </div>
        <div class="flex items-end">
          <button type="button" class="btn-primary w-full" :disabled="!planItem || planBusy" @click="runWhatIfCtp">
            Run what-if CTP
          </button>
        </div>
      </div>

      <div class="flex flex-wrap gap-3 text-sm text-gray-600">
        <label class="flex items-center gap-2">
          Forecast lookback
          <input v-model.number="forecastLookback" type="number" min="1" max="36" class="form-input w-20" />
        </label>
        <label class="flex items-center gap-2">
          Horizon
          <input v-model.number="forecastHorizon" type="number" min="1" max="12" class="form-input w-20" />
        </label>
      </div>

      <div v-if="ctpResult" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="border-b border-gray-100 px-4 py-3 text-sm">
          <span class="font-medium">{{ ctpResult.item_name ?? ctpResult.item_code }}</span>
          — earliest promise <strong>{{ ctpResult.earliest_promise_date }}</strong>
          (procurement {{ ctpResult.procurement_days }}d + manufacture {{ ctpResult.manufacturing_days }}d)
        </div>
        <ul v-if="ctpResult.notes.length" class="list-disc px-8 py-2 text-xs text-gray-500">
          <li v-for="(n, i) in ctpResult.notes" :key="i">{{ n }}</li>
        </ul>
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Component</th>
              <th class="px-4 py-2 text-right">Required</th>
              <th class="px-4 py-2 text-right">Available</th>
              <th class="px-4 py-2 text-right">Shortfall</th>
              <th class="px-4 py-2 text-right">Lead days</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in ctpResult.components" :key="r.item_id" class="border-t border-gray-100">
              <td class="px-4 py-1.5">{{ r.item_name ?? r.item_code }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.required_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.available_qty) }}</td>
              <td class="px-4 py-1.5 text-right">
                <span v-if="Number(r.shortfall_qty) > 0" class="text-red-600">{{ formatQty(r.shortfall_qty) }}</span>
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="px-4 py-1.5 text-right">{{ r.lead_time_days }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="reverseResult" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="border-b border-gray-100 px-4 py-3 text-sm">
          Deliver by <strong>{{ reverseResult.delivery_date }}</strong> —
          <span :class="reverseResult.on_time ? 'text-green-700' : 'text-red-600'">
            {{ reverseResult.on_time ? `On time (${reverseResult.slack_days}d slack)` : `Late by ${-reverseResult.slack_days}d` }}
          </span>
          · start manufacture {{ reverseResult.manufacturing_start_date }}
          · materials ready {{ reverseResult.materials_ready_by }}
          · earliest promise {{ reverseResult.earliest_promise_date }}
        </div>
        <p class="px-4 pt-2 text-xs font-medium uppercase text-gray-500">Procurement (order by)</p>
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Component</th>
              <th class="px-4 py-2 text-right">Shortfall</th>
              <th class="px-4 py-2 text-right">Lead days</th>
              <th class="px-4 py-2">Latest order date</th>
              <th class="px-4 py-2 text-right">Days until order</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in reverseResult.procurement" :key="r.item_id" class="border-t border-gray-100">
              <td class="px-4 py-1.5">{{ r.item_name ?? r.item_code }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.shortfall_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ r.lead_time_days }}</td>
              <td class="px-4 py-1.5 font-medium">{{ r.latest_order_date }}</td>
              <td class="px-4 py-1.5 text-right" :class="r.days_until_order < 0 ? 'text-red-600' : ''">
                {{ r.days_until_order }}
              </td>
            </tr>
            <tr v-if="!reverseResult.procurement.length">
              <td colspan="5" class="px-4 py-6 text-center text-gray-400">No shortfalls — nothing to order.</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="peggingResult" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="border-b border-gray-100 px-4 py-3 text-sm">
          <span class="font-medium">{{ peggingResult.item_name ?? peggingResult.item_code }}</span>
          — demand {{ formatQty(peggingResult.demand_qty) }}
          / supply {{ formatQty(peggingResult.supply_qty) }}
          / shortfall
          <span :class="Number(peggingResult.net_shortfall) > 0 ? 'font-medium text-red-600' : ''">
            {{ formatQty(peggingResult.net_shortfall) }}
          </span>
          <span v-if="peggingResult.earliest_promise_date">
            · CTP promise {{ peggingResult.earliest_promise_date }}
          </span>
        </div>
        <ul v-if="peggingResult.notes.length" class="list-disc px-8 py-2 text-xs text-gray-500">
          <li v-for="(n, i) in peggingResult.notes" :key="i">{{ n }}</li>
        </ul>
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Side</th>
              <th class="px-4 py-2">Source</th>
              <th class="px-4 py-2">Name</th>
              <th class="px-4 py-2 text-right">Qty</th>
              <th class="px-4 py-2">Date</th>
              <th class="px-4 py-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, idx) in peggingResult.rows" :key="idx" class="border-t border-gray-100">
              <td class="px-4 py-1.5">
                <span :class="r.side === 'demand' ? 'text-amber-700' : 'text-green-700'">{{ r.side }}</span>
              </td>
              <td class="px-4 py-1.5">{{ r.source_type }}</td>
              <td class="px-4 py-1.5 font-medium">{{ r.source_name ?? "—" }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.qty) }}</td>
              <td class="px-4 py-1.5">{{ r.due_date ?? "—" }}</td>
              <td class="px-4 py-1.5 text-xs text-gray-500">{{ r.notes }}</td>
            </tr>
            <tr v-if="!peggingResult.rows.length">
              <td colspan="6" class="px-4 py-6 text-center text-gray-400">No demand or supply rows.</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="forecastResult" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="border-b border-gray-100 px-4 py-3 text-sm">
          <span class="font-medium">{{ forecastResult.item_name ?? forecastResult.item_code }}</span>
          — avg {{ formatQty(forecastResult.average_monthly_demand) }}/mo
          ({{ forecastResult.method }})
        </div>
        <ul v-if="forecastResult.notes.length" class="list-disc px-8 py-2 text-xs text-gray-500">
          <li v-for="(n, i) in forecastResult.notes" :key="i">{{ n }}</li>
        </ul>
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Period</th>
              <th class="px-4 py-2">Type</th>
              <th class="px-4 py-2 text-right">Qty</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in forecastResult.rows" :key="r.period + String(r.is_forecast)" class="border-t border-gray-100">
              <td class="px-4 py-1.5 font-medium">{{ r.period }}</td>
              <td class="px-4 py-1.5">
                <span :class="r.is_forecast ? 'text-indigo-700' : 'text-gray-600'">
                  {{ r.is_forecast ? "forecast" : "actual" }}
                </span>
              </td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.demand_qty) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="capacityBoard" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="border-b border-gray-100 px-4 py-3 text-sm font-medium">
          Capacity board (as of {{ capacityBoard.as_of }})
        </div>
        <ul v-if="capacityBoard.notes.length" class="list-disc px-8 py-2 text-xs text-gray-500">
          <li v-for="(n, i) in capacityBoard.notes" :key="i">{{ n }}</li>
        </ul>
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Workstation</th>
              <th class="px-4 py-2 text-right">Hrs/day</th>
              <th class="px-4 py-2 text-right">Capacity mins</th>
              <th class="px-4 py-2 text-right">Planned mins</th>
              <th class="px-4 py-2 text-right">Open WOs</th>
              <th class="px-4 py-2 text-right">Util %</th>
              <th class="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in capacityBoard.rows" :key="r.workstation_id" class="border-t border-gray-100">
              <td class="px-4 py-1.5 font-medium">{{ r.workstation_name }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.working_hours_per_day) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.capacity_mins_per_day) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.planned_mins) }}</td>
              <td class="px-4 py-1.5 text-right">{{ r.open_work_orders }}</td>
              <td class="px-4 py-1.5 text-right">{{ r.utilization_pct }}%</td>
              <td class="px-4 py-1.5">
                <span v-if="r.overloaded" class="font-medium text-red-600">Overloaded</span>
                <span v-else class="text-green-700">OK</span>
              </td>
            </tr>
            <tr v-if="!capacityBoard.rows.length">
              <td colspan="7" class="px-4 py-6 text-center text-gray-400">No workstations.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Material Shortage (all open Work Orders) -->
    <div v-else-if="tab === 'shortage'">
      <div class="mb-3 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          Still-to-consume component demand across every open Work Order vs on-hand stock.
        </p>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="onlyShort" type="checkbox" @change="loadShortage" /> Only shortfalls
        </label>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Component</th>
              <th class="px-4 py-2">Warehouse</th>
              <th class="px-4 py-2 text-right">Pending</th>
              <th class="px-4 py-2 text-right">Available</th>
              <th class="px-4 py-2 text-right">Shortfall</th>
              <th class="px-4 py-2">Needed by</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in shortage" :key="`${r.item_id}-${r.warehouse_id ?? 'all'}`" class="border-t border-gray-100">
              <td class="px-4 py-1.5">
                <div>{{ r.item_name ?? r.item_code }}</div>
                <div class="text-xs text-gray-400">{{ r.item_code }}</div>
              </td>
              <td class="px-4 py-1.5 text-gray-500">{{ r.warehouse_name ?? "All warehouses" }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.pending_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.available_qty) }}</td>
              <td class="px-4 py-1.5 text-right">
                <span v-if="Number(r.shortfall_qty) > 0" class="font-medium text-red-600">{{ formatQty(r.shortfall_qty) }}</span>
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="px-4 py-1.5 text-xs text-gray-500">{{ r.work_orders.join(", ") }}</td>
            </tr>
            <tr v-if="!shortage.length">
              <td colspan="6" class="px-4 py-8 text-center text-gray-400">
                {{ onlyShort ? "No shortfalls — every open Work Order is fully stocked." : "No open Work Orders." }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- BOM Where-Used -->
    <div v-else-if="tab === 'where-used'">
      <div class="mb-3 max-w-md">
        <label class="form-label">Component item</label>
        <select v-model="whereUsedItem" class="form-input" @change="loadWhereUsed">
          <option value="" disabled>Select an item…</option>
          <option v-for="i in items" :key="i.id" :value="i.id">{{ i.item_code }} — {{ i.item_name }}</option>
        </select>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">BOM</th>
              <th class="px-4 py-2">Finished good</th>
              <th class="px-4 py-2 text-right">Qty / batch</th>
              <th class="px-4 py-2">Default</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in whereUsed" :key="r.bom_id" class="cursor-pointer border-t border-gray-100 hover:bg-gray-50" @click="router.push(`/bom/${r.bom_id}`)">
              <td class="px-4 py-1.5 font-medium text-gray-900">{{ r.bom_name }}</td>
              <td class="px-4 py-1.5">{{ r.production_item_name ?? r.production_item_code }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.qty_per_batch) }}</td>
              <td class="px-4 py-1.5">{{ r.is_default ? "★" : "" }}</td>
            </tr>
            <tr v-if="whereUsedItem && !whereUsed.length"><td colspan="4" class="px-4 py-8 text-center text-gray-400">No BOMs use this item.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- BOM Stock -->
    <div v-else-if="tab === 'bom-stock'">
      <div class="mb-3 flex flex-wrap items-end gap-3">
        <div class="min-w-[18rem]">
          <label class="form-label">BOM</label>
          <select v-model="stockBom" class="form-input" @change="loadBomStock">
            <option value="" disabled>Select a BOM…</option>
            <option v-for="b in boms" :key="b.id" :value="b.id">{{ b.name }} — {{ b.production_item_name }}</option>
          </select>
        </div>
        <div class="w-40">
          <label class="form-label">Finished units</label>
          <input v-model.number="stockQty" type="number" min="0.000001" step="any" class="form-input" @change="loadBomStock" />
        </div>
      </div>
      <div v-if="stockReport" class="mb-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <span class="text-sm text-gray-500">Current stock can build</span>
        <span class="ml-2 text-lg font-semibold text-primary">{{ formatQty(stockReport.buildable_qty) }}</span>
        <span class="text-sm text-gray-500"> finished units.</span>
      </div>
      <div v-if="stockReport" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Component</th>
              <th class="px-4 py-2 text-right">Required</th>
              <th class="px-4 py-2 text-right">Available</th>
              <th class="px-4 py-2 text-right">Shortfall</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in stockReport.rows" :key="r.item_id" class="border-t border-gray-100">
              <td class="px-4 py-1.5">{{ r.item_name ?? r.item_code }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.required_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.available_qty) }}</td>
              <td class="px-4 py-1.5 text-right">
                <span v-if="Number(r.shortfall_qty) > 0" class="font-medium text-red-600">{{ formatQty(r.shortfall_qty) }}</span>
                <span v-else class="text-gray-300">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- BOM Explorer -->
    <div v-else>
      <div class="mb-3 flex flex-wrap items-end gap-3">
        <div class="min-w-[18rem]">
          <label class="form-label">BOM</label>
          <select v-model="explorerBom" class="form-input" @change="loadExplorer">
            <option value="" disabled>Select a BOM…</option>
            <option v-for="b in boms" :key="b.id" :value="b.id">{{ b.name }} — {{ b.production_item_name }}</option>
          </select>
        </div>
        <div class="w-40">
          <label class="form-label">Finished units</label>
          <input v-model.number="explorerQty" type="number" min="0.000001" step="any" class="form-input" @change="loadExplorer" />
        </div>
        <label class="mb-2 flex items-center gap-2 text-sm text-gray-700">
          <input v-model="explorerFlattenAll" type="checkbox" @change="loadExplorer" />
          Flatten all levels (else phantoms only)
        </label>
      </div>
      <div v-if="explorerReport" class="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <div class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
          <div class="text-xs uppercase text-gray-400">Raw materials</div>
          <div class="font-semibold">{{ formatCurrency(explorerReport.raw_material_cost) }}</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
          <div class="text-xs uppercase text-gray-400">Scrap recovery</div>
          <div class="font-semibold">{{ formatCurrency(explorerReport.scrap_cost) }}</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
          <div class="text-xs uppercase text-gray-400">Operating</div>
          <div class="font-semibold">{{ formatCurrency(explorerReport.operating_cost) }}</div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
          <div class="text-xs uppercase text-gray-400">Total</div>
          <div class="font-semibold text-primary">{{ formatCurrency(explorerReport.total_cost) }}</div>
        </div>
      </div>
      <div v-if="explorerReport" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Lvl</th>
              <th class="px-4 py-2">Item</th>
              <th class="px-4 py-2 text-right">Qty</th>
              <th class="px-4 py-2 text-right">Rate</th>
              <th class="px-4 py-2 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in explorerReport.rows" :key="`${r.item_id}-${r.level}`" class="border-t border-gray-100">
              <td class="px-4 py-1.5 text-gray-400">{{ r.level }}</td>
              <td class="px-4 py-1.5" :style="{ paddingLeft: `${1 + r.level}rem` }">
                {{ r.item_name ?? r.item_code }}
                <div class="text-xs text-gray-400">{{ r.item_code }}</div>
              </td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.stock_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatCurrency(r.rate) }}</td>
              <td class="px-4 py-1.5 text-right font-medium">{{ formatCurrency(r.amount) }}</td>
            </tr>
            <tr v-for="r in explorerReport.scrap_rows" :key="`scrap-${r.item_id}`" class="border-t border-amber-50 bg-amber-50/30">
              <td class="px-4 py-1.5 text-amber-600">scrap</td>
              <td class="px-4 py-1.5">{{ r.item_name ?? r.item_code }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatQty(r.stock_qty) }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatCurrency(r.rate) }}</td>
              <td class="px-4 py-1.5 text-right font-medium">{{ formatCurrency(r.amount) }}</td>
            </tr>
            <tr v-if="explorerBom && !explorerReport.rows.length">
              <td colspan="5" class="px-4 py-8 text-center text-gray-400">No components after explosion.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
