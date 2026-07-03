<script setup lang="ts">
// Asset detail: header + book-value summary + the depreciation schedule, plus Phase-2
// actions — Submit, Depreciate now (posts due rows as JEs), Move (location/custodian),
// and Dispose (sell/scrap → gain/loss JE, halts depreciation).
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatCurrency, formatDate } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";

const props = defineProps<{ id: string }>();

interface ScheduleRow {
  id: string;
  idx: number;
  schedule_date: string;
  depreciation_amount: string;
  accumulated_depreciation: string;
  posted: boolean;
  posted_date: string | null;
  journal_entry_id: string | null;
}
interface MovementRow {
  id: string;
  movement_date: string;
  from_location_name: string | null;
  to_location_name: string | null;
  from_custodian: string | null;
  to_custodian: string | null;
}
interface Asset {
  id: string;
  name: string;
  asset_name: string;
  category_name: string | null;
  location_id: string | null;
  location_name: string | null;
  depreciation_method: string | null;
  custodian: string | null;
  gross_purchase_amount: string;
  opening_accumulated_depreciation: string;
  accumulated_depreciation: string;
  book_value: string;
  purchase_date: string | null;
  available_for_use_date: string;
  status: string;
  remarks: string | null;
  disposal_date: string | null;
  disposal_type: string | null;
  disposal_amount: string;
  gain_loss_amount: string | null;
  disposal_journal_entry_id: string | null;
  schedule: ScheduleRow[];
  movements: MovementRow[];
}
interface AccountOpt { value: string; label: string }
interface LocationOpt { id: string; location_name: string }

const asset = ref<Asset | null>(null);
const loading = ref(false);
const busy = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

const accounts = ref<AccountOpt[]>([]);
const locations = ref<LocationOpt[]>([]);
const showAllRows = ref(false);
const visibleSchedule = computed(() =>
  showAllRows.value ? asset.value?.schedule ?? [] : (asset.value?.schedule ?? []).slice(0, 12),
);

const active = computed(() => asset.value && ["Submitted", "Partially Depreciated"].includes(asset.value.status));
const disposed = computed(() => asset.value && ["Sold", "Scrapped"].includes(asset.value.status));

// dispose form
const showDispose = ref(false);
const today = new Date().toISOString().slice(0, 10);
const dType = ref<"Sell" | "Scrap">("Sell");
const dDate = ref(today);
const dAmount = ref<number | null>(null);
const dProceeds = ref("");
const dGainLoss = ref("");

// move form
const showMove = ref(false);
const mDate = ref(today);
const mLocation = ref("");
const mCustodian = ref("");

// revalue form
const showRevalue = ref(false);
const rDate = ref(today);
const rNewValue = ref<number | null>(null);
const rDiffAccount = ref("");

function openForm(which: "dispose" | "move" | "revalue"): void {
  showDispose.value = which === "dispose" ? !showDispose.value : false;
  showMove.value = which === "move" ? !showMove.value : false;
  showRevalue.value = which === "revalue" ? !showRevalue.value : false;
}

