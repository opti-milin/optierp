<script setup lang="ts">
// GST Returns — GSTR-1 (outward supplies) and GSTR-3B (summary), computed read-only
// from submitted invoices for a filing period. GSTR-1 can be exported to the GST
// portal's offline-tool JSON. See India Compliance Phase 2.
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatCurrency, formatDate, formatNumber } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type {
  Cmp08Report,
  Gstr1Report,
  Gstr2bReconReport,
  Gstr3bReport,
  Gstr4Report,
  IffReport,
} from "@/types/compliance";

type Tab = "gstr-1" | "gstr-3b" | "gstr-2b" | "iff" | "cmp-08" | "gstr-4";
type PeriodMode = "month" | "quarter" | "annual";
interface TabDesc { key: Tab; label: string; mode: PeriodMode }

// A single source of truth for tabs — the period picker, the endpoint and the rendered
// section all key off the active descriptor (no parallel switch statements to keep in sync).
const REGULAR_TABS: TabDesc[] = [
  { key: "gstr-1", label: "GSTR-1", mode: "month" },
  { key: "gstr-3b", label: "GSTR-3B", mode: "month" },
  { key: "gstr-2b", label: "GSTR-2B recon", mode: "month" },
];
const IFF_TAB: TabDesc = { key: "iff", label: "IFF", mode: "month" };
const COMPOSITION_TABS: TabDesc[] = [
  { key: "cmp-08", label: "CMP-08", mode: "quarter" },
  { key: "gstr-4", label: "GSTR-4", mode: "annual" },
];

// Composition dealers file CMP-08/GSTR-4 (not GSTR-1/3B); QRMP filers also get the IFF.
const isComposition = ref(false);
const isQrmp = ref(false);
const tabs = computed<TabDesc[]>(() => {
  if (isComposition.value) return COMPOSITION_TABS;
  return isQrmp.value ? [REGULAR_TABS[0], REGULAR_TABS[1], IFF_TAB, REGULAR_TABS[2]] : REGULAR_TABS;
});

const tab = ref<Tab>("gstr-1");
const activeDesc = computed<TabDesc>(() => tabs.value.find((t) => t.key === tab.value) ?? tabs.value[0]);
// QRMP filers furnish GSTR-1/3B quarterly (the same endpoints over a quarter range).
const effectiveMode = computed<PeriodMode>(() =>
  (tab.value === "gstr-1" || tab.value === "gstr-3b") && isQrmp.value ? "quarter" : activeDesc.value.mode,
);

const now = new Date();
const month = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
function fyQuarter(d: Date): 1 | 2 | 3 | 4 {
  const m = d.getMonth() + 1;
  return (m >= 4 && m <= 6 ? 1 : m >= 7 && m <= 9 ? 2 : m >= 10 && m <= 12 ? 3 : 4) as 1 | 2 | 3 | 4;
}
const fyYear = ref(now.getMonth() + 1 >= 4 ? now.getFullYear() : now.getFullYear() - 1); // FY start year
const quarter = ref<1 | 2 | 3 | 4>(fyQuarter(now));

const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const gstr1 = ref<Gstr1Report | null>(null);
const gstr3b = ref<Gstr3bReport | null>(null);
const iff = ref<IffReport | null>(null);
const cmp08 = ref<Cmp08Report | null>(null);
const gstr4 = ref<Gstr4Report | null>(null);
const recon = ref<Gstr2bReconReport | null>(null);
const recon2bFileName = ref("");
// remember the last uploaded 2B so Refresh / a period change can re-reconcile without re-uploading
const lastGstr2b = ref<unknown | null>(null);

const range = computed(() => {
  if (effectiveMode.value === "annual") {
    return { from_date: `${fyYear.value}-04-01`, to_date: `${fyYear.value + 1}-03-31` };
  }
  if (effectiveMode.value === "quarter") {
    const startMonth = { 1: 4, 2: 7, 3: 10, 4: 1 }[quarter.value];
    const y = quarter.value === 4 ? fyYear.value + 1 : fyYear.value;
    const from = `${y}-${String(startMonth).padStart(2, "0")}-01`;
    const to = new Date(y, startMonth + 2, 0).toISOString().slice(0, 10); // last day of the quarter's 3rd month
    return { from_date: from, to_date: to };
  }
  const [y, m] = month.value.split("-").map(Number);
  return { from_date: `${y}-${String(m).padStart(2, "0")}-01`, to_date: new Date(y, m, 0).toISOString().slice(0, 10) };
});

