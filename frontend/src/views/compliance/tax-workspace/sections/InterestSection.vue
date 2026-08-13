<script setup lang="ts">
/**
 * Advance Tax and Interest under Sections 234A, 234B and 234C.
 *
 * Nothing on this screen is typed except the date the return was filed. The
 * instalment schedule, the shortfall against each due date and the three interest
 * charges are all derived from the liability, the challans and the statutory rules.
 */
import { computed } from "vue";
import DerivedField from "@/components/shared/DerivedField.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import type {
  AdvanceTaxInstalment,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency, formatDate, formatNumber } from "@/utils/format";

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

const calendar = computed(() => props.workspace.advance_tax);
const instalments = computed<AdvanceTaxInstalment[]>(() => calendar.value?.instalments ?? []);

const totalShortfall = computed(() =>
  instalments.value.reduce((total, row) => total + num(row.shortfall), 0),
);

function instalmentTone(instalment: AdvanceTaxInstalment): string {
  if (num(instalment.shortfall) <= 0) return "complete";
  return new Date(instalment.due_date) < new Date() ? "has-errors" : "in-progress";
}

function instalmentLabel(instalment: AdvanceTaxInstalment): string {
  if (num(instalment.shortfall) <= 0) return "Paid in full";
  return new Date(instalment.due_date) < new Date() ? "Short paid" : "Due";
}

const returnDueDate = computed(() => context.value?.return_due_date ?? null);
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-interest">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <h2 class="text-base font-semibold text-slate-900">Advance tax and interest</h2>
      <p class="text-sm text-slate-600">
        Advance tax is payable in instalments during the year. Falling short of an instalment, of
        the year's liability, or of the filing deadline each attracts its own interest charge. Every
        figure below is worked out from your liability, the challans you have recorded and the
        statutory rules — there is nothing to calculate by hand.
      </p>

      <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <DerivedField
          label="Interest for Late Filing of Return"
          :value="props.preview?.interest_234a ?? null"
          kind="money"
          :currency="currency"
          statutory-ref="Section 234A"
          explain="One per cent per month, or part of a month, on the unpaid tax from the day after the return was due until it is filed."
          testid="interest-234a"
        />
        <DerivedField
          label="Interest for Short Payment of Advance Tax"
          :value="props.preview?.interest_234b ?? null"
          kind="money"
          :currency="currency"
          statutory-ref="Section 234B"
          explain="One per cent per month from the first day of the assessment year, where less than ninety per cent of the assessed tax was paid as advance tax."
          testid="interest-234b"
        />
        <DerivedField
          label="Interest for Deferment of Advance Tax Instalments"
          :value="props.preview?.interest_234c ?? null"
          kind="money"
          :currency="currency"
          statutory-ref="Section 234C"
          explain="One per cent per month for three months on each instalment paid short of the prescribed cumulative percentage."
          testid="interest-234c"
        />
        <DerivedField
          label="Total interest payable"
          :value="props.preview?.total_interest ?? null"
          kind="money"
          emphasis="strong"
          :currency="currency"
          explain="The three interest charges together. It is added to the tax before the taxes you have already paid are deducted."
          testid="interest-total"
        />
      </div>

      <div v-if="visible('interest.interest234a')" class="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
        <p>
          The return for this year is due on <strong>{{ formatDate(returnDueDate) }}</strong>.
          Interest for late filing runs from the day after that date until the return is filed.
        </p>
        <p class="mt-1">
          Record the date you filed under
          <button
            class="font-medium text-primary underline"
            type="button"
            data-testid="interest-go-filing"
            @click="emit('navigate', 'filing')"
          >
            Return Filing
          </button>
          so the charge stops accruing.
        </p>
      </div>
    </section>

    <section class="space-y-2" data-field="interest.schedule" tabindex="-1">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-900">
          Advance tax instalment schedule
          <InfoTip
            title="Section 211"
            text="Advance tax is payable in four instalments — fifteen, forty-five, seventy-five and one hundred per cent of the year's liability by the fifteenth of June, September, December and March."
            statutory-ref="Section 211"
            size="sm"
          />
        </h3>
        <p v-if="calendar" class="text-xs text-slate-500">
          Based on an estimated liability of {{ money(calendar.estimated_tax_net) }}.
        </p>
      </div>

      <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table class="w-full text-sm">
          <thead class="bg-slate-50">
            <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
              <th class="px-3 py-2 font-medium">Instalment</th>
              <th class="px-3 py-2 font-medium">Due on</th>
              <th class="px-3 py-2 text-right font-medium">Cumulative percentage</th>
              <th class="px-3 py-2 text-right font-medium">Required by this date</th>
              <th class="px-3 py-2 text-right font-medium">Paid to date</th>
              <th class="px-3 py-2 text-right font-medium">Shortfall</th>
              <th class="px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="instalment in instalments"
              :key="instalment.code"
              class="border-t border-slate-100"
              :data-testid="`interest-instalment-${instalment.code}`"
            >
              <td class="px-3 py-2 text-slate-800">{{ instalment.label }}</td>
              <td class="px-3 py-2 text-slate-700">{{ formatDate(instalment.due_date) }}</td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-600">
                {{ formatNumber(instalment.cumulative_percent) }}%
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(instalment.required_cumulative) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(instalment.paid_to_date) }}
              </td>
              <td
                class="px-3 py-2 text-right tabular-nums font-medium"
                :class="num(instalment.shortfall) > 0 ? 'text-red-700' : 'text-slate-500'"
              >
                {{ money(instalment.shortfall) }}
              </td>
              <td class="px-3 py-2">
                <StatusPill
                  :status="instalmentTone(instalment)"
                  :label="instalmentLabel(instalment)"
                />
              </td>
            </tr>
            <tr v-if="!instalments.length">
              <td class="px-3 py-6 text-center text-sm text-slate-500" colspan="7">
                The instalment schedule appears once the year's liability has been calculated. Save a
                computation run to see it.
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div
        v-if="totalShortfall > 0"
        class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
        data-testid="interest-shortfall-notice"
      >
        <p>
          <strong>{{ money(totalShortfall) }}</strong> of advance tax is still short across the
          instalments above. Interest keeps accruing until it is paid.
        </p>
        <button
          class="mt-1 font-medium text-primary underline"
          type="button"
          data-testid="interest-go-challans"
          @click="emit('navigate', 'challans')"
        >
          Record a challan for the shortfall
        </button>
      </div>
    </section>
  </div>
</template>
