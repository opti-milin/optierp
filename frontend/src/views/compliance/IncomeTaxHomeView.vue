<script setup lang="ts">
/**
 * Income Tax landing — a year-by-year status board, not a record table.
 *
 * One round trip (`GET /tax/workspace/bootstrap`) supplies the registration state,
 * every assessment year with its status / due date / next action, and the starting
 * templates. Every action from here lands inside the computation workspace, which is
 * where the work actually happens.
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { api } from "@/api/client";
import InfoTip from "@/components/shared/InfoTip.vue";
import SearchSelect from "@/components/shared/SearchSelect.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { useTaxLookups } from "@/composables/useTaxLookups";
import { taxHelp, taxLabel } from "@/config/taxTerminology";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxComputation,
  TaxOption,
  TaxSectionKey,
  TaxTemplate,
  TaxWorkspaceBootstrap,
  TaxYearSummary,
  TaxTemplateApplyIn,
} from "@/types/taxation";
import { formatCurrency } from "@/utils/format";

const router = useRouter();
const { lookups, set: adoptLookups } = useTaxLookups();

const bootstrap = ref<TaxWorkspaceBootstrap | null>(null);
const loading = ref(true);
const busy = ref(false);
const error = ref("");

// Template chooser (an inline panel, never a separate page).
const chooserOpen = ref(false);
const chooserAyCode = ref("");
const chosenTemplateCode = ref("");
const carryPreviousYear = ref(true);
const pullProfitFromBooks = ref(false);

const SECTION_KEYS: readonly string[] = [
  "overview",
  "income",
  "adjustments",
  "depreciation",
  "losses",
  "mat",
  "credits",
  "challans",
  "reconciliation",
  "interest",
  "summary",
  "review",
  "filing",
  "audit",
];

function sectionKey(value: string | null | undefined): TaxSectionKey {
  return (SECTION_KEYS.includes(value ?? "") ? value : "overview") as TaxSectionKey;
}

function messageOf(e: unknown): string {
  const detail = (e as ErrorEnvelope | null)?.detail;
  if (detail) return detail;
  return "Something went wrong while talking to the server. Please try again.";
}

const years = computed<TaxYearSummary[]>(() => {
  const list = [...(bootstrap.value?.years ?? [])];
  // Current year first, then most recent assessment year downwards.
  list.sort((a, b) => {
    if (a.is_current !== b.is_current) return a.is_current ? -1 : 1;
    return b.ay_code.localeCompare(a.ay_code);
  });
  return list;
});

const templates = computed<TaxTemplate[]>(() => bootstrap.value?.templates ?? []);
const registrationComplete = computed(() => bootstrap.value?.registration_complete === true);

const assessmentYearOptions = computed<TaxOption[]>(() => {
  if (lookups.value.assessment_years.length) return lookups.value.assessment_years;
  return years.value.map((year) => ({
    value: year.ay_code,
    label: year.ay_label,
    hint: year.financial_year_label,
  }));
});

const chosenTemplate = computed<TaxTemplate | null>(
  () => templates.value.find((t) => t.code === chosenTemplateCode.value) ?? null,
);

const longDateFormatter = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function longDate(value: string | null | undefined): string {
  if (!value) return "not yet known";
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return longDateFormatter.format(parsed);
}

function daysFromToday(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((parsed.getTime() - today.getTime()) / 86_400_000);
}

/** Plain-English urgency, only once the date is within thirty days or already past. */
function urgencyNote(value: string | null | undefined): string {
  const days = daysFromToday(value);
  if (days === null) return "";
  if (days < 0) {
    return days === -1
      ? "The due date passed yesterday."
      : `The due date passed ${Math.abs(days)} days ago.`;
  }
  if (days === 0) return "The due date is today.";
  if (days === 1) return "Only one day left.";
  if (days <= 30) return `Only ${days} days left.`;
  return "";
}

function isOverdue(value: string | null | undefined): boolean {
  const days = daysFromToday(value);
  return days !== null && days < 0;
}

function amountLabel(year: TaxYearSummary): string {
  return Number(year.net_payable ?? 0) < 0
    ? taxLabel("term.refundDue", "Refund Due")
    : taxLabel("term.netPayable", "Net Tax Payable");
}

