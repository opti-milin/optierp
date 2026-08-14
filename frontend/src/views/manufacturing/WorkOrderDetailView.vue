<script setup lang="ts">
// Work Order detail: required items, produced progress, material availability, and the
// Finish action (posts a Manufacture Stock Entry). Also submit / cancel / stop / resume and
// a one-click Material Request for shortfalls.
//
// Alternates: pick a substitute on the materials row (stock-aware). Finish reuses that choice.
import { computed, onMounted, ref, watch } from "vue";
import { api } from "@/api/client";
import { formatCurrency, formatQty, formatDate } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope } from "@/types/core";
import type { MaterialAvailability, WorkOrderDetail, WorkOrderItemRow } from "@/types/manufacturing";
import type { StockBalanceRow } from "@/types/stock";

const props = defineProps<{ id: string }>();

const wo = ref<WorkOrderDetail | null>(null);
const avail = ref<MaterialAvailability | null>(null);
const busy = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

const showFinish = ref(false);
const showTransfer = ref(false);
const showConsume = ref(false);
const today = new Date().toISOString().slice(0, 10);
const finQty = ref<number | null>(null);
const finDate = ref(today);
const finAccount = ref("");
const finFgSerials = ref("");
const finFgBatch = ref("");
const finConsumed = ref<Record<string, { serials: string; batch: string; substitute: string }>>({});
const finBatchOptions = ref<Record<string, { value: string; label: string }[]>>({});
const xferQty = ref<number | null>(null);
const xferDate = ref(today);
const consumeQty = ref<number | null>(null);
const consumeDate = ref(today);

/** Planned item_id → substitute item_id (empty = use planned). Chosen on the materials table. */
const substitutes = ref<Record<string, string>>({});
const altFilter = ref<Record<string, string>>({});
/** Planned item_id → allowed alternatives from Item Alternative master. */
const alternativesByItem = ref<
  Record<string, Array<{ id: string; item_code: string; item_name: string }>>
>({});
const stockByItem = ref<Record<string, number>>({});
const altItemsLoading = ref(false);

const remaining = computed(() =>
  wo.value ? Number(wo.value.qty) - Number(wo.value.produced_qty) : 0,
);
const remainingTransfer = computed(() =>
  wo.value ? Number(wo.value.qty) - Number(wo.value.material_transferred_qty) : 0,
);
const needsWipTransfer = computed(
  () => !!wo.value && !wo.value.skip_transfer && !!wo.value.wip_warehouse_id,
);
const needsTracking = computed(() => {
  if (!wo.value) return false;
  if (wo.value.production_has_serial_no || wo.value.production_has_batch_no) return true;
  return wo.value.items.some((i) => i.has_serial_no || i.has_batch_no);
});
const hasAlternates = computed(
  () => !!wo.value && wo.value.items.some((i) => i.allow_alternative_item),
);
const canFinish = computed(
  () =>
    !!wo.value
    && wo.value.docstatus === 1
    && ["Not Started", "In Process"].includes(wo.value.status)
    && remaining.value > 0,
);
const altShortfalls = computed(() => {
  if (!wo.value || !avail.value) return [];
  return wo.value.items.filter((row) => {
    if (!row.allow_alternative_item) return false;
    const short = Number(avail.value?.rows.find((a) => a.item_id === row.item_id)?.shortfall_qty ?? 0);
    return short > 0;
  });
});

function availFor(itemId: string): { available: number; shortfall: number } {
  const row = avail.value?.rows.find((a) => a.item_id === itemId);
  return {
    available: Number(row?.available_qty ?? 0),
    shortfall: Number(row?.shortfall_qty ?? 0),
  };
}

function substituteChoices(planned: WorkOrderItemRow): Array<{
  id: string;
  item_code: string;
  item_name: string;
  stock: number;
}> {
  const q = (altFilter.value[planned.item_id] || "").trim().toLowerCase();
  const rows = (alternativesByItem.value[planned.item_id] ?? [])
    .map((o) => ({
      ...o,
      stock: stockByItem.value[o.id] ?? 0,
    }))
    .filter((o) => {
      if (!q) return true;
      return (
        o.item_code.toLowerCase().includes(q)
        || o.item_name.toLowerCase().includes(q)
      );
    })
    .sort((a, b) => {
      if (a.stock > 0 && b.stock <= 0) return -1;
      if (b.stock > 0 && a.stock <= 0) return 1;
      return a.item_code.localeCompare(b.item_code);
    });
  return rows;
}

