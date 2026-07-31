<script setup lang="ts">
// Financial reports: General Ledger / Trial Balance / P&L / Balance Sheet / AR / AP / Bank Reconciliation.

import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/client";
import { useAccountsStore } from "@/stores/accounts";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { formatCurrency, formatDate, formatNumber } from "@/utils/format";
import type { AccountNode, ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  AgingRow,
  BankReconciliationReport,
  BudgetVarianceRow,
  FiscalYearInfo,
  GeneralLedgerReport,
  CollectionSummaryRow,
  GrossProfitReport,
  ContributionMarginReport,
  PartyLedgerSummaryRow,
  PartyOutstandingSummaryRow,
  RegisterReport,
  ShareBalanceRow,
  ShareLedgerRow,
  StatementRow,
  TrialBalanceRow,
} from "@/types/accounts";

type Tab =
  | "general-ledger"
  | "trial-balance"
  | "profit-loss"
  | "balance-sheet"
  | "receivable"
  | "payable"
  | "bank-recon"
  | "sales-register"
  | "purchase-register"
  | "customer-ledger"
  | "supplier-ledger"
  | "ar-summary"
  | "ap-summary"
  | "collection"
  | "gross-profit"
  | "contribution-margin"
  | "budget-variance"
  | "share-balance"
  | "share-ledger";
const route = useRoute();
const tab = ref<Tab>("trial-balance");

const today = new Date().toISOString().slice(0, 10);
const yearStart = `${new Date().getFullYear()}-01-01`;
const fromDate = ref(yearStart);
const toDate = ref(today);
const asOf = ref(today);

const fiscalYears = ref<FiscalYearInfo[]>([]);
const fiscalYearId = ref("");

const store = useAccountsStore();
const companyCurrency = useCompanyCurrency();
const bankAccountId = ref("");
const bankAccounts = computed(() =>
  store.leafAccounts.filter(
    (a: AccountNode) => a.account_type === "Bank" || a.account_type === "Cash",
  ),
);
const bankRecon = ref<BankReconciliationReport | null>(null);

// General Ledger
const glAccountId = ref("");
const gl = ref<GeneralLedgerReport | null>(null);
const accountName = (id: string): string =>
  store.leafAccounts.find((a: AccountNode) => a.id === id)?.account_name ?? "—";
// running balance per row (opening + cumulative debit - credit)
const glRows = computed(() => {
  if (!gl.value) return [];
  let bal = Number(gl.value.opening_balance);
  return gl.value.entries.map((e) => {
    bal += Number(e.debit) - Number(e.credit);
    return { ...e, balance: bal };
  });
});

const tbRows = ref<TrialBalanceRow[]>([]);
const pl = ref<{ income: StatementRow[]; expenses: StatementRow[]; net_profit: string } | null>(null);
const bs = ref<{
  assets: StatementRow[]; liabilities: StatementRow[]; equity: StatementRow[];
  total_assets: string; total_liabilities: string; total_equity: string;
  provisional_profit_loss: string;
} | null>(null);
const aging = ref<AgingRow[]>([]);
const register = ref<RegisterReport | null>(null);
const ledger = ref<PartyLedgerSummaryRow[]>([]);
const grossProfit = ref<GrossProfitReport | null>(null);
const contributionMargin = ref<ContributionMarginReport | null>(null);
const cmCostCenterId = ref("");
const cmTemplates = ref<Array<{ id: string; label: string; description: string | null }>>([]);
const cmTemplateId = ref("manufacturing");
const cmApplying = ref(false);
const budgetVariance = ref<BudgetVarianceRow[]>([]);
const arSummary = ref<PartyOutstandingSummaryRow[]>([]);
const apSummary = ref<PartyOutstandingSummaryRow[]>([]);
const collection = ref<CollectionSummaryRow[]>([]);
const shareBalance = ref<ShareBalanceRow[]>([]);
const shareLedger = ref<ShareLedgerRow[]>([]);
const error = ref<ErrorEnvelope | null>(null);
const loading = ref(false);

