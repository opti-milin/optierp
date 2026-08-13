<script setup lang="ts">
/**
 * Brought Forward Losses and Set-off.
 *
 * The ledger is entered once, when a loss first arises; every later year reads it.
 * The expiry year is derived from the kind of loss and the year it arose, and the
 * set-off actually applied is written by the computation engine, never by hand.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import DerivedField from "@/components/shared/DerivedField.vue";
import EditableGrid from "@/components/shared/EditableGrid.vue";
import type { EditableGridColumn } from "@/components/shared/EditableGrid.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import { taxLabel } from "@/config/taxTerminology";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxLossCarryForward,
  TaxLossSetoffEntry,
  TaxOption,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const { visible } = useFieldVisibility(context);
const currency = computed(() => context.value?.currency ?? "INR");

/**
 * Years a loss may be carried forward, by kind. Unabsorbed depreciation is carried
 * forward indefinitely, which is why it has no entry here.
 */
const CARRY_FORWARD_YEARS: Record<string, number> = {
  Business: 8,
  Speculation: 4,
  STCG: 8,
  LTCG: 8,
  OS: 4,
};

const saving = ref(false);
const errorText = ref("");

function num(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value: unknown): string {
  return formatCurrency(num(value), currency.value);
}

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

/** "AY 2024-25" style codes advance by one year at a time; parse the leading year. */
function shiftAyCode(code: string, years: number): string {
  const match = /(\d{4})\s*[-/]\s*(\d{2,4})/.exec(code);
  if (!match) return "";
  const start = Number(match[1]) + years;
  return `${start}-${String(start + 1).slice(-2)}`;
}

function expiryFor(originAy: string, lossKind: string): string {
  const years = CARRY_FORWARD_YEARS[lossKind];
  if (!years) return "";
  return shiftAyCode(originAy, years);
}

/* ------------------------------------------------------------------ ledger */

const losses = computed<TaxLossCarryForward[]>(() => props.workspace.losses ?? []);
const setoffs = computed<TaxLossSetoffEntry[]>(() => props.workspace.setoff_entries ?? []);

/**
 * ``amount_remaining`` is the balance left in the ledger *after* the set-off recorded for
 * this year has been applied, so it is what carries forward. The opening figure has to add
 * this year's set-off back on; subtracting it again would report a negative carry-forward.
 */
const carriedForwardTotal = computed(() =>
  losses.value.reduce((total, row) => total + num(row.amount_remaining), 0),
);

const setOffTotal = computed(() =>
  setoffs.value.reduce((total, row) => total + num(row.amount_set_off), 0),
);

const availableTotal = computed(() => carriedForwardTotal.value + setOffTotal.value);

const lossKindOptions = computed<TaxOption[]>(() => {
  const all = props.lookups.loss_kinds;
  if (visible("losses.unabsorbedDepreciation")) return all;
  return all.filter((option) => option.value !== "UnabsorbedDep");
});

/* ------------------------------------------------------------- new entries */

const rows = ref<Record<string, unknown>[]>([]);

function newRow(): Record<string, unknown> {
  const previous = context.value?.previous_ay_code ?? "";
  return {
    origin_ay_code: previous,
    loss_kind: lossKindOptions.value[0]?.value ?? "Business",
    setoff_group: "ORDINARY",
    amount: "0",
    expires_after_ay: expiryFor(previous, lossKindOptions.value[0]?.value ?? "Business"),
    remarks: "",
  };
}

