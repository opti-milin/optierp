<script setup lang="ts">
// TDS Returns — Form 26Q (quarterly TDS on non-salary payments) and Form 16A (the
// deductee's TDS certificate), computed read-only from submitted Purchase Invoices
// that withheld tax. See India Compliance Phase 6.2.
import { onMounted, ref } from "vue";
import { api } from "@/api/client";
import { formatCurrency, formatDate, formatNumber } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { Form16A, Tds26qReport, Tds26qRow } from "@/types/compliance";

// default to the current quarter
function quarterRange(): { from: string; to: string } {
  const now = new Date();
  const q = Math.floor(now.getMonth() / 3);
  const from = new Date(now.getFullYear(), q * 3, 1);
  const to = new Date(now.getFullYear(), q * 3 + 3, 0);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(to) };
}
const qr = quarterRange();
const fromDate = ref(qr.from);
const toDate = ref(qr.to);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const report = ref<Tds26qReport | null>(null);
const cert = ref<Form16A | null>(null);

async function run(): Promise<void> {
  loading.value = true;
  error.value = null;
  cert.value = null;
  try {
    report.value = (
      await api.get<Tds26qReport>("/tds-returns/26q", {
        params: { from_date: fromDate.value, to_date: toDate.value },
      })
    ).data;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function form16a(row: Tds26qRow): Promise<void> {
  if (!row.supplier_id) return;
  error.value = null;
  try {
    cert.value = (
      await api.get<Form16A>("/tds-returns/16a", {
        params: { supplier_id: row.supplier_id, from_date: fromDate.value, to_date: toDate.value },
      })
    ).data;
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

onMounted(run);
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">TDS Returns</h1>
        <p class="text-sm text-gray-500">
          Form 26Q (TDS on non-salary payments) and Form 16A certificates, from purchase invoices that
          withheld tax.
        </p>
      </div>
      <div class="flex items-end gap-2">
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">From</span>
          <input v-model="fromDate" type="date" class="form-input" />
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">To</span>
          <input v-model="toDate" type="date" class="form-input" />
        </label>
        <button class="btn-primary" :disabled="loading" @click="run">
          {{ loading ? "Loading…" : "Run" }}
        </button>
      </div>
    </div>

    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <template v-if="report">
      <div class="flex flex-wrap items-center gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm">
        <span>Deductor <strong>{{ report.deductor_name || "—" }}</strong></span>
        <span>GSTIN <strong class="font-mono">{{ report.deductor_gstin || "—" }}</strong></span>
        <span>TAN <strong>{{ report.deductor_tan || "— (add on the return)" }}</strong></span>
        <span class="ml-auto">Deductees <strong>{{ report.summary.deductee_count }}</strong></span>
        <span>Base <strong>{{ formatCurrency(report.summary.total_base) }}</strong></span>
        <span>TDS <strong>{{ formatCurrency(report.summary.total_tds) }}</strong></span>
      </div>

      <section>
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Form 26Q — deductee × section</h2>
        <table class="report-table">
          <thead>
            <tr>
              <th>Section</th><th>Deductee</th><th>PAN</th><th>Nature (category)</th>
              <th class="text-right">Rate %</th><th class="text-right">Amount paid</th>
              <th class="text-right">TDS</th><th class="text-right">Docs</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in report.rows" :key="i">
              <td class="font-medium">{{ row.section || "—" }}</td>
              <td>{{ row.deductee_name }}</td>
              <td class="font-mono text-xs">{{ row.pan || "—" }}</td>
              <td class="max-w-xs truncate text-xs">{{ row.category }}</td>
              <td class="text-right">{{ formatNumber(row.rate) }}</td>
              <td class="text-right">{{ formatCurrency(row.total_base) }}</td>
              <td class="text-right">{{ formatCurrency(row.total_tds) }}</td>
              <td class="text-right">{{ row.doc_count }}</td>
              <td class="text-right">
                <button class="text-xs text-blue-600 hover:underline" @click="form16a(row)">Form 16A</button>
              </td>
            </tr>
            <tr v-if="!report.rows.length">
              <td colspan="9" class="py-3 text-center text-gray-400">
                No TDS withheld in this period — tag a purchase invoice with a TDS category to see it here.
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Form 16A certificate -->
      <section v-if="cert" class="rounded-lg border border-gray-300 bg-white p-6 shadow-sm">
        <div class="mb-4 flex items-start justify-between">
          <div>
            <h2 class="text-base font-semibold text-gray-900">Form 16A — TDS Certificate</h2>
            <p class="text-xs text-gray-500">
              {{ formatDate(cert.from_date) }} to {{ formatDate(cert.to_date) }}
            </p>
          </div>
          <button class="text-xs text-gray-400 hover:text-gray-600" @click="cert = null">✕ close</button>
        </div>
        <dl class="mb-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt class="text-gray-500">Deductor</dt>
            <dd class="font-medium">{{ cert.deductor_name }} <span class="font-mono text-xs">{{ cert.deductor_gstin }}</span></dd>
          </div>
          <div>
            <dt class="text-gray-500">Deductee</dt>
            <dd class="font-medium">{{ cert.deductee_name }} · PAN <span class="font-mono">{{ cert.deductee_pan || "—" }}</span></dd>
          </div>
        </dl>
        <table class="report-table">
          <thead>
            <tr>
              <th>Section</th><th class="text-right">Rate %</th><th class="text-right">Amount paid</th>
              <th class="text-right">TDS deducted</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(sec, i) in cert.sections" :key="i">
              <td>{{ sec.section }}</td>
              <td class="text-right">{{ formatNumber(sec.rate) }}</td>
              <td class="text-right">{{ formatCurrency(sec.total_base) }}</td>
              <td class="text-right">{{ formatCurrency(sec.total_tds) }}</td>
            </tr>
            <tr class="border-t-2 font-semibold">
              <td colspan="2">Total</td>
              <td class="text-right">{{ formatCurrency(cert.total_base) }}</td>
              <td class="text-right">{{ formatCurrency(cert.total_tds) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
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
