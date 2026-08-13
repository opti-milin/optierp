<script setup lang="ts">
/**
 * Statement of Income — head-wise income lines. Net income is never typed: it is
 * gross income less the deductions allowed against that line.
 */
import { computed, ref, watch } from "vue";
import DerivedField from "@/components/shared/DerivedField.vue";
import EditableGrid, { type EditableGridColumn } from "@/components/shared/EditableGrid.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import { taxLabel, taxShort } from "@/config/taxTerminology";
import type {
  TaxIncomeLine,
  TaxOption,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const { visible } = useFieldVisibility(context);
const currency = computed(() => context.value?.currency ?? "INR");

/* ------------------------------------------------------------------ helpers */

function num(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value: unknown): string {
  return num(value).toFixed(2);
}

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

/* --------------------------------------------------------------- grid rows */

function toRow(line: TaxIncomeLine): Record<string, unknown> {
  return {
    id: line.id ?? null,
    seq: line.seq,
    head: line.head,
    income_character_code: line.income_character_code,
    sub_ref: line.sub_ref ?? "",
    gross: money(line.gross),
    deductions: money(line.deductions),
    net: money(num(line.gross) - num(line.deductions)),
  };
}

function toLine(row: Record<string, unknown>, index: number): TaxIncomeLine {
  const gross = money(row.gross);
  const deductions = money(row.deductions);
  const line: TaxIncomeLine = {
    seq: index,
    head: text(row.head) || "PGBP",
    income_character_code: text(row.income_character_code) || "ORDINARY",
    sub_ref: text(row.sub_ref) || null,
    gross,
    deductions,
    net: money(num(gross) - num(deductions)),
  };
  const id = text(row.id);
  if (id) line.id = id;
  return line;
}

function signature(lines: TaxIncomeLine[]): string {
  return lines
    .map((l) => [l.head, l.income_character_code, l.sub_ref ?? "", money(l.gross), money(l.deductions)].join("|"))
    .join("¶");
}

const rows = ref<Record<string, unknown>[]>([]);
let lastEmitted = "";

watch(
  () => props.incomeLines,
  (lines) => {
    if (signature(lines) === lastEmitted) return;
    rows.value = lines.map(toRow);
  },
  { immediate: true, deep: true },
);

function newRow(): Record<string, unknown> {
  return {
    id: null,
    seq: rows.value.length,
    head: "PGBP",
    income_character_code: "ORDINARY",
    sub_ref: "",
    gross: "0",
    deductions: "0",
    net: "0",
  };
}

function onRows(next: Record<string, unknown>[]): void {
  const lines = next.map((row, index) => toLine(row, index));
  rows.value = lines.map(toRow);
  lastEmitted = signature(lines);
  emit("update:incomeLines", lines);
  emit("dirty");
}

/* ----------------------------------------------------------------- columns */

const headOptions = computed<TaxOption[]>(() => {
  const options = props.lookups.income_heads;
  if (visible("income.headSalary")) return options;
  return options.filter((o) => o.value !== "Salary");
});

const columns = computed<EditableGridColumn[]>(() => {
  const list: EditableGridColumn[] = [
    {
      key: "head",
      label: "Head of income",
      short: "Head",
      help: "The head of income under which this amount is charged to tax — business or profession, house property, capital gains, salary or other sources.",
      type: "select",
      options: headOptions.value,
      required: true,
      width: "16rem",
    },
  ];
  if (visible("income.characterColumn")) {
    list.push({
      key: "income_character_code",
      label: "Income character",
      short: "Character",
      help: "How the amount is taxed within its head — ordinary income, short-term capital gain, long-term capital gain and so on. It decides the rate and the cap on surcharge.",
      type: "select",
      options: props.lookups.income_characters,
      required: true,
      width: "16rem",
    });
  }
  list.push(
    {
      key: "sub_ref",
      label: "Description",
      help: "A short description of what this line is, for the reviewer and for next year's file.",
      type: "text",
      placeholder: "For example, manufacturing operations",
    },
    {
      key: "gross",
      label: "Gross income",
      short: "Gross",
      help: "The gross amount assessable under this head, before the deductions allowed against it.",
      type: "money",
      align: "right",
      width: "10rem",
    },
    {
      key: "deductions",
      label: "Deductions allowed",
      short: "Deductions",
      help: "Deductions allowed against this line — for example the standard deduction of thirty per cent from income from house property.",
      type: "money",
      align: "right",
      width: "10rem",
    },
    {
      key: "net",
      label: "Net income",
      short: "Net",
      help: "Automatically calculated. Gross income less deductions allowed against this line.",
      type: "derived",
      align: "right",
      width: "10rem",
      derive: (row) => money(num(row.gross) - num(row.deductions)),
      explain: () => "Gross income less deductions allowed against this line.",
    },
  );
  return list;
});

/* ------------------------------------------------------------ live figures */

const netOfLines = computed(() =>
  money(rows.value.reduce((total, row) => total + num(row.gross) - num(row.deductions), 0)),
);
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-income">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <h2 class="text-base font-semibold text-slate-900">Statement of income</h2>
      <p class="text-sm text-slate-600">
        Enter one line for each source of income, under the head it belongs to. Type the gross amount
        and any deduction allowed against it — the net figure is worked out for you, and the result on
        the right updates as you go.
      </p>

      <div class="grid gap-4 sm:grid-cols-3">
        <div data-field="income.gross" tabindex="-1">
          <DerivedField
            label="Total of the lines entered"
            :value="netOfLines"
            kind="money"
            :currency="currency"
            explain="The sum of gross income less deductions across every line above. It is not yet reduced by tax adjustments, depreciation or brought forward losses."
            testid="income-lines-total"
          />
        </div>
        <DerivedField
          label="Gross total income"
          :value="props.preview?.gross_total_income ?? null"
          kind="money"
          :currency="currency"
          explain="Income under all heads after the tax adjustments and depreciation allowed by the Income-tax Act, before brought forward losses and Chapter VI-A deductions."
          testid="income-gross-total-income"
        />
        <DerivedField
          label="Total income chargeable to tax"
          :value="props.preview?.total_income ?? null"
          kind="money"
          emphasis="strong"
          :currency="currency"
          explain="Gross total income less brought forward losses set off this year and the deductions allowed under Chapter VI-A. This is the figure the rates are applied to."
          testid="income-total-income"
        />
      </div>

      <p class="text-sm text-slate-600">
        Nothing here yet?
        <button
          class="font-medium text-primary underline"
          type="button"
          data-testid="income-populate-from-books"
          @click="emit('navigate', 'overview', 'overview.populateFromBooks')"
        >
          Populate from the books
        </button>
        reads the general ledger for the financial year and fills in the profit for you.
      </p>
    </section>

    <section class="space-y-2">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-900">
          Income lines
          <InfoTip
            text="Press Enter on the last row to add another. You can also paste several rows at once from a spreadsheet."
            size="sm"
          />
        </h3>
        <p class="text-xs text-slate-500">
          {{ taxShort("term.netIncome", "Net") }} is calculated, never typed.
        </p>
      </div>

      <div data-field="income.lines" tabindex="-1">
        <EditableGrid
          :model-value="rows"
          :columns="columns"
          :new-row="newRow"
          :disabled="props.disabled"
          add-label="Add an income line"
          empty-text="No income lines yet. Add one, or populate from the books."
          :total-keys="['gross', 'deductions', 'net']"
          show-row-numbers
          testid="income-grid"
          @update:model-value="onRows"
        />
      </div>

      <p class="text-xs text-slate-500">
        {{ taxLabel("term.grossTotalIncome", "Gross total income") }} is built from these lines by the
        computation engine — the tax adjustments, Income-tax Act depreciation and brought forward
        losses are applied in their own sections.
      </p>
    </section>
  </div>
</template>