const columns = computed<EditableGridColumn[]>(() => [
  {
    key: "origin_ay_code",
    label: "Assessment year the loss arose in",
    short: "Year it arose",
    help: "The assessment year whose return reported this loss. The year it expires is worked out from this.",
    type: "select",
    options: props.lookups.assessment_years,
    required: true,
    width: "16rem",
    onSelect: (row: Record<string, unknown>, option: TaxOption) => {
      row.expires_after_ay = expiryFor(option.value, text(row.loss_kind));
    },
  },
  {
    key: "loss_kind",
    label: "Kind of loss",
    help: "What the loss was — business, speculation business, capital gains or other sources. It decides which income it can be set off against and how long it survives.",
    type: "select",
    options: lossKindOptions.value,
    required: true,
    width: "16rem",
    onSelect: (row: Record<string, unknown>, option: TaxOption) => {
      row.expires_after_ay = expiryFor(text(row.origin_ay_code), option.value);
      if (option.meta?.setoff_group) row.setoff_group = option.meta.setoff_group;
    },
  },
  {
    key: "setoff_group",
    label: "Set-off group",
    help: "The bucket of income this loss may be set off against — for example ordinary income, or long-term capital gains only.",
    type: "select",
    options: props.lookups.setoff_groups,
    width: "15rem",
  },
  {
    key: "amount",
    label: "Amount of the loss",
    short: "Amount",
    type: "money",
    align: "right",
    width: "12rem",
    help: "The unabsorbed amount still available to set off. Enter it as a positive figure.",
  },
  {
    key: "expires_after_ay",
    label: "Expires after assessment year",
    short: "Expires after",
    type: "derived",
    width: "14rem",
    help: "Automatically calculated from the kind of loss and the year it arose. Unabsorbed depreciation never expires.",
    derive: (row) => text(row.expires_after_ay) || "Does not expire",
    explain: (row) => {
      const years = CARRY_FORWARD_YEARS[text(row.loss_kind)];
      return years
        ? `Carried forward for ${years} assessment years after the year it arose.`
        : "Unabsorbed depreciation is carried forward indefinitely.";
    },
  },
  {
    key: "remarks",
    label: "Notes",
    type: "text",
    placeholder: "Optional",
  },
]);

function onRows(next: Record<string, unknown>[]): void {
  rows.value = next;
}

async function saveLedgerRows(): Promise<void> {
  const pending = rows.value.filter((row) => num(row.amount) > 0 && text(row.origin_ay_code));
  if (!pending.length) return;
  saving.value = true;
  errorText.value = "";
  try {
    for (const row of pending) {
      await api.post("/tax/losses", {
        origin_ay_code: text(row.origin_ay_code),
        loss_kind: text(row.loss_kind),
        setoff_group: text(row.setoff_group) || "ORDINARY",
        amount: num(row.amount).toFixed(2),
        expires_after_ay: text(row.expires_after_ay) || null,
        remarks: text(row.remarks) || null,
      });
    }
    rows.value = [];
    emit("changed");
  } catch (e: unknown) {
    const detail = (e as ErrorEnvelope | null)?.detail;
    errorText.value =
      detail ?? (e instanceof Error ? e.message : "The loss ledger entry could not be saved.");
  } finally {
    saving.value = false;
  }
}

