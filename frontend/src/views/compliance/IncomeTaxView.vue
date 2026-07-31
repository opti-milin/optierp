<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import { formatCurrency, formatDate } from "@/utils/format";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { IncomeTaxComputation, IncomeTaxComputationListItem } from "@/types/compliance";

const route = useRoute();
const router = useRouter();

type AdjRow = {
  category_id: string;
  provision_id: string;
  section_code: string;
  stage: string;
  description: string;
  direction: string;
  amount: string;
  computed_amount: string;
  override_amount: string;
  final_amount: string;
  status: string;
  explanation: Record<string, unknown>;
};
type SpecialRow = {
  income_category_code: string;
  amount: string;
  rate_percent: string;
  description: string;
  tax_amount?: string;
};
type EfileResult = { status: string; provider: string; message: string; result: Record<string, unknown> | null };

const ADJ_STAGES = ["PGBP", "ICDS", "ChapterVIA", "SetOff", "MAT", "Other"] as const;

const SPECIAL_CODES = [
  "LOTTERY",
  "CRYPTO",
  "LTCG_EQUITY",
  "STCG_EQUITY",
] as const;

const list = ref<IncomeTaxComputationListItem[]>([]);
const doc = ref<IncomeTaxComputation | null>(null);
const loading = ref(false);
const saving = ref(false);
const exporting = ref(false);
const efiling = ref(false);
const payrollSeeding = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const efileResult = ref<EfileResult | null>(null);
const recon = ref<{
  summary: { status: string; books_tds_credit: string; portal_tds_credit: string; difference: string };
} | null>(null);
const calendar = ref<{
  assessment_year: string;
  instalments: Array<{ instalment: number; due_date: string; cumulative_percent: number; suggested_amount: string | null }>;
} | null>(null);
const rateInfo = ref<string | null>(null);
const detailId = computed(() => (route.params.id as string | undefined) ?? null);
const isNew = computed(() => route.name === "income-tax-new");

const createForm = reactive({
  assessment_year: "2025-26",
  from_date: "2024-04-01",
  to_date: "2025-03-31",
  assessee_mode: "EntityBooks",
  employee_id: "",
  advance_tax_paid: "0",
  remarks: "",
  seed_from_books: true,
  salary_income: "0",
  house_property_income: "0",
  other_sources_income: "0",
  capital_gains_income: "0",
  chapter_via_deduction: "0",
  standard_deduction: "0",
  salary_tds: "0",
  employer_name: "",
  employer_tan: "",
  employer_address: "",
  employee_name: "",
  employee_pan: "",
  gross_salary: "0",
  exemptions_total: "0",
  taxable_salary: "0",
  tax_deducted: "0",
});

const edit = reactive({
  advance_tax_paid: "0",
  remarks: "",
  salary_income: "0",
  house_property_income: "0",
  other_sources_income: "0",
  capital_gains_income: "0",
  chapter_via_deduction: "0",
  standard_deduction: "0",
  salary_tds: "0",
  employer_name: "",
  employer_tan: "",
  employer_address: "",
  employee_name: "",
  employee_pan: "",
  gross_salary: "0",
  exemptions_total: "0",
  taxable_salary: "0",
  tax_deducted: "0",
});
const adjRows = ref<AdjRow[]>([]);
const specialRows = ref<SpecialRow[]>([]);
const createSpecialRows = ref<SpecialRow[]>([]);

const isHeadsMode = computed(() => createForm.assessee_mode === "IndividualHeads");
const detailHeadsMode = computed(() => doc.value?.assessee_mode === "IndividualHeads");

function resetCreateForm(): void {
  Object.assign(createForm, {
    assessment_year: "2025-26",
    from_date: "2024-04-01",
    to_date: "2025-03-31",
    assessee_mode: "EntityBooks",
    employee_id: "",
    advance_tax_paid: "0",
    remarks: "",
    seed_from_books: true,
    salary_income: "0",
    house_property_income: "0",
    other_sources_income: "0",
    capital_gains_income: "0",
    chapter_via_deduction: "0",
    standard_deduction: "0",
    salary_tds: "0",
    employer_name: "",
    employer_tan: "",
    employer_address: "",
    employee_name: "",
    employee_pan: "",
    gross_salary: "0",
    exemptions_total: "0",
    taxable_salary: "0",
    tax_deducted: "0",
  });
  createSpecialRows.value = [];
}

