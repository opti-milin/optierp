<script setup lang="ts">
// Manufacturing Settings — the lean per-company defaults: source / WIP / finished-goods
// warehouses (prefilled on new Work Orders) and the over-production allowance.
import { onMounted, ref } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { ManufacturingSettings } from "@/types/manufacturing";

interface WarehouseOpt { id: string; warehouse_name: string; is_group?: boolean }

const warehouses = ref<WarehouseOpt[]>([]);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const saving = ref(false);
// saving before the stored settings loaded would overwrite them with a blank form
const loaded = ref(false);

const fSource = ref("");
const fWip = ref("");
const fFg = ref("");
const fOverProd = ref<number>(0);

async function load(): Promise<void> {
  error.value = null;
  try {
    const [w, s] = await Promise.all([
      api.get<WarehouseOpt[]>("/warehouses"),
      api.get<ManufacturingSettings>("/manufacturing/settings"),
    ]);
    warehouses.value = w.data.filter((x) => !x.is_group);
    fSource.value = s.data.default_source_warehouse_id ?? "";
    fWip.value = s.data.default_wip_warehouse_id ?? "";
    fFg.value = s.data.default_fg_warehouse_id ?? "";
    fOverProd.value = Number(s.data.over_production_percentage) || 0;
    loaded.value = true;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.put("/manufacturing/settings", {
      default_source_warehouse_id: fSource.value || null,
      default_wip_warehouse_id: fWip.value || null,
      default_fg_warehouse_id: fFg.value || null,
      over_production_percentage: fOverProd.value || 0,
    });
    notice.value = "Settings saved.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <h1 class="text-xl font-semibold text-gray-900">Manufacturing Settings</h1>
    <p class="mb-4 text-sm text-gray-500">
      Per-company defaults used by new Work Orders. Each Work Order can still override them.
    </p>

    <p v-if="notice" class="mb-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <form class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="save">
      <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <label class="form-label">Default source warehouse (raws)</label>
          <select v-model="fSource" class="form-input">
            <option value="">—</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Default finished-goods warehouse</label>
          <select v-model="fFg" class="form-input">
            <option value="">—</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Default WIP warehouse (optional)</label>
          <select v-model="fWip" class="form-input">
            <option value="">—</option>
            <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Over-production allowance (%)</label>
          <input v-model.number="fOverProd" type="number" min="0" max="100" step="any" class="form-input" />
          <p class="mt-1 text-xs text-gray-500">
            Allows finishing up to qty × (1 + %) on a Work Order — e.g. 10% lets a 10-unit order
            produce 11.
          </p>
        </div>
      </div>
      <div class="mt-4 flex items-center justify-end gap-3">
        <button v-if="!loaded" type="button" class="btn-secondary" @click="load">Retry loading</button>
        <button type="submit" class="btn-primary" :disabled="saving || !loaded">
          {{ saving ? "Saving…" : "Save settings" }}
        </button>
      </div>
    </form>
  </div>
</template>