function amountValue(year: TaxYearSummary): string {
  if (year.net_payable === null) return "—";
  return formatCurrency(Math.abs(Number(year.net_payable)));
}

function moneyOrDash(value: string | null): string {
  return value === null ? "—" : formatCurrency(value);
}

/** The soonest few statutory dates across every year, for the deadlines strip. */
const upcomingDeadlines = computed(() =>
  years.value
    .filter((year) => Boolean(year.next_due_date && year.next_due_label))
    .sort((a, b) => (a.next_due_date ?? "").localeCompare(b.next_due_date ?? ""))
    .slice(0, 4),
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const { data } = await api.get<TaxWorkspaceBootstrap>("/tax/workspace/bootstrap");
    bootstrap.value = data;
    adoptLookups(data.lookups);
    if (!chooserAyCode.value) chooserAyCode.value = data.default_ay_code || data.current_ay_code;
  } catch (e: unknown) {
    error.value = messageOf(e);
  } finally {
    loading.value = false;
  }
}

function openWorkspace(year: TaxYearSummary): void {
  if (!year.computation_id) {
    openChooser(year.ay_code);
    return;
  }
  void router.push({
    name: "tax-workspace",
    params: { id: year.computation_id },
    query: { section: sectionKey(year.next_action_section) },
  });
}

function openChooser(ayCode: string): void {
  chooserAyCode.value = ayCode;
  if (!chosenTemplateCode.value) {
    const preferred =
      templates.value.find((t) => t.recommended && t.available) ??
      templates.value.find((t) => t.available);
    chosenTemplateCode.value = preferred?.code ?? "";
  }
  chooserOpen.value = true;
}

function chooseTemplate(template: TaxTemplate): void {
  if (!template.available) return;
  chosenTemplateCode.value = template.code;
}

async function startFromTemplate(): Promise<void> {
  const template = chosenTemplate.value;
  if (!template || !chooserAyCode.value) {
    error.value = "Choose an assessment year and a starting point first.";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const payload: TaxTemplateApplyIn = {
      template_code: template.code,
      ay_code: chooserAyCode.value,
      copy_from_previous_year: carryPreviousYear.value,
      populate_from_books: pullProfitFromBooks.value,
    };
    const { data } = await api.post<TaxComputation>("/tax/workspace/templates/apply", payload);
    await router.push({
      name: "tax-workspace",
      params: { id: data.id },
      query: { section: "overview" },
    });
  } catch (e: unknown) {
    error.value = messageOf(e);
  } finally {
    busy.value = false;
  }
}

async function startBlank(): Promise<void> {
  if (!chooserAyCode.value) {
    error.value = "Choose an assessment year first.";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const { data } = await api.post<TaxComputation>("/tax/computations", {
      ay_code: chooserAyCode.value,
    });
    await router.push({
      name: "tax-workspace",
      params: { id: data.id },
      query: { section: "overview" },
    });
  } catch (e: unknown) {
    error.value = messageOf(e);
  } finally {
    busy.value = false;
  }
}

function templatePrepopulates(template: TaxTemplate): string[] {
  const notes: string[] = [];
  if (template.income_heads.length) {
    notes.push(
      `Income heads: ${template.income_heads
        .map((head) => taxLabel(`head.${head}`, head))
        .join(", ")}`,
    );
  }
  if (template.suggested_adjustments.length) {
    notes.push(
      `${template.suggested_adjustments.length} suggested tax adjustment${
        template.suggested_adjustments.length === 1 ? "" : "s"
      }`,
    );
  }
  notes.push(
    template.audit_applicable
      ? "Books of account treated as subject to audit"
      : "Books of account treated as not subject to audit",
  );
  if (template.mat_applicable) {
    notes.push(taxLabel("term.minimumAlternateTax", "Minimum Alternate Tax (Section 115JB)") + " applies");
  }
  if (template.requires_book_profit) {
    notes.push(`${taxLabel("term.bookProfit", "Book Profit")} is needed for this starting point`);
  }
  for (const forfeited of template.forfeited_incentives) {
    notes.push(`Forfeited: ${taxLabel(forfeited, forfeited)}`);
  }
  return notes;
}

