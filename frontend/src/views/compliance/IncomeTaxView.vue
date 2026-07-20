<script setup lang="ts">
// Income Tax — entity computation pack (Phase 1). List + create + detail with
// adjustments, mirroring the TDS/GST returns compliance surface.
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatCurrency, formatDate } from "@/utils/format";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  IncomeTaxComputation,
  IncomeTaxComputationListItem,
} from "@/types/compliance";

const route = useRoute();
const router = useRouter();

const list = ref<IncomeTaxComputationListItem[]>([]);
const doc = ref<IncomeTaxComputation | null>(null);
const loading = ref(false);
const saving = ref(false);
const exporting = ref(false);
const efiling = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
type EfileResult = {
  status: string;
  provider: string;
  message: string;
  result: Record<string, unknown> | null;
};

const efileResult = ref<EfileResult | null>(null);
const calendar = ref<{
  assessment_year: string;
  instalments: Array<{
    instalment: number;
    due_date: string;
    cumulative_percent: number;
    suggested_amount: string | null;
  }>;
} | null>(null);
const recon = ref<{
  summary: {
    status: string;
    books_tds_credit: string;
    portal_tds_credit: string;
    difference: string;
  };
} | null>(null);

const detailId = computed(() => (route.params.id as string | undefined) ?? null);
const isNew = computed(() => route.name === "income-tax-new");

// create form
const assessmentYear = ref("2025-26");
const fromDate = ref("2024-04-01");
const toDate = ref("2025-03-31");
const advanceTax = ref("0");
const remarks = ref("");

// detail edit
const editAdvance = ref("0");
const editRemarks = ref("");
const adjRows = ref<
  Array<{ category_id: string; description: string; direction: string; amount: string }>
>([]);
const categories = ref<
  Array<{ id: string; category_code: string; category_name: string; direction: string }>
>([]);
const rateInfo = ref<string | null>(null);

async function loadList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const r = await api.get<ListResponse<IncomeTaxComputationListItem>>("/income-tax-computations");
    list.value = r.data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function loadCategories(): Promise<void> {
  try {
    const r = await api.get<{
      items: Array<{
        id: string;
        category_code: string;
        category_name: string;
        direction: string;
        disabled?: boolean;
      }>;
    }>("/registry/tax-adjustment-category", { params: { page_size: 200 } });
    categories.value = (r.data.items || []).filter((c) => !c.disabled);
  } catch {
    categories.value = [];
  }
}

async function loadRateLabel(rateTableId: string | null): Promise<void> {
  if (!rateTableId) {
    rateInfo.value = "No rate table matched — seed masters or pick a rate for this entity/regime.";
    return;
  }
  try {
    const r = await api.get<{
      assessment_year: string;
      entity_type?: string;
      filing_regime?: string;
      tax_rate: string | number;
      surcharge_rate: string | number;
      cess_rate: string | number;
    }>(`/registry/income-tax-rate-table/${rateTableId}`);
    const row = r.data;
    rateInfo.value =
      `${row.assessment_year} · ${row.entity_type || "?"} / ${row.filing_regime || "?"} — ` +
      `tax ${row.tax_rate}% · surcharge ${row.surcharge_rate}% · cess ${row.cess_rate}%`;
  } catch {
    rateInfo.value = `Rate table ${rateTableId}`;
  }
}