function syncEditFromDoc(): void {
  if (!doc.value) return;
  edit.advance_tax_paid = doc.value.advance_tax_paid;
  edit.remarks = doc.value.remarks ?? "";
  edit.salary_income = doc.value.salary_income;
  edit.house_property_income = doc.value.house_property_income;
  edit.other_sources_income = doc.value.other_sources_income;
  edit.capital_gains_income = doc.value.capital_gains_income;
  edit.chapter_via_deduction = doc.value.chapter_via_deduction;
  edit.standard_deduction = doc.value.standard_deduction;
  edit.salary_tds = doc.value.salary_tds;
  edit.employer_name = doc.value.employer_name ?? "";
  edit.employer_tan = doc.value.employer_tan ?? "";
  edit.employer_address = doc.value.employer_address ?? "";
  edit.employee_name = doc.value.employee_name ?? "";
  edit.employee_pan = doc.value.employee_pan ?? "";
  edit.gross_salary = doc.value.gross_salary;
  edit.exemptions_total = doc.value.exemptions_total;
  edit.taxable_salary = doc.value.taxable_salary;
  edit.tax_deducted = doc.value.tax_deducted;
  adjRows.value = doc.value.adjustments.map((a) => ({
    category_id: a.category_id || "",
    provision_id: a.provision_id || "",
    section_code: a.section_code || "",
    stage: a.stage || "PGBP",
    description: a.description,
    direction: a.direction,
    amount: a.final_amount || a.amount,
    computed_amount: a.computed_amount || a.amount,
    override_amount: a.override_amount || "",
    final_amount: a.final_amount || a.amount,
    status: a.status || "Manual",
    explanation: a.explanation || {},
  }));
  specialRows.value = (doc.value.special_income_lines || []).map((ln) => ({
    income_category_code: ln.income_category_code,
    amount: ln.amount,
    rate_percent: ln.rate_percent,
    description: ln.description || "",
    tax_amount: ln.tax_amount,
  }));
}

async function loadRateLabel(docRow: IncomeTaxComputation): Promise<void> {
  const method = docRow.computation_method || "—";
  if (docRow.policy_id) {
    rateInfo.value = `Policy ${docRow.policy_id.slice(0, 8)}… · method ${method}`;
  } else if (!docRow.rate_table_id) {
    rateInfo.value = `Method ${method}. No tax policy matched — seed masters or set Tax Policy.`;
    return;
  } else {
    rateInfo.value = `Method ${method}`;
  }
  if (!docRow.rate_table_id) return;
  try {
    const response = await api.get<{
      assessment_year: string;
      entity_type?: string;
      filing_regime?: string;
      tax_rate: string | number;
    }>(`/registry/income-tax-rate-table/${docRow.rate_table_id}`);
    const row = response.data;
    rateInfo.value =
      `${method} · ${row.assessment_year} · ${row.entity_type || "?"} / ${row.filing_regime || "?"} — ` +
      `flat ${row.tax_rate}%`;
  } catch {
    /* keep method label */
  }
}

