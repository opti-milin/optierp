<script setup lang="ts">
/**
 * Minimum Alternate Tax (Section 115JB) and the Section 115JAA credit.
 *
 * One number is typed — the book profit. Everything else is a comparison the
 * engine performs: tax on book profit against tax under the normal provisions,
 * the credit that arises when the floor tax wins, and the credit set off when it
 * does not. The section hides itself entirely where Section 115JB does not apply.
 */
import { computed } from "vue";
import DerivedField from "@/components/shared/DerivedField.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import { taxLabel } from "@/config/taxTerminology";
import type {
  TaxMatCredit,
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

function num(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value: unknown): string {
  return formatCurrency(num(value), currency.value);
}

const applies = computed(() => context.value?.mat_applicable ?? false);
const credits = computed<TaxMatCredit[]>(() => props.workspace.mat_credits ?? []);
const creditAvailable = computed(() => props.workspace.mat_credit_available);

const taxNormal = computed(() => props.preview?.tax_normal ?? null);
const taxMat = computed(() => props.preview?.tax_mat ?? null);
const basis = computed(() => props.preview?.tax_applied_basis ?? "Normal");
const matWins = computed(() => basis.value.toUpperCase() === "MAT");

const basisMessage = computed(() => {
  if (!applies.value) {
    return "Minimum Alternate Tax does not apply under the regime elected for this year, so the comparison is not made.";
  }
  if (taxMat.value === null) {
    return "Enter the book profit to compare the floor tax with tax under the normal provisions.";
  }
  return matWins.value
    ? "Tax on book profit is higher, so Minimum Alternate Tax applies this year and a credit arises for the excess."
    : "Tax under the normal provisions is higher, so it applies and any credit brought forward may be set off.";
});

function onBookProfit(event: Event): void {
  const target = event.target as HTMLInputElement;
  emit("update:bookProfit", target.value);
}

const entryTone: Record<string, string> = {
  Created: "in-progress",
  Utilised: "complete",
  Expired: "has-errors",
};
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-mat">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <h2 class="text-base font-semibold text-slate-900">
        Minimum Alternate Tax
        <InfoTip
          title="Section 115JB"
          text="A company must pay at least a prescribed percentage of its book profit as tax. Where that floor exceeds tax under the normal provisions, the excess becomes a credit that can be set off in a later year."
          statutory-ref="Section 115JB"
          size="md"
        />
      </h2>
      <p class="text-sm text-slate-600">{{ basisMessage }}</p>

      <div v-if="visible('mat.bookProfit')" class="max-w-sm" data-field="mat.bookProfit" tabindex="-1">
        <label class="form-label" for="mat-book-profit">
          Book profit
          <InfoTip
            text="Profit as shown in the statement of profit and loss prepared under the Companies Act, adjusted by the additions and reductions listed in Explanation 1 to Section 115JB."
            statutory-ref="Section 115JB"
            size="sm"
          />
        </label>
        <input
          id="mat-book-profit"
          class="form-input"
          type="text"
          inputmode="decimal"
          :value="props.bookProfit"
          :disabled="props.disabled"
          placeholder="0.00"
          data-testid="mat-book-profit"
          @input="onBookProfit"
        />
        <p class="mt-1 text-xs text-slate-500">
          Leave this blank if the Minimum Alternate Tax comparison should not be made this year.
        </p>
      </div>

      <div class="grid gap-4 sm:grid-cols-3">
        <DerivedField
          label="Tax on book profit"
          :value="taxMat"
          kind="money"
          :currency="currency"
          statutory-ref="Section 115JB"
          explain="The book profit at the rate prescribed by Section 115JB, plus surcharge and Health and Education Cess."
          testid="mat-tax-on-book-profit"
        />
        <DerivedField
          label="Tax under the normal provisions"
          :value="taxNormal"
          kind="money"
          :currency="currency"
          explain="Tax on total income computed under the normal provisions, including surcharge, marginal relief and Health and Education Cess."
          testid="mat-tax-normal"
        />
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-xs uppercase tracking-wide text-slate-500">Basis applied</p>
          <div class="mt-1 flex items-center gap-2">
            <StatusPill
              :status="matWins ? 'in-progress' : 'complete'"
              :label="matWins ? 'Minimum Alternate Tax' : 'Normal provisions'"
              size="md"
            />
          </div>
          <p class="mt-1 text-xs text-slate-500">The higher of the two figures is what you pay.</p>
        </div>
      </div>
    </section>

    <section
      v-if="visible('mat.creditLedger')"
      class="rounded-lg border border-slate-200 bg-white p-4 space-y-3"
      data-field="mat.creditLedger"
      tabindex="-1"
    >
      <h3 class="text-sm font-semibold text-slate-900">
        Minimum Alternate Tax credit
        <InfoTip
          title="Section 115JAA"
          text="Credit for the excess of the floor tax over tax under the normal provisions. It may be carried forward for fifteen assessment years and set off in a year when the normal provisions apply."
          statutory-ref="Section 115JAA"
          size="sm"
        />
      </h3>

      <div class="grid gap-4 sm:grid-cols-3">
        <DerivedField
          label="Credit available to set off"
          :value="creditAvailable"
          kind="money"
          :currency="currency"
          statutory-ref="Section 115JAA"
          explain="Credit created in earlier years, less what has already been set off or has expired."
          testid="mat-credit-available"
        />
        <DerivedField
          label="Credit created this year"
          :value="props.preview?.mat_credit_created ?? null"
          kind="money"
          :currency="currency"
          explain="Tax on book profit less tax under the normal provisions, when the floor tax is the higher of the two."
          testid="mat-credit-created"
        />
        <DerivedField
          label="Credit set off this year"
          :value="props.preview?.mat_credit_utilised ?? null"
          kind="money"
          emphasis="strong"
          :currency="currency"
          explain="Limited to the excess of tax under the normal provisions over the floor tax, and to the credit available."
          testid="mat-credit-utilised"
        />
      </div>

      <div
        v-if="!applies && credits.length"
        class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
      >
        The regime elected for this year does not attract Minimum Alternate Tax, so credit brought
        forward cannot be set off and will lapse. The ledger stays visible so the position is on
        record.
      </div>

      <div class="overflow-x-auto rounded-lg border border-slate-200">
        <table class="w-full text-sm">
          <thead class="bg-slate-50">
            <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
              <th class="px-3 py-2 font-medium">Assessment year</th>
              <th class="px-3 py-2 font-medium">Entry</th>
              <th class="px-3 py-2 font-medium">Expires after</th>
              <th class="px-3 py-2 text-right font-medium">Tax on book profit</th>
              <th class="px-3 py-2 text-right font-medium">Tax under normal provisions</th>
              <th class="px-3 py-2 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in credits" :key="entry.id" class="border-t border-slate-100">
              <td class="px-3 py-2 text-slate-800">{{ entry.ay_code }}</td>
              <td class="px-3 py-2">
                <StatusPill
                  :status="entryTone[entry.entry_kind] ?? 'not-started'"
                  :label="taxLabel(`matEntry.${entry.entry_kind}`, entry.entry_kind)"
                />
              </td>
              <td class="px-3 py-2 text-slate-600">{{ entry.expires_after_ay ?? "—" }}</td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-600">
                {{ entry.tax_mat === null ? "—" : money(entry.tax_mat) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-600">
                {{ entry.tax_normal === null ? "—" : money(entry.tax_normal) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums font-medium text-slate-900">
                {{ money(entry.amount) }}
              </td>
            </tr>
            <tr v-if="!credits.length">
              <td class="px-3 py-6 text-center text-sm text-slate-500" colspan="6">
                No Minimum Alternate Tax credit has arisen or been set off yet. Entries appear here
                automatically when a computation run is saved.
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="text-xs text-slate-500">
        This ledger is written by the computation engine when a run is saved. It is append-only, so
        every movement stays on the record.
      </p>
    </section>
  </div>
</template>