async function run(): Promise<void> {
  // explicit per-tab branching: never fire a report the tab doesn't show
  if ((tab.value === "trial-balance" || tab.value === "budget-variance") && !fiscalYearId.value) return;
  if (tab.value === "bank-recon" && !bankAccountId.value) return;
  loading.value = true;
  error.value = null;
  try {
    if (tab.value === "general-ledger") {
      gl.value = (
        await api.get<GeneralLedgerReport>("/reports/general-ledger", {
          params: {
            from_date: fromDate.value,
            to_date: toDate.value,
            ...(glAccountId.value ? { account_id: glAccountId.value } : {}),
          },
        })
      ).data;
    } else if (tab.value === "trial-balance") {
      tbRows.value = (
        await api.get<TrialBalanceRow[]>("/reports/trial-balance", {
          params: { fiscal_year_id: fiscalYearId.value },
        })
      ).data;
    } else if (tab.value === "profit-loss") {
      pl.value = (
        await api.get("/reports/profit-loss", {
          params: { from_date: fromDate.value, to_date: toDate.value },
        })
      ).data;
    } else if (tab.value === "balance-sheet") {
      bs.value = (await api.get("/reports/balance-sheet", { params: { as_of: asOf.value } })).data;
    } else if (tab.value === "bank-recon") {
      bankRecon.value = (
        await api.get<BankReconciliationReport>("/reports/bank-reconciliation", {
          params: { gl_account_id: bankAccountId.value, as_of: asOf.value },
        })
      ).data;
    } else if (tab.value === "receivable" || tab.value === "payable") {
      const endpoint = tab.value === "receivable" ? "accounts-receivable" : "accounts-payable";
      aging.value = (
        await api.get<AgingRow[]>(`/reports/${endpoint}`, { params: { as_of: asOf.value } })
      ).data;
    } else if (tab.value === "sales-register" || tab.value === "purchase-register") {
      const endpoint = tab.value === "sales-register" ? "sales-register" : "purchase-register";
      register.value = (
        await api.get<RegisterReport>(`/reports/${endpoint}`, {
          params: { from_date: fromDate.value, to_date: toDate.value },
        })
      ).data;
    } else if (tab.value === "customer-ledger" || tab.value === "supplier-ledger") {
      const endpoint = tab.value === "customer-ledger" ? "customer-ledger-summary" : "supplier-ledger-summary";
      ledger.value = (
        await api.get<PartyLedgerSummaryRow[]>(`/reports/${endpoint}`, {
          params: { from_date: fromDate.value, to_date: toDate.value },
        })
      ).data;
    } else if (tab.value === "gross-profit") {
      grossProfit.value = (
        await api.get<GrossProfitReport>("/reports/gross-profit", {
          params: { from_date: fromDate.value, to_date: toDate.value },
        })
      ).data;
    } else if (tab.value === "contribution-margin") {
      contributionMargin.value = (
        await api.get<ContributionMarginReport>("/reports/contribution-margin", {
          params: {
            from_date: fromDate.value,
            to_date: toDate.value,
            ...(cmCostCenterId.value ? { cost_center_id: cmCostCenterId.value } : {}),
          },
        })
      ).data;
    } else if (tab.value === "budget-variance") {
      budgetVariance.value = (
        await api.get<BudgetVarianceRow[]>("/reports/budget-variance", {
          params: { fiscal_year_id: fiscalYearId.value },
        })
      ).data;
    } else if (tab.value === "ar-summary" || tab.value === "ap-summary") {
      const endpoint =
        tab.value === "ar-summary" ? "accounts-receivable-summary" : "accounts-payable-summary";
      const data = (
        await api.get<PartyOutstandingSummaryRow[]>(`/reports/${endpoint}`, {
          params: { as_of: asOf.value },
        })
      ).data;
      if (tab.value === "ar-summary") arSummary.value = data;
      else apSummary.value = data;
    } else if (tab.value === "collection") {
      collection.value = (
        await api.get<CollectionSummaryRow[]>("/reports/collection-summary", {
          params: { from_date: fromDate.value, to_date: toDate.value },
        })
      ).data;
    } else if (tab.value === "share-balance") {
      shareBalance.value = (
        await api.get<ShareBalanceRow[]>("/reports/share-balance", { params: { as_of: asOf.value } })
      ).data;
    } else if (tab.value === "share-ledger") {
      shareLedger.value = (
        await api.get<ShareLedgerRow[]>("/reports/share-ledger", {
          params: { from_date: fromDate.value, to_date: toDate.value },
        })
      ).data;
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

function switchTab(t: Tab): void {
  tab.value = t;
  void run();
}

watch(bankAccountId, () => {
  bankRecon.value = null;
  if (tab.value === "bank-recon") void run();
});
watch(glAccountId, () => {
  if (tab.value === "general-ledger") void run();
});
// honor nav clicks to ?tab=... even when this view is already mounted
watch(
  () => route.query.tab,
  (t) => {
    if (t && tabs.some((x) => x.key === t)) switchTab(t as Tab);
  },
);
watch(fiscalYearId, () => {
  if (tab.value === "trial-balance" || tab.value === "budget-variance") void run();
});

onMounted(async () => {
  // allow deep links like /reports?tab=general-ledger
  const wanted = route.query.tab as string | undefined;
  if (wanted && tabs.some((t) => t.key === wanted)) tab.value = wanted as Tab;
  try {
    const resp = await api.get<ListResponse<FiscalYearInfo>>("/fiscal-years", { params: {} });
    fiscalYears.value = resp.data.items;
    // default the Trial Balance to the current FY and P&L to its start date
    const current = fiscalYears.value[0];
    if (current) {
      fiscalYearId.value = current.id;
      fromDate.value = current.year_start_date;
    }
  } catch {
    fiscalYears.value = [];
  }
  await store.fetchAccounts();
  await store.fetchCostCenters();
  try {
    cmTemplates.value = (await api.get("/contribution-margin/templates")).data;
  } catch {
    cmTemplates.value = [];
  }
  if (bankAccounts.value.length === 1) bankAccountId.value = bankAccounts.value[0].id;
  void run();
});

const tabs: Array<{ key: Tab; label: string }> = [
  { key: "general-ledger", label: "General Ledger" },
  { key: "trial-balance", label: "Trial Balance" },
  { key: "profit-loss", label: "Profit & Loss" },
  { key: "contribution-margin", label: "Contribution Margin" },
  { key: "balance-sheet", label: "Balance Sheet" },
  { key: "receivable", label: "Receivable" },
  { key: "payable", label: "Payable" },
  { key: "ar-summary", label: "AR Summary" },
  { key: "ap-summary", label: "AP Summary" },
  { key: "collection", label: "Collection Period" },
  { key: "bank-recon", label: "Uncleared Items" },
  { key: "sales-register", label: "Sales Register" },
  { key: "purchase-register", label: "Purchase Register" },
  { key: "customer-ledger", label: "Customer Ledger" },
  { key: "supplier-ledger", label: "Supplier Ledger" },
  { key: "gross-profit", label: "Gross Profit" },
  { key: "budget-variance", label: "Budget Variance" },
  { key: "share-balance", label: "Share Balance" },
  { key: "share-ledger", label: "Share Ledger" },
];

async function applyCmTemplate(): Promise<void> {
  if (!cmTemplateId.value) return;
  cmApplying.value = true;
  error.value = null;
  try {
    await api.post("/contribution-margin/apply-template", {
      template_id: cmTemplateId.value,
      overwrite: false,
    });
    if (tab.value === "contribution-margin") await run();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    cmApplying.value = false;
  }
}
</script>

<template>
  <div>
    <h1 class="mb-4 text-xl font-semibold text-gray-900">Financial Reports</h1>

    <div class="mb-4 flex gap-1 rounded-lg bg-gray-100 p-1">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="flex-1 rounded-md px-3 py-1.5 text-sm font-medium"
        :class="tab === t.key ? 'bg-white text-primary shadow-sm' : 'text-gray-600'"
        @click="switchTab(t.key)"
      >
        {{ t.label }}
      </button>
    </div>

    <div class="mb-4 flex items-end gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <template v-if="tab === 'general-ledger'">
        <div><label class="form-label">From</label><input v-model="fromDate" type="date" class="form-input" /></div>
        <div><label class="form-label">To</label><input v-model="toDate" type="date" class="form-input" /></div>
        <div>
          <label class="form-label">Account</label>
          <select v-model="glAccountId" class="form-input w-64">
            <option value="">All accounts</option>
            <option v-for="a in store.leafAccounts" :key="a.id" :value="a.id">{{ a.account_name }}</option>
          </select>
        </div>
      </template>
      <template v-else-if="tab === 'trial-balance' || tab === 'budget-variance'">
        <div>
          <label class="form-label">Fiscal Year</label>
          <select v-model="fiscalYearId" class="form-input w-56">
            <option value="" disabled>Select…</option>
            <option v-for="fy in fiscalYears" :key="fy.id" :value="fy.id">{{ fy.year }}</option>
          </select>
        </div>
      </template>
      <template v-else-if="['profit-loss', 'contribution-margin', 'sales-register', 'purchase-register', 'customer-ledger', 'supplier-ledger', 'gross-profit', 'collection', 'share-ledger'].includes(tab)">
        <div><label class="form-label">From</label><input v-model="fromDate" type="date" class="form-input" /></div>
        <div><label class="form-label">To</label><input v-model="toDate" type="date" class="form-input" /></div>
        <template v-if="tab === 'contribution-margin'">
          <div>
            <label class="form-label">Cost Center</label>
            <select v-model="cmCostCenterId" class="form-input w-56">
              <option value="">All (company)</option>
              <option v-for="opt in store.costCenterOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </select>
          </div>
        </template>
      </template>
      <template v-else-if="tab === 'bank-recon'">
        <div>
          <label class="form-label">Bank/Cash Account</label>
          <select v-model="bankAccountId" class="form-input w-56">
            <option value="" disabled>Select…</option>
            <option v-for="a in bankAccounts" :key="a.id" :value="a.id">{{ a.account_name }}</option>
          </select>
        </div>
        <div><label class="form-label">As of</label><input v-model="asOf" type="date" class="form-input" /></div>
      </template>
      <template v-else>
        <div><label class="form-label">As of</label><input v-model="asOf" type="date" class="form-input" /></div>
      </template>
      <button class="btn-primary" :disabled="loading" @click="run">Run</button>
      <p v-if="error" class="text-sm text-red-600">{{ error.detail }}</p>
    </div>

    <!-- General Ledger -->
    <div v-if="tab === 'general-ledger'">
      <div v-if="gl" class="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="flex justify-end gap-8 text-sm">
          <span>Opening: <strong>{{ formatCurrency(gl.opening_balance, companyCurrency) }}</strong></span>
          <span>Debit: <strong>{{ formatCurrency(gl.total_debit, companyCurrency) }}</strong></span>
          <span>Credit: <strong>{{ formatCurrency(gl.total_credit, companyCurrency) }}</strong></span>
          <span>Closing: <strong>{{ formatCurrency(gl.closing_balance, companyCurrency) }}</strong></span>
        </div>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Date</th><th class="px-4 py-2">Account</th>
              <th class="px-4 py-2">Voucher</th><th class="px-4 py-2">Against</th>
              <th class="px-4 py-2 text-right">Debit</th><th class="px-4 py-2 text-right">Credit</th>
              <th class="px-4 py-2 text-right">Balance</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in glRows" :key="row.id" class="border-t border-gray-100"
                :class="row.is_cancellation ? 'text-gray-400 line-through' : ''">
              <td class="px-4 py-1.5 text-gray-500">{{ formatDate(row.posting_date) }}</td>
              <td class="px-4 py-1.5">{{ accountName(row.account_id) }}</td>
              <td class="px-4 py-1.5">
                <span class="text-gray-500">{{ row.voucher_type }}</span> {{ row.voucher_no }}
              </td>
              <td class="px-4 py-1.5 text-gray-500">{{ row.against ?? "—" }}</td>
              <td class="px-4 py-1.5 text-right">{{ Number(row.debit) ? formatNumber(row.debit) : "" }}</td>
              <td class="px-4 py-1.5 text-right">{{ Number(row.credit) ? formatNumber(row.credit) : "" }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatNumber(row.balance) }}</td>
            </tr>
            <tr v-if="!glRows.length"><td colspan="7" class="px-4 py-8 text-center text-gray-400">
              No ledger entries for this period. Pick a date range (and optionally an account) and Run.
            </td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Trial Balance -->
    <div v-else-if="tab === 'trial-balance'" class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Account</th>
            <th class="px-4 py-2 text-right">Opening Dr</th><th class="px-4 py-2 text-right">Opening Cr</th>
            <th class="px-4 py-2 text-right">Debit</th><th class="px-4 py-2 text-right">Credit</th>
            <th class="px-4 py-2 text-right">Closing Dr</th><th class="px-4 py-2 text-right">Closing Cr</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in tbRows" :key="row.account_id" class="border-t border-gray-100">
            <td class="px-4 py-1.5" :style="{ paddingLeft: `${16 + (row.path.split('.').length - 1) * 14}px` }"
                :class="row.is_group ? 'font-semibold' : ''">{{ row.account_name }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.opening_debit) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.opening_credit) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.debit) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.credit) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.closing_debit) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.closing_credit) }}</td>
          </tr>
          <tr v-if="!tbRows.length"><td colspan="7" class="px-4 py-8 text-center text-gray-400">
            Select a fiscal year and run.
          </td></tr>
        </tbody>
      </table>
    </div>

    <!-- P&L -->
    <div v-else-if="tab === 'profit-loss' && pl" class="grid grid-cols-2 gap-4">
      <div v-for="(rows, section) in { Income: pl.income, Expenses: pl.expenses }" :key="section"
           class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">{{ section }}</h2>
        <div v-for="row in rows" :key="row.account_id ?? row.account_name"
             class="flex justify-between py-0.5 text-sm"
             :style="{ paddingLeft: `${row.indent * 12}px` }"
             :class="row.is_group ? 'font-semibold' : ''">
          <span>{{ row.account_name }}</span><span>{{ formatNumber(row.amount) }}</span>
        </div>
      </div>
      <div class="col-span-2 rounded-lg border border-gray-200 bg-white p-4 text-right shadow-sm">
        <span class="text-sm text-gray-500">Net Profit:</span>
        <span class="ml-2 text-lg font-semibold text-gray-900">{{ formatNumber(pl.net_profit) }}</span>
      </div>
    </div>

    <!-- Contribution Margin -->
    <div v-else-if="tab === 'contribution-margin'" class="space-y-4">
      <div class="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div>
          <label class="form-label">Industry template</label>
          <select v-model="cmTemplateId" class="form-input w-56">
            <option v-for="t in cmTemplates" :key="t.id" :value="t.id">{{ t.label }}</option>
          </select>
        </div>
        <button type="button" class="btn-secondary" :disabled="cmApplying || !cmTemplateId" @click="applyCmTemplate">
          {{ cmApplying ? "Applying…" : "Apply template" }}
        </button>
        <p class="text-xs text-gray-500">
          Tags P&amp;L leaf accounts with CM classes. Does not overwrite existing tags.
          Refine under <router-link class="text-primary underline" to="/chart-of-accounts">Chart of Accounts</router-link>.
        </p>
      </div>
      <div v-if="contributionMargin?.warnings?.length" class="rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
        <p v-for="(w, i) in contributionMargin.warnings" :key="i">{{ w }}</p>
      </div>
      <div v-if="contributionMargin" class="grid gap-4 lg:grid-cols-3">
        <div class="space-y-3 lg:col-span-2">
          <div
            v-for="section in contributionMargin.sections"
            :key="section.cm_class ?? 'unclassified'"
            class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
          >
            <div class="mb-2 flex items-baseline justify-between">
              <h2 class="text-sm font-semibold text-gray-900">{{ section.label }}</h2>
              <span class="text-sm font-medium tabular-nums">
                {{ formatNumber(section.total) }}
                <span v-if="section.pct_of_revenue != null" class="ml-2 text-xs text-gray-500">
                  {{ section.pct_of_revenue }}% of rev
                </span>
              </span>
            </div>
            <div
              v-for="row in section.rows"
              :key="row.account_id ?? row.account_name"
              class="flex justify-between py-0.5 text-sm text-gray-700"
              :style="{ paddingLeft: `${row.indent * 12}px` }"
            >
              <span>{{ row.account_name }}</span>
              <span class="tabular-nums">{{ formatNumber(row.amount) }}</span>
            </div>
            <p v-if="!section.rows.length" class="text-xs text-gray-400">No activity in this class.</p>
          </div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm h-fit">
          <h2 class="mb-3 text-sm font-semibold text-gray-900">Waterfall</h2>
          <dl class="space-y-2 text-sm">
            <div class="flex justify-between"><dt class="text-gray-500">Revenue</dt><dd class="font-medium tabular-nums">{{ formatNumber(contributionMargin.revenue) }}</dd></div>
            <div class="flex justify-between"><dt class="text-gray-500">− Variable</dt><dd class="tabular-nums">{{ formatNumber(contributionMargin.variable_cost) }}</dd></div>
            <div class="flex justify-between border-t border-gray-100 pt-2"><dt class="font-semibold">CM1</dt><dd class="font-semibold tabular-nums">{{ formatNumber(contributionMargin.cm1) }} <span v-if="contributionMargin.cm1_pct" class="text-xs text-gray-500">({{ contributionMargin.cm1_pct }}%)</span></dd></div>
            <div class="flex justify-between"><dt class="text-gray-500">− Product / Channel fixed</dt><dd class="tabular-nums">{{ formatNumber(contributionMargin.product_channel_fixed) }}</dd></div>
            <div class="flex justify-between border-t border-gray-100 pt-2"><dt class="font-semibold">CM2</dt><dd class="font-semibold tabular-nums">{{ formatNumber(contributionMargin.cm2) }} <span v-if="contributionMargin.cm2_pct" class="text-xs text-gray-500">({{ contributionMargin.cm2_pct }}%)</span></dd></div>
            <div class="flex justify-between"><dt class="text-gray-500">− Segment / BU fixed</dt><dd class="tabular-nums">{{ formatNumber(contributionMargin.segment_bu_fixed) }}</dd></div>
            <div class="flex justify-between border-t border-gray-100 pt-2"><dt class="font-semibold">CM3</dt><dd class="font-semibold tabular-nums">{{ formatNumber(contributionMargin.cm3) }} <span v-if="contributionMargin.cm3_pct" class="text-xs text-gray-500">({{ contributionMargin.cm3_pct }}%)</span></dd></div>
            <div class="flex justify-between"><dt class="text-gray-500">− Corporate overhead</dt><dd class="tabular-nums">{{ formatNumber(contributionMargin.corporate_overhead) }}</dd></div>
            <div class="flex justify-between border-t border-gray-200 pt-2"><dt class="font-semibold text-gray-900">Operating profit</dt><dd class="font-semibold tabular-nums text-gray-900">{{ formatNumber(contributionMargin.operating_profit) }}</dd></div>
          </dl>
        </div>
      </div>
      <p v-else-if="!loading" class="text-sm text-gray-400">Select a period and run the report.</p>
    </div>

    <!-- Balance Sheet -->
    <div v-else-if="tab === 'balance-sheet' && bs" class="grid grid-cols-3 gap-4">
      <div v-for="(rows, section) in { Assets: bs.assets, Liabilities: bs.liabilities, Equity: bs.equity }"
           :key="section" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">{{ section }}</h2>
        <div v-for="row in rows" :key="row.account_id ?? row.account_name"
             class="flex justify-between py-0.5 text-sm"
             :style="{ paddingLeft: `${row.indent * 12}px` }"
             :class="row.is_group ? 'font-semibold' : ''">
          <span>{{ row.account_name }}</span><span>{{ formatNumber(row.amount) }}</span>
        </div>
      </div>
      <div class="col-span-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="flex justify-end gap-8 text-sm">
          <span>Assets: <strong>{{ formatNumber(bs.total_assets) }}</strong></span>
          <span>Liabilities: <strong>{{ formatNumber(bs.total_liabilities) }}</strong></span>
          <span>Equity: <strong>{{ formatNumber(bs.total_equity) }}</strong></span>
          <span>Provisional P&amp;L: <strong>{{ formatNumber(bs.provisional_profit_loss) }}</strong></span>
        </div>
      </div>
    </div>

    <!-- Uncleared / outstanding items -->
    <div v-else-if="tab === 'bank-recon'">
      <p class="mb-3 text-xs text-gray-500">
        Vouchers posted to this account that the bank hasn't cleared yet (outstanding items). Matching a
        line in
        <router-link to="/bank-reconciliation" class="text-primary underline">Bank Reconciliation</router-link>
        clears its voucher, so it drops off this list. <strong>Balance per bank</strong> = books − uncleared.
      </p>
      <div v-if="bankRecon" class="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="flex justify-end gap-8 text-sm">
          <span>Balance per books: <strong>{{ formatNumber(bankRecon.balance_per_books) }}</strong></span>
          <span>Uncleared: <strong>{{ formatNumber(bankRecon.uncleared_amount) }}</strong></span>
          <span>Balance per bank: <strong>{{ formatNumber(bankRecon.balance_per_bank) }}</strong></span>
        </div>
      </div>
      <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <th class="px-4 py-2">Voucher</th><th class="px-4 py-2">Type</th>
              <th class="px-4 py-2">Date</th><th class="px-4 py-2">Reference</th>
              <th class="px-4 py-2 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in bankRecon?.uncleared_entries ?? []" :key="row.voucher_id"
                class="border-t border-gray-100">
              <td class="px-4 py-1.5">{{ row.voucher_no }}</td>
              <td class="px-4 py-1.5">{{ row.voucher_type }}</td>
              <td class="px-4 py-1.5">{{ formatDate(row.posting_date) }}</td>
              <td class="px-4 py-1.5">{{ row.reference_no ?? "—" }}</td>
              <td class="px-4 py-1.5 text-right">{{ formatNumber(row.amount) }}</td>
            </tr>
            <tr v-if="!bankRecon || !bankRecon.uncleared_entries.length">
              <td colspan="5" class="px-4 py-8 text-center text-gray-400">
                {{ bankRecon ? "Everything is cleared. 🎉" : "Pick a bank account and run." }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- AR / AP aging -->
    <div v-else-if="tab === 'receivable' || tab === 'payable'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Party</th><th class="px-4 py-2">Voucher</th>
            <th class="px-4 py-2">Due</th><th class="px-4 py-2 text-right">Outstanding</th>
            <th class="px-4 py-2 text-right">0-30</th><th class="px-4 py-2 text-right">31-60</th>
            <th class="px-4 py-2 text-right">61-90</th><th class="px-4 py-2 text-right">90+</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in aging" :key="row.voucher_no" class="border-t border-gray-100">
            <td class="px-4 py-1.5">{{ row.party_name }}</td>
            <td class="px-4 py-1.5">{{ row.voucher_no }}</td>
            <td class="px-4 py-1.5">{{ formatDate(row.due_date ?? row.posting_date) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.outstanding_amount) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_0_30) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_31_60) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_61_90) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_90_plus) }}</td>
          </tr>
          <tr v-if="!aging.length"><td colspan="8" class="px-4 py-8 text-center text-gray-400">
            Nothing outstanding. 🎉
          </td></tr>
        </tbody>
      </table>
    </div>

    <!-- AR / AP Summary (per party) -->
    <div v-else-if="tab === 'ar-summary' || tab === 'ap-summary'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">{{ tab === 'ar-summary' ? 'Customer' : 'Supplier' }}</th>
            <th class="px-4 py-2 text-right">Outstanding</th>
            <th class="px-4 py-2 text-right">0-30</th><th class="px-4 py-2 text-right">31-60</th>
            <th class="px-4 py-2 text-right">61-90</th><th class="px-4 py-2 text-right">90+</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in (tab === 'ar-summary' ? arSummary : apSummary)" :key="row.party_id"
              class="border-t border-gray-100">
            <td class="px-4 py-1.5">{{ row.party_name }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ formatNumber(row.outstanding_amount) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_0_30) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_31_60) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_61_90) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.bucket_90_plus) }}</td>
          </tr>
          <tr v-if="!(tab === 'ar-summary' ? arSummary : apSummary).length">
            <td colspan="6" class="px-4 py-8 text-center text-gray-400">Nothing outstanding. 🎉</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Collection period (per customer) -->
    <div v-else-if="tab === 'collection'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Customer</th>
            <th class="px-4 py-2 text-right">Invoices Paid</th>
            <th class="px-4 py-2 text-right">Avg Days to Pay</th>
            <th class="px-4 py-2 text-right">Collected</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in collection" :key="row.party_id" class="border-t border-gray-100">
            <td class="px-4 py-1.5">{{ row.party_name }}</td>
            <td class="px-4 py-1.5 text-right">{{ row.paid_invoices }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ row.avg_days_to_pay }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.total_collected) }}</td>
          </tr>
          <tr v-if="!collection.length">
            <td colspan="4" class="px-4 py-8 text-center text-gray-400">No invoices were paid in this period.</td>
          </tr>
        </tbody>
      </table>
      <p v-if="collection.length" class="border-t border-gray-100 px-4 py-2 text-xs text-gray-400">
        "Avg days to pay" = invoice date → final payment date, for invoices fully paid in this window. Lower is better.
      </p>
    </div>

    <!-- Sales / Purchase Register -->
    <div v-else-if="tab === 'sales-register' || tab === 'purchase-register'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Date</th><th class="px-4 py-2">Invoice</th>
            <th class="px-4 py-2">{{ tab === 'sales-register' ? 'Customer' : 'Supplier' }}</th>
            <th class="px-4 py-2 text-right">Net</th><th class="px-4 py-2 text-right">Tax</th>
            <th class="px-4 py-2 text-right">Grand Total</th><th class="px-4 py-2 text-right">Outstanding</th>
            <th class="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in register?.rows ?? []" :key="row.voucher_id" class="border-t border-gray-100">
            <td class="px-4 py-1.5">{{ formatDate(row.posting_date) }}</td>
            <td class="px-4 py-1.5">{{ row.name }}</td>
            <td class="px-4 py-1.5">{{ row.party_name }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.net_total) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.total_taxes_and_charges) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.grand_total) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.outstanding_amount) }}</td>
            <td class="px-4 py-1.5">{{ row.status }}</td>
          </tr>
          <tr v-if="register && register.rows.length" class="border-t-2 border-gray-300 bg-gray-50 font-semibold">
            <td class="px-4 py-2" colspan="3">Total ({{ register.rows.length }})</td>
            <td class="px-4 py-2 text-right">{{ formatNumber(register.total_net) }}</td>
            <td class="px-4 py-2 text-right">{{ formatNumber(register.total_tax) }}</td>
            <td class="px-4 py-2 text-right">{{ formatNumber(register.total_grand) }}</td>
            <td class="px-4 py-2 text-right">{{ formatNumber(register.total_outstanding) }}</td>
            <td></td>
          </tr>
          <tr v-if="!register || !register.rows.length"><td colspan="8" class="px-4 py-8 text-center text-gray-400">
            {{ register ? "No invoices in this period." : "Pick a date range and run." }}
          </td></tr>
        </tbody>
      </table>
    </div>

    <!-- Customer / Supplier Ledger Summary -->
    <div v-else-if="tab === 'customer-ledger' || tab === 'supplier-ledger'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">{{ tab === 'customer-ledger' ? 'Customer' : 'Supplier' }}</th>
            <th class="px-4 py-2 text-right">Opening</th><th class="px-4 py-2 text-right">Debit</th>
            <th class="px-4 py-2 text-right">Credit</th><th class="px-4 py-2 text-right">Closing</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ledger" :key="row.party_id" class="border-t border-gray-100">
            <td class="px-4 py-1.5">{{ row.party_name }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.opening) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.debit) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.credit) }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ formatNumber(row.closing) }}</td>
          </tr>
          <tr v-if="!ledger.length"><td colspan="5" class="px-4 py-8 text-center text-gray-400">
            No ledger activity in this period.
          </td></tr>
        </tbody>
      </table>
    </div>

    <!-- Gross Profit (by item) -->
    <div v-else-if="tab === 'gross-profit'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Item</th><th class="px-4 py-2 text-right">Qty</th>
            <th class="px-4 py-2 text-right">Selling</th><th class="px-4 py-2 text-right">COGS</th>
            <th class="px-4 py-2 text-right">Gross Profit</th><th class="px-4 py-2 text-right">Margin %</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in grossProfit?.rows ?? []" :key="row.item_name" class="border-t border-gray-100">
            <td class="px-4 py-1.5">{{ row.item_name }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.qty) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.selling) }}</td>
            <td class="px-4 py-1.5 text-right" :class="Number(row.cogs) === 0 ? 'text-gray-400' : ''">{{ formatNumber(row.cogs) }}</td>
            <td class="px-4 py-1.5 text-right font-medium">{{ formatNumber(row.gross_profit) }}</td>
            <td class="px-4 py-1.5 text-right">{{ Number(row.margin_pct).toFixed(1) }}%</td>
          </tr>
          <tr v-if="grossProfit && grossProfit.rows.length" class="border-t-2 border-gray-300 bg-gray-50 font-semibold">
            <td class="px-4 py-2">Total</td><td></td>
            <td class="px-4 py-2 text-right">{{ formatNumber(grossProfit.total_selling) }}</td>
            <td class="px-4 py-2 text-right">{{ formatNumber(grossProfit.total_cogs) }}</td>
            <td class="px-4 py-2 text-right">{{ formatNumber(grossProfit.total_gross_profit) }}</td>
            <td class="px-4 py-2 text-right">{{ Number(grossProfit.margin_pct).toFixed(1) }}%</td>
          </tr>
          <tr v-if="!grossProfit || !grossProfit.rows.length"><td colspan="6" class="px-4 py-8 text-center text-gray-400">
            {{ grossProfit ? "No sales in this period." : "Pick a date range and run." }}
          </td></tr>
        </tbody>
      </table>
      <p v-if="grossProfit && grossProfit.rows.length" class="border-t border-gray-100 px-4 py-2 text-xs text-gray-400">
        COGS uses each item's latest stock valuation. Items sold without a recorded stock cost show 0 (100% margin).
      </p>
    </div>

    <!-- Budget Variance -->
    <div v-else-if="tab === 'budget-variance'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Account</th><th class="px-4 py-2 text-right">Budget</th>
            <th class="px-4 py-2 text-right">Actual</th><th class="px-4 py-2 text-right">Variance</th>
            <th class="px-4 py-2 text-right">Variance %</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in budgetVariance" :key="row.account_id" class="border-t border-gray-100">
            <td class="px-4 py-1.5">{{ row.account_name }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.budget) }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatNumber(row.actual) }}</td>
            <td class="px-4 py-1.5 text-right" :class="Number(row.variance) < 0 ? 'font-medium text-red-700' : 'text-green-700'">
              {{ formatNumber(row.variance) }}
            </td>
            <td class="px-4 py-1.5 text-right">{{ Number(row.variance_pct).toFixed(1) }}%</td>
          </tr>
          <tr v-if="!budgetVariance.length"><td colspan="5" class="px-4 py-8 text-center text-gray-400">
            No submitted budgets for this fiscal year.
          </td></tr>
        </tbody>
      </table>
      <p v-if="budgetVariance.length" class="border-t border-gray-100 px-4 py-2 text-xs text-gray-400">
        Positive variance (green) = under budget. Negative (red) = over budget.
      </p>
    </div>

    <!-- Share Balance (cap table) -->
    <div v-else-if="tab === 'share-balance'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Shareholder</th><th class="px-4 py-2">Share Type</th>
            <th class="px-4 py-2 text-right">Shares</th><th class="px-4 py-2 text-right">% Held</th>
            <th class="px-4 py-2 text-right">Nominal Value</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in shareBalance" :key="row.shareholder_id + row.share_type_id" class="border-t border-gray-100">
            <td class="px-4 py-1.5 font-medium text-gray-900">{{ row.shareholder_name }}</td>
            <td class="px-4 py-1.5">{{ row.share_type_name }}</td>
            <td class="px-4 py-1.5 text-right">{{ row.no_of_shares }}</td>
            <td class="px-4 py-1.5 text-right">{{ Number(row.percent_of_type).toFixed(2) }}%</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(row.nominal_value, companyCurrency) }}</td>
          </tr>
          <tr v-if="!shareBalance.length"><td colspan="5" class="px-4 py-8 text-center text-gray-400">
            No shareholdings yet. Issue shares under Share Transfer to populate the cap table.
          </td></tr>
        </tbody>
      </table>
    </div>

    <!-- Share Ledger -->
    <div v-else-if="tab === 'share-ledger'"
         class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Date</th><th class="px-4 py-2">Transfer</th>
            <th class="px-4 py-2">Type</th><th class="px-4 py-2">Share Type</th>
            <th class="px-4 py-2">From → To</th><th class="px-4 py-2 text-right">Shares</th>
            <th class="px-4 py-2 text-right">Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in shareLedger" :key="row.id" class="border-t border-gray-100">
            <td class="px-4 py-1.5 text-gray-500">{{ formatDate(row.transfer_date) }}</td>
            <td class="px-4 py-1.5">{{ row.name }}</td>
            <td class="px-4 py-1.5">{{ row.transfer_type }}</td>
            <td class="px-4 py-1.5">{{ row.share_type_name ?? "—" }}</td>
            <td class="px-4 py-1.5 text-gray-500">{{ row.from_shareholder_name ?? "—" }} → {{ row.to_shareholder_name ?? "—" }}</td>
            <td class="px-4 py-1.5 text-right">{{ row.no_of_shares }}</td>
            <td class="px-4 py-1.5 text-right">{{ formatCurrency(row.amount, companyCurrency) }}</td>
          </tr>
          <tr v-if="!shareLedger.length"><td colspan="7" class="px-4 py-8 text-center text-gray-400">
            No submitted share transfers in this period.
          </td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
