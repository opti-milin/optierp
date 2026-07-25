<script setup lang="ts">
// Manufacturing Planning Dashboard — central planner workspace.
// Composes Phase 7 CTP / reverse / pegging / forecast / capacity APIs around a
// Sales Order, Quotation, Production Plan, or Finished Item context.
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatQty } from "@/utils/format";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  CapacityBoard,
  DemandForecast,
  LeadTimeEstimate,
  PeggingTimeline,
  PlanningContext,
  PlanningContextLine,
  ReverseSchedule,
} from "@/types/manufacturing";

type ContextType = "item" | "sales-order" | "quotation" | "production-plan";

interface ItemOpt {
  id: string;
  item_code: string;
  item_name: string;
  is_stock_item?: boolean;
}
interface DocOpt {
  id: string;
  name: string;
}

const route = useRoute();
const router = useRouter();

const error = ref<ErrorEnvelope | null>(null);
const busy = ref(false);
const items = ref<ItemOpt[]>([]);
const salesOrders = ref<DocOpt[]>([]);
const quotations = ref<DocOpt[]>([]);
const productionPlans = ref<DocOpt[]>([]);

const contextType = ref<ContextType>("item");
const documentId = ref("");
const itemId = ref("");
const qty = ref(1);
const asOf = ref(new Date().toISOString().slice(0, 10));
const deliveryDate = ref("");

const ctx = ref<PlanningContext | null>(null);
const selectedLineIdx = ref(0);
const selectedLine = computed<PlanningContextLine | null>(() => {
  const lines = ctx.value?.lines ?? [];
  return lines[selectedLineIdx.value] ?? lines[0] ?? null;
});

const ctp = ref<LeadTimeEstimate | null>(null);
const reverse = ref<ReverseSchedule | null>(null);
const pegging = ref<PeggingTimeline | null>(null);
const forecast = ref<DemandForecast | null>(null);
const capacity = ref<CapacityBoard | null>(null);

const whatIfExtraItem = ref("");
const whatIfExtraQty = ref(0);
const whatIfLeadItem = ref("");
const whatIfLeadDays = ref(0);

async function loadMasters(): Promise<void> {
  const [it, so, qtn, pp] = await Promise.all([
    api.get<ListResponse<ItemOpt>>("/items", { params: { page_size: 200 } }),
    api.get<ListResponse<DocOpt>>("/sales-orders", { params: { page_size: 50 } }),
    api.get<ListResponse<DocOpt>>("/quotations", { params: { page_size: 50 } }),
    api.get<ListResponse<DocOpt>>("/production-plans", { params: { page_size: 50 } }),
  ]);
  items.value = (it.data.items ?? []).filter((i) => i.is_stock_item !== false);
  salesOrders.value = so.data.items ?? [];
  quotations.value = qtn.data.items ?? [];
  productionPlans.value = pp.data.items ?? [];
}

function applyQuery(): void {
  const c = String(route.query.context ?? "item") as ContextType;
  if (["item", "sales-order", "quotation", "production-plan"].includes(c)) {
    contextType.value = c;
  }
  if (typeof route.query.id === "string") documentId.value = route.query.id;
  if (typeof route.query.item_id === "string") itemId.value = route.query.item_id;
  if (typeof route.query.qty === "string") qty.value = Number(route.query.qty) || 1;
  if (typeof route.query.delivery === "string") deliveryDate.value = route.query.delivery;
}

async function resolveContext(): Promise<void> {
  error.value = null;
  busy.value = true;
  ctp.value = null;
  reverse.value = null;
  pegging.value = null;
  forecast.value = null;
  try {
    const params: Record<string, string | number> = { context: contextType.value };
    if (contextType.value === "item") {
      if (!itemId.value) {
        ctx.value = null;
        return;
      }
      params.item_id = itemId.value;
      params.qty = qty.value || 1;
      if (deliveryDate.value) params.delivery_date = deliveryDate.value;
    } else {
      if (!documentId.value) {
        ctx.value = null;
        return;
      }
      params.id = documentId.value;
    }
    ctx.value = (await api.get<PlanningContext>("/manufacturing/planning/context", { params })).data;
    selectedLineIdx.value = 0;
    const line = selectedLine.value;
    if (line) {
      itemId.value = line.item_id;
      qty.value = Number(line.qty) || 1;
      if (line.delivery_date) deliveryDate.value = line.delivery_date;
    }
    await runAllForLine();
  } catch (e) {
    error.value = e as ErrorEnvelope;
    ctx.value = null;
  } finally {
    busy.value = false;
  }
}

