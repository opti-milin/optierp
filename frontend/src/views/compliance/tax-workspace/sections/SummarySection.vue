<script setup lang="ts">
/**
 * Statement of Total Income — the computation as a reviewer reads it, with the
 * variance against last year and against the books of account.
 *
 * Laid out like a CA computation sheet: Particulars | working amount | total.
 * Detail lines (indented, normal emphasis) sit in the working column; top-level
 * figures and subtotals/totals sit in the outer amount column so the eye can
 * follow the hierarchy without ragged alignment.
 */
import { computed, onMounted } from "vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import type {
  TaxSectionEmits,
  TaxSectionProps,
  TaxStatementRow,
  TaxStatementSection,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const currency = computed(() => context.value?.currency ?? "INR");

const statement = computed(() => props.statement);
const sections = computed<TaxStatementSection[]>(() => statement.value?.sections ?? []);

/** Indent step matches a standard computation sheet (≈ one tab stop). */
const INDENT_REM = 1.5;

function money(value: string | null): string {
  if (value === null || value === "") return "";
  return formatCurrency(value, currency.value);
}

function hasAmount(row: TaxStatementRow): boolean {
  return row.amount !== null && row.amount !== "";
}

/**
 * Outer column = carry-forward / subtotal / total figures.
 * Working column = supporting line items under a group.
 */
function amountInOuterColumn(row: TaxStatementRow): boolean {
  if (!hasAmount(row)) return false;
  if (row.emphasis === "total" || row.emphasis === "subtotal") return true;
  return row.indent === 0;
}

function workingAmount(row: TaxStatementRow): string {
  if (!hasAmount(row) || amountInOuterColumn(row)) return "";
  return money(row.amount);
}

function outerAmount(row: TaxStatementRow): string {
  if (!hasAmount(row) || !amountInOuterColumn(row)) return "";
  return money(row.amount);
}

function isGroupHeader(row: TaxStatementRow): boolean {
  return !hasAmount(row) && row.emphasis === "normal";
}

function rowClass(row: TaxStatementRow): string {
  if (row.emphasis === "total") {
    return "font-semibold text-slate-900 border-t-2 border-slate-300 bg-slate-50/60";
  }
  if (row.emphasis === "subtotal") {
    return "font-medium text-slate-900 border-t border-slate-200";
  }
  if (isGroupHeader(row)) {
    return "font-medium text-slate-800";
  }
  return "text-slate-700";
}

function labelPad(row: TaxStatementRow): Record<string, string> {
  return { paddingLeft: `${row.indent * INDENT_REM}rem` };
}

const basisLabel = computed(() =>
  (statement.value?.basis_applied ?? "Normal").toUpperCase() === "MAT"
    ? "Minimum Alternate Tax"
    : "Normal provisions",
);

function printStatement(): void {
  window.print();
}