const pendingCount = computed(
  () => rows.value.filter((row) => num(row.amount) > 0 && text(row.origin_ay_code)).length,
);
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-losses">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <h2 class="text-base font-semibold text-slate-900">
        Brought forward losses and set-off
        <InfoTip
          title="Sections 70 to 74"
          text="Losses of earlier years are set off against this year's income in the order the Income-tax Act prescribes, within the time limit for each kind of loss."
          statutory-ref="Sections 70 to 74"
          size="md"
        />
      </h2>
      <p class="text-sm text-slate-600">
        Record each unabsorbed loss once, in the year it arose. Every later year reads this ledger,
        and the computation applies the set-off in the statutory order — you never allocate it by
        hand.
      </p>

      <div class="grid gap-4 sm:grid-cols-3">
        <DerivedField
          label="Losses available to set off"
          :value="availableTotal"
          kind="money"
          :currency="currency"
          explain="The unabsorbed balance carried into this year across every entry in the ledger that has not expired, before this year's set-off is applied."
          testid="losses-available"
        />
        <DerivedField
          label="Set off against this year's income"
          :value="setOffTotal"
          kind="money"
          emphasis="strong"
          :currency="currency"
          explain="What the last saved computation actually absorbed. It is limited by the income available in each set-off group."
          testid="losses-set-off"
        />
        <DerivedField
          label="Carried forward to next year"
          :value="carriedForwardTotal"
          kind="money"
          :currency="currency"
          explain="Losses available less the amount set off this year. This is what carries forward, subject to each entry's expiry."
          testid="losses-carried-forward"
        />
      </div>
    </section>

    <section class="space-y-2">
      <h3 class="text-sm font-semibold text-slate-900">Loss ledger</h3>
      <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table class="w-full text-sm">
          <thead class="bg-slate-50">
            <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
              <th class="px-3 py-2 font-medium">Year it arose</th>
              <th class="px-3 py-2 font-medium">Kind of loss</th>
              <th class="px-3 py-2 font-medium">Set-off group</th>
              <th class="px-3 py-2 font-medium">Expires after</th>
              <th class="px-3 py-2 text-right font-medium">Original amount</th>
              <th class="px-3 py-2 text-right font-medium">Still available</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="loss in losses" :key="loss.id" class="border-t border-slate-100">
              <td class="px-3 py-2 text-slate-800">{{ loss.origin_ay_code }}</td>
              <td class="px-3 py-2 text-slate-700">
                {{ taxLabel(`lossKind.${loss.loss_kind}`, loss.loss_kind) }}
              </td>
              <td class="px-3 py-2 text-slate-600">{{ loss.setoff_group }}</td>
              <td class="px-3 py-2 text-slate-600">
                {{ loss.expires_after_ay ?? "Does not expire" }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(loss.amount) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums font-medium text-slate-900">
                {{ money(loss.amount_remaining) }}
              </td>
            </tr>
            <tr v-if="!losses.length">
              <td class="px-3 py-6 text-center text-sm text-slate-500" colspan="6">
                No brought forward losses are on record. Add one below if an earlier year left an
                unabsorbed loss.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="space-y-2" data-field="losses.ledger" tabindex="-1">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-900">
          Add losses to the ledger
          <InfoTip
            text="The expiry year fills itself in from the kind of loss and the year it arose. Press Enter on the last row to add another."
            size="sm"
          />
        </h3>
        <button
          type="button"
          class="btn-primary"
          :disabled="props.disabled || saving || pendingCount === 0"
          data-testid="losses-save"
          @click="saveLedgerRows"
        >
          {{ saving ? "Saving…" : `Add ${pendingCount || ""} to the ledger`.trim() }}
        </button>
      </div>

      <EditableGrid
        :model-value="rows"
        :columns="columns"
        :new-row="newRow"
        :disabled="props.disabled"
        add-label="Add a brought forward loss"
        empty-text="Nothing to add. Use this grid only when an earlier year left an unabsorbed loss that is not already listed above."
        :total-keys="['amount']"
        show-row-numbers
        testid="losses-grid"
        @update:model-value="onRows"
      />

      <p v-if="errorText" class="text-sm text-red-700" data-testid="losses-error">
        {{ errorText }}
      </p>
    </section>

    <section v-if="setoffs.length" class="space-y-2" data-testid="losses-setoff-applied">
      <h3 class="text-sm font-semibold text-slate-900">
        Set-off applied by the last computation
        <InfoTip
          text="Written by the computation engine when the run was saved, in the order the Income-tax Act requires. It is part of the audit trail and cannot be edited."
          size="sm"
        />
      </h3>
      <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table class="w-full text-sm">
          <thead class="bg-slate-50">
            <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
              <th class="px-3 py-2 font-medium">Order</th>
              <th class="px-3 py-2 font-medium">Set off against</th>
              <th class="px-3 py-2 text-right font-medium">Amount set off</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in setoffs" :key="entry.id" class="border-t border-slate-100">
              <td class="px-3 py-2 text-slate-600">{{ entry.sequence + 1 }}</td>
              <td class="px-3 py-2 text-slate-700">
                {{ taxLabel(`character.${entry.against_character}`, entry.against_character) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-900">
                {{ money(entry.amount_set_off) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