async function loadDetail(id: string): Promise<void> {
  loading.value = true;
  error.value = null;
  recon.value = null;
  efileResult.value = null;
  try {
    await loadCategories();
    doc.value = (await api.get<IncomeTaxComputation>(`/income-tax-computations/${id}`)).data;
    editAdvance.value = doc.value.advance_tax_paid;
    editRemarks.value = doc.value.remarks ?? "";
    adjRows.value = doc.value.adjustments.map((a) => ({
      category_id: a.category_id || "",
      description: a.description,
      direction: a.direction,
      amount: a.amount,
    }));
    await loadRateLabel(doc.value.rate_table_id);
    try {
      calendar.value = (
        await api.get("/income-tax-computations/advance-tax-calendar", {
          params: {
            assessment_year: doc.value.assessment_year,
            total_tax: doc.value.total_tax,
          },
        })
      ).data as typeof calendar.value;
    } catch {
      calendar.value = null;
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
    doc.value = null;
  } finally {
    loading.value = false;
  }
}

async function downloadItr6Json(): Promise<void> {
  if (!doc.value) return;
  exporting.value = true;
  error.value = null;
  try {
    const pack = (await api.get(`/income-tax-computations/${doc.value.id}/itr`)).data as {
      form?: string;
      assessment_year?: string;
    };
    const blob = new Blob([JSON.stringify(pack, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const form = (pack.form || "ITR").toLowerCase().replace("-", "");
    a.download = `${form}-${pack.assessment_year || doc.value.assessment_year}.json`;
    a.click();
    URL.revokeObjectURL(url);
    notice.value = `${pack.form || "ITR"} JSON downloaded.`;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    exporting.value = false;
  }
}

async function downloadItr6Csv(): Promise<void> {
  if (!doc.value) return;
  error.value = null;
  try {
    const resp = await api.get(`/income-tax-computations/${doc.value.id}/itr.csv`, {
      responseType: "blob",
    });
    const url = URL.createObjectURL(resp.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `itr-${doc.value.assessment_year}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function efileItr(): Promise<void> {
  if (!doc.value) return;
  efiling.value = true;
  error.value = null;
  efileResult.value = null;
  try {
    efileResult.value = (
      await api.post<EfileResult>(`/income-tax-computations/${doc.value.id}/itr/efile`)
    ).data;
    notice.value = efileResult.value.message || "E-file completed.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    efiling.value = false;
  }
}

async function on26asFile(ev: Event): Promise<void> {
  if (!doc.value) return;
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  error.value = null;
  try {
    const text = await file.text();
    const form26as = JSON.parse(text) as Record<string, unknown>;
    recon.value = (
      await api.post("/income-tax-computations/26as/reconcile", {
        computation_id: doc.value.id,
        form26as,
      })
    ).data as typeof recon.value;
  } catch (e) {
    error.value = e as ErrorEnvelope;
    recon.value = null;
  }
}

async function createDoc(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<IncomeTaxComputation>("/income-tax-computations", {
        assessment_year: assessmentYear.value,
        from_date: fromDate.value,
        to_date: toDate.value,
        advance_tax_paid: advanceTax.value,
        remarks: remarks.value || null,
        adjustments: [],
        seed_from_books: true,
      })
    ).data;
    notice.value = "Computation created from books.";
    void router.push({ name: "income-tax-detail", params: { id: created.id } });
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function saveDraft(): Promise<void> {
  if (!doc.value || doc.value.docstatus !== 0) return;
  saving.value = true;
  error.value = null;
  try {
    doc.value = (
      await api.put<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}`, {
        advance_tax_paid: editAdvance.value,
        remarks: editRemarks.value || null,
        adjustments: adjPayload(),
      })
    ).data;
    notice.value = "Draft saved.";
    await loadRateLabel(doc.value.rate_table_id);
    setTimeout(() => (notice.value = null), 2000);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function reseed(): Promise<void> {
  if (!doc.value || doc.value.docstatus !== 0) return;
  saving.value = true;
  error.value = null;
  try {
    doc.value = (
      await api.put<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}`, {
        reseeds_from_books: true,
      })
    ).data;
    editAdvance.value = doc.value.advance_tax_paid;
    notice.value = "Re-seeded book profit and TDS credit from books.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function submitDoc(): Promise<void> {
  if (!doc.value || doc.value.docstatus !== 0) return;
  saving.value = true;
  error.value = null;
  try {
    await api.put(`/income-tax-computations/${doc.value.id}`, {
      advance_tax_paid: editAdvance.value,
      remarks: editRemarks.value || null,
      adjustments: adjPayload(),
    });
    doc.value = (
      await api.post<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}/submit`)
    ).data;
    notice.value = "Submitted.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function cancelDoc(): Promise<void> {
  if (!doc.value) return;
  saving.value = true;
  error.value = null;
  try {
    doc.value = (
      await api.post<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}/cancel`)
    ).data;
    notice.value = "Cancelled.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

function adjPayload() {
  return adjRows.value.map((r) => ({
    category_id: r.category_id || null,
    description: r.description,
    direction: r.direction,
    amount: r.amount,
  }));
}

function addAdj(): void {
  adjRows.value.push({ category_id: "", description: "", direction: "Add", amount: "0" });
}

function removeAdj(i: number): void {
  adjRows.value.splice(i, 1);
}

function onCategoryChange(i: number): void {
  const row = adjRows.value[i];
  if (!row?.category_id) return;
  const cat = categories.value.find((c) => c.id === row.category_id);
  if (!cat) return;
  row.direction = cat.direction;
  if (!row.description.trim()) {
    row.description = cat.category_name;
  }
}

async function applyRateFromSettings(): Promise<void> {
  if (!doc.value || doc.value.docstatus !== 0) return;
  saving.value = true;
  error.value = null;
  try {
    doc.value = (
      await api.put<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}`, {
        resolve_rate_from_settings: true,
        advance_tax_paid: editAdvance.value,
        remarks: editRemarks.value || null,
        adjustments: adjPayload(),
      })
    ).data;
    await loadRateLabel(doc.value.rate_table_id);
    notice.value = "Rate table refreshed from Income Tax Settings (entity + regime).";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

watch(
  () => [route.name, detailId.value] as const,
  async ([name, id]) => {
    if (name === "income-tax-detail" && id) {
      await loadDetail(id);
    } else if (name === "income-tax" || name === "income-tax-new") {
      doc.value = null;
      await loadList();
    }
  },
  { immediate: true },
);

onMounted(() => {
  /* watch handles initial load */
});
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Income Tax</h1>
        <p class="text-sm text-gray-500">
          Entity tax computation from books (P&amp;L + adjustments + rate table − TDS / advance tax).
          Submitted packs export ITR-6 JSON/CSV; advance-tax calendar and 26AS reconcile on the detail page.
        </p>
      </div>
      <div class="flex gap-2">
        <RouterLink class="btn-secondary" to="/income-tax-settings">Settings</RouterLink>
        <RouterLink class="btn-secondary" to="/m/income-tax-rate-table">Rate tables</RouterLink>
        <RouterLink class="btn-secondary" to="/m/tax-adjustment-category">Adjustments</RouterLink>
        <RouterLink v-if="!isNew" class="btn-primary" :to="{ name: 'income-tax-new' }">New</RouterLink>
      </div>
    </div>

    <p v-if="notice" class="rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <!-- New -->
    <section v-if="isNew" class="max-w-xl rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">New computation</h2>
      <div class="grid grid-cols-2 gap-3">
        <label class="text-sm col-span-2">
          <span class="mb-1 block text-gray-500">Assessment year</span>
          <input v-model="assessmentYear" class="form-input" placeholder="2025-26" />
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">FY from</span>
          <input v-model="fromDate" type="date" class="form-input" />
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">FY to</span>
          <input v-model="toDate" type="date" class="form-input" />
        </label>
        <label class="text-sm col-span-2">
          <span class="mb-1 block text-gray-500">Advance tax paid</span>
          <input v-model="advanceTax" class="form-input" />
        </label>
        <label class="text-sm col-span-2">
          <span class="mb-1 block text-gray-500">Remarks</span>
          <input v-model="remarks" class="form-input" />
        </label>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        Book profit and TDS credit are seeded from the P&amp;L and purchase TDS for the FY window.
      </p>
      <div class="mt-4 flex justify-end gap-2">
        <RouterLink class="btn-secondary" :to="{ name: 'income-tax' }">Cancel</RouterLink>
        <button class="btn-primary" :disabled="saving" @click="createDoc">
          {{ saving ? "Creating…" : "Create from books" }}
        </button>
      </div>
    </section>

    <!-- Detail -->
    <template v-else-if="detailId && doc">
      <div class="flex flex-wrap items-center gap-3 text-sm">
        <RouterLink class="text-blue-600 hover:underline" :to="{ name: 'income-tax' }">← List</RouterLink>
        <span class="font-mono font-medium">{{ doc.name }}</span>
        <span class="rounded bg-gray-100 px-2 py-0.5">{{ doc.status }}</span>
        <span>AY {{ doc.assessment_year }}</span>
        <span>{{ formatDate(doc.from_date) }} → {{ formatDate(doc.to_date) }}</span>
      </div>

      <div class="grid gap-4 md:grid-cols-2">
        <section class="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
          <h2 class="mb-2 font-semibold text-gray-900">Books → taxable income</h2>
          <dl class="space-y-1">
            <div class="flex justify-between"><dt>Book profit (P&amp;L)</dt><dd>{{ formatCurrency(doc.book_profit) }}</dd></div>
            <div class="flex justify-between"><dt>Net adjustments</dt><dd>{{ formatCurrency(doc.net_adjustments) }}</dd></div>
            <div class="flex justify-between font-semibold"><dt>Taxable income</dt><dd>{{ formatCurrency(doc.taxable_income) }}</dd></div>
          </dl>
          <button
            v-if="doc.docstatus === 0"
            class="btn-secondary mt-3 text-xs"
            :disabled="saving"
            @click="reseed"
          >
            Re-seed from books
          </button>
        </section>
        <section class="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
          <h2 class="mb-2 font-semibold text-gray-900">Tax</h2>
          <p v-if="rateInfo" class="mb-2 text-xs text-gray-500">{{ rateInfo }}</p>
          <dl class="space-y-1">
            <div class="flex justify-between"><dt>Tax</dt><dd>{{ formatCurrency(doc.tax_amount) }}</dd></div>
            <div class="flex justify-between"><dt>Surcharge</dt><dd>{{ formatCurrency(doc.surcharge_amount) }}</dd></div>
            <div class="flex justify-between"><dt>Cess</dt><dd>{{ formatCurrency(doc.cess_amount) }}</dd></div>
            <div class="flex justify-between font-semibold"><dt>Total tax</dt><dd>{{ formatCurrency(doc.total_tax) }}</dd></div>
            <div class="flex justify-between"><dt>TDS credit</dt><dd>−{{ formatCurrency(doc.tds_credit) }}</dd></div>
            <div class="flex justify-between"><dt>Advance tax</dt><dd>−{{ formatCurrency(doc.advance_tax_paid) }}</dd></div>
            <div class="flex justify-between border-t pt-1 font-semibold">
              <dt>{{ Number(doc.tax_payable) >= 0 ? "Tax payable" : "Refundable" }}</dt>
              <dd>{{ formatCurrency(doc.tax_payable) }}</dd>
            </div>
          </dl>
          <button
            v-if="doc.docstatus === 0"
            class="btn-secondary mt-3 text-xs"
            :disabled="saving"
            @click="applyRateFromSettings"
          >
            Apply rate for current entity / regime
          </button>
        </section>
      </div>

      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-900">Adjustments</h2>
          <button
            v-if="doc.docstatus === 0"
            type="button"
            class="btn-secondary text-xs"
            @click="addAdj"
          >
            Add line
          </button>
        </div>
        <table class="w-full text-left text-sm">
          <thead class="text-xs text-gray-500">
            <tr>
              <th class="py-1 w-48">Category</th>
              <th class="py-1">Description</th>
              <th class="py-1 w-28">Direction</th>
              <th class="py-1 w-36 text-right">Amount</th>
              <th v-if="doc.docstatus === 0" class="w-10" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in adjRows" :key="i" class="border-t border-gray-100">
              <td class="py-1 pr-2">
                <select
                  v-if="doc.docstatus === 0"
                  v-model="row.category_id"
                  class="form-input"
                  @change="onCategoryChange(i)"
                >
                  <option value="">— custom —</option>
                  <option v-for="c in categories" :key="c.id" :value="c.id">
                    {{ c.category_code }} — {{ c.category_name }}
                  </option>
                </select>
                <span v-else>
                  {{
                    categories.find((c) => c.id === row.category_id)?.category_code
                      || row.category_id
                      || "—"
                  }}
                </span>
              </td>
              <td class="py-1 pr-2">
                <input
                  v-if="doc.docstatus === 0"
                  v-model="row.description"
                  class="form-input"
                />
                <span v-else>{{ row.description || "—" }}</span>
              </td>
              <td class="py-1 pr-2">
                <select
                  v-if="doc.docstatus === 0 && !row.category_id"
                  v-model="row.direction"
                  class="form-input"
                >
                  <option value="Add">Add</option>
                  <option value="Deduct">Deduct</option>
                </select>
                <span v-else>{{ row.direction }}</span>
              </td>
              <td class="py-1 text-right">
                <input
                  v-if="doc.docstatus === 0"
                  v-model="row.amount"
                  class="form-input text-right"
                />
                <span v-else>{{ formatCurrency(row.amount) }}</span>
              </td>
              <td v-if="doc.docstatus === 0" class="py-1 text-center">
                <button type="button" class="text-red-500 text-xs" @click="removeAdj(i)">×</button>
              </td>
            </tr>
            <tr v-if="!adjRows.length">
              <td colspan="5" class="py-3 text-center text-gray-400">No adjustments</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="doc.docstatus === 0" class="flex flex-wrap items-end gap-3">
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">Advance tax paid</span>
          <input v-model="editAdvance" class="form-input" />
        </label>
        <label class="text-sm flex-1 min-w-[12rem]">
          <span class="mb-1 block text-gray-500">Remarks</span>
          <input v-model="editRemarks" class="form-input" />
        </label>
        <button class="btn-secondary" :disabled="saving" @click="saveDraft">Save draft</button>
        <button class="btn-primary" :disabled="saving" @click="submitDoc">Submit</button>
      </section>
      <div v-else-if="doc.docstatus === 1" class="flex flex-wrap gap-2">
        <button class="btn-secondary" :disabled="saving" @click="cancelDoc">Cancel</button>
        <button class="btn-primary" :disabled="exporting" @click="downloadItr6Json">
          {{ exporting ? "Exporting…" : "Download ITR JSON" }}
        </button>
        <a
          class="btn-secondary"
          :href="`/api/v1/income-tax-computations/${doc.id}/itr.csv`"
          @click.prevent="downloadItr6Csv"
        >Download ITR CSV</a>
        <button class="btn-primary" :disabled="efiling" @click="efileItr">
          {{ efiling ? "E-filing…" : "E-file (sandbox / provider)" }}
        </button>
      </div>
      <p v-if="efileResult" class="rounded bg-blue-50 px-3 py-2 text-sm text-blue-800">
        {{ efileResult.status }} via {{ efileResult.provider }} —
        {{ efileResult.message }}
        <span v-if="efileResult.result?.ack_no"> (ack {{ efileResult.result.ack_no }})</span>
      </p>

      <section v-if="calendar" class="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
        <h2 class="mb-2 font-semibold text-gray-900">Advance tax calendar (AY {{ calendar.assessment_year }})</h2>
        <table class="w-full text-left">
          <thead class="text-xs text-gray-500">
            <tr>
              <th class="py-1">#</th>
              <th class="py-1">Due</th>
              <th class="py-1 text-right">Cumulative %</th>
              <th class="py-1 text-right">Suggested (cum.)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="i in calendar.instalments" :key="i.instalment" class="border-t border-gray-100">
              <td class="py-1">{{ i.instalment }}</td>
              <td class="py-1">{{ formatDate(i.due_date) }}</td>
              <td class="py-1 text-right">{{ i.cumulative_percent }}%</td>
              <td class="py-1 text-right">
                {{ i.suggested_amount != null ? formatCurrency(i.suggested_amount) : "—" }}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="doc.docstatus === 1" class="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
        <h2 class="mb-2 font-semibold text-gray-900">26AS / AIS reconcile</h2>
        <p class="mb-2 text-xs text-gray-500">
          Upload a JSON extract with a <code>tds</code> / <code>credits</code> array
          (<code>{ deductor_name, tan, section, tds }</code>.
        </p>
        <input type="file" accept="application/json,.json" class="text-sm" @change="on26asFile" />
        <div v-if="recon" class="mt-3 space-y-1">
          <p>
            Status <strong>{{ recon.summary.status }}</strong> —
            books {{ formatCurrency(recon.summary.books_tds_credit) }} vs portal
            {{ formatCurrency(recon.summary.portal_tds_credit) }}
            (diff {{ formatCurrency(recon.summary.difference) }})
          </p>
        </div>
      </section>
    </template>

    <!-- List -->
    <section v-else class="rounded-lg border border-gray-200 bg-white shadow-sm overflow-hidden">
      <table class="w-full text-left text-sm">
        <thead class="bg-gray-50 text-xs text-gray-500">
          <tr>
            <th class="px-3 py-2">Name</th>
            <th class="px-3 py-2">AY</th>
            <th class="px-3 py-2">Period</th>
            <th class="px-3 py-2 text-right">Taxable</th>
            <th class="px-3 py-2 text-right">Tax</th>
            <th class="px-3 py-2 text-right">Payable</th>
            <th class="px-3 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in list"
            :key="row.id"
            class="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
            @click="router.push({ name: 'income-tax-detail', params: { id: row.id } })"
          >
            <td class="px-3 py-2 font-mono">{{ row.name }}</td>
            <td class="px-3 py-2">{{ row.assessment_year }}</td>
            <td class="px-3 py-2">{{ formatDate(row.from_date) }} – {{ formatDate(row.to_date) }}</td>
            <td class="px-3 py-2 text-right">{{ formatCurrency(row.taxable_income) }}</td>
            <td class="px-3 py-2 text-right">{{ formatCurrency(row.total_tax) }}</td>
            <td class="px-3 py-2 text-right">{{ formatCurrency(row.tax_payable) }}</td>
            <td class="px-3 py-2">{{ row.status }}</td>
          </tr>
          <tr v-if="!loading && !list.length">
            <td colspan="7" class="px-3 py-8 text-center text-gray-400">
              No computations yet —
              <RouterLink class="text-blue-600 hover:underline" :to="{ name: 'income-tax-new' }">
                create one
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