onMounted(() => {
  if (!statement.value) emit("load-statement");
});
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-summary">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-2">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-slate-900">
            Statement of Total Income and Tax Computation
          </h2>
          <p v-if="statement" class="mt-1 text-sm text-slate-600">
            {{ statement.entity_label }}
            <span v-if="statement.pan"> · Permanent Account Number {{ statement.pan }}</span>
            · {{ statement.regime_label }}
          </p>
        </div>
        <div class="flex items-center gap-2">
          <StatusPill
            v-if="statement"
            :status="basisLabel === 'Minimum Alternate Tax' ? 'in-progress' : 'complete'"
            :label="`Taxed on the ${basisLabel.toLowerCase()}`"
            size="md"
          />
          <button
            type="button"
            class="btn-secondary"
            data-testid="summary-refresh"
            @click="emit('load-statement')"
          >
            Refresh
          </button>
          <button
            v-if="statement"
            type="button"
            class="btn-secondary"
            data-testid="summary-print"
            @click="printStatement"
          >
            Print
          </button>
        </div>
      </div>

      <p v-if="!statement" class="text-sm text-slate-500" data-testid="summary-loading">
        Preparing the statement…
      </p>

      <ul
        v-if="statement && statement.notes.length"
        class="space-y-1 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
        data-testid="summary-notes"
      >
        <li v-for="(note, index) in statement.notes" :key="index">• {{ note }}</li>
      </ul>
    </section>

    <section
      v-for="section in sections"
      :key="section.key"
      class="overflow-hidden rounded-lg border border-slate-200 bg-white"
      :data-testid="`summary-section-${section.key}`"
    >
      <h3 class="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-900">
        {{ section.title }}
      </h3>
      <table class="w-full table-fixed text-sm">
        <colgroup>
          <col />
          <col class="w-[9.5rem]" />
          <col class="w-[9.5rem]" />
        </colgroup>
        <thead>
          <tr class="border-b border-slate-100 text-xs font-medium uppercase tracking-wide text-slate-500">
            <th scope="col" class="px-4 py-1.5 text-left font-medium">Particulars</th>
            <th scope="col" class="px-3 py-1.5 text-right font-medium">₹</th>
            <th scope="col" class="px-3 py-1.5 text-right font-medium">₹</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in section.rows" :key="row.key" :class="rowClass(row)">
            <td class="align-top px-4 py-1.5">
              <div class="min-w-0" :style="labelPad(row)">
                <div class="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                  <span>{{ row.label }}</span>
                  <span
                    v-if="row.statutory_ref"
                    class="shrink-0 text-xs font-normal text-slate-500"
                  >
                    ({{ row.statutory_ref }})
                  </span>
                  <InfoTip
                    v-if="row.note && row.emphasis === 'normal'"
                    :text="row.note"
                    size="sm"
                  />
                </div>
                <p
                  v-if="row.note && row.emphasis !== 'normal'"
                  class="mt-0.5 text-xs font-normal text-slate-500"
                >
                  {{ row.note }}
                </p>
              </div>
            </td>
            <td
              class="align-top whitespace-nowrap px-3 py-1.5 text-right tabular-nums text-slate-700"
            >
              {{ workingAmount(row) }}
            </td>
            <td
              class="align-top whitespace-nowrap px-3 py-1.5 text-right tabular-nums"
              :class="
                row.emphasis === 'total' || row.emphasis === 'subtotal'
                  ? 'text-slate-900'
                  : 'text-slate-800'
              "
            >
              {{ outerAmount(row) }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section
      v-if="statement && statement.variance_previous_year.length"
      class="overflow-hidden rounded-lg border border-slate-200 bg-white"
      data-testid="summary-variance-previous-year"
    >
      <h3 class="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-900">
        Compared with the previous assessment year
        <InfoTip
          text="Movement against the last computed return, so an unexpected jump is visible before the return is filed."
          size="sm"
        />
      </h3>
      <table class="w-full table-fixed text-sm">
        <colgroup>
          <col />
          <col class="w-[9.5rem]" />
        </colgroup>
        <thead>
          <tr class="border-b border-slate-100 text-xs font-medium uppercase tracking-wide text-slate-500">
            <th scope="col" class="px-4 py-1.5 text-left font-medium">Particulars</th>
            <th scope="col" class="px-3 py-1.5 text-right font-medium">Change (₹)</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in statement.variance_previous_year"
            :key="row.key"
            class="text-slate-700"
          >
            <td class="align-top px-4 py-1.5">
              <span>{{ row.label }}</span>
              <span v-if="row.note" class="mt-0.5 block text-xs text-slate-500">{{ row.note }}</span>
            </td>
            <td class="align-top whitespace-nowrap px-3 py-1.5 text-right tabular-nums">
              {{ money(row.amount) }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section
      v-if="statement && statement.variance_books.length"
      class="overflow-hidden rounded-lg border border-slate-200 bg-white"
      data-testid="summary-variance-books"
    >
      <h3 class="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-900">
        Reconciled with the books of account
        <InfoTip
          text="The bridge from profit as per the books to total income chargeable to tax, which a reviewer will look for."
          size="sm"
        />
      </h3>
      <table class="w-full table-fixed text-sm">
        <colgroup>
          <col />
          <col class="w-[9.5rem]" />
          <col class="w-[9.5rem]" />
        </colgroup>
        <thead>
          <tr class="border-b border-slate-100 text-xs font-medium uppercase tracking-wide text-slate-500">
            <th scope="col" class="px-4 py-1.5 text-left font-medium">Particulars</th>
            <th scope="col" class="px-3 py-1.5 text-right font-medium">₹</th>
            <th scope="col" class="px-3 py-1.5 text-right font-medium">₹</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in statement.variance_books" :key="row.key" :class="rowClass(row)">
            <td class="align-top px-4 py-1.5">
              <div :style="labelPad(row)">{{ row.label }}</div>
            </td>
            <td class="align-top whitespace-nowrap px-3 py-1.5 text-right tabular-nums">
              {{ workingAmount(row) }}
            </td>
            <td class="align-top whitespace-nowrap px-3 py-1.5 text-right tabular-nums">
              {{ outerAmount(row) }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <p class="text-sm text-slate-600">
      Ready to file?
      <button
        class="font-medium text-primary underline"
        type="button"
        data-testid="summary-go-review"
        @click="emit('navigate', 'review')"
      >
        Review and validate
      </button>
      lists anything that would stop the return being submitted.
    </p>
  </div>
</template>
