<script setup lang="ts">
/**
 * Tax Adjustments — the bridge from profit as per the books to business income
 * chargeable to tax.
 *
 * The provision is picked from the statutory catalogue rather than typed, and
 * choosing it fills in the stage, the direction and the description, so a normal
 * add-back needs one selection and one amount.
 */
import { computed, ref, watch } from "vue";
import DerivedField from "@/components/shared/DerivedField.vue";
import EditableGrid from "@/components/shared/EditableGrid.vue";
import type { EditableGridColumn } from "@/components/shared/EditableGrid.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import type {
  TaxAdjustmentLine,
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

const DEDUCT_DIRECTIONS = new Set(["deduct", "less", "deduction"]);

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

function isDeduction(direction: unknown): boolean {
  return DEDUCT_DIRECTIONS.has(text(direction).toLowerCase());
}

/* --------------------------------------------------------------- grid rows */

/** Only manual rows are editable; rows written by a run are shown read-only below. */
const manualLines = computed<TaxAdjustmentLine[]>(() =>
  props.adjustmentLines.filter((line) => !line.run_id),
);

const engineLines = computed<TaxAdjustmentLine[]>(() =>
  props.adjustmentLines.filter((line) => Boolean(line.run_id)),
);

function toRow(line: TaxAdjustmentLine): Record<string, unknown> {
  return {
    id: line.id ?? null,
    section_code: line.section_code,
    stage: line.stage,
    direction: line.direction,
    description: line.description ?? "",
    amount: money(line.amount),
  };
}

function toLine(row: Record<string, unknown>): TaxAdjustmentLine {
  const line: TaxAdjustmentLine = {
    section_code: text(row.section_code),
    stage: text(row.stage) || "PGBP",
    direction: text(row.direction) || "Add",
    description: text(row.description) || null,
    amount: money(row.amount),
    status: "Manual",
  };
  const id = text(row.id);
  if (id) line.id = id;
  return line;
}

function signature(lines: TaxAdjustmentLine[]): string {
  return lines
    .map((l) => [l.section_code, l.stage, l.direction, l.description ?? "", money(l.amount)].join("|"))
    .join("¶");
}

const rows = ref<Record<string, unknown>[]>([]);
let lastEmitted = "";

watch(
  manualLines,
  (lines) => {
    if (signature(lines) === lastEmitted) return;
    rows.value = lines.map(toRow);
  },
  { immediate: true, deep: true },
);

const defaultStage = computed<string>(() => props.lookups.adjustment_stages[0]?.value ?? "PGBP");

function newRow(): Record<string, unknown> {
  return {
    id: null,
    section_code: "",
    stage: defaultStage.value,
    direction: "Add",
    description: "",
    amount: "0",
  };
}

function onRows(next: Record<string, unknown>[]): void {
  const manual = next.map(toLine);
  rows.value = manual.map(toRow);
  lastEmitted = signature(manual);
  // Engine-written rows belong to their run and must survive an edit here.
  emit("update:adjustmentLines", [...manual, ...engineLines.value]);
  emit("dirty");
}

/* ----------------------------------------------------------------- columns */

const provisionOptions = computed<TaxOption[]>(() => {
  const all = props.lookups.adjustment_provisions;
  if (visible("adjustments.additionalDepreciation")) return all;
  // A concessional election forfeits additional depreciation — hide the provision
  // rather than let it be selected and then silently disallowed by the engine.
  return all.filter((option) => !/32\(1\)\(iia\)/i.test(`${option.value} ${option.label}`));
});

const stageOptions = computed<TaxOption[]>(() => {
  const all = props.lookups.adjustment_stages;
  const drop = new Set<string>();
  if (!visible("adjustments.chapterViaStage")) drop.add("CHAPTER_VIA");
  if (!visible("adjustments.icdsStage")) drop.add("ICDS");
  if (drop.size === 0) return all;
  return all.filter((option) => !drop.has(option.value.toUpperCase()));
});

const columns = computed<EditableGridColumn[]>(() => [
  {
    key: "section_code",
    label: "Statutory provision",
    short: "Provision",
    help: "The section of the Income-tax Act under which the amount is disallowed or allowed. Choosing it fills in the stage, the direction and a description for you.",
    type: "select",
    options: provisionOptions.value,
    allowFree: true,
    required: true,
    width: "20rem",
    onSelect: (row: Record<string, unknown>, option: TaxOption) => {
      if (option.meta?.stage) row.stage = option.meta.stage;
      if (option.meta?.direction) row.direction = option.meta.direction;
      if (!text(row.description)) row.description = option.label;
    },
  },
  {
    key: "stage",
    label: "Stage of the computation",
    short: "Stage",
    help: "Where the adjustment bites — business income, the book profit for Minimum Alternate Tax, or the Chapter VI-A deductions.",
    type: "select",
    options: stageOptions.value,
    required: true,
    width: "13rem",
  },
  {
    key: "direction",
    label: "Add back or deduct",
    short: "Effect",
    help: "Add back increases business income because the Income-tax Act disallows the expense. Deduct reduces it because the Act allows something the books do not carry.",
    type: "select",
    options: props.lookups.adjustment_directions,
    required: true,
    width: "12rem",
  },
  {
    key: "description",
    label: "Description",
    help: "What this adjustment is, in your own words, for the reviewer and for next year's file.",
    type: "text",
    placeholder: "For example, provision for doubtful debts",
  },
  {
    key: "amount",
    label: "Amount",
    type: "money",
    align: "right",
    width: "11rem",
    help: "The amount added back or deducted. Enter it as a positive figure — the effect column decides the sign.",
  },
  {
    key: "effect",
    label: "Effect on business income",
    short: "Effect on income",
    type: "derived",
    align: "right",
    width: "13rem",
    help: "Automatically calculated. The amount, signed according to whether it is added back or deducted.",
    derive: (row) => money(isDeduction(row.direction) ? -num(row.amount) : num(row.amount)),
    explain: (row) =>
      isDeduction(row.direction)
        ? "Deducted from business income."
        : "Added back to business income.",
  },
]);