// Human label for the current period (used in the GSTR-2B copy).
const periodLabel = computed(() => {
  if (effectiveMode.value === "annual") return `FY ${fyYear.value}-${String((fyYear.value + 1) % 100).padStart(2, "0")}`;
  if (effectiveMode.value === "quarter") return `Q${quarter.value}-${fyYear.value}`;
  return month.value;
});

async function loadSettings(): Promise<void> {
  try {
    const s = (await api.get("/gst-settings")).data as { registration_type: string; filing_cadence: string };
    isComposition.value = s.registration_type === "Composition";
    isQrmp.value = s.filing_cadence === "QRMP";
    if (!tabs.value.some((t) => t.key === tab.value)) tab.value = tabs.value[0].key;
  } catch {
    /* fall back to Regular/Monthly tabs */
  }
}

async function run(): Promise<void> {
  // On the GSTR-2B tab, Refresh / a period change re-reconciles the last uploaded 2B
  // (the recon needs an uploaded file; do nothing until one is provided).
  if (tab.value === "gstr-2b") {
    if (lastGstr2b.value) await runRecon(lastGstr2b.value);
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    const params = range.value;
    if (tab.value === "gstr-1") gstr1.value = (await api.get<Gstr1Report>("/gst-returns/gstr-1", { params })).data;
    else if (tab.value === "gstr-3b") gstr3b.value = (await api.get<Gstr3bReport>("/gst-returns/gstr-3b", { params })).data;
    else if (tab.value === "iff") iff.value = (await api.get<IffReport>("/gst-returns/iff", { params })).data;
    else if (tab.value === "cmp-08") cmp08.value = (await api.get<Cmp08Report>("/gst-returns/cmp-08", { params })).data;
    else if (tab.value === "gstr-4") gstr4.value = (await api.get<Gstr4Report>("/gst-returns/gstr-4", { params })).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function runRecon(gstr2b: unknown): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    recon.value = (
      await api.post<Gstr2bReconReport>("/gst-returns/gstr-2b/reconcile", {
        ...range.value,
        gstr2b,
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

// GSTR-2B reconciliation: upload the portal 2B JSON → match against the purchase register.
async function reconcile2b(ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  recon2bFileName.value = file.name;
  try {
    const gstr2b = JSON.parse(await file.text());
    lastGstr2b.value = gstr2b;
    await runRecon(gstr2b);
  } catch (e) {
    error.value =
      e instanceof SyntaxError
        ? ({ detail: "That file isn't valid JSON — upload the GSTR-2B JSON downloaded from the portal." } as ErrorEnvelope)
        : (e as ErrorEnvelope);
  } finally {
    input.value = ""; // allow re-uploading the same file
  }
}

function reconStatusClass(status: string): string {
  return (
    {
      Matched: "text-green-700",
      Mismatch: "text-red-600 font-medium",
      "Only in Books": "text-amber-600",
      "Only in 2B": "text-blue-600",
    }[status] || ""
  );
}

function select(t: Tab): void {
  tab.value = t;
  run();
}

async function downloadJson(kind: "gstr-1" | "iff"): Promise<void> {
  try {
    const data = (await api.get(`/gst-returns/${kind}/json`, { params: range.value })).data;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${kind === "iff" ? "IFF" : "GSTR1"}_${(data as { fp?: string }).fp ?? periodLabel.value}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

onMounted(async () => {
  await loadSettings();
  await run();
});
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-end justify-between gap-4">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">GST Returns</h1>
        <p class="text-sm text-gray-500">
          <template v-if="isComposition">
            Composition scheme — CMP-08 (quarterly) and GSTR-4 (annual), from submitted documents.
          </template>
          <template v-else>
            GSTR-1 (outward supplies) and GSTR-3B (summary)<template v-if="isQrmp"> — filed quarterly under QRMP, with the monthly IFF</template>, from submitted invoices.
          </template>
        </p>
      </div>
      <div class="flex items-end gap-2">
        <label v-if="effectiveMode === 'month'" class="text-sm">
          <span class="mb-1 block text-gray-500">Month</span>
          <input v-model="month" type="month" class="form-input" @change="run" />
        </label>
        <template v-else-if="effectiveMode === 'quarter'">
          <label class="text-sm">
            <span class="mb-1 block text-gray-500">Quarter</span>
            <select v-model.number="quarter" class="form-input" @change="run">
              <option :value="1">Q1 (Apr–Jun)</option>
              <option :value="2">Q2 (Jul–Sep)</option>
              <option :value="3">Q3 (Oct–Dec)</option>
              <option :value="4">Q4 (Jan–Mar)</option>
            </select>
          </label>
          <label class="text-sm">
            <span class="mb-1 block text-gray-500">FY start</span>
            <input v-model.number="fyYear" type="number" class="form-input w-24" @change="run" />
          </label>
        </template>
        <label v-else class="text-sm">
          <span class="mb-1 block text-gray-500">FY start</span>
          <input v-model.number="fyYear" type="number" class="form-input w-24" @change="run" />
        </label>
        <button class="btn-primary" :disabled="loading" @click="run">
          {{ loading ? "Loading…" : "Refresh" }}
        </button>
      </div>
    </div>

    <div class="flex gap-1 border-b border-gray-200">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="-mb-px border-b-2 px-4 py-2 text-sm font-medium"
        :class="tab === t.key ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'"
        @click="select(t.key)"
      >
        {{ t.label }}
      </button>
    </div>

    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <!-- ===================== GSTR-1 ===================== -->
    <div v-if="tab === 'gstr-1' && gstr1" class="space-y-6">
      <div class="flex flex-wrap items-center gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm">
        <span>GSTIN <strong class="font-mono">{{ gstr1.gstin || "—" }}</strong></span>
        <span>Period <strong>{{ gstr1.filing_period }}</strong></span>
        <span>Taxable <strong>{{ formatCurrency(gstr1.totals.taxable_value) }}</strong></span>
        <span>CGST <strong>{{ formatCurrency(gstr1.totals.cgst) }}</strong></span>
        <span>SGST <strong>{{ formatCurrency(gstr1.totals.sgst) }}</strong></span>
        <span>IGST <strong>{{ formatCurrency(gstr1.totals.igst) }}</strong></span>
        <span>Invoices <strong>{{ gstr1.totals.invoice_count }}</strong></span>
        <button class="btn-secondary ml-auto" @click="downloadJson('gstr-1')">Download portal JSON</button>
      </div>

      <!-- B2B -->
      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">B2B — registered recipients</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>GSTIN</th><th>Party</th><th>Invoice</th><th>Date</th><th>POS</th>
              <th class="text-right">Rate %</th><th class="text-right">Taxable</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="blk in gstr1.b2b" :key="blk.gstin">
              <tr v-for="inv in blk.invoices" :key="inv.invoice_id">
                <td class="font-mono text-xs">{{ blk.gstin }}</td>
                <td>{{ blk.party_name }}</td>
                <td>{{ inv.name }}</td>
                <td>{{ formatDate(inv.posting_date) }}</td>
                <td class="text-xs">{{ inv.place_of_supply }}</td>
                <td class="text-right">{{ formatNumber(inv.rate) }}</td>
                <td class="text-right">{{ formatCurrency(inv.taxable_value) }}</td>
                <td class="text-right">{{ formatCurrency(inv.cgst) }}</td>
                <td class="text-right">{{ formatCurrency(inv.sgst) }}</td>
                <td class="text-right">{{ formatCurrency(inv.igst) }}</td>
              </tr>
            </template>
            <tr v-if="!gstr1.b2b.length"><td colspan="10" class="py-3 text-center text-gray-400">No B2B supplies</td></tr>
          </tbody>
        </table>
      </section>

      <!-- B2C Large -->
      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">B2C Large — inter-state, above ₹1,00,000</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Invoice</th><th>Date</th><th>POS</th><th class="text-right">Rate %</th>
              <th class="text-right">Taxable</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="inv in gstr1.b2cl" :key="inv.invoice_id">
              <td>{{ inv.name }}</td>
              <td>{{ formatDate(inv.posting_date) }}</td>
              <td class="text-xs">{{ inv.place_of_supply }}</td>
              <td class="text-right">{{ formatNumber(inv.rate) }}</td>
              <td class="text-right">{{ formatCurrency(inv.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(inv.igst) }}</td>
            </tr>
            <tr v-if="!gstr1.b2cl.length"><td colspan="6" class="py-3 text-center text-gray-400">None</td></tr>
          </tbody>
        </table>
      </section>

      <!-- B2C Small -->
      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">B2C Small — consolidated</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>POS</th><th>Type</th><th class="text-right">Rate %</th><th class="text-right">Taxable</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr1.b2cs" :key="i">
              <td class="text-xs">{{ row.place_of_supply }}</td>
              <td>{{ row.supply_type }}</td>
              <td class="text-right">{{ formatNumber(row.rate) }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
            </tr>
            <tr v-if="!gstr1.b2cs.length"><td colspan="7" class="py-3 text-center text-gray-400">None</td></tr>
          </tbody>
        </table>
      </section>

      <!-- Credit/Debit notes -->
      <section v-if="gstr1.cdnr.length || gstr1.cdnur.length">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Credit / Debit notes</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Note</th><th>Date</th><th>Recipient</th><th>POS</th>
              <th class="text-right">Taxable</th><th class="text-right">CGST</th>
              <th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="inv in [...gstr1.cdnr, ...gstr1.cdnur]" :key="inv.invoice_id">
              <td>{{ inv.name }}</td>
              <td>{{ formatDate(inv.posting_date) }}</td>
              <td class="font-mono text-xs">{{ inv.counterparty_gstin || "Unregistered" }}</td>
              <td class="text-xs">{{ inv.place_of_supply }}</td>
              <td class="text-right">{{ formatCurrency(inv.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(inv.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(inv.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(inv.igst) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- HSN summary -->
      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">HSN / SAC summary</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>HSN/SAC</th><th>Description</th><th>UQC</th><th class="text-right">Qty</th>
              <th class="text-right">Rate %</th><th class="text-right">Taxable</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr1.hsn" :key="i">
              <td class="font-mono text-xs">{{ row.hsn_code || "—" }}</td>
              <td class="max-w-xs truncate">{{ row.description }}</td>
              <td>{{ row.uqc }}</td>
              <td class="text-right">{{ formatNumber(row.qty) }}</td>
              <td class="text-right">{{ formatNumber(row.rate) }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
            </tr>
            <tr v-if="!gstr1.hsn.length"><td colspan="9" class="py-3 text-center text-gray-400">None</td></tr>
          </tbody>
        </table>
      </section>

      <!-- Document summary -->
      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Documents issued</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Nature</th><th>From</th><th>To</th><th class="text-right">Total</th>
              <th class="text-right">Cancelled</th><th class="text-right">Net issued</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(d, i) in gstr1.docs" :key="i">
              <td>{{ d.nature }}</td>
              <td>{{ d.from_no }}</td>
              <td>{{ d.to_no }}</td>
              <td class="text-right">{{ d.total_count }}</td>
              <td class="text-right">{{ d.cancelled }}</td>
              <td class="text-right">{{ d.net_issued }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Table 11 — advances (received / adjusted) -->
      <section v-if="gstr1.advances.length || gstr1.advances_adjusted.length">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">11 — Tax on advances (received / adjusted)</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Part</th><th>POS</th><th>Type</th><th class="text-right">Rate %</th>
              <th class="text-right">Taxable</th><th class="text-right">CGST</th>
              <th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr1.advances" :key="`a${i}`">
              <td>11A received</td>
              <td class="text-xs">{{ row.place_of_supply }}</td>
              <td>{{ row.supply_type }}</td>
              <td class="text-right">{{ formatNumber(row.rate) }}</td>
              <td class="text-right">{{ formatCurrency(row.gross_advance) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
            </tr>
            <tr v-for="(row, i) in gstr1.advances_adjusted" :key="`b${i}`" class="text-gray-500">
              <td>11B adjusted</td>
              <td class="text-xs">{{ row.place_of_supply }}</td>
              <td>{{ row.supply_type }}</td>
              <td class="text-right">{{ formatNumber(row.rate) }}</td>
              <td class="text-right">−{{ formatCurrency(row.gross_advance) }}</td>
              <td class="text-right">−{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">−{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">−{{ formatCurrency(row.igst) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Table 14 — supplies through e-commerce operators (u/s 52) -->
      <section v-if="gstr1.eco.length">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">14 — Supplies through e-commerce operators (TCS u/s 52)</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Operator GSTIN</th><th class="text-right">Invoices</th><th class="text-right">Taxable</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr1.eco" :key="i">
              <td class="font-mono text-xs">{{ row.ecommerce_gstin }}</td>
              <td class="text-right">{{ row.invoice_count }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- ===================== GSTR-3B ===================== -->
    <div v-if="tab === 'gstr-3b' && gstr3b" class="space-y-6">
      <div class="flex flex-wrap items-center gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm">
        <span>GSTIN <strong class="font-mono">{{ gstr3b.gstin || "—" }}</strong></span>
        <span>Period <strong>{{ gstr3b.filing_period }}</strong></span>
      </div>

      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">3.1 — Details of outward supplies and inward reverse-charge supplies</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Nature of supply</th><th class="text-right">Taxable value</th>
              <th class="text-right">IGST</th><th class="text-right">CGST</th>
              <th class="text-right">SGST</th><th class="text-right">Cess</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr3b.outward" :key="i">
              <td>{{ row.label }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cess) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">3.2 — Inter-state supplies to unregistered persons</h2>
        <table class="report-table">
          <thead>
            <tr><th>Place of supply</th><th class="text-right">Taxable value</th><th class="text-right">IGST</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr3b.inter_state_unreg" :key="i">
              <td>{{ row.place_of_supply }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
            </tr>
            <tr v-if="!gstr3b.inter_state_unreg.length"><td colspan="3" class="py-3 text-center text-gray-400">None</td></tr>
          </tbody>
        </table>
      </section>

      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">4 — Eligible ITC</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Details</th><th class="text-right">IGST</th><th class="text-right">CGST</th>
              <th class="text-right">SGST</th><th class="text-right">Cess</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr3b.itc" :key="i">
              <td>{{ row.label }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cess) }}</td>
            </tr>
            <tr class="border-t-2 font-semibold">
              <td>{{ gstr3b.net_tax_payable.label }}</td>
              <td class="text-right">{{ formatCurrency(gstr3b.net_tax_payable.igst) }}</td>
              <td class="text-right">{{ formatCurrency(gstr3b.net_tax_payable.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(gstr3b.net_tax_payable.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(gstr3b.net_tax_payable.cess) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- ===================== GSTR-2B reconciliation ===================== -->
    <div v-if="tab === 'gstr-2b'" class="space-y-5">
      <div class="rounded-lg border border-gray-200 bg-white p-4 text-sm">
        <p class="mb-2 text-gray-600">
          Reconcile your purchase register for <strong>{{ periodLabel }}</strong> against the GST portal's
          <strong>GSTR-2B</strong> — so you only claim Input Tax Credit that your suppliers actually filed.
          Download the 2B JSON from the portal and upload it here.
        </p>
        <label class="inline-flex cursor-pointer items-center gap-2">
          <span class="btn-primary">Upload GSTR-2B JSON</span>
          <input type="file" accept=".json,application/json" class="hidden" @change="reconcile2b" />
          <span v-if="recon2bFileName" class="text-xs text-gray-500">{{ recon2bFileName }}</span>
        </label>
      </div>

      <template v-if="recon">
        <!-- summary cards -->
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div class="rounded-lg border border-green-200 bg-green-50 p-3">
            <div class="text-xs text-green-700">Matched</div>
            <div class="text-xl font-semibold text-green-800">{{ recon.summary.matched }}</div>
            <div class="text-xs text-green-600">ITC {{ formatCurrency(recon.summary.matched_itc) }}</div>
          </div>
          <div class="rounded-lg border border-red-200 bg-red-50 p-3">
            <div class="text-xs text-red-700">Mismatch</div>
            <div class="text-xl font-semibold text-red-800">{{ recon.summary.mismatch }}</div>
            <div class="text-xs text-red-600">values differ</div>
          </div>
          <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
            <div class="text-xs text-amber-700">Only in books (ITC at risk)</div>
            <div class="text-xl font-semibold text-amber-800">{{ recon.summary.only_in_books }}</div>
            <div class="text-xs text-amber-600">{{ formatCurrency(recon.summary.at_risk_itc) }}</div>
          </div>
          <div class="rounded-lg border border-blue-200 bg-blue-50 p-3">
            <div class="text-xs text-blue-700">Only in 2B (missing bill)</div>
            <div class="text-xl font-semibold text-blue-800">{{ recon.summary.only_in_2b }}</div>
            <div class="text-xs text-blue-600">supplier filed</div>
          </div>
        </div>
        <div class="flex flex-wrap gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm">
          <span>ITC per books <strong>{{ formatCurrency(recon.summary.books_itc) }}</strong></span>
          <span>ITC per 2B <strong>{{ formatCurrency(recon.summary.portal_itc) }}</strong></span>
          <span>Books docs <strong>{{ recon.summary.books_count }}</strong></span>
          <span>2B docs <strong>{{ recon.summary.portal_count }}</strong></span>
        </div>

        <!-- detail table -->
        <table class="report-table">
          <thead>
            <tr>
              <th>Status</th><th>Supplier GSTIN</th><th>Supplier</th><th>Invoice</th><th>Date</th>
              <th>Our doc</th>
              <th class="text-right">Books ITC</th><th class="text-right">2B ITC</th>
              <th class="text-right">Diff</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in recon.rows" :key="i">
              <td :class="reconStatusClass(row.status)">{{ row.status }}</td>
              <td class="font-mono text-xs">{{ row.supplier_gstin }}</td>
              <td>{{ row.supplier_name || "—" }}</td>
              <td>{{ row.invoice_no || "—" }}</td>
              <td>{{ row.invoice_date ? formatDate(row.invoice_date) : "—" }}</td>
              <td>{{ row.books_ref || "—" }}</td>
              <td class="text-right">{{ row.books_tax != null ? formatCurrency(row.books_tax) : "—" }}</td>
              <td class="text-right">{{ row.portal_tax != null ? formatCurrency(row.portal_tax) : "—" }}</td>
              <td class="text-right" :class="reconStatusClass(row.status)">
                {{ row.tax_diff != null ? formatCurrency(row.tax_diff) : "—" }}
              </td>
            </tr>
            <tr v-if="!recon.rows.length"><td colspan="9" class="py-3 text-center text-gray-400">Nothing to reconcile</td></tr>
          </tbody>
        </table>
      </template>
    </div>

    <!-- ===================== IFF (QRMP) ===================== -->
    <div v-if="tab === 'iff' && iff" class="space-y-6">
      <div class="flex flex-wrap items-center gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm">
        <span>GSTIN <strong class="font-mono">{{ iff.gstin || "—" }}</strong></span>
        <span>Period <strong>{{ iff.filing_period }}</strong></span>
        <span>Taxable <strong>{{ formatCurrency(iff.totals.taxable_value) }}</strong></span>
        <span>CGST <strong>{{ formatCurrency(iff.totals.cgst) }}</strong></span>
        <span>SGST <strong>{{ formatCurrency(iff.totals.sgst) }}</strong></span>
        <span>IGST <strong>{{ formatCurrency(iff.totals.igst) }}</strong></span>
        <span>Invoices <strong>{{ iff.totals.invoice_count }}</strong></span>
        <button class="btn-secondary ml-auto" @click="downloadJson('iff')">Download portal JSON</button>
      </div>
      <p class="text-xs text-gray-500">
        The IFF furnishes B2B, B2C-Large and credit/debit notes for the first two months of a quarter;
        B2C-Small, HSN and the document summary are furnished with the quarterly GSTR-1.
      </p>

      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">B2B — registered recipients</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>GSTIN</th><th>Party</th><th>Invoice</th><th>Date</th><th>POS</th>
              <th class="text-right">Rate %</th><th class="text-right">Taxable</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="blk in iff.b2b" :key="blk.gstin">
              <tr v-for="inv in blk.invoices" :key="inv.invoice_id">
                <td class="font-mono text-xs">{{ blk.gstin }}</td>
                <td>{{ blk.party_name }}</td>
                <td>{{ inv.name }}</td>
                <td>{{ formatDate(inv.posting_date) }}</td>
                <td class="text-xs">{{ inv.place_of_supply }}</td>
                <td class="text-right">{{ formatNumber(inv.rate) }}</td>
                <td class="text-right">{{ formatCurrency(inv.taxable_value) }}</td>
                <td class="text-right">{{ formatCurrency(inv.cgst) }}</td>
                <td class="text-right">{{ formatCurrency(inv.sgst) }}</td>
                <td class="text-right">{{ formatCurrency(inv.igst) }}</td>
              </tr>
            </template>
            <tr v-if="!iff.b2b.length"><td colspan="10" class="py-3 text-center text-gray-400">No B2B supplies</td></tr>
          </tbody>
        </table>
      </section>

      <section v-if="iff.b2cl.length || iff.cdnr.length || iff.cdnur.length">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">B2C-Large &amp; credit / debit notes</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Document</th><th>Date</th><th>POS</th><th class="text-right">Taxable</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">IGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="inv in [...iff.b2cl, ...iff.cdnr, ...iff.cdnur]" :key="inv.invoice_id">
              <td>{{ inv.name }}</td>
              <td>{{ formatDate(inv.posting_date) }}</td>
              <td class="text-xs">{{ inv.place_of_supply }}</td>
              <td class="text-right">{{ formatCurrency(inv.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(inv.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(inv.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(inv.igst) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- ===================== CMP-08 (composition) ===================== -->
    <div v-if="tab === 'cmp-08' && cmp08" class="space-y-6">
      <div class="flex flex-wrap items-center gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm">
        <span>GSTIN <strong class="font-mono">{{ cmp08.gstin || "—" }}</strong></span>
        <span>Quarter <strong>{{ cmp08.filing_period }}</strong></span>
        <span>Category <strong>{{ cmp08.composition_category }}</strong></span>
        <span>Rate <strong>{{ formatNumber(cmp08.composition_rate) }}%</strong></span>
        <span class="ml-auto">Tax payable <strong>{{ formatCurrency(cmp08.total_tax) }}</strong></span>
      </div>
      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Table 3 — Summary of self-assessed liability</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Nature</th><th class="text-right">Value</th><th class="text-right">IGST</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">Cess</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in cmp08.rows" :key="i" :class="{ 'border-t-2 font-semibold': i === cmp08.rows.length - 1 }">
              <td>{{ row.label }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cess) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- ===================== GSTR-4 (composition annual) ===================== -->
    <div v-if="tab === 'gstr-4' && gstr4" class="space-y-6">
      <div class="flex flex-wrap items-center gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm">
        <span>GSTIN <strong class="font-mono">{{ gstr4.gstin || "—" }}</strong></span>
        <span>Year <strong>{{ gstr4.filing_period }}</strong></span>
        <span>Category <strong>{{ gstr4.composition_category }}</strong></span>
        <span>Rate <strong>{{ formatNumber(gstr4.composition_rate) }}%</strong></span>
        <span class="ml-auto">Tax payable <strong>{{ formatCurrency(gstr4.total_tax) }}</strong></span>
      </div>
      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Annual summary</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Nature</th><th class="text-right">Value</th><th class="text-right">IGST</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th><th class="text-right">Cess</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr4.rows" :key="i" :class="{ 'border-t-2 font-semibold': i === gstr4.rows.length - 1 }">
              <td>{{ row.label }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.igst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.cess) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
      <section v-if="gstr4.quarters.length">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Quarter-wise turnover &amp; composite tax (CMP-08)</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Quarter</th><th class="text-right">Turnover</th>
              <th class="text-right">CGST</th><th class="text-right">SGST</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in gstr4.quarters" :key="i">
              <td>{{ row.label }}</td>
              <td class="text-right">{{ formatCurrency(row.taxable_value) }}</td>
              <td class="text-right">{{ formatCurrency(row.cgst) }}</td>
              <td class="text-right">{{ formatCurrency(row.sgst) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>

<style scoped>
.report-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}
.report-table th {
  border-bottom: 1px solid #e5e7eb;
  padding: 0.5rem 0.75rem;
  text-align: left;
  font-weight: 500;
  color: #6b7280;
}
.report-table td {
  border-bottom: 1px solid #f3f4f6;
  padding: 0.375rem 0.75rem;
  color: #1f2937;
}
/* numeric columns: right-align the header too, so it lines up with its values
   (the base `th { text-align:left }` rule outspecifies the `text-right` utility) */
.report-table th.text-right,
.report-table td.text-right {
  text-align: right;
}
</style>
