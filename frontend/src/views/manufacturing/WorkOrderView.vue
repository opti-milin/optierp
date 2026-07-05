<script setup lang="ts">
// Work Order: make N units of a finished good per a submitted BOM. Creating one (Draft)
// explodes the BOM × qty into required items; submit, then Finish from its detail page to
// consume the raws and produce the finished good at input cost.
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { BomListItem, ManufacturingSettings, WorkOrderListItem } from "@/types/manufacturing";

interface WarehouseOpt { id: string; warehouse_name: string; is_group?: boolean }
interface AccountOpt { value: string; label: string }

const router = useRouter();

const rows = ref<WorkOrderListItem[]>([]);
const boms = ref<BomListItem[]>([]);
const warehouses = ref<WarehouseOpt[]>([]);
const accounts = ref<AccountOpt[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const showForm = ref(false);
const today = new Date().toISOString().slice(0, 10);
const fBom = ref("");
const fQty = ref<number | null>(null);
const fSource = ref("");
const fFg = ref("");
const fOpAccount = ref("");
const fPlannedStart = ref(today);
const saving = ref(false);

const activeBoms = computed(() => boms.value.filter((b) => b.docstatus === 1 && b.is_active));

async function fetchOptions(): Promise<void> {
  // /warehouses returns a bare array (WarehouseResponse[]); /boms is a ListResponse envelope.
  const [b, w, a, s] = await Promise.all([
    api.get<ListResponse<BomListItem>>("/boms", { params: { page_size: 200 } }),
    api.get<WarehouseOpt[]>("/warehouses"),
    api.get<AccountOpt[]>("/registry/account/options").catch(() => ({ data: [] as AccountOpt[] })),
    api.get<ManufacturingSettings>("/manufacturing/settings").catch(() => ({ data: null })),
  ]);
  boms.value = b.data.items;
  warehouses.value = w.data.filter((x) => !x.is_group);
  accounts.value = a.data;
  // prefill warehouses from the company's Manufacturing defaults (user can still change)
  if (s.data) {
    if (!fSource.value && s.data.default_source_warehouse_id) fSource.value = s.data.default_source_warehouse_id;
    if (!fFg.value && s.data.default_fg_warehouse_id) fFg.value = s.data.default_fg_warehouse_id;
  }
}

async function fetchList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rows.value = (
      await api.get<ListResponse<WorkOrderListItem>>("/work-orders", { params: { page_size: 100 } })
    ).data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  if (!fBom.value || !fQty.value || !fFg.value) return;
  saving.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<{ id: string }>("/work-orders", {
        bom_id: fBom.value,
        qty: fQty.value,
        source_warehouse_id: fSource.value || null,
        fg_warehouse_id: fFg.value,
        operating_cost_account_id: fOpAccount.value || null,
        planned_start_date: fPlannedStart.value || null,
      })
    ).data;
    void router.push(`/work-orders/${created.id}`);
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
        <h1 class="text-xl font-semibold text-gray-900">Work Orders</h1>
        <p class="text-sm text-gray-500">
          Make N units of a finished good per a
          <RouterLink to="/bom" class="text-blue-600 hover:underline">BOM</RouterLink>. Finishing a Work
          Order consumes the raw materials and produces the finished good into stock at its input cost.
        </p>
      </div>
      <button class="btn-primary" @click="showForm = !showForm">
        {{ showForm ? "Close" : "New Work Order" }}
      </button>
    </div>

    <form v-if="showForm" class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="save">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <div class="md:col-span-2">
          <label class="form-label">BOM*</label>
          <select v-model="fBom" required class="form-input">
            <option value="" disabled>Select an active BOM…</option>
            <option v-for="b in activeBoms" :key="b.id" :value="b.id">
              {{ b.name }} — {{ b.production_item_name }}
            </option>
          </select>
        </div>
        <div>
          <label class="form-label">Quantity to make*</label>
          <input v-model.number="fQty" type="number" min="0.000001" step="any" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Source warehouse (raws)</label>
          <select v-model="fSource" class="form-input">
            <option value="">—</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Finished-goods warehouse*</label>
          <select v-model="fFg" required class="form-input">
            <option value="" disabled>Select…</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Operating cost account</label>
          <select v-model="fOpAccount" class="form-input">
            <option value="">— (needed only if labour &gt; 0 with perpetual inventory)</option>
            <option v-for="a in accounts" :key="a.value" :value="a.value">{{ a.label }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Planned start</label>
          <input v-model="fPlannedStart" type="date" class="form-input" />
        </div>
      </div>
      <p v-if="!activeBoms.length" class="mt-2 text-xs text-amber-600">
        No active submitted BOMs yet — create and submit a BOM first.
      </p>
      <p v-if="error" class="mt-2 text-sm text-red-600">{{ error.detail }}</p>
      <div class="mt-4 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="saving || !fBom || !fQty || !fFg">
          {{ saving ? "Saving…" : "Create draft" }}
        </button>
      </div>
    </form>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Work Order</th>
            <th class="px-4 py-2">Finished good</th>
            <th class="px-4 py-2 text-right">Qty</th>
            <th class="px-4 py-2 text-right">Produced</th>
            <th class="px-4 py-2">Planned start</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
            @click="router.push(`/work-orders/${row.id}`)"
          >
            <td class="px-4 py-1.5 font-medium text-gray-900">{{ row.name }}</td>
            <td class="px-4 py-1.5">
              <div>{{ row.production_item_name ?? "—" }}</div>
              <div class="text-xs text-gray-400">{{ row.production_item_code }}</div>
            </td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.qty) }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ formatQty(row.produced_qty) }}</td>
            <td class="px-4 py-1.5 text-gray-500">{{ row.planned_start_date ?? "—" }}</td>
            <td class="px-4 py-1.5"><StatusBadge :status="row.status" /></td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="6" class="px-4 py-8 text-center text-gray-400">
              No Work Orders yet. Create one from a submitted BOM to start producing.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
