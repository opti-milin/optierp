<script setup lang="ts">
// Production Plan list — aggregate Sales Order / manual demand into proposed WOs + MRs.
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { ManufacturingSettings, ProductionPlanListItem } from "@/types/manufacturing";

interface WarehouseOpt { id: string; warehouse_name: string; is_group?: boolean }

const router = useRouter();
const rows = ref<ProductionPlanListItem[]>([]);
const warehouses = ref<WarehouseOpt[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const showForm = ref(false);
const saving = ref(false);

const today = new Date().toISOString().slice(0, 10);
const fPosting = ref(today);
const fFrom = ref(today);
const fTo = ref(today);
const fFg = ref("");
const fSource = ref("");

async function fetchList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rows.value = (
      await api.get<ListResponse<ProductionPlanListItem>>("/production-plans", {
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
  const [w, s] = await Promise.all([
    api.get<WarehouseOpt[]>("/warehouses"),
    api.get<ManufacturingSettings>("/manufacturing/settings").catch(() => ({ data: null })),
  ]);
  warehouses.value = w.data.filter((x) => !x.is_group);
  if (s.data) {
    fFg.value = s.data.default_fg_warehouse_id ?? "";
    fSource.value = s.data.default_source_warehouse_id ?? "";
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<{ id: string }>("/production-plans", {
        posting_date: fPosting.value,
        from_date: fFrom.value || null,
        to_date: fTo.value || null,
        get_items_from: "Sales Order",
        fg_warehouse_id: fFg.value || null,
        source_warehouse_id: fSource.value || null,
        items: [],
      })
    ).data;
    void router.push(`/production-plans/${created.id}`);
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
        <h1 class="text-xl font-semibold text-gray-900">Production Plans</h1>
        <p class="text-sm text-gray-500">
          Pull demand from Sales Orders, propose Work Orders and Material Requests for shortfalls.
        </p>
      </div>
      <button class="btn-primary" @click="showForm = !showForm">
        {{ showForm ? "Close" : "New Production Plan" }}
      </button>
    </div>

    <form
      v-if="showForm"
      class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
      @submit.prevent="save"
    >
      <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div>
          <label class="form-label">Posting date*</label>
          <input v-model="fPosting" type="date" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Demand from*</label>
          <input v-model="fFrom" type="date" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Demand to*</label>
          <input v-model="fTo" type="date" required class="form-input" />
        </div>
        <div>
          <label class="form-label">FG warehouse</label>
          <select v-model="fFg" class="form-input">
            <option value="">— settings default —</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Source warehouse (raws)</label>
          <select v-model="fSource" class="form-input">
            <option value="">— settings default —</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
      </div>
      <p v-if="error" class="mt-2 text-sm text-red-600">{{ error.detail }}</p>
      <div class="mt-4 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="saving">
          {{ saving ? "Saving…" : "Create draft" }}
        </button>
      </div>
    </form>

    <p v-if="error && !showForm" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
      {{ error.detail }}
    </p>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Plan</th>
            <th class="px-4 py-2">Posting</th>
            <th class="px-4 py-2">Demand window</th>
            <th class="px-4 py-2">WOs</th>
            <th class="px-4 py-2">MRs</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
            @click="router.push(`/production-plans/${row.id}`)"
          >
            <td class="px-4 py-1.5 font-medium text-gray-900">{{ row.name }}</td>
            <td class="px-4 py-1.5">{{ formatDate(row.posting_date) }}</td>
            <td class="px-4 py-1.5 text-gray-600">
              {{ row.from_date || "—" }} → {{ row.to_date || "—" }}
            </td>
            <td class="px-4 py-1.5">{{ row.work_orders_created ? "Yes" : "—" }}</td>
            <td class="px-4 py-1.5">{{ row.material_requests_created ? "Yes" : "—" }}</td>
            <td class="px-4 py-1.5"><StatusBadge :status="row.status" /></td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="6" class="px-4 py-8 text-center text-gray-400">
              No Production Plans yet.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