function substituteLabel(itemId: string): string {
  for (const list of Object.values(alternativesByItem.value)) {
    const hit = list.find((o) => o.id === itemId);
    if (hit) {
      const stock = stockByItem.value[itemId];
      const stockBit = stock != null ? ` · ${formatQty(String(stock))} in stock` : "";
      return `${hit.item_code}${stockBit}`;
    }
  }
  return "substitute";
}

async function loadAlternateCatalog(): Promise<void> {
  if (!wo.value || !hasAlternates.value) return;
  altItemsLoading.value = true;
  try {
    const map: Record<string, Array<{ id: string; item_code: string; item_name: string }>> = {};
    await Promise.all(
      wo.value.items
        .filter((i) => i.allow_alternative_item)
        .map(async (row) => {
          const { data } = await api.get<
            Array<{ id: string; item_code: string; item_name: string }>
          >(`/items/${row.item_id}/alternatives`);
          map[row.item_id] = data ?? [];
        }),
    );
    alternativesByItem.value = map;

    const wh = wo.value.source_warehouse_id;
    if (wh) {
      const bal = (
        await api.get<StockBalanceRow[]>("/reports/stock-balance", {
          params: { warehouse_id: wh },
        })
      ).data;
      const stock: Record<string, number> = {};
      for (const r of bal) {
        stock[r.item_id] = (stock[r.item_id] ?? 0) + Number(r.actual_qty);
      }
      stockByItem.value = stock;
    }
  } catch {
    // non-fatal
  } finally {
    altItemsLoading.value = false;
  }
}

async function loadBatchOptions(itemId: string): Promise<void> {
  if (finBatchOptions.value[itemId]) return;
  try {
    const { data } = await api.get<{ items: Array<{ batch_no: string; expiry_date: string | null }> }>(
      "/registry/batch",
      { params: { item_id: itemId, page_size: 200 } },
    );
    finBatchOptions.value = {
      ...finBatchOptions.value,
      [itemId]: (data.items ?? []).map((b) => ({
        value: b.batch_no,
        label: b.expiry_date ? `${b.batch_no} (exp ${b.expiry_date})` : b.batch_no,
      })),
    };
  } catch {
    finBatchOptions.value = { ...finBatchOptions.value, [itemId]: [] };
  }
}

function initFinishTracking(): void {
  if (!wo.value) return;
  const map: Record<string, { serials: string; batch: string; substitute: string }> = {};
  for (const row of wo.value.items) {
    if (row.has_serial_no || row.has_batch_no || row.allow_alternative_item) {
      map[row.item_id] = {
        serials: "",
        batch: "",
        substitute: substitutes.value[row.item_id] || "",
      };
      if (row.has_batch_no) void loadBatchOptions(row.item_id);
    }
  }
  finConsumed.value = map;
  finFgSerials.value = "";
  finFgBatch.value = "";
  if (wo.value.production_has_batch_no) void loadBatchOptions(wo.value.production_item_id);
}

function openFinish(): void {
  showFinish.value = !showFinish.value;
  showTransfer.value = false;
  showConsume.value = false;
  if (showFinish.value) {
    // Prefer finishing what stock allows unless a substitute covers the shortfall
    const can = avail.value ? Number(avail.value.can_finish_qty) : remaining.value;
    const usingAlt = Object.values(substitutes.value).some(Boolean);
    finQty.value = usingAlt ? remaining.value : Math.min(remaining.value, can || remaining.value);
    initFinishTracking();
  }
}

function setSubstitute(plannedId: string, value: string): void {
  substitutes.value = { ...substitutes.value, [plannedId]: value };
  if (finConsumed.value[plannedId]) {
    finConsumed.value[plannedId].substitute = value;
  }
}

async function fetchWo(): Promise<void> {
  wo.value = (await api.get<WorkOrderDetail>(`/work-orders/${props.id}`)).data;
  finQty.value = Number(wo.value.qty) - Number(wo.value.produced_qty);
  finAccount.value = wo.value.operating_cost_account_id ?? "";
  xferQty.value = Number(wo.value.qty) - Number(wo.value.material_transferred_qty);
}

async function fetchAvailability(): Promise<void> {
  try {
    avail.value = (
      await api.get<MaterialAvailability>(`/work-orders/${props.id}/material-availability`)
    ).data;
  } catch {
    avail.value = null;
  }
}