async function runAllForLine(): Promise<void> {
  const line = selectedLine.value;
  if (!line) return;
  error.value = null;
  busy.value = true;
  try {
    const base = {
      item_id: line.item_id,
      qty: Number(line.qty) || qty.value || 1,
      as_of: asOf.value || undefined,
      warehouse_id: line.warehouse_id || undefined,
    };
    const delivery = deliveryDate.value || line.delivery_date || undefined;
    const [ctpRes, pegRes, fcRes, capRes, revRes] = await Promise.all([
      api.get<LeadTimeEstimate>("/manufacturing-reports/capable-to-promise", { params: base }),
      api.get<PeggingTimeline>("/manufacturing-reports/pegging", {
        params: { item_id: line.item_id, as_of: asOf.value || undefined, warehouse_id: line.warehouse_id || undefined },
      }),
      api.get<DemandForecast>("/manufacturing-reports/demand-forecast", {
        params: { item_id: line.item_id, lookback_months: 6, horizon_months: 3, as_of: asOf.value || undefined },
      }),
      api.get<CapacityBoard>("/manufacturing-reports/capacity-board", {
        params: { as_of: asOf.value || undefined },
      }),
      delivery
        ? api.get<ReverseSchedule>("/manufacturing-reports/reverse-schedule", {
            params: { ...base, delivery_date: delivery },
          })
        : Promise.resolve({ data: null as ReverseSchedule | null }),
    ]);
    ctp.value = ctpRes.data;
    pegging.value = pegRes.data;
    forecast.value = fcRes.data;
    capacity.value = capRes.data;
    reverse.value = revRes.data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function runWhatIf(): Promise<void> {
  const line = selectedLine.value;
  if (!line) return;
  error.value = null;
  busy.value = true;
  try {
    const body: Record<string, unknown> = {
      item_id: line.item_id,
      qty: Number(line.qty) || qty.value || 1,
      as_of: asOf.value || null,
      warehouse_id: line.warehouse_id || null,
      extra_stock: [],
      lead_time_overrides: [],
    };
    if (whatIfExtraItem.value && whatIfExtraQty.value > 0) {
      (body.extra_stock as object[]).push({
        item_id: whatIfExtraItem.value,
        extra_qty: whatIfExtraQty.value,
      });
    }
    if (whatIfLeadItem.value) {
      (body.lead_time_overrides as object[]).push({
        item_id: whatIfLeadItem.value,
        lead_time_days: whatIfLeadDays.value || 0,
      });
    }
    ctp.value = (await api.post<LeadTimeEstimate>("/manufacturing-reports/what-if-ctp", body)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

function syncQuery(): void {
  const q: Record<string, string> = { context: contextType.value };
  if (contextType.value === "item" && itemId.value) {
    q.item_id = itemId.value;
    q.qty = String(qty.value || 1);
  } else if (documentId.value) {
    q.id = documentId.value;
  }
  if (deliveryDate.value) q.delivery = deliveryDate.value;
  void router.replace({ query: q });
}

watch(selectedLineIdx, () => {
  void runAllForLine();
});

onMounted(async () => {
  applyQuery();
  await loadMasters();
  if (
    (contextType.value === "item" && itemId.value) ||
    (contextType.value !== "item" && documentId.value)
  ) {
    await resolveContext();
  } else {
    capacity.value = (
      await api.get<CapacityBoard>("/manufacturing-reports/capacity-board", {
        params: { as_of: asOf.value || undefined },
      })
    ).data;
  }
});
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-4">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Manufacturing Planning Dashboard</h1>
        <p class="text-sm text-gray-500">
          Demand, supply, capacity, timeline, and CTP decision support for day-to-day planning.
        </p>
      </div>
      <RouterLink to="/manufacturing-reports" class="text-sm text-primary hover:underline">
        Classic reports →
      </RouterLink>
    </div>

    <p v-if="error" class="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <div class="grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-5">
      <div>
        <label class="form-label">Context</label>
        <select v-model="contextType" class="form-input" @change="documentId = ''; itemId = ''">
          <option value="item">Finished Item</option>
          <option value="sales-order">Sales Order</option>
          <option value="quotation">Quotation</option>
          <option value="production-plan">Production Plan</option>
        </select>
      </div>
      <div v-if="contextType === 'item'">
        <label class="form-label">Finished item</label>
        <select v-model="itemId" class="form-input">
          <option value="" disabled>Select…</option>
          <option v-for="i in items" :key="i.id" :value="i.id">{{ i.item_code }} — {{ i.item_name }}</option>
        </select>
      </div>
      <div v-else-if="contextType === 'sales-order'">
        <label class="form-label">Sales Order</label>
        <select v-model="documentId" class="form-input">
          <option value="" disabled>Select…</option>
          <option v-for="d in salesOrders" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </div>
      <div v-else-if="contextType === 'quotation'">
        <label class="form-label">Quotation</label>
        <select v-model="documentId" class="form-input">
          <option value="" disabled>Select…</option>
          <option v-for="d in quotations" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </div>
      <div v-else>
        <label class="form-label">Production Plan</label>
        <select v-model="documentId" class="form-input">
          <option value="" disabled>Select…</option>
          <option v-for="d in productionPlans" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </div>
      <div v-if="contextType === 'item'">
        <label class="form-label">Qty</label>
        <input v-model.number="qty" type="number" min="0.000001" step="any" class="form-input" />
      </div>
      <div>
        <label class="form-label">As of</label>
        <input v-model="asOf" type="date" class="form-input" />
      </div>
      <div>
        <label class="form-label">Delivery date</label>
        <input v-model="deliveryDate" type="date" class="form-input" />
      </div>
    </div>

    <div class="flex flex-wrap gap-2">
      <button type="button" class="btn-primary" :disabled="busy" @click="syncQuery(); resolveContext()">
        Load context
      </button>
      <button type="button" class="btn-secondary" :disabled="busy || !selectedLine" @click="runAllForLine">
        Refresh panels
      </button>
    </div>

    <div v-if="ctx?.lines?.length" class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
      <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
        Lines — {{ ctx.document_name || contextType }}
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="(ln, idx) in ctx.lines"
          :key="ln.item_id + idx"
          type="button"
          class="rounded-md border px-3 py-1.5 text-sm"
          :class="selectedLineIdx === idx ? 'border-primary bg-primary/5 text-primary' : 'border-gray-200'"
          @click="selectedLineIdx = idx"
        >
          {{ ln.item_code || ln.item_name }} × {{ formatQty(ln.qty) }}
        </button>
      </div>
    </div>

    <div v-if="selectedLine" class="grid gap-4 lg:grid-cols-2">
      <!-- Demand -->
      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Demand</h2>
        <p class="mb-2 text-sm text-gray-600">
          Open SO demand
          <strong>{{ pegging ? formatQty(pegging.demand_qty) : "—" }}</strong>
          · Net shortfall
          <strong :class="pegging && Number(pegging.net_shortfall) > 0 ? 'text-red-600' : ''">
            {{ pegging ? formatQty(pegging.net_shortfall) : "—" }}
          </strong>
        </p>
        <div v-if="forecast" class="overflow-x-auto">
          <table class="min-w-full text-sm">
            <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
              <tr>
                <th class="px-3 py-1.5">Period</th>
                <th class="px-3 py-1.5 text-right">Qty</th>
                <th class="px-3 py-1.5">Type</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in forecast.rows" :key="r.period" class="border-t border-gray-100">
                <td class="px-3 py-1">{{ r.period }}</td>
                <td class="px-3 py-1 text-right">{{ formatQty(r.demand_qty) }}</td>
                <td class="px-3 py-1">{{ r.is_forecast ? "Forecast" : "History" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Supply -->
      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Supply</h2>
        <p class="mb-2 text-sm text-gray-600">
          Supply qty <strong>{{ pegging ? formatQty(pegging.supply_qty) : "—" }}</strong>
        </p>
        <table v-if="pegging" class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-3 py-1.5">Side</th>
              <th class="px-3 py-1.5">Source</th>
              <th class="px-3 py-1.5 text-right">Qty</th>
              <th class="px-3 py-1.5">Due</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in pegging.rows" :key="i" class="border-t border-gray-100">
              <td class="px-3 py-1">{{ r.side }}</td>
              <td class="px-3 py-1">{{ r.source_type }} {{ r.source_name || "" }}</td>
              <td class="px-3 py-1 text-right">{{ formatQty(r.qty) }}</td>
              <td class="px-3 py-1">{{ r.due_date ?? "—" }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Capacity -->
      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Capacity (soft)</h2>
        <table v-if="capacity" class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-3 py-1.5">Workstation</th>
              <th class="px-3 py-1.5 text-right">Util %</th>
              <th class="px-3 py-1.5">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in capacity.rows" :key="r.workstation_id" class="border-t border-gray-100">
              <td class="px-3 py-1">{{ r.workstation_name }}</td>
              <td class="px-3 py-1 text-right">{{ r.utilization_pct }}</td>
              <td class="px-3 py-1">
                <span :class="r.overloaded ? 'text-red-600' : 'text-green-700'">
                  {{ r.overloaded ? "Overloaded" : "OK" }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-sm text-gray-500">Load context to refresh capacity.</p>
      </section>

      <!-- Timeline -->
      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Timeline</h2>
        <div v-if="reverse" class="space-y-1 text-sm text-gray-700">
          <p>Delivery <strong>{{ reverse.delivery_date }}</strong>
            — {{ reverse.on_time ? "on time" : "late" }}
            (slack {{ reverse.slack_days }}d)</p>
          <p>Materials ready by <strong>{{ reverse.materials_ready_by }}</strong></p>
          <p>Manufacture start <strong>{{ reverse.manufacturing_start_date }}</strong></p>
          <p>Earliest promise <strong>{{ reverse.earliest_promise_date }}</strong></p>
        </div>
        <div v-else-if="ctp" class="text-sm text-gray-700">
          Earliest promise <strong>{{ ctp.earliest_promise_date }}</strong>
          ({{ ctp.procurement_days }}d procurement + {{ ctp.manufacturing_days }}d manufacture)
        </div>
        <p v-else class="text-sm text-gray-500">Set a delivery date for reverse schedule.</p>
      </section>
    </div>

    <!-- Decision support -->
    <section v-if="selectedLine" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Decision support</h2>
      <div v-if="ctp" class="mb-3 text-sm">
        <p>
          CTP for <strong>{{ ctp.item_name ?? ctp.item_code }}</strong>:
          promise <strong>{{ ctp.earliest_promise_date }}</strong>
        </p>
        <ul v-if="ctp.notes.length" class="mt-1 list-disc px-5 text-xs text-gray-500">
          <li v-for="(n, i) in ctp.notes" :key="i">{{ n }}</li>
        </ul>
        <table class="mt-2 min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-3 py-1.5">Component</th>
              <th class="px-3 py-1.5 text-right">Required</th>
              <th class="px-3 py-1.5 text-right">Available</th>
              <th class="px-3 py-1.5 text-right">Shortfall</th>
              <th class="px-3 py-1.5 text-right">Lead days</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in ctp.components" :key="r.item_id" class="border-t border-gray-100">
              <td class="px-3 py-1">{{ r.item_name ?? r.item_code }}</td>
              <td class="px-3 py-1 text-right">{{ formatQty(r.required_qty) }}</td>
              <td class="px-3 py-1 text-right">{{ formatQty(r.available_qty) }}</td>
              <td class="px-3 py-1 text-right">
                <span v-if="Number(r.shortfall_qty) > 0" class="text-red-600">{{ formatQty(r.shortfall_qty) }}</span>
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="px-3 py-1 text-right">{{ r.lead_time_days }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="mb-3 flex flex-wrap gap-2 text-sm">
        <RouterLink
          v-if="contextType === 'sales-order' && documentId"
          :to="`/production-plans`"
          class="btn-secondary"
        >
          Open Production Plans
        </RouterLink>
        <RouterLink to="/work-orders" class="btn-secondary">Open Work Orders</RouterLink>
        <button
          v-if="ctp?.earliest_promise_date"
          type="button"
          class="btn-secondary"
          @click="deliveryDate = ctp.earliest_promise_date; syncQuery()"
        >
          Suggest delivery {{ ctp.earliest_promise_date }}
        </button>
      </div>

      <div class="grid gap-3 rounded-md border border-dashed border-gray-300 bg-gray-50 p-3 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <label class="form-label">What-if extra stock</label>
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
          <button type="button" class="btn-primary w-full" :disabled="busy" @click="runWhatIf">
            Run what-if CTP
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