async function fetchAsset(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    asset.value = (await api.get<Asset>(`/assets/${props.id}`)).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function fetchPickers(): Promise<void> {
  try {
    const [acc, loc] = await Promise.all([
      api.get<AccountOpt[]>("/registry/account/options"),
      api.get<ListResponse<LocationOpt>>("/registry/location", { params: { page_size: 200 } }),
    ]);
    accounts.value = acc.data;
    locations.value = loc.data.items;
    // sensible default for the gain/loss account
    const gl = accounts.value.find((a) => /gain.*loss.*disposal/i.test(a.label));
    if (gl) dGainLoss.value = gl.value;
  } catch {
    /* pickers are best-effort */
  }
}

async function act(action: "submit" | "cancel"): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/assets/${props.id}/${action}`);
    await fetchAsset();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function depreciate(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (await api.post<{ posted_count: number }>(`/assets/${props.id}/depreciate`)).data;
    notice.value =
      res.posted_count > 0
        ? `Posted ${res.posted_count} depreciation entr${res.posted_count === 1 ? "y" : "ies"}.`
        : "Nothing due to depreciate right now.";
    await fetchAsset();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function cancelDepreciation(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/assets/${props.id}/cancel-depreciation`);
    notice.value = "Posted depreciation reversed.";
    await fetchAsset();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function dispose(): Promise<void> {
  if (!dGainLoss.value) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/assets/${props.id}/dispose`, {
      disposal_type: dType.value,
      disposal_date: dDate.value,
      sale_amount: dType.value === "Sell" ? dAmount.value || 0 : 0,
      proceeds_account_id: dType.value === "Sell" ? dProceeds.value || null : null,
      gain_loss_account_id: dGainLoss.value,
    });
    showDispose.value = false;
    notice.value = `Asset ${dType.value === "Sell" ? "sold" : "scrapped"}.`;
    await fetchAsset();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function adjustValue(): Promise<void> {
  if (!rNewValue.value || !rDiffAccount.value) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/assets/${props.id}/adjust-value`, {
      adjustment_date: rDate.value,
      new_asset_value: rNewValue.value,
      difference_account_id: rDiffAccount.value,
    });
    showRevalue.value = false;
    rNewValue.value = null;
    notice.value = "Asset revalued.";
    await fetchAsset();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function move(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post(`/assets/${props.id}/move`, {
      movement_date: mDate.value,
      to_location_id: mLocation.value || null,
      to_custodian: mCustodian.value || null,
    });
    showMove.value = false;
    mLocation.value = "";
    mCustodian.value = "";
    notice.value = "Asset moved.";
    await fetchAsset();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  await Promise.all([fetchAsset(), fetchPickers()]);
});
</script>

