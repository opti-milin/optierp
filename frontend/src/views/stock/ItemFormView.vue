<script setup lang="ts">
// Product / Service (Item) master — create + view/edit, with a live per-warehouse
// stock summary on the detail page. Mirrors ERPNext's Item form sections,
// simplified to our model. ItemUpdate can't change item_code / UOM / the is_*
// flags / valuation method, so those are read-only when editing.

import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { useStockStore } from "@/stores/stock";
import { useAccountsStore } from "@/stores/accounts";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { formatCurrency, formatQty } from "@/utils/format";
import type { AccountNode, ErrorEnvelope } from "@/types/core";
import type { Item, StockBalanceRow } from "@/types/stock";

const route = useRoute();
const router = useRouter();
const store = useStockStore();
const accounts = useAccountsStore();
const companyCurrency = useCompanyCurrency();

const itemId = computed(() => route.params.id as string | undefined);
const isEdit = computed(() => !!itemId.value);

const uoms = ref<string[]>([]);
const balance = ref<StockBalanceRow[]>([]);
const lastPurchaseRate = ref("0");
const loading = ref(false);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const form = reactive({
  item_code: "",
  item_name: "",
  item_group_id: "",
  stock_uom: "Nos",
  purchase_uom: "",
  purchase_uom_factor: 1,
  sales_uom: "",
  sales_uom_factor: 1,
  description: "",
  is_stock_item: true,
  is_sales_item: true,
  is_purchase_item: true,
  has_serial_no: false,
  has_batch_no: false,
  valuation_method: "Moving Average",
  valuation_rate: 0,
  standard_rate: 0,
  income_account_id: "",
  expense_account_id: "",
  item_tax_template_id: "",
  default_warehouse_id: "",
  reorder_level: 0,
  reorder_qty: 0,
  lead_time_days: 0,
  brand: "",
  barcode: "",
  hsn_sac_code: "",
  gst_treatment: "Taxable",
  is_fixed_asset: false,
  asset_category_id: "",
  disabled: false,
});

const assetCategories = ref<Array<{ id: string; category_name: string }>>([]);

const incomeAccounts = computed(() =>
  accounts.leafAccounts.filter((a: AccountNode) => a.root_type === "Income"),
);
const expenseAccounts = computed(() =>
  accounts.leafAccounts.filter((a: AccountNode) => a.root_type === "Expense"),
);
const itemTaxTemplates = ref<Array<{ id: string; title: string }>>([]);

const totalStock = computed(() =>
  balance.value.reduce((s, r) => s + Number(r.actual_qty), 0),
);
const totalValue = computed(() =>
  balance.value.reduce((s, r) => s + Number(r.stock_value), 0),
);

// --- HSN / GST auto-fetch: type a product name → pick a match → fill code + rate.
interface HsnMatch {
  hsn_code: string;
  description: string;
  gst_rate: string | number;
  gst_treatment: string;
  chapter: number | null;
  schedule: string | null;
}
const hsnQuery = ref("");
const hsnResults = ref<HsnMatch[]>([]);
const hsnOpen = ref(false);
const hsnLoading = ref(false);
const hsnApplied = ref("");
let hsnDebounce: ReturnType<typeof setTimeout> | undefined;

function fmtRate(rate: string | number): string {
  const n = Number(rate);
  return n === 0 ? "Nil (0%)" : `${n}% GST`;
}

async function runHsnSearch(): Promise<void> {
  const q = hsnQuery.value.trim();
  if (q.length < 2) {
    hsnResults.value = [];
    hsnOpen.value = false;
    return;
  }
  hsnLoading.value = true;
  try {
    const { data } = await api.get<HsnMatch[]>("/hsn-codes", {
      params: { search: q, limit: 12 },
    });
    hsnResults.value = data;
    hsnOpen.value = true;
  } catch {
    hsnResults.value = [];
  } finally {
    hsnLoading.value = false;
  }
}

watch(hsnQuery, () => {
  clearTimeout(hsnDebounce);
  hsnDebounce = setTimeout(() => void runHsnSearch(), 300);
});