async function refresh(): Promise<void> {
  await Promise.all([fetchWo(), fetchAvailability()]);
  if (hasAlternates.value) await loadAlternateCatalog();
}

async function act(action: "submit" | "cancel" | "stop" | "resume"): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = await api.post<WorkOrderDetail>(`/work-orders/${props.id}/${action}`);
    await refresh();
    const past: Record<string, string> = {
      submit: "submitted", cancel: "cancelled", stop: "stopped", resume: "resumed",
    };
    let msg = `Work Order ${past[action]}.`;
    if (action === "submit" && res.data.warnings?.length) {
      msg += ` Warnings: ${res.data.warnings.join(" · ")}`;
    }
    notice.value = msg;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function finish(): Promise<void> {
  if (!finQty.value || finQty.value <= 0) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    // Merge table-level substitutes into finish payload
    const consumedMap = { ...finConsumed.value };
    for (const row of wo.value?.items ?? []) {
      if (!row.allow_alternative_item) continue;
      const sub = substitutes.value[row.item_id] || "";
      if (!consumedMap[row.item_id]) {
        consumedMap[row.item_id] = { serials: "", batch: "", substitute: sub };
      } else {
        consumedMap[row.item_id] = { ...consumedMap[row.item_id], substitute: sub };
      }
    }
    const consumed = Object.entries(consumedMap).map(([item_id, t]) => ({
      item_id,
      substitute_item_id: t.substitute || null,
      serial_nos: t.serials
        ? t.serials.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean)
        : null,
      batch_no: t.batch || null,
    }));
    const res = (
      await api.post<{ stock_entry_no: string; produced_qty: string; status: string }>(
        `/work-orders/${props.id}/finish`,
        {
          qty: finQty.value,
          posting_date: finDate.value,
          operating_cost_account_id: finAccount.value || null,
          consumed,
          finished_serial_nos: finFgSerials.value
            ? finFgSerials.value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean)
            : null,
          finished_batch_no: finFgBatch.value || null,
        },
      )
    ).data;
    notice.value = `Manufactured ${finQty.value} — posted ${res.stock_entry_no}.`;
    showFinish.value = false;
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function raiseMaterialRequest(): Promise<void> {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const mr = (await api.post<{ name: string }>(`/work-orders/${props.id}/material-request`)).data;
    notice.value = `Raised Material Request ${mr.name} for the shortfall.`;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function transfer(): Promise<void> {
  if (!xferQty.value || xferQty.value <= 0) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (
      await api.post<{ stock_entry_no: string; status: string }>(
        `/work-orders/${props.id}/transfer`,
        { qty: xferQty.value, posting_date: xferDate.value },
      )
    ).data;
    notice.value = `Transferred materials for ${xferQty.value} — posted ${res.stock_entry_no}.`;
    showTransfer.value = false;
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

async function consume(): Promise<void> {
  if (!consumeQty.value || consumeQty.value <= 0) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = (
      await api.post<{ stock_entry_no: string }>(`/work-orders/${props.id}/consume`, {
        qty: consumeQty.value,
        posting_date: consumeDate.value,
      })
    ).data;
    notice.value = `Consumed materials for ${consumeQty.value} — posted ${res.stock_entry_no}.`;
    showConsume.value = false;
    await refresh();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    busy.value = false;
  }
}

const hasShortfall = computed(() => avail.value?.rows.some((r) => Number(r.shortfall_qty) > 0) ?? false);

watch(
  () => wo.value?.id,
  () => {
    substitutes.value = {};
    altFilter.value = {};
  },
);

onMounted(refresh);
</script>

<template>
  <div v-if="wo">
    <div class="mb-4 flex items-start justify-between">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-xl font-semibold text-gray-900">{{ wo.name }}</h1>
          <StatusBadge :status="wo.status" />
        </div>
        <p class="text-sm text-gray-500">
          {{ wo.production_item_name }} <span class="text-gray-400">({{ wo.production_item_code }})</span>
          — BOM {{ wo.bom_name }}
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <RouterLink to="/work-orders" class="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50">Back</RouterLink>
        <button v-if="wo.docstatus === 0" class="btn-primary" :disabled="busy" @click="act('submit')">Submit</button>
        <button
          v-if="wo.docstatus === 1 && needsWipTransfer && ['Not Started', 'In Process'].includes(wo.status)"
          class="rounded-md border border-indigo-200 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-50"
          :disabled="busy || remainingTransfer <= 0"
          @click="showTransfer = !showTransfer; showFinish = false; showConsume = false"
        >
          Transfer to WIP…
        </button>
        <button
          v-if="canFinish"
          class="rounded-md border border-violet-200 px-3 py-1.5 text-sm font-medium text-violet-700 hover:bg-violet-50"
          :disabled="busy"
          @click="showConsume = !showConsume; showFinish = false; showTransfer = false"
        >
          Consume materials…
        </button>
        <button
          v-if="canFinish"
          class="btn-primary" :disabled="busy" @click="openFinish"
        >
          Finish…
        </button>
        <button
          v-if="wo.docstatus === 1 && ['Not Started', 'In Process'].includes(wo.status)"
          class="rounded-md border border-amber-200 px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-50" :disabled="busy" @click="act('stop')"
        >
          Stop
        </button>
        <button v-if="wo.status === 'Stopped'" class="btn-primary" :disabled="busy" @click="act('resume')">Resume</button>
        <button
          v-if="wo.docstatus === 1 && Number(wo.produced_qty) === 0 && Number(wo.material_transferred_qty) === 0 && wo.status !== 'Completed' && !wo.items.some((i) => Number(i.consumed_qty) > 0)"
          class="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50" :disabled="busy" @click="act('cancel')"
        >
          Cancel
        </button>
      </div>
    </div>

    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <div
      v-if="altShortfalls.length && canFinish"
      class="mb-4 rounded-lg border border-violet-200 bg-violet-50 px-4 py-3"
    >
      <p class="text-sm font-medium text-violet-900">Material shortfall — use a substitute</p>
      <p class="mt-1 text-xs text-violet-800/90">
        Pick “Use instead” on the materials row below (stocked items listed first), then click Finish.
        Substituting replaces the planned item for this manufacture qty.
      </p>
      <ul class="mt-2 space-y-1 text-sm text-violet-900">
        <li v-for="row in altShortfalls" :key="row.item_id">
          {{ row.item_code }} short by {{ formatQty(String(availFor(row.item_id).shortfall)) }}
          <template v-if="substitutes[row.item_id]">
            → will consume <span class="font-medium">{{ substituteLabel(substitutes[row.item_id]) }}</span>
          </template>
        </li>
      </ul>
    </div>

    <form v-if="showTransfer" class="mb-5 rounded-lg border border-indigo-100 bg-indigo-50/40 p-5 shadow-sm" @submit.prevent="transfer">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">FG qty to transfer for* (≤ {{ formatQty(remainingTransfer) }})</label>
          <input v-model.number="xferQty" type="number" min="0.000001" step="any" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Posting date*</label>
          <input v-model="xferDate" type="date" required class="form-input" />
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Moves required components from the source warehouse into WIP. Finish will then consume from WIP.
      </p>
      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy || !xferQty">Confirm transfer</button>
      </div>
    </form>

    <form v-if="showConsume" class="mb-5 rounded-lg border border-violet-100 bg-violet-50/40 p-5 shadow-sm" @submit.prevent="consume">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">FG qty to consume for* (≤ {{ formatQty(remaining) }})</label>
          <input v-model.number="consumeQty" type="number" min="0.000001" step="any" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Posting date*</label>
          <input v-model="consumeDate" type="date" required class="form-input" />
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Consumes raws mid-process without finishing. Finish later only consumes the remaining pending qty.
      </p>
      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy || !consumeQty">Confirm consume</button>
      </div>
    </form>

    <form v-if="showFinish" class="mb-5 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="finish">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div>
          <label class="form-label">Quantity to produce* (≤ {{ formatQty(remaining) }})</label>
          <input v-model.number="finQty" type="number" min="0.000001" step="any" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Posting date*</label>
          <input v-model="finDate" type="date" required class="form-input" />
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Consumes components and produces the finished good at
        (materials + operating cost − scrap) ÷ qty.
      </p>

      <div
        v-if="wo.items.some((i) => i.allow_alternative_item && substitutes[i.item_id])"
        class="mt-3 rounded-md border border-violet-100 bg-violet-50/60 px-3 py-2 text-sm text-violet-900"
      >
        <span class="font-medium">Substitutes for this finish:</span>
        <ul class="mt-1 list-inside list-disc text-xs">
          <li v-for="row in wo.items.filter((i) => i.allow_alternative_item && substitutes[i.item_id])" :key="row.item_id">
            {{ row.item_code }} → {{ substituteLabel(substitutes[row.item_id]) }}
          </li>
        </ul>
        <p class="mt-1 text-xs text-violet-700/80">Change them in the materials table below if needed.</p>
      </div>

      <div v-if="needsTracking" class="mt-4 space-y-3 rounded-md border border-amber-100 bg-amber-50/40 p-3">
        <p class="text-xs font-medium text-amber-800">Serial / batch tracking</p>
        <div v-for="row in wo.items.filter((i) => i.has_serial_no || i.has_batch_no)" :key="row.item_id" class="grid grid-cols-2 gap-3">
          <div class="col-span-2 text-sm font-medium text-gray-700">
            Consume {{ row.item_name }}
            <span class="text-xs text-gray-400">({{ row.item_code }})</span>
          </div>
          <div v-if="row.has_serial_no">
            <label class="form-label">Serials (one per line, count = consume qty)</label>
            <textarea v-model="finConsumed[row.item_id].serials" rows="3" class="form-input font-mono text-xs" />
          </div>
          <div v-if="row.has_batch_no">
            <label class="form-label">Batch*</label>
            <select v-model="finConsumed[row.item_id].batch" class="form-input">
              <option value="">Select…</option>
              <option v-for="b in finBatchOptions[row.item_id] || []" :key="b.value" :value="b.value">{{ b.label }}</option>
            </select>
          </div>
        </div>
        <div v-if="wo.production_has_serial_no || wo.production_has_batch_no" class="grid grid-cols-2 gap-3 border-t border-amber-100 pt-3">
          <div class="col-span-2 text-sm font-medium text-gray-700">
            Produce {{ wo.production_item_name }}
          </div>
          <div v-if="wo.production_has_serial_no">
            <label class="form-label">New serials (one per line)</label>
            <textarea v-model="finFgSerials" rows="3" class="form-input font-mono text-xs" />
          </div>
          <div v-if="wo.production_has_batch_no">
            <label class="form-label">Batch*</label>
            <select v-model="finFgBatch" class="form-input">
              <option value="">Select…</option>
              <option
                v-for="b in finBatchOptions[wo.production_item_id] || []"
                :key="b.value"
                :value="b.value"
              >{{ b.label }}</option>
            </select>
            <p class="mt-1 text-xs text-gray-400">Create the batch under Masters → Batch first if missing.</p>
          </div>
        </div>
      </div>

      <div class="mt-3 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy || !finQty">Confirm &amp; manufacture</button>
      </div>
    </form>

    <div class="mb-5 grid grid-cols-2 gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">To make</div>
        <div class="mt-1 text-lg font-semibold">{{ formatQty(wo.qty) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Produced</div>
        <div class="mt-1 text-lg font-semibold text-primary">{{ formatQty(wo.produced_qty) }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">{{ needsWipTransfer ? "Transferred to WIP" : "Operating cost" }}</div>
        <div class="mt-1 text-lg font-semibold">
          {{ needsWipTransfer ? formatQty(wo.material_transferred_qty) : formatCurrency(wo.operating_cost) }}
        </div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="text-xs uppercase text-gray-400">Can finish now</div>
        <div class="mt-1 text-lg font-semibold">{{ avail ? formatQty(avail.can_finish_qty) : "—" }}</div>
      </div>
    </div>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div class="flex items-center justify-between border-b border-gray-100 px-4 py-2">
        <span class="text-sm font-medium text-gray-700">Required materials</span>
        <button
          v-if="hasShortfall && wo.docstatus === 1"
          class="text-sm text-blue-600 hover:underline" :disabled="busy" @click="raiseMaterialRequest"
        >
          Raise Material Request for shortfall
        </button>
      </div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Item</th>
            <th class="px-4 py-2 text-right">Required</th>
            <th v-if="needsWipTransfer" class="px-4 py-2 text-right">Transferred</th>
            <th class="px-4 py-2 text-right">Consumed</th>
            <th class="px-4 py-2 text-right">Available</th>
            <th class="px-4 py-2 text-right">Shortfall</th>
            <th v-if="hasAlternates" class="px-4 py-2">Use instead</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in wo.items" :key="row.id" class="border-t border-gray-100 align-top">
            <td class="px-4 py-2">
              <div class="flex items-center gap-2">
                <span>{{ row.item_name }}</span>
                <span
                  v-if="row.allow_alternative_item"
                  class="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-violet-700"
                >Alt</span>
              </div>
              <div class="text-xs text-gray-400">{{ row.item_code }}</div>
            </td>
            <td class="px-4 py-2 text-right">{{ formatQty(row.required_qty) }}</td>
            <td v-if="needsWipTransfer" class="px-4 py-2 text-right text-gray-500">{{ formatQty(row.transferred_qty) }}</td>
            <td class="px-4 py-2 text-right text-gray-500">{{ formatQty(row.consumed_qty) }}</td>
            <td class="px-4 py-2 text-right">
              {{ avail ? formatQty(avail.rows.find((a) => a.item_id === row.item_id)?.available_qty ?? "0") : "—" }}
            </td>
            <td class="px-4 py-2 text-right">
              <span
                v-if="avail && Number(avail.rows.find((a) => a.item_id === row.item_id)?.shortfall_qty ?? 0) > 0"
                class="font-medium text-red-600"
              >
                {{ formatQty(avail.rows.find((a) => a.item_id === row.item_id)?.shortfall_qty ?? "0") }}
              </span>
              <span v-else class="text-gray-300">—</span>
            </td>
            <td v-if="hasAlternates" class="px-4 py-2">
              <template v-if="row.allow_alternative_item && (canFinish || wo.docstatus === 0)">
                <input
                  v-model="altFilter[row.item_id]"
                  type="search"
                  placeholder="Type RAW-B…"
                  class="form-input mb-1 text-xs"
                  :disabled="wo.docstatus === 0"
                />
                <select
                  class="form-input text-xs"
                  :value="substitutes[row.item_id] || ''"
                  :disabled="altItemsLoading || wo.docstatus === 0"
                  @change="setSubstitute(row.item_id, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="">{{ row.item_code }} (planned)</option>
                  <option
                    v-for="i in substituteChoices(row)"
                    :key="i.id"
                    :value="i.id"
                  >
                    {{ i.item_code }} — {{ i.item_name }}{{ i.stock > 0 ? ` (${formatQty(String(i.stock))} avail)` : "" }}
                  </option>
                </select>
                <p v-if="wo.docstatus === 0" class="mt-1 text-[10px] text-gray-400">Submit to apply a substitute on Finish.</p>
                <p
                  v-else-if="!altItemsLoading && !(alternativesByItem[row.item_id]?.length)"
                  class="mt-1 text-[10px] text-amber-700"
                >
                  No alternatives configured —
                  <RouterLink class="underline" to="/m/item-alternative">add Item Alternative</RouterLink>
                </p>
                <p v-else-if="altItemsLoading" class="mt-1 text-[10px] text-gray-400">Loading…</p>
              </template>
              <span v-else-if="row.allow_alternative_item" class="text-xs text-gray-400">—</span>
              <span v-else class="text-gray-300">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="wo.operations?.length" class="mt-5 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div class="flex items-center justify-between border-b border-gray-100 px-4 py-2">
        <span class="text-sm font-medium text-gray-700">Operations</span>
        <RouterLink
          :to="`/job-cards?work_order_id=${wo.id}`"
          class="text-sm text-blue-600 hover:underline"
        >
          Job Cards
        </RouterLink>
      </div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">#</th>
            <th class="px-4 py-2">Operation</th>
            <th class="px-4 py-2">Workstation</th>
            <th class="px-4 py-2 text-right">Time (mins)</th>
            <th class="px-4 py-2 text-right">Completed</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in wo.operations" :key="row.id" class="border-t border-gray-100">
            <td class="px-4 py-1.5 text-gray-400">{{ row.idx }}</td>
            <td class="px-4 py-1.5">{{ row.operation_name }}</td>
            <td class="px-4 py-1.5">{{ row.workstation_name ?? "—" }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.time_in_mins) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.completed_qty) }}</td>
            <td class="px-4 py-1.5"><StatusBadge :status="row.status" /></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="mt-3 text-xs text-gray-400">
      Created {{ formatDate(wo.creation) }}
      <span v-if="wo.actual_start_date"> · started {{ wo.actual_start_date }}</span>
      <span v-if="wo.actual_end_date"> · completed {{ wo.actual_end_date }}</span>
    </p>
  </div>
</template>
