<script setup lang="ts">
// GST Returns — GSTR-1 (outward supplies) and GSTR-3B (summary), computed read-only
// from submitted invoices for a filing period. GSTR-1 can be exported to the GST
// portal's offline-tool JSON. See India Compliance Phase 2.
import { computed, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatCurrency, formatDate, formatNumber } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type {
  Gstr1Report,
  Gstr3bReport,
} from "@/types/compliance";

type Tab = "gstr-1" | "gstr-3b";

const tab = ref<Tab>("gstr-1");
// default to the current month
const now = new Date();
const period = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const gstr1 = ref<Gstr1Report | null>(null);
const gstr3b = ref<Gstr3bReport | null>(null);

const range = computed(() => {
  const [y, m] = period.value.split("-").map(Number);
  const from = `${y}-${String(m).padStart(2, "0")}-01`;
  const to = new Date(y, m, 0).toISOString().slice(0, 10); // last day of month
  return { from_date: from, to_date: to };
});

async function run(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    if (tab.value === "gstr-1") {
      gstr1.value = (await api.get<Gstr1Report>("/gst-returns/gstr-1", { params: range.value })).data;
    } else {
      gstr3b.value = (await api.get<Gstr3bReport>("/gst-returns/gstr-3b", { params: range.value })).data;
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

function select(t: Tab): void {
  tab.value = t;
  run();
}

async function downloadJson(): Promise<void> {
  try {
    const data = (await api.get("/gst-returns/gstr-1/json", { params: range.value })).data;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `GSTR1_${(data as { fp?: string }).fp ?? period.value}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

onMounted(run);
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-end justify-between gap-4">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">GST Returns</h1>
        <p class="text-sm text-gray-500">
          GSTR-1 (outward supplies) and GSTR-3B (summary) for a filing period, from submitted invoices.
        </p>
      </div>
      <div class="flex items-end gap-2">
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">Period</span>
          <input v-model="period" type="month" class="form-input" @change="run" />
        </label>
        <button class="btn-primary" :disabled="loading" @click="run">
          {{ loading ? "Loading…" : "Refresh" }}
        </button>
      </div>
    </div>

    <div class="flex gap-1 border-b border-gray-200">
      <button
        v-for="t in (['gstr-1', 'gstr-3b'] as Tab[])"
        :key="t"
        class="-mb-px border-b-2 px-4 py-2 text-sm font-medium"
        :class="tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'"
        @click="select(t)"
      >
        {{ t === "gstr-1" ? "GSTR-1" : "GSTR-3B" }}
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
        <button class="btn-secondary ml-auto" @click="downloadJson">Download portal JSON</button>
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
</style>