<template>
  <div v-if="asset">
    <div class="mb-4 flex items-start justify-between">
      <div>
        <RouterLink to="/assets" class="text-sm text-blue-600 hover:underline">← Assets</RouterLink>
        <h1 class="mt-1 text-xl font-semibold text-gray-900">
          {{ asset.asset_name }}
          <span class="ml-2 align-middle"><StatusBadge :status="asset.status" /></span>
        </h1>
        <p class="text-sm text-gray-400">{{ asset.name }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button v-if="asset.status === 'Draft'" class="btn-primary" :disabled="busy" @click="act('submit')">
          Submit
        </button>
        <button
          v-if="asset.status === 'Submitted' || asset.status === 'Partially Depreciated'"
          class="btn-primary"
          :disabled="busy"
          @click="depreciate"
        >
          Depreciate now
        </button>
        <button v-if="active" class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50" :disabled="busy" @click="openForm('move')">
          Move
        </button>
        <button v-if="active" class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50" :disabled="busy" @click="openForm('revalue')">
          Revalue
        </button>
        <button
          v-if="asset.status === 'Partially Depreciated' || asset.status === 'Fully Depreciated'"
          class="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
          :disabled="busy"
          @click="cancelDepreciation"
        >
          Cancel depreciation
        </button>
        <button v-if="active" class="rounded-md border border-amber-300 px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-50" :disabled="busy" @click="openForm('dispose')">
          Dispose
        </button>
        <button
          v-if="asset.status === 'Submitted'"
          class="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
          :disabled="busy"
          @click="act('cancel')"
        >
          Cancel
        </button>
      </div>
    </div>

    <p v-if="notice" class="mb-3 rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <!-- dispose form -->
    <form v-if="showDispose" class="mb-6 rounded-lg border border-amber-200 bg-amber-50/40 p-5 shadow-sm" @submit.prevent="dispose">
      <h2 class="mb-3 text-sm font-semibold text-amber-800">Dispose asset</h2>
      <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div>
          <label class="form-label">Type</label>
          <select v-model="dType" class="form-input">
            <option value="Sell">Sell</option>
            <option value="Scrap">Scrap</option>
          </select>
        </div>
        <div>
          <label class="form-label">Date</label>
          <input v-model="dDate" type="date" class="form-input" />
        </div>
        <div v-if="dType === 'Sell'">
          <label class="form-label">Sale amount</label>
          <input v-model.number="dAmount" type="number" min="0" step="any" class="form-input" />
        </div>
        <div v-if="dType === 'Sell'">
          <label class="form-label">Proceeds account (bank/cash)</label>
          <select v-model="dProceeds" class="form-input">
            <option value="">Select…</option>
            <option v-for="a in accounts" :key="a.value" :value="a.value">{{ a.label }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Gain/Loss account</label>
          <select v-model="dGainLoss" class="form-input">
            <option value="">Select…</option>
            <option v-for="a in accounts" :key="a.value" :value="a.value">{{ a.label }}</option>
          </select>
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Books a Journal Entry removing the cost ({{ formatCurrency(asset.gross_purchase_amount) }}) and
        accumulated depreciation ({{ formatCurrency(asset.accumulated_depreciation) }}), with the gain/loss
        vs the current book value ({{ formatCurrency(asset.book_value) }}). Depreciation then stops.
      </p>
      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy || !dGainLoss">Confirm disposal</button>
      </div>
    </form>

    <!-- revalue form -->
    <form v-if="showRevalue" class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="adjustValue">
      <h2 class="mb-3 text-sm font-semibold text-gray-700">Revalue asset</h2>
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">Date</label>
          <input v-model="rDate" type="date" class="form-input" />
        </div>
        <div>
          <label class="form-label">New book value</label>
          <input v-model.number="rNewValue" type="number" min="0" step="any" class="form-input" :placeholder="asset.book_value" />
        </div>
        <div>
          <label class="form-label">Difference account (impairment / surplus)</label>
          <select v-model="rDiffAccount" class="form-input">
            <option value="">Select…</option>
            <option v-for="a in accounts" :key="a.value" :value="a.value">{{ a.label }}</option>
          </select>
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Current book value is {{ formatCurrency(asset.book_value) }}. A lower new value books an impairment
        (Dr difference / Cr Accumulated Depreciation); a higher value books a write-up. The remaining
        depreciation is rescheduled to the new value.
      </p>
      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy || !rNewValue || !rDiffAccount">Revalue</button>
      </div>
    </form>

    <!-- move form -->
    <form v-if="showMove" class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="move">
      <h2 class="mb-3 text-sm font-semibold text-gray-700">Move asset</h2>
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">Date</label>
          <input v-model="mDate" type="date" class="form-input" />
        </div>
        <div>
          <label class="form-label">New location</label>
          <select v-model="mLocation" class="form-input">
            <option value="">—</option>
            <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.location_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">New custodian</label>
          <input v-model="mCustodian" type="text" class="form-input" placeholder="who holds it now" />
        </div>
      </div>
      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy">Record move</button>
      </div>
    </form>

    <!-- summary cards -->
    <div class="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Cost</div>
        <div class="text-lg font-semibold text-gray-900">{{ formatCurrency(asset.gross_purchase_amount) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Accumulated Depreciation</div>
        <div class="text-lg font-semibold text-gray-900">{{ formatCurrency(asset.accumulated_depreciation) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Book Value</div>
        <div class="text-lg font-semibold text-primary">{{ formatCurrency(asset.book_value) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Method</div>
        <div class="text-lg font-semibold text-gray-900">{{ asset.depreciation_method ?? "—" }}</div>
      </div>
    </div>

    <!-- disposal summary -->
    <div v-if="disposed" class="mb-6 rounded-lg border border-amber-200 bg-amber-50/50 p-5 text-sm shadow-sm">
      <div class="mb-1 font-semibold text-amber-800">{{ asset.disposal_type }} on {{ formatDate(asset.disposal_date) }}</div>
      <div class="grid grid-cols-2 gap-x-8 gap-y-1 md:grid-cols-3">
        <div><span class="text-gray-400">Proceeds:</span> {{ formatCurrency(asset.disposal_amount) }}</div>
        <div>
          <span class="text-gray-400">{{ Number(asset.gain_loss_amount) >= 0 ? "Gain" : "Loss" }}:</span>
          <span :class="Number(asset.gain_loss_amount) >= 0 ? 'text-green-700' : 'text-red-600'">
            {{ formatCurrency(Math.abs(Number(asset.gain_loss_amount ?? 0))) }}
          </span>
        </div>
      </div>
    </div>

    <!-- meta -->
    <div class="mb-6 grid grid-cols-2 gap-x-8 gap-y-2 rounded-lg border border-gray-200 bg-white p-5 text-sm shadow-sm md:grid-cols-3">
      <div><span class="text-gray-400">Category:</span> {{ asset.category_name ?? "—" }}</div>
      <div><span class="text-gray-400">Location:</span> {{ asset.location_name ?? "—" }}</div>
      <div><span class="text-gray-400">Custodian:</span> {{ asset.custodian ?? "—" }}</div>
      <div><span class="text-gray-400">Purchase date:</span> {{ formatDate(asset.purchase_date) }}</div>
      <div><span class="text-gray-400">Available for use:</span> {{ formatDate(asset.available_for_use_date) }}</div>
      <div><span class="text-gray-400">Opening accum. dep.:</span> {{ formatCurrency(asset.opening_accumulated_depreciation) }}</div>
    </div>

    <!-- movement history -->
    <template v-if="asset.movements.length">
      <h2 class="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">Movement History</h2>
      <div class="mb-6 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Date</th><th class="px-4 py-2">From → To (location)</th>
              <th class="px-4 py-2">From → To (custodian)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="mv in asset.movements" :key="mv.id" class="border-t border-gray-100">
              <td class="px-4 py-1.5">{{ formatDate(mv.movement_date) }}</td>
              <td class="px-4 py-1.5 text-gray-500">{{ mv.from_location_name ?? "—" }} → {{ mv.to_location_name ?? "—" }}</td>
              <td class="px-4 py-1.5 text-gray-500">{{ mv.from_custodian ?? "—" }} → {{ mv.to_custodian ?? "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- schedule -->
    <h2 class="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">Depreciation Schedule</h2>
    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">#</th>
            <th class="px-4 py-2">Date</th>
            <th class="px-4 py-2 text-right">Depreciation</th>
            <th class="px-4 py-2 text-right">Accumulated</th>
            <th class="px-4 py-2">Posted</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in visibleSchedule" :key="row.id" class="border-t border-gray-100" :class="{ 'bg-green-50/40': row.posted }">
            <td class="px-4 py-1.5 text-gray-400">{{ row.idx }}</td>
            <td class="px-4 py-1.5">{{ formatDate(row.schedule_date) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(row.depreciation_amount) }}</td>
            <td class="px-4 py-1.5 text-right text-gray-500">{{ formatCurrency(row.accumulated_depreciation) }}</td>
            <td class="px-4 py-1.5">
              <span v-if="row.posted" class="text-xs font-medium text-green-700">
                ✓ {{ formatDate(row.posted_date) }}
              </span>
              <span v-else class="text-xs text-gray-400">pending</span>
            </td>
          </tr>
          <tr v-if="!asset.schedule.length">
            <td colspan="5" class="px-4 py-8 text-center text-gray-400">No schedule rows.</td>
          </tr>
          <tr v-else-if="asset.schedule.length > 12" class="border-t border-gray-100">
            <td colspan="5" class="px-4 py-2 text-center">
              <button class="text-sm text-blue-600 hover:underline" @click="showAllRows = !showAllRows">
                {{ showAllRows ? "Show less" : `Show all ${asset.schedule.length} rows` }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <div v-else-if="loading" class="py-12 text-center text-gray-400">Loading…</div>
  <div v-else class="py-12 text-center text-gray-400">Asset not found.</div>
</template>
