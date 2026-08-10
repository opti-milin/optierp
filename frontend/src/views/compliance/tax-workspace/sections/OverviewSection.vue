<script setup lang="ts">
/**
 * Setup & Basis — the basis on which this year's computation stands, the company's
 * tax registration, the regime election for the year, and the two big time-savers
 * (carry last year forward, read this year's books).
 */
import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import DateField from "@/components/shared/DateField.vue";
import DerivedField from "@/components/shared/DerivedField.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import SearchSelect from "@/components/shared/SearchSelect.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import { taxLabel, taxStatutoryRef } from "@/config/taxTerminology";
import type {
  TaxCopyPreviousYearIn,
  TaxOption,
  TaxPopulateFromBooksIn,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency, formatDate } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const { visible } = useFieldVisibility(context);

/* ------------------------------------------------------------------ helpers */

function option(value: string, label: string, hint?: string): TaxOption {
  return { value, label, group: null, hint: hint ?? null, statutory_ref: null, meta: {} };
}

function errorMessage(err: unknown): string {
  if (err && typeof err === "object" && "detail" in err) {
    const detail = (err as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return err instanceof Error ? err.message : String(err);
}

function isTrue(value: string | null | undefined): boolean {
  return value != null && ["true", "1", "yes"].includes(value.toLowerCase());
}

/* ------------------------------------------------- read-only basis summary */

const computation = computed(() => props.workspace.computation);

const assessmentYearLine = computed(() => {
  const ctx = context.value;
  if (!ctx) return "—";
  // ay_label already reads "Assessment Year 2026-27 (Financial Year 2025-26)"; the tooltip
  // beside the field explains which year the income was actually earned in.
  return ctx.ay_label;
});

const regimeLine = computed(() => {
  const ctx = context.value;
  if (!ctx) return "—";
  return taxLabel(`regime.${ctx.regime_code}`, ctx.regime_label);
});

const filingTypeLabel = computed(() => {
  const ctx = context.value;
  const code = ctx?.filing_type ?? computation.value.filing_type;
  return taxLabel(`filingType.${code}`, code);
});

const auditLabel = computed(() =>
  context.value?.audit_applicable
    ? "Yes — the accounts of this year are subject to tax audit"
    : "No — the accounts of this year are not subject to tax audit",
);

const itrFormLabel = computed(() => {
  const code = context.value?.itr_form_code;
  if (!code) return "Not determined yet";
  return taxLabel(`itrForm.${code}`, `Income Tax Return form ${code}`);
});

/* ------------------------------------------------- company tax registration */

interface RegistrationForm {
  pan: string;
  tan: string;
  cin: string;
  assessee_class_code: string;
  residential_status: string;
}

const residentialStatusOptions: TaxOption[] = [
  option("Resident", "Resident"),
  option("Non-Resident", "Non-resident"),
  option("RNOR", "Resident but not ordinarily resident"),
];

const registrationForm = ref<RegistrationForm>({
  pan: "",
  tan: "",
  cin: "",
  assessee_class_code: "Company",
  residential_status: "Resident",
});
const registrationBusy = ref(false);
const registrationSaved = ref(false);
const registrationError = ref("");

watch(
  () => props.workspace.registration,
  (registration) => {
    registrationForm.value = {
      pan: registration?.pan ?? "",
      tan: registration?.tan ?? "",
      cin: registration?.cin ?? "",
      assessee_class_code:
        registration?.assessee_class_code ?? context.value?.entity_class_code ?? "Company",
      residential_status: registration?.residential_status ?? "Resident",
    };
  },
  { immediate: true, deep: true },
);

const panMissing = computed(() => registrationForm.value.pan.trim() === "");
const panMalformed = computed(() => {
  const pan = registrationForm.value.pan.trim().toUpperCase();
  return pan !== "" && !/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(pan);
});

async function saveRegistration(): Promise<void> {
  registrationBusy.value = true;
  registrationError.value = "";
  registrationSaved.value = false;
  const existing = props.workspace.registration;
  try {
    await api.put("/tax/registrations", {
      pan: registrationForm.value.pan.trim().toUpperCase() || null,
      tan: registrationForm.value.tan.trim().toUpperCase() || null,
      cin: registrationForm.value.cin.trim().toUpperCase() || null,
      assessee_class_code: registrationForm.value.assessee_class_code || "Company",
      residential_status: registrationForm.value.residential_status || "Resident",
      incorporation_date: existing?.incorporation_date ?? null,
      nature_of_business_codes: existing?.nature_of_business_codes ?? [],
      jurisdiction: existing?.jurisdiction ?? null,
      default_assessment_year: existing?.default_assessment_year ?? null,
      itr_efile_provider: existing?.itr_efile_provider ?? null,
      remarks: existing?.remarks ?? null,
    });
    registrationSaved.value = true;
    emit("changed");
  } catch (err: unknown) {
    registrationError.value = errorMessage(err);
  } finally {
    registrationBusy.value = false;
  }
}

/* ------------------------------------------------------- regime election */

interface ElectionForm {
  regime_code: string;
  elected_on: string;
  form_ack_no: string;
}

const electionForm = ref<ElectionForm>({ regime_code: "", elected_on: "", form_ack_no: "" });
const electionBusy = ref(false);
const electionSaved = ref(false);
const electionError = ref("");
const irrevocableAcknowledged = ref(false);

watch(
  () => props.workspace.election,
  (election) => {
    electionForm.value = {
      regime_code: election?.regime_code ?? context.value?.regime_code ?? "",
      elected_on: election?.elected_on ?? "",
      form_ack_no: election?.form_ack_no ?? "",
    };
    irrevocableAcknowledged.value = false;
  },
  { immediate: true, deep: true },
);

const selectedRegime = computed<TaxOption | null>(
  () => props.lookups.tax_regimes.find((o) => o.value === electionForm.value.regime_code) ?? null,
);

const electionFormCode = computed(() => selectedRegime.value?.meta?.election_form ?? "");
const electionIsIrrevocable = computed(() => isTrue(selectedRegime.value?.meta?.election_irrevocable));
const electionBlocked = computed(
  () => electionForm.value.regime_code === "" || (electionIsIrrevocable.value && !irrevocableAcknowledged.value),
);

async function saveElection(): Promise<void> {
  const ctx = context.value;
  if (!ctx) return;
  electionBusy.value = true;
  electionError.value = "";
  electionSaved.value = false;
  try {
    await api.post("/tax/elections", {
      ay_code: ctx.ay_code,
      regime_code: electionForm.value.regime_code,
      assessee_class_code: registrationForm.value.assessee_class_code || ctx.entity_class_code,
      elected_on: electionForm.value.elected_on || null,
      form_ack_no: electionForm.value.form_ack_no.trim() || null,
      irrevocable: electionIsIrrevocable.value,
      remarks: null,
    });
    electionSaved.value = true;
    emit("changed");
  } catch (err: unknown) {
    electionError.value = errorMessage(err);
  } finally {
    electionBusy.value = false;
  }
}

/* ---------------------------------------------- editable basis (this year) */

const basisBusy = ref("");
const basisSaved = ref("");
const basisError = ref("");

const filingType = ref(computation.value.filing_type);
const auditApplicable = ref(computation.value.audit_applicable !== false);
const returnFiledDate = ref(computation.value.return_filed_date ?? "");
const itrDueDateOverride = ref(props.workspace.computation.itr_due_date_override ?? "");

watch(
  () => props.workspace.computation,
  (doc) => {
    filingType.value = doc.filing_type;
    auditApplicable.value = doc.audit_applicable !== false;
    returnFiledDate.value = doc.return_filed_date ?? "";
    itrDueDateOverride.value = doc.itr_due_date_override ?? "";
  },
  { immediate: true, deep: true },
);

/**
 * The shell only autosaves income lines, adjustment lines and book profit. The four
 * basis fields below live on the computation record itself, so this panel saves them
 * directly — one field per request, nothing else touched.
 */
async function saveBasisField(field: string, value: string | boolean | null): Promise<void> {
  basisBusy.value = field;
  basisError.value = "";
  basisSaved.value = "";
  try {
    await api.put(`/tax/computations/${props.workspace.computation.id}`, { [field]: value });
    basisSaved.value = field;
    emit("changed");
  } catch (err: unknown) {
    basisError.value = errorMessage(err);
  } finally {
    basisBusy.value = "";
  }
}

/* -------------------------------------- copy from the previous year / books */

interface CopyPreviousYearResult {
  source_ay_code: string | null;
  income_lines_copied: number;
  adjustment_lines_copied: number;
  depreciation_blocks_carried: number;
  brought_forward_losses_available: number;
  notes: string[];
}

interface PopulateFromBooksResult {
  book_profit: string;
  accounting_depreciation: string;
  income_lines_written: number;
  adjustment_lines_written: number;
  notes: string[];
}

const copyOptions = ref<TaxCopyPreviousYearIn>({
  source_ay_code: null,
  copy_income_heads: true,
  copy_adjustments: true,
  carry_depreciation_blocks: true,
  overwrite_existing: false,
});
const copyBusy = ref(false);
const copyError = ref("");
const copyResult = ref<CopyPreviousYearResult | null>(null);

async function copyPreviousYear(): Promise<void> {
  copyBusy.value = true;
  copyError.value = "";
  copyResult.value = null;
  try {
    const response = await api.post<CopyPreviousYearResult>(
      `/tax/workspace/computation/${props.workspace.computation.id}/copy-previous-year`,
      copyOptions.value,
    );
    copyResult.value = response.data;
    emit("changed");
  } catch (err: unknown) {
    copyError.value = errorMessage(err);
  } finally {
    copyBusy.value = false;
  }
}

const booksOptions = ref<TaxPopulateFromBooksIn>({
  set_book_profit: true,
  replace_income_heads: true,
  add_accounting_depreciation_addback: true,
});
const booksBusy = ref(false);
const booksError = ref("");
const booksResult = ref<PopulateFromBooksResult | null>(null);

async function populateFromBooks(): Promise<void> {
  booksBusy.value = true;
  booksError.value = "";
  booksResult.value = null;
  try {
    const response = await api.post<PopulateFromBooksResult>(
      `/tax/workspace/computation/${props.workspace.computation.id}/populate-from-books`,
      booksOptions.value,
    );
    booksResult.value = response.data;
    emit("changed");
  } catch (err: unknown) {
    booksError.value = errorMessage(err);
  } finally {
    booksBusy.value = false;
  }
}

const showCopyDetail = ref(false);
</script>

<template>
  <div class="space-y-6" data-testid="tax-section-overview">
    <!-- ------------------------------------------------------ basis summary -->
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-slate-900">Basis of this computation</h2>
          <p class="mt-1 text-sm text-slate-600">
            Everything below is settled before the numbers are entered. It decides the rates, the
            due date and which reliefs are available.
          </p>
        </div>
        <StatusPill
          :status="String(computation.docstatus)"
          :label="taxLabel(`status.${computation.docstatus}`, computation.status)"
          size="sm"
        />
      </div>

      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 [&>*]:min-w-0">
        <DerivedField
          label="Assessment year"
          short="Assessment year"
          :value="assessmentYearLine"
          kind="text"
          emphasis="strong"
          explain="The assessment year is the year in which the income of the previous financial year is assessed to tax."
        />
        <DerivedField
          label="Entity class"
          :value="taxLabel(`entityClass.${context?.entity_class_code ?? ''}`, context?.entity_class_label ?? '—')"
          kind="text"
          explain="Taken from the company tax registration below. It selects the rate schedule and the return form."
        />
        <DerivedField
          label="Regime applied"
          :value="regimeLine"
          kind="text"
          :statutory-ref="context?.regime_statutory_ref ?? taxStatutoryRef(`regime.${context?.regime_code ?? ''}`)"
          explain="Taken from the regime election recorded for this assessment year, or the default regime for the entity class when there is no election."
        />
        <DerivedField
          label="Filing type"
          :value="filingTypeLabel"
          kind="text"
          explain="An original return, or a revised, belated or updated return that supersedes an earlier one."
        />
        <DerivedField
          label="Accounts audited under the Income-tax Act"
          short="Tax audit"
          :value="auditLabel"
          kind="text"
          explain="Tax audit applicability moves the return due date and changes how interest for late filing is charged."
        />
        <DerivedField
          label="Return form"
          :value="itrFormLabel"
          kind="text"
          explain="Derived from the entity class recorded on the company tax registration."
        />
        <DerivedField
          label="Return due date"
          :value="formatDate(context?.return_due_date ?? null)"
          kind="text"
          emphasis="strong"
          explain="Derived from the statutory due-date rules for this entity class together with the tax-audit flag above. An override entered below wins over the derived date."
        />
        <DerivedField
          v-if="visible('overview.bookProfit')"
          label="Book profit for Minimum Alternate Tax"
          short="Book profit (MAT)"
          :value="props.bookProfit || '0'"
          kind="money"
          :currency="context?.currency ?? 'INR'"
          explain="Entered in the Minimum Alternate Tax section, or filled by reading the books. It is the starting point for tax under Section 115JB."
        />
      </div>

      <p v-if="visible('overview.presumptive')" class="rounded-md bg-sky-50 px-3 py-2 text-sm text-sky-900">
        Income for this year is computed on a presumptive basis, so the detailed profit and loss
        adjustments are not required.
      </p>

      <p
        v-if="visible('overview.revisesComputation') && computation.revises_computation_id"
        class="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700"
      >
        This computation revises an earlier one for the same assessment year. The earlier
        computation stays on record and is not altered.
      </p>

      <div
        v-if="context && visible('overview.forfeitedNotice') && context.concessional_regime"
        class="rounded-md border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900"
        data-testid="overview-forfeited"
      >
        <p class="font-medium">
          Because this year is computed under {{ regimeLine }}, the following are not available:
        </p>
        <ul v-if="context.forfeited_incentives.length" class="mt-1 list-disc space-y-0.5 pl-5">
          <li v-for="item in context.forfeited_incentives" :key="item">
            {{ taxLabel(`forfeited.${item}`, item) }}
          </li>
        </ul>
        <p v-else class="mt-1">
          The concessional regime withdraws the specified deductions and incentives for this year.
        </p>
        <p class="mt-2">
          Fields for these reliefs are hidden across the workspace so they cannot be claimed by
          mistake.
        </p>
      </div>

      <p
        v-if="context?.regime_irrevocable"
        class="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900"
      >
        The election for this regime is irrevocable — once exercised it applies to this and every
        later assessment year.
      </p>
    </section>

    <!-- --------------------------------------------- company tax registration -->
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-4">
      <div>
        <h2 class="text-base font-semibold text-slate-900">Company tax registration</h2>
        <p class="mt-1 text-sm text-slate-600">
          Held once for the company and reused by every assessment year. Edit it here — there is no
          separate page to visit.
        </p>
      </div>

      <p
        v-if="panMissing"
        class="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900"
        data-testid="overview-pan-missing"
      >
        The Permanent Account Number is not on record. The return cannot be filed without it.
      </p>

      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 [&>*]:min-w-0">
        <div>
          <label class="form-label" for="overview-pan">
            Permanent Account Number
            <InfoTip
              text="The ten-character Permanent Account Number of the company, in the form AAAAA9999A. The fourth character identifies the class of assessee."
              size="sm"
            />
          </label>
          <input
            id="overview-pan"
            v-model="registrationForm.pan"
            class="form-input uppercase"
            data-field="overview.pan"
            :disabled="props.disabled || registrationBusy"
            placeholder="AAAAA9999A"
          />
          <p v-if="panMalformed" class="mt-1 text-xs text-amber-700">
            This does not look like a Permanent Account Number — five letters, four digits, then one
            letter.
          </p>
        </div>
        <div>
          <label class="form-label" for="overview-tan">
            Tax Deduction and Collection Account Number
            <InfoTip
              text="The ten-character Tax Deduction and Collection Account Number used when the company deducts tax at source."
              size="sm"
            />
          </label>
          <input
            id="overview-tan"
            v-model="registrationForm.tan"
            class="form-input uppercase"
            data-field="overview.tan"
            :disabled="props.disabled || registrationBusy"
          />
        </div>
        <div>
          <label class="form-label" for="overview-cin">
            Corporate Identity Number
            <InfoTip
              text="The Corporate Identity Number allotted by the Registrar of Companies on incorporation."
              size="sm"
            />
          </label>
          <input
            id="overview-cin"
            v-model="registrationForm.cin"
            class="form-input uppercase"
            data-field="overview.cin"
            :disabled="props.disabled || registrationBusy"
          />
        </div>
        <div>
          <span class="form-label">Entity class</span>
          <div data-field="overview.entity_class" tabindex="-1">
            <SearchSelect
              v-model="registrationForm.assessee_class_code"
              :options="props.lookups.entity_classes"
              :disabled="props.disabled || registrationBusy"
              placeholder="Select the class of assessee"
              testid="overview-entity-class"
            />
          </div>
        </div>
        <div>
          <span class="form-label">Residential status</span>
          <div data-field="overview.residential_status" tabindex="-1">
            <SearchSelect
              v-model="registrationForm.residential_status"
              :options="residentialStatusOptions"
              :disabled="props.disabled || registrationBusy"
              placeholder="Select the residential status"
              testid="overview-residential-status"
            />
          </div>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button
          class="btn-primary"
          type="button"
          :disabled="props.disabled || registrationBusy"
          data-testid="overview-save-registration"
          @click="saveRegistration"
        >
          {{ registrationBusy ? "Saving…" : "Save company tax registration" }}
        </button>
        <span v-if="registrationSaved" class="text-sm text-emerald-700">Saved.</span>
        <span v-if="registrationError" class="text-sm text-rose-700">{{ registrationError }}</span>
      </div>
    </section>

    <!-- ------------------------------------------------------ regime election -->
    <section
      v-if="visible('overview.regimeElection')"
      class="rounded-lg border border-slate-200 bg-white p-4 space-y-4"
    >
      <div>
        <h2 class="text-base font-semibold text-slate-900">
          Regime election for {{ context?.ay_label ?? "this assessment year" }}
        </h2>
        <p class="mt-1 text-sm text-slate-600">
          The regime decides the rate of tax and which deductions survive. Record the election here
          and the whole workspace follows it.
        </p>
      </div>

      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 [&>*]:min-w-0">
        <div>
          <span class="form-label">Regime</span>
          <div data-field="overview.regime_code" tabindex="-1">
            <SearchSelect
              v-model="electionForm.regime_code"
              :options="props.lookups.tax_regimes"
              :disabled="props.disabled || electionBusy"
              placeholder="Select the regime for this year"
              testid="overview-regime"
            />
          </div>
          <p v-if="selectedRegime?.statutory_ref" class="mt-1 text-xs text-slate-500">
            {{ selectedRegime.statutory_ref }}
          </p>
        </div>
        <div>
          <label class="form-label">Date of election</label>
          <div data-field="overview.elected_on" tabindex="-1">
            <DateField v-model="electionForm.elected_on" />
          </div>
        </div>
        <div v-if="electionFormCode">
          <label class="form-label" for="overview-ack">
            Acknowledgement number of Form {{ electionFormCode }}
            <InfoTip
              :text="`The election is exercised by filing Form ${electionFormCode} on the income-tax portal before the return is filed. Record the acknowledgement number here.`"
              size="sm"
            />
          </label>
          <input
            id="overview-ack"
            v-model="electionForm.form_ack_no"
            class="form-input"
            data-field="overview.form_ack_no"
            :disabled="props.disabled || electionBusy"
          />
        </div>
      </div>

      <div
        v-if="electionIsIrrevocable"
        class="rounded-md border border-rose-200 bg-rose-50 px-3 py-3 text-sm text-rose-900"
      >
        <p class="font-medium">This election cannot be withdrawn.</p>
        <p class="mt-1">
          Once recorded it applies to this assessment year and to every assessment year after it, and
          the reliefs the regime withdraws are lost for all of them.
        </p>
        <label class="mt-2 flex items-start gap-2">
          <input
            v-model="irrevocableAcknowledged"
            type="checkbox"
            class="mt-1"
            data-testid="overview-irrevocable-ack"
          />
          <span>I have confirmed with the company that this irrevocable election is intended.</span>
        </label>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button
          class="btn-primary"
          type="button"
          :disabled="props.disabled || electionBusy || electionBlocked"
          data-testid="overview-save-election"
          @click="saveElection"
        >
          {{ electionBusy ? "Saving…" : "Save regime election" }}
        </button>
        <span v-if="electionSaved" class="text-sm text-emerald-700">Saved.</span>
        <span v-if="electionError" class="text-sm text-rose-700">{{ electionError }}</span>
      </div>
    </section>

    <!-- ----------------------------------------------- basis fields for the year -->
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-4">
      <div>
        <h2 class="text-base font-semibold text-slate-900">This year's filing basis</h2>
        <p class="mt-1 text-sm text-slate-600">
          Each of these is saved on its own as soon as it changes, and the Live preview button
          in the header updates with it.
        </p>
      </div>

      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 [&>*]:min-w-0">
        <div>
          <span class="form-label">Filing type</span>
          <div data-field="overview.filing_type" tabindex="-1">
            <SearchSelect
              v-model="filingType"
              :options="props.lookups.filing_types"
              :disabled="props.disabled || basisBusy === 'filing_type'"
              placeholder="Select the filing type"
              testid="overview-filing-type"
              @update:model-value="(value: string) => saveBasisField('filing_type', value)"
            />
          </div>
        </div>

        <div v-if="visible('overview.auditApplicable')">
          <span class="form-label">
            Accounts audited under the Income-tax Act
            <InfoTip
              text="Tax audit applicability moves the return due date and decides the period for which interest for late filing runs."
              statutory-ref="Section 44AB"
              size="sm"
            />
          </span>
          <label class="mt-1 flex items-center gap-2 text-sm text-slate-700">
            <input
              v-model="auditApplicable"
              type="checkbox"
              data-field="overview.audit_applicable"
              :disabled="props.disabled || basisBusy === 'audit_applicable'"
              @change="saveBasisField('audit_applicable', auditApplicable)"
            />
            <span>The accounts of this year are subject to tax audit</span>
          </label>
        </div>

        <div>
          <label class="form-label">Date the return was filed</label>
          <div data-field="overview.return_filed_date" tabindex="-1">
            <DateField
              v-model="returnFiledDate"
              @update:model-value="(value: string) => saveBasisField('return_filed_date', value || null)"
            />
          </div>
          <p class="mt-1 text-xs text-slate-500">
            Leave this empty until the return is actually filed. Interest for late filing is measured
            from the due date to this date.
          </p>
        </div>

        <div v-if="visible('overview.itrDueDateOverride')">
          <label class="form-label">
            Return due date — override
            <InfoTip
              text="Use this only where an extension notified by the Central Board of Direct Taxes applies to this company. Leaving it empty keeps the statutory derived date."
              size="sm"
            />
          </label>
          <div data-field="overview.itr_due_date_override" tabindex="-1">
            <DateField
              v-model="itrDueDateOverride"
              @update:model-value="(value: string) => saveBasisField('itr_due_date_override', value || null)"
            />
          </div>
        </div>
      </div>

      <p v-if="basisSaved" class="text-sm text-emerald-700">Saved.</p>
      <p v-if="basisError" class="text-sm text-rose-700">{{ basisError }}</p>
    </section>

    <!-- --------------------------------------------------------- time-savers -->
    <div class="grid gap-4 lg:grid-cols-2">
      <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h2 class="text-base font-semibold text-slate-900">
          Copy from {{ context?.previous_ay_code ? taxLabel(`ay.${context.previous_ay_code}`, `assessment year ${context.previous_ay_code}`) : "the previous assessment year" }}
        </h2>
        <p class="text-sm text-slate-600">One click brings across the structure of last year's file:</p>
        <ul class="list-disc space-y-1 pl-5 text-sm text-slate-600">
          <li>the income heads and their descriptions, so only the amounts need attention</li>
          <li>the standing tax adjustments the company makes every year</li>
          <li>the depreciation blocks, with last year's closing written down value as this year's opening value</li>
          <li>a count of the brought forward losses still available for set-off</li>
        </ul>

        <button
          class="text-sm font-medium text-primary underline"
          type="button"
          @click="showCopyDetail = !showCopyDetail"
        >
          {{ showCopyDetail ? "Hide what is copied" : "Choose what is copied" }}
        </button>
        <div v-if="showCopyDetail" class="space-y-2 rounded-md bg-slate-50 p-3 text-sm text-slate-700">
          <label class="flex items-center gap-2">
            <input v-model="copyOptions.copy_income_heads" type="checkbox" />
            <span>Copy the income heads</span>
          </label>
          <label class="flex items-center gap-2">
            <input v-model="copyOptions.copy_adjustments" type="checkbox" />
            <span>Copy the tax adjustments</span>
          </label>
          <label class="flex items-center gap-2">
            <input v-model="copyOptions.carry_depreciation_blocks" type="checkbox" />
            <span>Carry the depreciation blocks and their opening written down values</span>
          </label>
          <label class="flex items-center gap-2">
            <input v-model="copyOptions.overwrite_existing" type="checkbox" />
            <span>Replace what is already entered for this year</span>
          </label>
        </div>

        <button
          class="btn-secondary"
          type="button"
          data-field="overview.copyPreviousYear"
          data-testid="overview-copy-previous-year"
          :disabled="props.disabled || copyBusy"
          @click="copyPreviousYear"
        >
          {{ copyBusy ? "Copying…" : "Copy from the previous assessment year" }}
        </button>

        <div v-if="copyResult" class="rounded-md bg-emerald-50 p-3 text-sm text-emerald-900" data-testid="overview-copy-result">
          <p class="font-medium">
            Copied from {{ copyResult.source_ay_code ? `assessment year ${copyResult.source_ay_code}` : "the previous assessment year" }}.
          </p>
          <ul class="mt-1 space-y-0.5">
            <li>{{ copyResult.income_lines_copied }} income line(s)</li>
            <li>{{ copyResult.adjustment_lines_copied }} tax adjustment(s)</li>
            <li>{{ copyResult.depreciation_blocks_carried }} depreciation block(s) carried forward</li>
            <li>{{ copyResult.brought_forward_losses_available }} brought forward loss(es) available for set-off</li>
          </ul>
          <ul v-if="copyResult.notes.length" class="mt-2 list-disc space-y-0.5 pl-5">
            <li v-for="note in copyResult.notes" :key="note">{{ note }}</li>
          </ul>
        </div>
        <p v-if="copyError" class="text-sm text-rose-700">{{ copyError }}</p>
      </section>

      <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h2 class="text-base font-semibold text-slate-900">Populate from the books</h2>
        <p class="text-sm text-slate-600">
          Reads the general ledger for the financial year
          {{ context?.financial_year_label ?? "" }} and fills in the net profit as business income,
          together with the add-back of the depreciation charged in the books — because the
          Income-tax Act allows its own depreciation instead.
        </p>
        <div class="space-y-2 rounded-md bg-slate-50 p-3 text-sm text-slate-700">
          <label class="flex items-center gap-2">
            <input v-model="booksOptions.set_book_profit" type="checkbox" />
            <span>Set the book profit used for Minimum Alternate Tax</span>
          </label>
          <label class="flex items-center gap-2">
            <input v-model="booksOptions.replace_income_heads" type="checkbox" />
            <span>Replace the income lines with the profit from the books</span>
          </label>
          <label class="flex items-center gap-2">
            <input v-model="booksOptions.add_accounting_depreciation_addback" type="checkbox" />
            <span>Add back the depreciation charged in the books</span>
          </label>
        </div>

        <button
          class="btn-secondary"
          type="button"
          data-field="overview.populateFromBooks"
          data-testid="overview-populate-from-books"
          :disabled="props.disabled || booksBusy"
          @click="populateFromBooks"
        >
          {{ booksBusy ? "Reading the books…" : "Populate from the books" }}
        </button>

        <div v-if="booksResult" class="space-y-2 rounded-md bg-emerald-50 p-3 text-sm text-emerald-900" data-testid="overview-books-result">
          <p>
            Net profit as per the books:
            <span class="font-semibold">{{ formatCurrency(booksResult.book_profit, context?.currency ?? "INR") }}</span>
          </p>
          <p>
            Depreciation charged in the books:
            <span class="font-semibold">
              {{ formatCurrency(booksResult.accounting_depreciation, context?.currency ?? "INR") }}
            </span>
          </p>
          <p>
            {{ booksResult.income_lines_written }} income line(s) and
            {{ booksResult.adjustment_lines_written }} adjustment(s) written.
          </p>
          <ul v-if="booksResult.notes.length" class="list-disc space-y-0.5 pl-5">
            <li v-for="note in booksResult.notes" :key="note">{{ note }}</li>
          </ul>
        </div>
        <p v-if="booksError" class="text-sm text-rose-700">{{ booksError }}</p>
        <button
          class="text-sm font-medium text-primary underline"
          type="button"
          @click="emit('navigate', 'income')"
        >
          Review the statement of income
        </button>
      </section>
    </div>
  </div>
</template>
