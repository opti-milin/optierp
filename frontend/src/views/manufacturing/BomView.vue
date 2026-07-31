<script setup lang="ts">
// Bill of Materials: the recipe for a finished good. Creating a BOM (Draft) snapshots its
// cost (component valuation + flat operating cost); submit it to make it usable by Work
// Orders. Pick a production item + components; rates default from each item's valuation.
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import ItemSelect from "@/components/shared/ItemSelect.vue";
import { api } from "@/api/client";
import { formatCurrency, formatQty } from "@/utils/format";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { BomItemIn, BomListItem, BomOperationIn, BomScrapItemIn } from "@/types/manufacturing";

interface ItemOpt { id: string; item_code: string; item_name: string }
interface MasterOpt { id: string; operation_name?: string; workstation_name?: string }

const router = useRouter();

const rows = ref<BomListItem[]>([]);
const items = ref<ItemOpt[]>([]);
const operations = ref<MasterOpt[]>([]);
const workstations = ref<MasterOpt[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const showForm = ref(false);
const fProduction = ref("");
const fQuantity = ref<number>(1);
const fOperating = ref<number>(0);
const fDefault = ref(true);
const fPhantom = ref(false);
const fComponents = ref<BomItemIn[]>([{ item_id: "", qty: 1, allow_alternative_item: false }]);
const fScrap = ref<BomScrapItemIn[]>([]);
const fOps = ref<BomOperationIn[]>([]);
const saving = ref(false);

const itemOptions = computed(() =>
  items.value.map((item) => ({ value: item.id, label: `${item.item_code} — ${item.item_name}` })),
);

const validComponents = computed(() =>
  fComponents.value.filter((c) => c.item_id && Number(c.qty) > 0),
);
const validScrap = computed(() =>
  fScrap.value.filter((c) => c.item_id && Number(c.qty) > 0),
);
const validOps = computed(() =>
  fOps.value.filter((c) => c.operation_id && Number(c.time_in_mins ?? 0) >= 0),
);

function addRow(): void {
  fComponents.value.push({ item_id: "", qty: 1, allow_alternative_item: false });
}
function removeRow(idx: number): void {
  fComponents.value.splice(idx, 1);
  if (!fComponents.value.length) addRow();
}
function addScrap(): void {
  fScrap.value.push({ item_id: "", qty: 1, rate: 0 });
}
function removeScrap(idx: number): void {
  fScrap.value.splice(idx, 1);
}
function addOp(): void {
  fOps.value.push({ operation_id: "", workstation_id: "", time_in_mins: 0 });
}
function removeOp(idx: number): void {
  fOps.value.splice(idx, 1);
}

async function fetchOptions(): Promise<void> {
  const [itemRes, opRes, wsRes] = await Promise.all([
    api.get<ListResponse<ItemOpt>>("/items", { params: { page_size: 200 } }),
    api.get<ListResponse<MasterOpt>>("/registry/operation", { params: { page_size: 200 } }).catch(() => ({ data: { items: [] } })),
    api.get<ListResponse<MasterOpt>>("/registry/workstation", { params: { page_size: 200 } }).catch(() => ({ data: { items: [] } })),
  ]);
  items.value = itemRes.data.items;
  operations.value = opRes.data.items;
  workstations.value = wsRes.data.items;
}

async function fetchList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rows.value = (
      await api.get<ListResponse<BomListItem>>("/boms", { params: { page_size: 100 } })
    ).data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  if (!fProduction.value || !validComponents.value.length) return;
  saving.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<{ id: string }>("/boms", {
        production_item_id: fProduction.value,
        quantity: fQuantity.value || 1,
        operating_cost: fOperating.value || 0,
        is_default: fDefault.value,
        is_phantom: fPhantom.value,
        items: validComponents.value.map((c) => ({
          item_id: c.item_id,
          qty: Number(c.qty),
          allow_alternative_item: !!c.allow_alternative_item,
        })),
        scrap_items: validScrap.value.map((c) => ({
          item_id: c.item_id,
          qty: Number(c.qty),
          rate: Number(c.rate) || 0,
        })),
        operations: validOps.value.map((c) => ({
          operation_id: c.operation_id,
          workstation_id: c.workstation_id || null,
          time_in_mins: Number(c.time_in_mins) || 0,
        })),
      })
    ).data;
    void router.push(`/bom/${created.id}`);
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
        <h1 class="text-xl font-semibold text-gray-900">Bill of Materials</h1>
        <p class="text-sm text-gray-500">
          The recipe for a finished good — its components plus a flat operating (labour) cost. A
          <RouterLink to="/work-orders" class="text-blue-600 hover:underline">Work Order</RouterLink>
          uses a submitted BOM to actually build and stock the item.
        </p>
      </div>
      <button class="btn-primary" @click="showForm = !showForm">
        {{ showForm ? "Close" : "New BOM" }}
      </button>
    </div>

    <form v-if="showForm" class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="save">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div class="md:col-span-2">
          <label class="form-label">Finished good (production item)*</label>
          <ItemSelect v-model="fProduction" :options="itemOptions" placeholder="Select item…" />
        </div>
        <div>
          <label class="form-label">Batch quantity*</label>
          <input v-model.number="fQuantity" type="number" min="0.000001" step="any" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Operating (labour) cost / batch</label>
          <input v-model.number="fOperating" type="number" min="0" step="any" class="form-input" />
        </div>
      </div>

      <div class="mt-4">
        <div class="mb-1 flex items-center justify-between">
          <label class="form-label mb-0">Components*</label>
          <button type="button" class="text-sm text-blue-600 hover:underline" @click="addRow">+ Add component</button>
        </div>
        <table class="min-w-full text-sm">
          <thead class="text-left text-xs uppercase text-gray-400">
            <tr>
              <th class="py-1">Item</th>
              <th class="w-28 py-1">Qty / batch</th>
              <th class="w-20 py-1 text-center">Alt?</th>
              <th class="w-10"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(c, idx) in fComponents" :key="idx">
              <td class="py-1 pr-2">
                <ItemSelect v-model="c.item_id" :options="itemOptions" placeholder="Select item…" />
              </td>
              <td class="py-1 pr-2">
                <input v-model.number="c.qty" type="number" min="0" step="any" class="form-input" />
              </td>
              <td class="py-1 text-center">
                <input v-model="c.allow_alternative_item" type="checkbox" title="Allow alternate on Finish" />
              </td>
              <td class="py-1 text-center">
                <button type="button" class="text-gray-400 hover:text-red-600" @click="removeRow(idx)">✕</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="mt-4">
        <div class="mb-1 flex items-center justify-between">
          <label class="form-label mb-0">Scrap / by-product (optional)</label>
          <button type="button" class="text-sm text-blue-600 hover:underline" @click="addScrap">+ Add scrap</button>
        </div>
        <table v-if="fScrap.length" class="min-w-full text-sm">
          <thead class="text-left text-xs uppercase text-gray-400">
            <tr>
              <th class="py-1">Item</th>
              <th class="w-28 py-1">Qty / batch</th>
              <th class="w-32 py-1">Recovery rate</th>
              <th class="w-10"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(c, idx) in fScrap" :key="idx">
              <td class="py-1 pr-2">
                <ItemSelect v-model="c.item_id" :options="itemOptions" placeholder="Select item…" />
              </td>
              <td class="py-1 pr-2">
                <input v-model.number="c.qty" type="number" min="0" step="any" class="form-input" />
              </td>
              <td class="py-1 pr-2">
                <input v-model.number="c.rate" type="number" min="0" step="any" class="form-input" />
              </td>
              <td class="py-1 text-center">
                <button type="button" class="text-gray-400 hover:text-red-600" @click="removeScrap(idx)">✕</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-xs text-gray-400">No scrap rows — recovery value is netted from BOM cost when present.</p>
      </div>

      <div class="mt-4">
        <div class="mb-1 flex items-center justify-between">
          <label class="form-label mb-0">Operations (optional)</label>
          <button type="button" class="text-sm text-blue-600 hover:underline" @click="addOp">+ Add operation</button>
        </div>
        <table v-if="fOps.length" class="min-w-full text-sm">
          <thead class="text-left text-xs uppercase text-gray-400">
            <tr>
              <th class="py-1">Operation</th>
              <th class="py-1">Workstation</th>
              <th class="w-28 py-1">Time (mins)</th>
              <th class="w-10"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(c, idx) in fOps" :key="idx">
              <td class="py-1 pr-2">
                <select v-model="c.operation_id" class="form-input">
                  <option value="" disabled>Select…</option>
                  <option v-for="o in operations" :key="o.id" :value="o.id">{{ o.operation_name }}</option>
                </select>
              </td>
              <td class="py-1 pr-2">
                <select v-model="c.workstation_id" class="form-input">
                  <option value="">—</option>
                  <option v-for="w in workstations" :key="w.id" :value="w.id">{{ w.workstation_name }}</option>
                </select>
              </td>
              <td class="py-1 pr-2">
                <input v-model.number="c.time_in_mins" type="number" min="0" step="any" class="form-input" />
              </td>
              <td class="py-1 text-center">
                <button type="button" class="text-gray-400 hover:text-red-600" @click="removeOp(idx)">✕</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-xs text-gray-400">
          No operations — Finish stays a single step. Add operations to generate Job Cards on Work Order submit.
          Masters:
          <RouterLink to="/m/operation" class="text-blue-600 hover:underline">Operation</RouterLink>,
          <RouterLink to="/m/workstation" class="text-blue-600 hover:underline">Workstation</RouterLink>,
          <RouterLink to="/m/routing" class="text-blue-600 hover:underline">Routing</RouterLink>.
        </p>
      </div>

      <label class="mt-3 flex items-center gap-2 text-sm text-gray-700">
        <input v-model="fDefault" type="checkbox" /> Set as the default BOM for this item
      </label>
      <label class="mt-2 flex items-center gap-2 text-sm text-gray-700">
        <input v-model="fPhantom" type="checkbox" /> Phantom BOM (explode through when used as a component)
      </label>
      <p v-if="error" class="mt-2 text-sm text-red-600">{{ error.detail }}</p>
      <div class="mt-4 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="saving || !fProduction || !validComponents.length">
          {{ saving ? "Saving…" : "Create draft" }}
        </button>
      </div>
    </form>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">BOM</th>
            <th class="px-4 py-2">Finished good</th>
            <th class="px-4 py-2 text-right">Batch qty</th>
            <th class="px-4 py-2 text-right">Total cost</th>
            <th class="px-4 py-2 text-right">Per unit</th>
            <th class="px-4 py-2">Default</th>
            <th class="px-4 py-2">Phantom</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
            @click="router.push(`/bom/${row.id}`)"
          >
            <td class="px-4 py-1.5 font-medium text-gray-900">{{ row.name }}</td>
            <td class="px-4 py-1.5">
              <div>{{ row.production_item_name ?? "—" }}</div>
              <div class="text-xs text-gray-400">{{ row.production_item_code }}</div>
            </td>
            <td class="px-4 py-1.5 text-right">{{ formatQty(row.quantity) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(row.total_cost) }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ formatCurrency(row.cost_per_unit) }}</td>
            <td class="px-4 py-1.5">{{ row.is_default ? "★" : "" }}</td>
            <td class="px-4 py-1.5">{{ row.is_phantom ? "Yes" : "" }}</td>
            <td class="px-4 py-1.5"><StatusBadge :status="row.docstatus" /></td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="8" class="px-4 py-8 text-center text-gray-400">
              No BOMs yet. Create one to define a finished good's recipe.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