function searchFromName(): void {
  hsnQuery.value = form.item_name || form.item_code;
  void runHsnSearch();
}

function applyHsn(m: HsnMatch): void {
  form.hsn_sac_code = m.hsn_code;
  form.gst_treatment = m.gst_treatment;
  const rate = Number(m.gst_rate);
  // Map the slab onto the "GST {rate}%" Item Tax Template (keyed by title) so the
  // rate actually flows onto invoices, not just the HSN code onto the item.
  const slab = `gst ${rate}%`;
  const tpl = itemTaxTemplates.value.find(
    (t) => t.title.replace(/\s+/g, " ").trim().toLowerCase() === slab,
  );
  if (tpl) {
    form.item_tax_template_id = tpl.id;
    hsnApplied.value = `Applied HSN ${m.hsn_code} · ${fmtRate(rate)} via "${tpl.title}".`;
  } else {
    hsnApplied.value =
      `Set HSN ${m.hsn_code} · standard ${fmtRate(rate)}. No "GST ${rate}%" tax ` +
      `template exists yet — pick an Item Tax Template above to apply the rate on invoices.`;
  }
  hsnOpen.value = false;
  hsnResults.value = [];
}

async function loadItem(): Promise<void> {
  if (!itemId.value) return;
  loading.value = true;
  try {
    const { data } = await api.get<Item>(`/items/${itemId.value}`);
    Object.assign(form, {
      item_code: data.item_code,
      item_name: data.item_name,
      item_group_id: data.item_group_id ?? "",
      stock_uom: data.stock_uom,
      purchase_uom: data.purchase_uom ?? "",
      purchase_uom_factor: Number(data.purchase_uom_factor),
      sales_uom: data.sales_uom ?? "",
      sales_uom_factor: Number(data.sales_uom_factor),
      description: data.description ?? "",
      is_stock_item: data.is_stock_item,
      is_sales_item: data.is_sales_item,
      is_purchase_item: data.is_purchase_item,
      has_serial_no: data.has_serial_no,
      has_batch_no: data.has_batch_no,
      valuation_method: data.valuation_method,
      valuation_rate: Number(data.valuation_rate),
      standard_rate: Number(data.standard_rate),
      income_account_id: data.income_account_id ?? "",
      expense_account_id: data.expense_account_id ?? "",
      item_tax_template_id: data.item_tax_template_id ?? "",
      default_warehouse_id: data.default_warehouse_id ?? "",
      reorder_level: Number(data.reorder_level),
      reorder_qty: Number(data.reorder_qty),
      lead_time_days: data.lead_time_days,
      brand: data.brand ?? "",
      barcode: data.barcode ?? "",
      hsn_sac_code: data.hsn_sac_code ?? "",
      gst_treatment: data.gst_treatment ?? "Taxable",
      is_fixed_asset: data.is_fixed_asset ?? false,
      asset_category_id: data.asset_category_id ?? "",
      disabled: data.disabled,
    });
    lastPurchaseRate.value = data.last_purchase_rate;
    balance.value = (
      await api.get<StockBalanceRow[]>("/reports/stock-balance", {
        params: { item_id: itemId.value },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    if (isEdit.value) {
      // ItemUpdate: only the editable subset
      await api.patch(`/items/${itemId.value}`, {
        item_name: form.item_name || form.item_code,
        description: form.description || null,
        item_group_id: form.item_group_id || null,
        purchase_uom: form.purchase_uom || null,
        purchase_uom_factor: form.purchase_uom_factor || 1,
        sales_uom: form.sales_uom || null,
        sales_uom_factor: form.sales_uom_factor || 1,
        has_serial_no: form.has_serial_no,
        has_batch_no: form.has_batch_no,
        standard_rate: form.standard_rate || 0,
        income_account_id: form.income_account_id || null,
        expense_account_id: form.expense_account_id || null,
        item_tax_template_id: form.item_tax_template_id || null,
        default_warehouse_id: form.default_warehouse_id || null,
        reorder_level: form.reorder_level || 0,
        reorder_qty: form.reorder_qty || 0,
        lead_time_days: form.lead_time_days || 0,
        brand: form.brand || null,
        barcode: form.barcode || null,
        hsn_sac_code: form.hsn_sac_code || null,
        gst_treatment: form.gst_treatment,
        is_fixed_asset: form.is_fixed_asset,
        asset_category_id: form.is_fixed_asset ? form.asset_category_id || null : null,
        disabled: form.disabled,
      });
      await loadItem();
    } else {
      const { data } = await api.post<Item>("/items", {
        item_code: form.item_code,
        item_name: form.item_name || form.item_code,
        description: form.description || null,
        item_group_id: form.item_group_id || null,
        stock_uom: form.stock_uom || "Nos",
        purchase_uom: form.purchase_uom || null,
        purchase_uom_factor: form.purchase_uom_factor || 1,
        sales_uom: form.sales_uom || null,
        sales_uom_factor: form.sales_uom_factor || 1,
        has_serial_no: form.has_serial_no,
        has_batch_no: form.has_batch_no,
        is_stock_item: form.is_stock_item,
        is_sales_item: form.is_sales_item,
        is_purchase_item: form.is_purchase_item,
        valuation_method: form.valuation_method,
        valuation_rate: form.valuation_rate || 0,
        standard_rate: form.standard_rate || 0,
        income_account_id: form.income_account_id || null,
        expense_account_id: form.expense_account_id || null,
        item_tax_template_id: form.item_tax_template_id || null,
        default_warehouse_id: form.default_warehouse_id || null,
        reorder_level: form.reorder_level || 0,
        reorder_qty: form.reorder_qty || 0,
        lead_time_days: form.lead_time_days || 0,
        brand: form.brand || null,
        barcode: form.barcode || null,
        hsn_sac_code: form.hsn_sac_code || null,
        gst_treatment: form.gst_treatment,
        is_fixed_asset: form.is_fixed_asset,
        asset_category_id: form.is_fixed_asset ? form.asset_category_id || null : null,
      });
      router.push(`/items/${data.id}`);
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  await Promise.all([
    store.fetchItemGroups(),
    store.fetchWarehouses(),
    accounts.fetchAccounts(),
    api.get<Array<{ uom_name: string }>>("/uoms").then((r) => {
      uoms.value = r.data.map((u) => u.uom_name);
    }).catch(() => { uoms.value = ["Nos"]; }),
    api.get<{ items: Array<{ id: string; title: string }> }>("/registry/item-tax-template", {
      params: { page_size: 200 },
    }).then((r) => { itemTaxTemplates.value = r.data.items ?? []; }).catch(() => {}),
    api.get<{ items: Array<{ id: string; category_name: string }> }>("/registry/asset-category", {
      params: { page_size: 200 },
    }).then((r) => { assetCategories.value = r.data.items ?? []; }).catch(() => {}),
  ]);
  await loadItem();
});
</script>

<template>
  <div class="mx-auto max-w-4xl">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">
          {{ isEdit ? `${form.item_code} — ${form.item_name}` : "New Product / Service" }}
        </h1>
        <p class="text-sm text-gray-500">
          <router-link to="/items" class="text-primary hover:underline">Products &amp; Services</router-link>
          <span v-if="form.disabled" class="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">Disabled</span>
        </p>
      </div>
      <button class="btn-primary" :disabled="saving || !form.item_code" @click="save">
        {{ saving ? "Saving…" : isEdit ? "Save" : "Create" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{{ error.detail }}</p>

    <!-- Details -->
    <section class="mb-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Details</h2>
      <div class="grid grid-cols-3 gap-4">
        <div>
          <label class="form-label">Item Code*</label>
          <input v-model="form.item_code" :disabled="isEdit" required class="form-input"
                 placeholder="e.g. WIDGET-01" />
        </div>
        <div>
          <label class="form-label">Item Name</label>
          <input v-model="form.item_name" class="form-input" placeholder="defaults to code" />
        </div>
        <div>
          <label class="form-label">Item Group</label>
          <select v-model="form.item_group_id" class="form-input">
            <option value="">—</option>
            <option v-for="g in store.itemGroups" :key="g.id" :value="g.id">{{ g.item_group_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Stock UOM</label>
          <select v-model="form.stock_uom" :disabled="isEdit" class="form-input">
            <option v-for="u in uoms" :key="u" :value="u">{{ u }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Purchase UOM</label>
          <div class="flex gap-2">
            <select v-model="form.purchase_uom" class="form-input">
              <option value="">— (same as stock)</option>
              <option v-for="u in uoms" :key="u" :value="u">{{ u }}</option>
            </select>
            <input
              v-model.number="form.purchase_uom_factor" type="number" step="any" min="0"
              class="form-input w-24 text-right" title="Stock units per 1 purchase unit"
              :disabled="!form.purchase_uom || form.purchase_uom === form.stock_uom"
            />
          </div>
          <p class="mt-1 text-xs text-gray-400">1 {{ form.purchase_uom || form.stock_uom }} = {{ form.purchase_uom && form.purchase_uom !== form.stock_uom ? form.purchase_uom_factor : 1 }} {{ form.stock_uom }}</p>
        </div>
        <div>
          <label class="form-label">Sales UOM</label>
          <div class="flex gap-2">
            <select v-model="form.sales_uom" class="form-input">
              <option value="">— (same as stock)</option>
              <option v-for="u in uoms" :key="u" :value="u">{{ u }}</option>
            </select>
            <input
              v-model.number="form.sales_uom_factor" type="number" step="any" min="0"
              class="form-input w-24 text-right" title="Stock units per 1 sales unit"
              :disabled="!form.sales_uom || form.sales_uom === form.stock_uom"
            />
          </div>
          <p class="mt-1 text-xs text-gray-400">1 {{ form.sales_uom || form.stock_uom }} = {{ form.sales_uom && form.sales_uom !== form.stock_uom ? form.sales_uom_factor : 1 }} {{ form.stock_uom }}</p>
        </div>
        <div>
          <label class="form-label">Brand</label>
          <input v-model="form.brand" class="form-input" />
        </div>
        <div>
          <label class="form-label">Barcode</label>
          <input v-model="form.barcode" class="form-input" />
        </div>
        <div class="col-span-3">
          <label class="form-label">Description</label>
          <textarea v-model="form.description" rows="2" class="form-input"></textarea>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-6">
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.is_stock_item" type="checkbox" :disabled="isEdit" class="rounded border-gray-300" />
          Maintain stock (uncheck for a service)
        </label>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.is_sales_item" type="checkbox" :disabled="isEdit" class="rounded border-gray-300" />
          Sales item
        </label>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.is_purchase_item" type="checkbox" :disabled="isEdit" class="rounded border-gray-300" />
          Purchase item
        </label>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.has_serial_no" type="checkbox" :disabled="isEdit" class="rounded border-gray-300" />
          Track serial numbers
        </label>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.has_batch_no" type="checkbox" :disabled="isEdit" class="rounded border-gray-300" />
          Track batches (lots)
        </label>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.is_fixed_asset" type="checkbox" class="rounded border-gray-300" />
          Fixed asset
        </label>
        <label v-if="isEdit" class="flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.disabled" type="checkbox" class="rounded border-gray-300" />
          Disabled
        </label>
      </div>
      <div v-if="form.is_fixed_asset" class="mt-3 grid grid-cols-3 gap-4">
        <div>
          <label class="form-label">Asset Category</label>
          <select v-model="form.asset_category_id" class="form-input">
            <option value="">—</option>
            <option v-for="c in assetCategories" :key="c.id" :value="c.id">{{ c.category_name }}</option>
          </select>
        </div>
        <p class="col-span-2 self-end text-xs text-gray-400">
          A Purchase Invoice line for this item auto-creates a draft Asset under this category.
          Set the Expense Account (under Purchasing) to the Fixed Asset ledger account so the
          purchase debits the asset.
        </p>
      </div>
    </section>

    <!-- Inventory -->
    <section v-if="form.is_stock_item" class="mb-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Inventory</h2>
      <div class="grid grid-cols-3 gap-4">
        <div>
          <label class="form-label">Valuation Method</label>
          <select v-model="form.valuation_method" :disabled="isEdit" class="form-input">
            <option>Moving Average</option>
          </select>
        </div>
        <div>
          <label class="form-label">{{ isEdit ? "Opening Valuation Rate" : "Opening Valuation Rate" }}</label>
          <input v-model.number="form.valuation_rate" :disabled="isEdit" type="number" min="0" step="any"
                 class="form-input" />
        </div>
        <div>
          <label class="form-label">Default Warehouse</label>
          <select v-model="form.default_warehouse_id" class="form-input">
            <option value="">—</option>
            <option v-for="w in store.leafWarehouses" :key="w.id" :value="w.id">{{ w.warehouse_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Reorder Level</label>
          <input v-model.number="form.reorder_level" type="number" min="0" step="any" class="form-input" />
        </div>
        <div>
          <label class="form-label">Reorder Qty</label>
          <input v-model.number="form.reorder_qty" type="number" min="0" step="any" class="form-input" />
        </div>
        <div>
          <label class="form-label">Lead Time (days)</label>
          <input v-model.number="form.lead_time_days" type="number" min="0" class="form-input" />
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-400">
        Reorder Level &amp; Qty drive the Reorder report. The live valuation rate is maintained per
        warehouse by the stock ledger (see the summary below).
      </p>
    </section>

    <!-- Selling / Purchasing -->
    <section class="mb-4 grid grid-cols-2 gap-4">
      <div class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <h2 class="mb-3 text-sm font-semibold text-gray-900">Selling</h2>
        <label class="form-label">Selling Rate</label>
        <input v-model.number="form.standard_rate" type="number" min="0" step="any" class="form-input mb-3" />
        <label class="form-label">Income Account</label>
        <select v-model="form.income_account_id" class="form-input">
          <option value="">—</option>
          <option v-for="a in incomeAccounts" :key="a.id" :value="a.id">{{ a.account_name }}</option>
        </select>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <h2 class="mb-3 text-sm font-semibold text-gray-900">Purchasing</h2>
        <label class="form-label">Expense Account</label>
        <select v-model="form.expense_account_id" class="form-input mb-3">
          <option value="">—</option>
          <option v-for="a in expenseAccounts" :key="a.id" :value="a.id">{{ a.account_name }}</option>
        </select>
        <template v-if="itemTaxTemplates.length">
          <label class="form-label">Item Tax Template (GST override)</label>
          <select v-model="form.item_tax_template_id" class="form-input mb-3">
            <option value="">— use the invoice's tax rate —</option>
            <option v-for="t in itemTaxTemplates" :key="t.id" :value="t.id">{{ t.title }}</option>
          </select>
        </template>
        <!-- Auto-fetch HSN + GST rate by product name (or code) -->
        <label class="form-label">Auto-fetch HSN &amp; GST rate</label>
        <div class="relative mb-2">
          <div class="flex gap-2">
            <input
              v-model="hsnQuery"
              type="search"
              class="form-input"
              placeholder="Type a product name — e.g. refrigerator, cotton shirt, rice…"
              @focus="hsnResults.length && (hsnOpen = true)"
              @keydown.escape="hsnOpen = false"
            />
            <button
              type="button"
              class="btn-secondary whitespace-nowrap"
              :disabled="!form.item_name && !form.item_code"
              title="Search using the item name entered above"
              @click="searchFromName"
            >
              Use name
            </button>
          </div>
          <p v-if="hsnLoading" class="mt-1 text-xs text-gray-400">Searching HSN directory…</p>
          <ul
            v-if="hsnOpen && hsnResults.length"
            class="absolute z-10 mt-1 max-h-72 w-full overflow-auto rounded-md border border-gray-200 bg-white shadow-lg"
          >
            <li
              v-for="m in hsnResults"
              :key="m.hsn_code + m.description"
              class="cursor-pointer border-b border-gray-50 px-3 py-2 text-sm last:border-0 hover:bg-primary/5"
              @click="applyHsn(m)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="font-mono text-xs text-gray-500">{{ m.hsn_code }}</span>
                <span
                  class="rounded-full px-2 py-0.5 text-xs font-medium"
                  :class="Number(m.gst_rate) === 0 ? 'bg-gray-100 text-gray-600' : 'bg-emerald-50 text-emerald-700'"
                >{{ fmtRate(m.gst_rate) }}</span>
              </div>
              <div class="mt-0.5 text-gray-700">{{ m.description }}</div>
            </li>
          </ul>
          <p
            v-else-if="hsnOpen && !hsnLoading && hsnQuery.trim().length >= 2"
            class="mt-1 text-xs text-gray-400"
          >
            No HSN match — try a simpler commodity word, or fill the code manually below.
          </p>
        </div>
        <p v-if="hsnApplied" class="mb-2 rounded-md bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700">
          {{ hsnApplied }}
        </p>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="form-label">HSN / SAC code</label>
            <input v-model="form.hsn_sac_code" maxlength="8" class="form-input" placeholder="e.g. 8418" />
          </div>
          <div>
            <label class="form-label">GST treatment</label>
            <select v-model="form.gst_treatment" class="form-input">
              <option value="Taxable">Taxable</option>
              <option value="Nil-Rated">Nil-Rated</option>
              <option value="Exempt">Exempt</option>
              <option value="Non-GST">Non-GST</option>
            </select>
          </div>
        </div>
        <p class="mt-1 text-xs text-gray-400">
          Auto-filled from the lookup — edit either field to override.
        </p>
        <label v-if="isEdit" class="form-label">Last Purchase Rate</label>
        <input v-if="isEdit" :value="formatCurrency(lastPurchaseRate, companyCurrency)" disabled class="form-input" />
      </div>
    </section>

    <!-- Stock summary (detail page) -->
    <section v-if="isEdit && form.is_stock_item" class="mb-4 rounded-lg border border-gray-200 bg-white shadow-sm">
      <div class="flex items-center justify-between border-b border-gray-100 px-5 py-3">
        <h2 class="text-sm font-semibold text-gray-900">Stock Summary</h2>
        <span class="text-sm text-gray-500">
          {{ formatQty(String(totalStock)) }} in stock · {{ formatCurrency(String(totalValue), companyCurrency) }}
        </span>
      </div>
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-5 py-2">Warehouse</th>
            <th class="px-5 py-2 text-right">In Stock</th>
            <th class="px-5 py-2 text-right">Reserved</th>
            <th class="px-5 py-2 text-right">Ordered</th>
            <th class="px-5 py-2 text-right">Projected</th>
            <th class="px-5 py-2 text-right">Valuation</th>
            <th class="px-5 py-2 text-right">Stock Value</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in balance" :key="i" class="border-t border-gray-100">
            <td class="px-5 py-2 text-gray-700">{{ r.warehouse_name }}</td>
            <td class="px-5 py-2 text-right">{{ formatQty(r.actual_qty) }}</td>
            <td class="px-5 py-2 text-right text-amber-700">{{ formatQty(r.reserved_qty) }}</td>
            <td class="px-5 py-2 text-right text-blue-700">{{ formatQty(r.ordered_qty) }}</td>
            <td class="px-5 py-2 text-right">{{ formatQty(r.projected_qty) }}</td>
            <td class="px-5 py-2 text-right">{{ formatCurrency(r.valuation_rate, companyCurrency) }}</td>
            <td class="px-5 py-2 text-right font-medium">{{ formatCurrency(r.stock_value, companyCurrency) }}</td>
          </tr>
          <tr v-if="!loading && !balance.length">
            <td colspan="7" class="px-5 py-8 text-center text-gray-400">
              No stock yet — post a Purchase Receipt or Stock Entry.
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