async function loadList(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const response = await api.get<ListResponse<IncomeTaxComputationListItem>>("/income-tax-computations");
    list.value = response.data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function loadDetail(id: string): Promise<void> {
  loading.value = true;
  error.value = null;
  recon.value = null;
  efileResult.value = null;
  try {
    doc.value = (await api.get<IncomeTaxComputation>(`/income-tax-computations/${id}`)).data;
    syncEditFromDoc();
    await loadRateLabel(doc.value);
    await refreshAdvanceTaxCalendar();
  } catch (e) {
    error.value = e as ErrorEnvelope;
    doc.value = null;
  } finally {
    loading.value = false;
  }
}

async function refreshAdvanceTaxCalendar(): Promise<void> {
  if (!doc.value) {
    calendar.value = null;
    return;
  }
  try {
    calendar.value = (
      await api.get("/income-tax-computations/advance-tax-calendar", {
        params: { assessment_year: doc.value.assessment_year, total_tax: doc.value.total_tax },
      })
    ).data as typeof calendar.value;
  } catch {
    calendar.value = null;
  }
}

function createPayload() {
  return {
    assessment_year: createForm.assessment_year,
    from_date: createForm.from_date,
    to_date: createForm.to_date,
    assessee_mode: createForm.assessee_mode,
    employee_id: createForm.employee_id || null,
    advance_tax_paid: createForm.advance_tax_paid,
    remarks: createForm.remarks || null,
    adjustments: [],
    special_income: createSpecialRows.value
      .filter((r) => r.income_category_code && Number(r.amount) > 0)
      .map((r) => ({
        income_category_code: r.income_category_code,
        amount: r.amount,
        rate_percent: "0",
        description: r.description || null,
      })),
    seed_from_books: createForm.assessee_mode === "EntityBooks" ? createForm.seed_from_books : false,
    salary_income: createForm.salary_income,
    house_property_income: createForm.house_property_income,
    other_sources_income: createForm.other_sources_income,
    capital_gains_income: createForm.capital_gains_income,
    chapter_via_deduction: createForm.chapter_via_deduction,
    standard_deduction: createForm.standard_deduction,
    salary_tds: createForm.salary_tds,
    employer_name: createForm.employer_name || null,
    employer_tan: createForm.employer_tan || null,
    employer_address: createForm.employer_address || null,
    employee_name: createForm.employee_name || null,
    employee_pan: createForm.employee_pan || null,
    gross_salary: createForm.gross_salary,
    exemptions_total: createForm.exemptions_total,
    taxable_salary: createForm.taxable_salary,
    tax_deducted: createForm.tax_deducted,
  };
}

function updatePayload() {
  return {
    advance_tax_paid: edit.advance_tax_paid,
    remarks: edit.remarks || null,
    adjustments: adjRows.value.map((r) => ({
      category_id: r.category_id || null,
      provision_id: r.provision_id || null,
      section_code: r.section_code || "",
      stage: r.stage || "PGBP",
      description: r.description,
      direction: r.direction,
      amount: r.override_amount || r.final_amount || r.amount,
      computed_amount: r.computed_amount || r.amount,
      override_amount: r.override_amount || null,
      final_amount: r.override_amount || r.final_amount || r.amount,
      status: r.override_amount ? "Overridden" : r.status || "Manual",
      explanation: r.explanation || {},
    })),
    special_income: specialRows.value
      .filter((r) => r.income_category_code)
      .map((r) => ({
        income_category_code: r.income_category_code,
        amount: r.amount || "0",
        rate_percent: "0",
        description: r.description || null,
      })),
    salary_income: edit.salary_income,
    house_property_income: edit.house_property_income,
    other_sources_income: edit.other_sources_income,
    capital_gains_income: edit.capital_gains_income,
    chapter_via_deduction: edit.chapter_via_deduction,
    standard_deduction: edit.standard_deduction,
    salary_tds: edit.salary_tds,
    employer_name: edit.employer_name || null,
    employer_tan: edit.employer_tan || null,
    employer_address: edit.employer_address || null,
    employee_name: edit.employee_name || null,
    employee_pan: edit.employee_pan || null,
    gross_salary: edit.gross_salary,
    exemptions_total: edit.exemptions_total,
    taxable_salary: edit.taxable_salary,
    tax_deducted: edit.tax_deducted,
  };
}

async function createDoc(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const created = (await api.post<IncomeTaxComputation>("/income-tax-computations", createPayload())).data;
    notice.value =
      createForm.assessee_mode === "IndividualHeads"
        ? "Individual worksheet created."
        : "Entity books computation created.";
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
    doc.value = (await api.put<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}`, updatePayload())).data;
    syncEditFromDoc();
    await loadRateLabel(doc.value);
    await refreshAdvanceTaxCalendar();
    notice.value = "Draft saved.";
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
    syncEditFromDoc();
    await refreshAdvanceTaxCalendar();
    notice.value = "Re-seeded from books.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function applyRateFromSettings(): Promise<void> {
  if (!doc.value || doc.value.docstatus !== 0) return;
  saving.value = true;
  error.value = null;
  try {
    doc.value = (
      await api.put<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}`, {
        ...updatePayload(),
        resolve_rate_from_settings: true,
      })
    ).data;
    syncEditFromDoc();
    await loadRateLabel(doc.value);
    await refreshAdvanceTaxCalendar();
    notice.value = "Rate table refreshed from settings.";
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
    await api.put(`/income-tax-computations/${doc.value.id}`, updatePayload());
    doc.value = (await api.post<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}/submit`)).data;
    syncEditFromDoc();
    await refreshAdvanceTaxCalendar();
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
    doc.value = (await api.post<IncomeTaxComputation>(`/income-tax-computations/${doc.value.id}/cancel`)).data;
    syncEditFromDoc();
    notice.value = "Cancelled.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function seedFromPayroll(): Promise<void> {
  payrollSeeding.value = true;
  error.value = null;
  try {
    const created = (
      await api.post<IncomeTaxComputation>("/income-tax-computations/seed-from-payroll", {
        assessment_year: createForm.assessment_year,
        employee_id: createForm.employee_id || null,
        from_date: createForm.from_date,
        to_date: createForm.to_date,
      })
    ).data;
    notice.value = "Seeded from Payroll.";
    void router.push({ name: "income-tax-detail", params: { id: created.id } });
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    payrollSeeding.value = false;
  }
}

function fillDummyData(): void {
  if (createForm.assessee_mode === "IndividualHeads") {
    Object.assign(createForm, {
      salary_income: "850000",
      house_property_income: "120000",
      other_sources_income: "35000",
      capital_gains_income: "0",
      chapter_via_deduction: "150000",
      standard_deduction: "75000",
      salary_tds: "62000",
      employer_name: "OptiReach Technologies Pvt Ltd",
      employer_tan: "MUMR12345A",
      employer_address: "301, Trade Centre, BKC,\nMumbai 400051, Maharashtra",
      employee_name: "Rahul Sharma",
      employee_pan: "ABCPS1234K",
      gross_salary: "900000",
      exemptions_total: "50000",
      taxable_salary: "850000",
      tax_deducted: "62000",
      advance_tax_paid: "15000",
      remarks: "FY 2024-25 individual filing — sample data",
    });
  } else {
    Object.assign(createForm, {
      advance_tax_paid: "250000",
      remarks: "FY 2024-25 entity books — sample data",
      seed_from_books: true,
    });
  }
}

async function downloadItrJson(): Promise<void> {
  if (!doc.value) return;
  exporting.value = true;
  error.value = null;
  try {
    const pack = (await api.get(`/income-tax-computations/${doc.value.id}/itr`)).data as { form?: string; assessment_year?: string };
    const blob = new Blob([JSON.stringify(pack, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(pack.form || "ITR").toLowerCase().replace("-", "")}-${pack.assessment_year || doc.value.assessment_year}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    exporting.value = false;
  }
}

async function downloadItrCsv(): Promise<void> {
  if (!doc.value) return;
  error.value = null;
  try {
    const resp = await api.get(`/income-tax-computations/${doc.value.id}/itr.csv`, { responseType: "blob" });
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

async function downloadForm16(): Promise<void> {
  if (!doc.value) return;
  error.value = null;
  try {
    const resp = await api.get(`/income-tax-computations/${doc.value.id}/form-16.pdf`, { responseType: "blob" });
    const url = URL.createObjectURL(resp.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `form16-${doc.value.assessment_year}.pdf`;
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
    efileResult.value = (await api.post<EfileResult>(`/income-tax-computations/${doc.value.id}/itr/efile`)).data;
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
    const form26as = JSON.parse(await file.text()) as Record<string, unknown>;
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

function addAdj(): void {
  adjRows.value.push({
    category_id: "",
    provision_id: "",
    section_code: "",
    stage: "PGBP",
    description: "",
    direction: "Add",
    amount: "0",
    computed_amount: "0",
    override_amount: "",
    final_amount: "0",
    status: "Manual",
    explanation: {},
  });
}

function removeAdj(i: number): void {
  adjRows.value.splice(i, 1);
}

function emptySpecialRow(): SpecialRow {
  return { income_category_code: "LOTTERY", amount: "0", rate_percent: "", description: "" };
}

function addSpecial(target: "create" | "edit" = "edit"): void {
  if (target === "create") createSpecialRows.value.push(emptySpecialRow());
  else specialRows.value.push(emptySpecialRow());
}

function removeSpecial(i: number, target: "create" | "edit" = "edit"): void {
  if (target === "create") createSpecialRows.value.splice(i, 1);
  else specialRows.value.splice(i, 1);
}

const adjByStage = computed(() => {
  const map: Record<string, AdjRow[]> = {};
  for (const s of ADJ_STAGES) map[s] = [];
  for (const row of adjRows.value) {
    const stage = row.stage && ADJ_STAGES.includes(row.stage as typeof ADJ_STAGES[number])
      ? row.stage
      : "Other";
    if (!map[stage]) map[stage] = [];
    map[stage].push(row);
  }
  return map;
});

const needsInputCount = computed(
  () => adjRows.value.filter((r) => r.status === "NeedsInput").length,
);

async function recomputeAdjustments(): Promise<void> {
  if (!doc.value) return;
  saving.value = true;
  error.value = null;
  try {
    const response = await api.post<IncomeTaxComputation>(
      `/income-tax-computations/${doc.value.id}/recompute-adjustments`,
    );
    doc.value = response.data;
    syncEditFromDoc();
    notice.value = "Adjustments recomputed from rule pack.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

function statusBadgeClass(status: string): string {
  if (status === "NeedsInput") return "bg-amber-100 text-amber-800";
  if (status === "Computed") return "bg-emerald-100 text-emerald-800";
  if (status === "Overridden") return "bg-blue-100 text-blue-800";
  if (status === "Skipped") return "bg-gray-100 text-gray-500";
  return "bg-slate-100 text-slate-700";
}

watch(
  () => [route.name, detailId.value] as const,
  async ([name, id]) => {
    if (name === "income-tax-detail" && id) {
      await loadDetail(id);
      return;
    }
    doc.value = null;
    resetCreateForm();
    await loadList();
  },
  { immediate: true },
);
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Income Tax</h1>
        <p class="text-sm text-gray-500">
          Entity books, individual income heads, ITR export, Form 16 preview, and future payroll seeding.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <RouterLink class="btn-secondary" to="/income-tax-settings">Settings</RouterLink>
        <RouterLink class="btn-secondary" to="/m/tax-policy">Tax Policy</RouterLink>
        <RouterLink class="btn-secondary" to="/m/income-tax-rate-table">Rate tables</RouterLink>
        <RouterLink class="btn-secondary" to="/m/income-tax-slab-set">Slabs</RouterLink>
        <RouterLink class="btn-secondary" to="/m/tax-adjustment-provision">Provisions</RouterLink>
        <RouterLink v-if="!isNew" class="btn-primary" :to="{ name: 'income-tax-new' }">New</RouterLink>
      </div>
    </div>

    <p v-if="notice" class="rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <section v-if="isNew" class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">New computation</h2>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">Assessment year</span>
          <input v-model="createForm.assessment_year" class="form-input" placeholder="2025-26" />
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">Mode</span>
          <select v-model="createForm.assessee_mode" class="form-input">
            <option value="EntityBooks">Entity books (ITR-3/5/6)</option>
            <option value="IndividualHeads">Individual heads (ITR-1 / Form 16)</option>
          </select>
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">FY from</span>
          <input v-model="createForm.from_date" type="date" class="form-input" />
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">FY to</span>
          <input v-model="createForm.to_date" type="date" class="form-input" />
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">Advance tax paid</span>
          <input v-model="createForm.advance_tax_paid" class="form-input" />
        </label>
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">Employee ID (optional)</span>
          <input v-model="createForm.employee_id" class="form-input" />
        </label>
        <label class="text-sm md:col-span-2">
          <span class="mb-1 block text-gray-500">Remarks</span>
          <input v-model="createForm.remarks" class="form-input" />
        </label>
      </div>

      <div v-if="!isHeadsMode" class="mt-4 rounded bg-gray-50 p-3 text-xs text-gray-600">
        <label class="inline-flex items-center gap-2">
          <input v-model="createForm.seed_from_books" type="checkbox" class="rounded border-gray-300" />
          Seed book profit and TDS credit from books
        </label>
        <p class="mt-2 text-gray-500">
          If unchecked, book profit / TDS start at zero and you enter adjustments manually after creation.
        </p>
      </div>

      <div v-else class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <section class="rounded border border-gray-200 p-4">
          <h3 class="mb-2 text-sm font-semibold text-gray-900">Income heads</h3>
          <div class="space-y-3">
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Salary income</span>
              <input v-model="createForm.salary_income" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">House property income</span>
              <input v-model="createForm.house_property_income" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Other sources income</span>
              <input v-model="createForm.other_sources_income" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Capital gains income</span>
              <input v-model="createForm.capital_gains_income" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Chapter VI-A deduction</span>
              <input v-model="createForm.chapter_via_deduction" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Standard deduction</span>
              <input v-model="createForm.standard_deduction" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Salary TDS</span>
              <input v-model="createForm.salary_tds" class="form-input" /></label>
          </div>
        </section>
        <section class="rounded border border-gray-200 p-4">
          <h3 class="mb-2 text-sm font-semibold text-gray-900">Form 16 details</h3>
          <div class="space-y-3">
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Employer name</span>
              <input v-model="createForm.employer_name" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Employer TAN</span>
              <input v-model="createForm.employer_tan" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Employee name</span>
              <input v-model="createForm.employee_name" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Employee PAN</span>
              <input v-model="createForm.employee_pan" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Gross salary</span>
              <input v-model="createForm.gross_salary" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Exemptions total</span>
              <input v-model="createForm.exemptions_total" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Taxable salary</span>
              <input v-model="createForm.taxable_salary" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Tax deducted (TDS)</span>
              <input v-model="createForm.tax_deducted" class="form-input" /></label>
            <label class="block text-sm"><span class="mb-1 block text-gray-500">Employer address</span>
              <textarea v-model="createForm.employer_address" class="form-input" rows="2" /></label>
          </div>
          <p class="mt-2 text-xs text-gray-500">When Payroll is enabled, use Seed from Payroll instead of manual entry.</p>
        </section>
      </div>

      <section class="mt-4 rounded border border-gray-200 p-4">
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-sm font-semibold text-gray-900">Special-rate income</h3>
          <button type="button" class="btn-secondary text-xs" @click="addSpecial('create')">Add line</button>
        </div>
        <p class="mb-2 text-xs text-gray-500">
          Rate comes from <RouterLink class="text-blue-600 hover:underline" to="/m/special-income-tax-rate">Special Income Tax Rate</RouterLink> for this AY — do not re-enter it here.
        </p>
        <table class="w-full text-left text-sm">
          <thead class="text-xs text-gray-500">
            <tr>
              <th class="py-1 w-40">Category</th>
              <th class="py-1 w-36 text-right">Amount</th>
              <th class="py-1">Description</th>
              <th class="w-10" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in createSpecialRows" :key="i" class="border-t border-gray-100">
              <td class="py-1 pr-2">
                <select v-model="row.income_category_code" class="form-input">
                  <option v-for="c in SPECIAL_CODES" :key="c" :value="c">{{ c }}</option>
                </select>
              </td>
              <td class="py-1 pr-2">
                <input v-model="row.amount" class="form-input text-right" />
              </td>
              <td class="py-1 pr-2">
                <input v-model="row.description" class="form-input" />
              </td>
              <td class="py-1 text-center">
                <button type="button" class="text-xs text-red-500" @click="removeSpecial(i, 'create')">×</button>
              </td>
            </tr>
            <tr v-if="!createSpecialRows.length">
              <td colspan="4" class="py-3 text-center text-gray-400">No special-rate lines</td>
            </tr>
          </tbody>
        </table>
      </section>

      <div class="mt-4 flex flex-wrap justify-end gap-2">
        <RouterLink class="btn-secondary" :to="{ name: 'income-tax' }">Cancel</RouterLink>
        <button
          class="btn-secondary text-xs"
          @click="fillDummyData"
        >
          Fill sample data
        </button>
        <button
          v-if="isHeadsMode"
          class="btn-secondary"
          :disabled="payrollSeeding"
          @click="seedFromPayroll"
        >
          {{ payrollSeeding ? "Seeding…" : "Seed from Payroll" }}
        </button>
        <button class="btn-primary" :disabled="saving" @click="createDoc">
          {{ saving ? "Creating…" : "Create" }}
        </button>
      </div>
    </section>

    <template v-else-if="detailId && doc">
      <div class="flex flex-wrap items-center gap-3 text-sm">
        <RouterLink class="text-blue-600 hover:underline" :to="{ name: 'income-tax' }">← List</RouterLink>
        <span class="font-mono font-medium">{{ doc.name }}</span>
        <span class="rounded bg-gray-100 px-2 py-0.5">{{ doc.status }}</span>
        <span>{{ doc.assessee_mode === "IndividualHeads" ? "Individual heads" : "Entity books" }}</span>
        <span>AY {{ doc.assessment_year }}</span>
        <span>{{ formatDate(doc.from_date) }} → {{ formatDate(doc.to_date) }}</span>
      </div>

      <div class="grid gap-4 md:grid-cols-2">
        <section class="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
          <h2 class="mb-2 font-semibold text-gray-900">
            {{ detailHeadsMode ? "Income heads" : "Books → taxable income" }}
          </h2>
          <dl class="space-y-1">
            <template v-if="detailHeadsMode">
              <div class="flex justify-between"><dt>Salary</dt><dd>{{ formatCurrency(doc.salary_income) }}</dd></div>
              <div class="flex justify-between"><dt>House property</dt><dd>{{ formatCurrency(doc.house_property_income) }}</dd></div>
              <div class="flex justify-between"><dt>Other sources</dt><dd>{{ formatCurrency(doc.other_sources_income) }}</dd></div>
              <div class="flex justify-between"><dt>Capital gains</dt><dd>{{ formatCurrency(doc.capital_gains_income) }}</dd></div>
              <div class="flex justify-between"><dt>Chapter VI-A</dt><dd>−{{ formatCurrency(doc.chapter_via_deduction) }}</dd></div>
              <div class="flex justify-between"><dt>Standard deduction</dt><dd>−{{ formatCurrency(doc.standard_deduction) }}</dd></div>
            </template>
            <template v-else>
              <div class="flex justify-between"><dt>Book profit</dt><dd>{{ formatCurrency(doc.book_profit) }}</dd></div>
              <div class="flex justify-between"><dt>Net adjustments</dt><dd>{{ formatCurrency(doc.net_adjustments) }}</dd></div>
            </template>
            <div class="flex justify-between font-semibold"><dt>Taxable income</dt><dd>{{ formatCurrency(doc.taxable_income) }}</dd></div>
          </dl>
          <button
            v-if="doc.docstatus === 0 && !detailHeadsMode"
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
            <div class="flex justify-between"><dt>Tax (after rebate)</dt><dd>{{ formatCurrency(doc.tax_amount) }}</dd></div>
            <div class="flex justify-between"><dt>Rebate</dt><dd>−{{ formatCurrency(doc.rebate_amount || doc.rebate_87a) }}</dd></div>
            <div class="flex justify-between"><dt>Surcharge</dt><dd>{{ formatCurrency(doc.surcharge_amount) }}</dd></div>
            <div v-if="Number(doc.marginal_relief_amount) > 0" class="flex justify-between">
              <dt>Marginal relief</dt><dd>−{{ formatCurrency(doc.marginal_relief_amount) }}</dd>
            </div>
            <div class="flex justify-between"><dt>Cess</dt><dd>{{ formatCurrency(doc.cess_amount) }}</dd></div>
            <div class="flex justify-between font-semibold"><dt>Total tax</dt><dd>{{ formatCurrency(doc.total_tax) }}</dd></div>
            <div class="flex justify-between"><dt>TDS credit</dt><dd>−{{ formatCurrency(doc.tds_credit) }}</dd></div>
            <div class="flex justify-between"><dt>TCS credit</dt><dd>−{{ formatCurrency(doc.tcs_credit || '0') }}</dd></div>
            <div class="flex justify-between"><dt>Salary TDS</dt><dd>−{{ formatCurrency(doc.salary_tds) }}</dd></div>
            <div class="flex justify-between"><dt>Advance tax</dt><dd>−{{ formatCurrency(doc.advance_tax_paid) }}</dd></div>
            <div class="flex justify-between border-t pt-1 font-semibold">
              <dt>{{ Number(doc.tax_payable) >= 0 ? "Tax payable" : "Refundable" }}</dt>
              <dd>{{ formatCurrency(Math.abs(Number(doc.tax_payable))) }}</dd>
            </div>
          </dl>
          <details v-if="doc.tax_breakdown && Object.keys(doc.tax_breakdown).length" class="mt-3 text-xs text-gray-600">
            <summary class="cursor-pointer font-medium text-gray-800">Breakdown</summary>
            <pre class="mt-1 max-h-48 overflow-auto rounded bg-gray-50 p-2">{{ JSON.stringify(doc.tax_breakdown, null, 2) }}</pre>
          </details>
          <div
            v-if="doc.special_income_lines?.length"
            class="mt-3 border-t pt-2 text-xs"
          >
            <p class="mb-1 font-medium text-gray-800">Special-rate tax (computed)</p>
            <ul class="space-y-1">
              <li v-for="ln in doc.special_income_lines" :key="ln.id" class="flex justify-between gap-2">
                <span>{{ ln.income_category_code }} · {{ formatCurrency(ln.amount) }} @ {{ ln.rate_percent }}%</span>
                <span>{{ formatCurrency(ln.tax_amount) }}</span>
              </li>
            </ul>
          </div>
          <button
            v-if="doc.docstatus === 0"
            class="btn-secondary mt-3 text-xs"
            :disabled="saving"
            @click="applyRateFromSettings"
          >
            Apply policy from settings
          </button>
        </section>
      </div>

      <section
        class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
      >
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-900">Special-rate income</h2>
          <button
            v-if="doc.docstatus === 0"
            type="button"
            class="btn-secondary text-xs"
            @click="addSpecial('edit')"
          >
            Add line
          </button>
        </div>
        <p class="mb-2 text-xs text-gray-500">
          Enter category + amount only. Applied % comes from
          <RouterLink class="text-blue-600 hover:underline" to="/m/special-income-tax-rate">Special Income Tax Rate</RouterLink>
          after Save draft.
        </p>
        <table class="w-full text-left text-sm">
          <thead class="text-xs text-gray-500">
            <tr>
              <th class="py-1 w-40">Category</th>
              <th class="py-1 w-36 text-right">Amount</th>
              <th class="py-1 w-24 text-right">Applied %</th>
              <th class="py-1 w-32 text-right">Tax</th>
              <th class="py-1">Description</th>
              <th v-if="doc.docstatus === 0" class="w-10" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in specialRows" :key="i" class="border-t border-gray-100">
              <td class="py-1 pr-2">
                <select
                  v-if="doc.docstatus === 0"
                  v-model="row.income_category_code"
                  class="form-input"
                >
                  <option v-for="c in SPECIAL_CODES" :key="c" :value="c">{{ c }}</option>
                </select>
                <span v-else>{{ row.income_category_code }}</span>
              </td>
              <td class="py-1 pr-2 text-right">
                <input v-if="doc.docstatus === 0" v-model="row.amount" class="form-input text-right" />
                <span v-else>{{ formatCurrency(row.amount) }}</span>
              </td>
              <td class="py-1 pr-2 text-right text-gray-500">
                {{ row.rate_percent ? `${row.rate_percent}%` : "—" }}
              </td>
              <td class="py-1 pr-2 text-right text-gray-500">
                {{ row.tax_amount != null ? formatCurrency(row.tax_amount) : "—" }}
              </td>
              <td class="py-1 pr-2">
                <input v-if="doc.docstatus === 0" v-model="row.description" class="form-input" />
                <span v-else>{{ row.description || "—" }}</span>
              </td>
              <td v-if="doc.docstatus === 0" class="py-1 text-center">
                <button type="button" class="text-xs text-red-500" @click="removeSpecial(i, 'edit')">×</button>
              </td>
            </tr>
            <tr v-if="!specialRows.length">
              <td :colspan="doc.docstatus === 0 ? 6 : 5" class="py-3 text-center text-gray-400">
                No special-rate lines — click Add line
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section
        v-if="detailHeadsMode"
        class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
      >
        <h2 class="mb-3 text-sm font-semibold text-gray-900">Form 16 / payroll details</h2>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Employee name</span>
            <input v-model="edit.employee_name" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Employee PAN</span>
            <input v-model="edit.employee_pan" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Employer name</span>
            <input v-model="edit.employer_name" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Employer TAN</span>
            <input v-model="edit.employer_tan" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Gross salary</span>
            <input v-model="edit.gross_salary" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Exemptions total</span>
            <input v-model="edit.exemptions_total" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Taxable salary</span>
            <input v-model="edit.taxable_salary" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Tax deducted (TDS)</span>
            <input v-model="edit.tax_deducted" :disabled="doc.docstatus !== 0" class="form-input" /></label>
          <label class="block text-sm md:col-span-2"><span class="mb-1 block text-gray-500">Employer address</span>
            <textarea v-model="edit.employer_address" :disabled="doc.docstatus !== 0" class="form-input" rows="2" /></label>
        </div>
        <p class="mt-2 text-xs text-gray-500">
          Seed source: <strong>{{ doc.seed_source }}</strong>.
          <span v-if="doc.seed_source === 'Manual'"> When Payroll is enabled, use Seed from Payroll for automatic entry.</span>
        </p>
      </section>

      <section
        v-if="detailHeadsMode && doc.docstatus === 0"
        class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
      >
        <h2 class="mb-3 text-sm font-semibold text-gray-900">Editable income heads</h2>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Salary income</span>
            <input v-model="edit.salary_income" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">House property income</span>
            <input v-model="edit.house_property_income" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Other sources income</span>
            <input v-model="edit.other_sources_income" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Capital gains income</span>
            <input v-model="edit.capital_gains_income" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Chapter VI-A deduction</span>
            <input v-model="edit.chapter_via_deduction" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Standard deduction</span>
            <input v-model="edit.standard_deduction" class="form-input" /></label>
          <label class="block text-sm"><span class="mb-1 block text-gray-500">Salary TDS</span>
            <input v-model="edit.salary_tds" class="form-input" /></label>
        </div>
      </section>

      <section
        class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
      >
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 class="text-sm font-semibold text-gray-900">Tax adjustments</h2>
            <p v-if="needsInputCount" class="text-xs text-amber-700">
              {{ needsInputCount }} line(s) need CA input
            </p>
          </div>
          <div class="flex gap-2">
            <button
              v-if="doc.docstatus === 0"
              type="button"
              class="btn-secondary text-xs"
              :disabled="saving"
              @click="recomputeAdjustments"
            >
              Recompute from rules
            </button>
            <button
              v-if="doc.docstatus === 0"
              type="button"
              class="btn-secondary text-xs"
              @click="addAdj"
            >
              Add line
            </button>
            <RouterLink class="btn-secondary text-xs" to="/m/tax-adjustment-provision">
              Provisions
            </RouterLink>
          </div>
        </div>
        <div v-for="stage in ADJ_STAGES" :key="stage" class="mb-4">
          <h3
            v-if="(adjByStage[stage] || []).length"
            class="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500"
          >
            {{ stage }}
          </h3>
          <table
            v-if="(adjByStage[stage] || []).length"
            class="mb-2 w-full text-left text-sm"
          >
            <thead class="text-xs text-gray-500">
              <tr>
                <th class="py-1 w-28">Section</th>
                <th class="py-1">Description</th>
                <th class="py-1 w-20">Dir</th>
                <th class="py-1 w-24">Status</th>
                <th class="py-1 w-28 text-right">Computed</th>
                <th class="py-1 w-28 text-right">Override / Final</th>
                <th v-if="doc.docstatus === 0" class="w-10" />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, i) in adjRows"
                v-show="row.stage === stage || (!row.stage && stage === 'PGBP')"
                :key="`${stage}-${i}`"
                class="border-t border-gray-100"
              >
                <td class="py-1 pr-2 font-mono text-xs">
                  {{ row.section_code || "—" }}
                </td>
                <td class="py-1 pr-2">
                  <input v-if="doc.docstatus === 0" v-model="row.description" class="form-input" />
                  <span v-else>{{ row.description || "—" }}</span>
                  <p
                    v-if="row.explanation && (row.explanation as any).message"
                    class="mt-0.5 text-[11px] text-gray-400"
                  >
                    {{ (row.explanation as any).message }}
                  </p>
                </td>
                <td class="py-1 pr-2">{{ row.direction }}</td>
                <td class="py-1 pr-2">
                  <span
                    class="rounded px-1.5 py-0.5 text-[11px] font-medium"
                    :class="statusBadgeClass(row.status)"
                  >{{ row.status }}</span>
                </td>
                <td class="py-1 text-right tabular-nums">
                  {{ formatCurrency(row.computed_amount || row.amount) }}
                </td>
                <td class="py-1 text-right">
                  <input
                    v-if="doc.docstatus === 0"
                    v-model="row.override_amount"
                    class="form-input text-right"
                    placeholder="override"
                  />
                  <span v-else>{{ formatCurrency(row.final_amount || row.amount) }}</span>
                </td>
                <td v-if="doc.docstatus === 0" class="py-1 text-center">
                  <button type="button" class="text-red-500 text-xs" @click="removeAdj(i)">×</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!adjRows.length" class="py-3 text-center text-sm text-gray-400">
          No adjustments — click “Recompute from rules” to seed the catalogue.
        </p>
      </section>

      <section v-if="doc.docstatus === 0" class="flex flex-wrap items-end gap-3">
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">Advance tax paid</span>
          <input v-model="edit.advance_tax_paid" class="form-input" />
        </label>
        <label class="text-sm flex-1 min-w-[12rem]">
          <span class="mb-1 block text-gray-500">Remarks</span>
          <input v-model="edit.remarks" class="form-input" />
        </label>
        <button class="btn-secondary" :disabled="saving" @click="saveDraft">Save draft</button>
        <button class="btn-primary" :disabled="saving" @click="submitDoc">Submit</button>
      </section>

      <div v-else-if="doc.docstatus === 1" class="flex flex-wrap gap-2">
        <button class="btn-secondary" :disabled="saving" @click="cancelDoc">Cancel</button>
        <button class="btn-primary" :disabled="exporting" @click="downloadItrJson">
          {{ exporting ? "Exporting…" : "Download ITR JSON" }}
        </button>
        <button class="btn-secondary" @click="downloadItrCsv">Download ITR CSV</button>
        <button v-if="detailHeadsMode" class="btn-secondary" @click="downloadForm16">Download Form 16 PDF</button>
        <button class="btn-primary" :disabled="efiling" @click="efileItr">
          {{ efiling ? "E-filing…" : "E-file" }}
        </button>
      </div>

      <p v-if="efileResult" class="rounded bg-blue-50 px-3 py-2 text-sm text-blue-800">
        {{ efileResult.status }} via {{ efileResult.provider }} — {{ efileResult.message }}
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
              <td class="py-1 text-right">{{ i.suggested_amount != null ? formatCurrency(i.suggested_amount) : "—" }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="doc.docstatus === 1" class="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
        <h2 class="mb-2 font-semibold text-gray-900">26AS / AIS reconcile</h2>
        <p class="mb-2 text-xs text-gray-500">
          Upload a JSON extract with a <code>tds</code> / <code>credits</code> array.
        </p>
        <input type="file" accept="application/json,.json" class="text-sm" @change="on26asFile" />
        <div v-if="recon" class="mt-3 space-y-1">
          <p>
            Status <strong>{{ recon.summary.status }}</strong> — books
            {{ formatCurrency(recon.summary.books_tds_credit) }} vs portal
            {{ formatCurrency(recon.summary.portal_tds_credit) }}
            (diff {{ formatCurrency(recon.summary.difference) }})
          </p>
        </div>
      </section>
    </template>

    <section v-else class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="w-full text-left text-sm">
        <thead class="bg-gray-50 text-xs text-gray-500">
          <tr>
            <th class="px-3 py-2">Name</th>
            <th class="px-3 py-2">Mode</th>
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
            <td class="px-3 py-2">{{ row.assessee_mode === "IndividualHeads" ? "Individual" : "Entity" }}</td>
            <td class="px-3 py-2">{{ row.assessment_year }}</td>
            <td class="px-3 py-2">{{ formatDate(row.from_date) }} – {{ formatDate(row.to_date) }}</td>
            <td class="px-3 py-2 text-right">{{ formatCurrency(row.taxable_income) }}</td>
            <td class="px-3 py-2 text-right">{{ formatCurrency(row.total_tax) }}</td>
            <td class="px-3 py-2 text-right">{{ formatCurrency(row.tax_payable) }}</td>
            <td class="px-3 py-2">{{ row.status }}</td>
          </tr>
          <tr v-if="!loading && !list.length">
            <td colspan="8" class="px-3 py-8 text-center text-gray-400">
              No computations yet —
              <RouterLink class="text-blue-600 hover:underline" :to="{ name: 'income-tax-new' }">create one</RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