onMounted(load);
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-6" data-testid="income-tax-home">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-2xl font-semibold text-gray-900">Income Tax</h1>
        <p class="mt-1 max-w-3xl text-sm text-gray-600">
          One place for every assessment year: see where each year stands, what it will cost, when
          the return is due, and what to do next. The computation itself happens in the workspace.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <RouterLink class="btn-secondary" to="/income-tax-settings">
          Company Tax Registration
        </RouterLink>
        <RouterLink class="btn-secondary" to="/tax/catalogue">Statutory Catalogue</RouterLink>
      </div>
    </header>

    <p
      v-if="error"
      class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
      data-testid="income-tax-home-error"
    >
      {{ error }}
    </p>

    <p v-if="loading" class="py-10 text-center text-sm text-gray-500">Loading your tax years…</p>

    <template v-else>
      <!-- Setup gate: without the identifiers below no return can be filed. -->
      <section
        v-if="!registrationComplete"
        class="rounded-lg border border-amber-300 bg-amber-50 p-5"
        data-testid="income-tax-setup-prompt"
      >
        <h2 class="text-sm font-semibold text-amber-900">Finish the company tax registration</h2>
        <p class="mt-1 max-w-3xl text-sm text-amber-800">
          A return cannot be filed until this company's
          {{ taxLabel("term.pan", "Permanent Account Number") }} (PAN) and
          {{ taxLabel("term.entityClass", "Entity Class") }} are recorded. Both decide which rates
          apply, so the computation is only provisional until they are set.
        </p>
        <RouterLink class="btn-primary mt-3" to="/income-tax-settings">
          Open Company Tax Registration
        </RouterLink>
      </section>

      <!-- Next statutory dates across all years. -->
      <section
        v-if="upcomingDeadlines.length"
        class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
        data-testid="income-tax-deadlines"
      >
        <h2 class="mb-3 text-sm font-semibold text-gray-900">Next deadlines</h2>
        <ul class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <li
            v-for="year in upcomingDeadlines"
            :key="`${year.ay_code}-deadline`"
            class="rounded-md border p-3"
            :class="
              isOverdue(year.next_due_date)
                ? 'border-red-200 bg-red-50'
                : 'border-gray-200 bg-gray-50'
            "
          >
            <div class="text-sm font-medium text-gray-900">{{ year.next_due_label }}</div>
            <div class="text-sm text-gray-700">{{ longDate(year.next_due_date) }}</div>
            <div class="mt-1 text-xs text-gray-500">{{ year.ay_label }}</div>
            <div
              v-if="urgencyNote(year.next_due_date)"
              class="mt-1 text-xs font-medium"
              :class="isOverdue(year.next_due_date) ? 'text-red-700' : 'text-amber-700'"
            >
              {{ urgencyNote(year.next_due_date) }}
            </div>
          </li>
        </ul>
      </section>

      <!-- Nothing to work on: the statutory catalogue has no years loaded. -->
      <section
        v-if="!years.length"
        class="rounded-lg border border-gray-200 bg-white p-8 text-center shadow-sm"
        data-testid="income-tax-empty"
      >
        <h2 class="text-base font-semibold text-gray-900">No assessment years are available yet</h2>
        <p class="mx-auto mt-2 max-w-xl text-sm text-gray-600">
          The statutory catalogue does not have any assessment year loaded for this installation,
          so there is nothing to compute against. The catalogue carries the official rates,
          surcharge bands and due dates published for each year; once a year is loaded it will
          appear here automatically.
        </p>
        <RouterLink class="btn-primary mt-4" to="/tax/catalogue">
          Open the Statutory Catalogue
        </RouterLink>
      </section>

      <!-- One card per assessment year. -->
      <section v-else class="space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-sm font-semibold text-gray-900">Assessment years</h2>
          <button
            type="button"
            class="btn-secondary"
            data-testid="income-tax-start-any-year"
            @click="openChooser(chooserAyCode || years[0]!.ay_code)"
          >
            Start a year
          </button>
        </div>

        <article
          v-for="year in years"
          :key="year.ay_code"
          class="rounded-lg border bg-white p-5 shadow-sm"
          :class="year.is_current ? 'border-primary/40 ring-1 ring-primary/20' : 'border-gray-200'"
          data-testid="income-tax-year-row"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <!-- ay_label already reads "Assessment Year 2026-27 (Financial Year 2025-26)". -->
                <h3 class="text-base font-semibold text-gray-900">{{ year.ay_label }}</h3>
                <span
                  v-if="year.is_current"
                  class="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
                >
                  Current year
                </span>
                <StatusPill :status="year.status" size="sm" />
                <InfoTip
                  :text="
                    taxHelp('term.assessmentYear') ??
                    'The year in which the income of the previous year is assessed to tax.'
                  "
                  :title="taxLabel('term.assessmentYear', 'Assessment Year')"
                  size="sm"
                />
              </div>
              <p class="mt-1 text-sm text-gray-600">
                {{ year.regime_label ?? "Tax regime not elected yet" }}
                <span v-if="year.computation_name" class="text-gray-400">
                  · {{ year.computation_name }}
                </span>
              </p>
              <p
                v-if="year.filing_status"
                class="mt-1 text-sm text-gray-600"
                data-testid="income-tax-year-filing"
              >
                Return filing: {{ year.filing_status
                }}<span v-if="year.ack_no">
                  · {{ taxLabel("term.acknowledgementNumber", "Acknowledgement Number") }}
                  {{ year.ack_no }}</span
                >
              </p>
              <div class="mt-2 flex flex-wrap gap-2">
                <span
                  v-if="year.blocking_count > 0"
                  class="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700"
                >
                  {{ year.blocking_count }} issue{{ year.blocking_count === 1 ? "" : "s" }} to fix
                  before filing
                </span>
                <span
                  v-if="year.advisory_count > 0"
                  class="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                >
                  {{ year.advisory_count }} point{{ year.advisory_count === 1 ? "" : "s" }} to
                  review
                </span>
              </div>
            </div>

            <div class="flex w-full shrink-0 flex-col items-stretch gap-2 sm:w-auto sm:min-w-[14rem]">
              <button
                v-if="year.computation_id"
                type="button"
                class="btn-primary"
                data-testid="income-tax-year-action"
                @click="openWorkspace(year)"
              >
                {{ year.next_action }}
              </button>
              <button
                v-else
                type="button"
                class="btn-primary"
                data-testid="income-tax-start-year"
                @click="openChooser(year.ay_code)"
              >
                Start this year
              </button>
              <RouterLink
                v-if="year.computation_id"
                class="btn-secondary text-center"
                :to="{
                  name: 'tax-workspace',
                  params: { id: year.computation_id },
                  query: { section: 'summary' },
                }"
              >
                Statement of Total Income
              </RouterLink>
            </div>
          </div>

          <dl class="mt-4 grid grid-cols-1 gap-3 border-t border-gray-100 pt-4 sm:grid-cols-2 lg:grid-cols-4">
            <div class="min-w-0 rounded-md bg-gray-50 px-3 py-2">
              <dt class="text-xs font-medium uppercase tracking-wide text-gray-500">
                {{ taxLabel("term.totalIncome", "Total Income") }}
              </dt>
              <dd class="mt-1 break-words text-sm font-semibold tabular-nums text-gray-900">
                {{ moneyOrDash(year.total_income) }}
              </dd>
            </div>
            <div class="min-w-0 rounded-md bg-gray-50 px-3 py-2">
              <dt class="text-xs font-medium uppercase tracking-wide text-gray-500">
                {{ amountLabel(year) }}
              </dt>
              <dd class="mt-1 break-words text-sm font-semibold tabular-nums text-gray-900">
                {{ amountValue(year) }}
              </dd>
            </div>
            <div class="min-w-0 rounded-md bg-gray-50 px-3 py-2">
              <dt class="text-xs font-medium uppercase tracking-wide text-gray-500">
                Taxes Already Paid
              </dt>
              <dd class="mt-1 break-words text-sm font-semibold tabular-nums text-gray-900">
                {{ formatCurrency(year.taxes_paid) }}
              </dd>
            </div>
            <div class="min-w-0 rounded-md bg-gray-50 px-3 py-2">
              <dt class="text-xs font-medium uppercase tracking-wide text-gray-500">
                {{ taxLabel("term.returnDueDate", "Due Date for Filing the Return") }}
              </dt>
              <dd class="mt-1 break-words text-sm font-semibold text-gray-900">
                {{ longDate(year.return_due_date) }}
              </dd>
              <dd
                v-if="urgencyNote(year.return_due_date)"
                class="mt-0.5 text-xs font-medium"
                :class="isOverdue(year.return_due_date) ? 'text-red-700' : 'text-amber-700'"
              >
                {{ urgencyNote(year.return_due_date) }}
              </dd>
            </div>
          </dl>
        </article>
      </section>

      <!-- Inline template chooser — starting a year never leaves this page. -->
      <section
        v-if="chooserOpen"
        class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
        data-testid="income-tax-template-chooser"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-sm font-semibold text-gray-900">Start a computation</h2>
            <p class="mt-1 max-w-2xl text-sm text-gray-600">
              Pick the assessment year and a starting point. A starting point only pre-fills the
              setup and the usual adjustments — every figure stays editable afterwards.
            </p>
          </div>
          <button type="button" class="btn-secondary" @click="chooserOpen = false">Close</button>
        </div>

        <div class="mt-4 max-w-sm">
          <label class="form-label" for="income-tax-chooser-year">
            {{ taxLabel("term.assessmentYear", "Assessment Year") }}
          </label>
          <SearchSelect
            id="income-tax-chooser-year"
            :model-value="chooserAyCode"
            :options="assessmentYearOptions"
            placeholder="Choose an assessment year"
            testid="income-tax-chooser-year"
            @update:model-value="chooserAyCode = $event"
          />
        </div>

        <div class="mt-4 grid gap-3 md:grid-cols-2">
          <button
            v-for="template in templates"
            :key="template.code"
            type="button"
            class="rounded-md border p-4 text-left transition"
            :class="[
              template.available ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
              chosenTemplateCode === template.code && template.available
                ? 'border-primary bg-primary/5 ring-1 ring-primary/30'
                : 'border-gray-200 bg-white hover:border-gray-300',
            ]"
            :disabled="!template.available"
            data-testid="income-tax-template-card"
            @click="chooseTemplate(template)"
          >
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-sm font-semibold text-gray-900">{{ template.title }}</span>
              <span
                v-if="template.recommended"
                class="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800"
              >
                Recommended
              </span>
            </div>
            <p class="mt-1 text-sm text-gray-600">{{ template.description }}</p>
            <p class="mt-2 text-sm text-gray-700">
              {{ taxLabel(`regime.${template.regime_code}`, template.regime_code) }}
              <span v-if="template.regime_statutory_ref" class="text-gray-500">
                · {{ template.regime_statutory_ref }}
              </span>
            </p>
            <ul class="mt-2 space-y-0.5 text-xs text-gray-600">
              <li v-for="line in templatePrepopulates(template)" :key="line">• {{ line }}</li>
            </ul>
            <ul v-if="template.notes.length" class="mt-2 space-y-0.5 text-xs text-gray-500">
              <li v-for="note in template.notes" :key="note">{{ note }}</li>
            </ul>
            <p v-if="!template.available" class="mt-2 text-xs font-medium text-amber-700">
              {{ template.unavailable_reason ?? "Not available for this assessment year." }}
            </p>
          </button>
        </div>

        <div class="mt-4 space-y-2 text-sm text-gray-800">
          <label class="flex items-start gap-2">
            <input
              v-model="carryPreviousYear"
              type="checkbox"
              class="mt-0.5 rounded border-gray-300"
              data-testid="income-tax-carry-forward"
            />
            <span>
              Also carry forward last year's structure and balances — the same income heads and
              standing adjustments, the closing written down value of each block of assets as this
              year's opening value, and the losses still available for set-off.
            </span>
          </label>
          <label class="flex items-start gap-2">
            <input
              v-model="pullProfitFromBooks"
              type="checkbox"
              class="mt-0.5 rounded border-gray-300"
              data-testid="income-tax-from-books"
            />
            <span>
              Also pull the profit from the books — the net profit recorded in the accounts for that
              financial year, together with the depreciation charged in the books.
            </span>
          </label>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            class="btn-primary"
            :disabled="busy || !chosenTemplate"
            data-testid="income-tax-template-apply"
            @click="startFromTemplate"
          >
            {{ busy ? "Working…" : "Start with this starting point" }}
          </button>
          <button
            type="button"
            class="btn-secondary"
            :disabled="busy"
            data-testid="income-tax-start-blank"
            @click="startBlank"
          >
            Start blank instead
          </button>
        </div>
      </section>
    </template>
  </div>
</template>