/* ------------------------------------------------------------ live figures */

const addedBack = computed(() =>
  money(rows.value.reduce((t, r) => (isDeduction(r.direction) ? t : t + num(r.amount)), 0)),
);

const deducted = computed(() =>
  money(rows.value.reduce((t, r) => (isDeduction(r.direction) ? t + num(r.amount) : t), 0)),
);

const netEffect = computed(() => money(num(addedBack.value) - num(deducted.value)));

const forfeited = computed<string[]>(() => context.value?.forfeited_incentives ?? []);
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-adjustments">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <h2 class="text-base font-semibold text-slate-900">Tax adjustments</h2>
      <p class="text-sm text-slate-600">
        Add back the amounts the Income-tax Act does not allow, and deduct the allowances the books
        do not carry. Pick the statutory provision and the stage, direction and description are
        filled in for you — you only type the amount.
      </p>

      <div class="grid gap-4 sm:grid-cols-3">
        <DerivedField
          label="Total added back"
          :value="addedBack"
          kind="money"
          :currency="currency"
          explain="The sum of every adjustment that increases business income because the Income-tax Act disallows it."
          testid="adjustments-added-back"
        />
        <DerivedField
          label="Total deducted"
          :value="deducted"
          kind="money"
          :currency="currency"
          explain="The sum of every adjustment that reduces business income because the Income-tax Act allows it."
          testid="adjustments-deducted"
        />
        <DerivedField
          label="Net effect on business income"
          :value="netEffect"
          kind="money"
          emphasis="strong"
          :currency="currency"
          explain="Amounts added back less amounts deducted. This is what moves gross total income on the right."
          testid="adjustments-net-effect"
        />
      </div>

      <div
        v-if="forfeited.length"
        class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
        data-testid="adjustments-forfeited"
      >
        <p class="font-medium">
          The regime elected for this year gives up the following, so they cannot be claimed here:
        </p>
        <ul class="mt-1 space-y-0.5">
          <li v-for="(item, index) in forfeited" :key="index">• {{ item }}</li>
        </ul>
      </div>
    </section>

    <section class="space-y-2">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-900">
          Adjustments you have entered
          <InfoTip
            text="Press Enter on the last row to add another. You can also paste several rows at once from a spreadsheet."
            size="sm"
          />
        </h3>
        <p class="text-xs text-slate-500">Enter every amount as a positive figure.</p>
      </div>

      <div data-field="adjustments.lines" tabindex="-1">
        <EditableGrid
          :model-value="rows"
          :columns="columns"
          :new-row="newRow"
          :disabled="props.disabled"
          add-label="Add an adjustment"
          empty-text="No adjustments yet. Add the first one, or copy last year's standing adjustments from Setup & Basis."
          :total-keys="['amount', 'effect']"
          show-row-numbers
          testid="adjustments-grid"
          @update:model-value="onRows"
        />
      </div>

      <p class="text-sm text-slate-600">
        Carrying the same adjustments every year?
        <button
          class="font-medium text-primary underline"
          type="button"
          data-testid="adjustments-copy-previous-year"
          @click="emit('navigate', 'overview', 'overview.copyPreviousYear')"
        >
          Copy from the previous assessment year
        </button>
        brings the list across with the amounts cleared.
      </p>
    </section>

    <section
      v-if="engineLines.length"
      class="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-2"
      data-testid="adjustments-engine-lines"
    >
      <h3 class="text-sm font-semibold text-slate-900">
        Adjustments applied by the last computation
        <InfoTip
          text="These were written by the computation engine when the run was saved. They are part of the audit trail and cannot be edited by hand."
          size="sm"
        />
      </h3>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
            <th class="py-1 pr-3 font-medium">Provision</th>
            <th class="py-1 pr-3 font-medium">Description</th>
            <th class="py-1 pr-3 font-medium">Effect</th>
            <th class="py-1 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="line in engineLines" :key="line.id ?? line.section_code" class="border-t border-slate-200">
            <td class="py-1 pr-3 text-slate-700">{{ line.section_code || "—" }}</td>
            <td class="py-1 pr-3 text-slate-700">{{ line.description ?? "—" }}</td>
            <td class="py-1 pr-3 text-slate-600">
              {{ isDeduction(line.direction) ? "Deducted" : "Added back" }}
            </td>
            <td class="py-1 text-right tabular-nums text-slate-800">
              {{ money(line.final_amount ?? line.amount) }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
