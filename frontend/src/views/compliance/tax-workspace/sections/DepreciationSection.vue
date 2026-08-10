<script setup lang="ts">
/**
 * Depreciation under the Income-tax Act — the block-wise written down value register.
 *
 * Almost nothing here is typed. The block is chosen from the statutory catalogue,
 * which carries its own rate; opening written down value, allowable depreciation and
 * closing written down value are all computed on the server and shown read-only with
 * an explanation. Rebuilding from the fixed asset register fills the whole thing in.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import DerivedField from "@/components/shared/DerivedField.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxDepreciationRegister,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency, formatNumber } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const { visible } = useFieldVisibility(context);
const currency = computed(() => context.value?.currency ?? "INR");
const ayCode = computed(() => props.workspace.computation.ay_code);

const syncing = ref(false);
const message = ref("");
const errorText = ref("");

function num(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value: unknown): string {
  return formatCurrency(num(value), currency.value);
}

const registers = computed<TaxDepreciationRegister[]>(
  () => props.workspace.depreciation_registers ?? [],
);

/**
 * The statutory catalogue carries a business title for every block ("Plant and
 * machinery — 15%"). The register stores only the code, so resolve it for display;
 * a code is never shown to the user unless the catalogue has no entry for it.
 */
const blockLabels = computed<Map<string, string>>(() => {
  const map = new Map<string, string>();
  for (const option of props.lookups.depreciation_blocks ?? []) {
    // The rate has its own column here, so prefer the bare title over the
    // rate-suffixed label the dropdowns use.
    map.set(option.value, option.meta?.title ?? option.label);
  }
  return map;
});

function blockLabel(code: string): string {
  return blockLabels.value.get(code) ?? code;
}

const totals = computed(() => {
  let opening = 0;
  let additions = 0;
  let deletions = 0;
  let depreciation = 0;
  let additional = 0;
  let closing = 0;
  for (const row of registers.value) {
    opening += num(row.opening_wdv);
    additions += num(row.additions_full) + num(row.additions_half);
    deletions += num(row.deletions);
    depreciation += num(row.depreciation_amount);
    additional += num(row.additional_depreciation_amount);
    closing += num(row.closing_wdv);
  }
  return { opening, additions, deletions, depreciation, additional, closing };
});

const totalClaimed = computed(() => props.workspace.depreciation_total);

/** Depreciation charged in the books, taken from the add-back the user recorded. */
const accountingAddBack = computed(() => {
  const line = props.adjustmentLines.find(
    (entry) =>
      !entry.run_id &&
      /deprecia/i.test(`${entry.description ?? ""} ${entry.section_code}`) &&
      !/deduct|less/i.test(entry.direction),
  );
  return line ? num(line.amount) : null;
});

const difference = computed(() => {
  const books = accountingAddBack.value;
  if (books === null) return null;
  return books - num(totalClaimed.value);
});

async function rebuildFromAssets(): Promise<void> {
  syncing.value = true;
  message.value = "";
  errorText.value = "";
  try {
    const { data } = await api.post<TaxDepreciationRegister[]>("/tax/depreciation/sync", {
      ay_code: ayCode.value,
    });
    message.value =
      data.length === 0
        ? "No depreciable assets were found for this year, so no blocks were created."
        : `Rebuilt ${data.length} ${data.length === 1 ? "block" : "blocks"} from the fixed asset register.`;
    emit("changed");
  } catch (e: unknown) {
    const detail = (e as ErrorEnvelope | null)?.detail;
    errorText.value =
      detail ?? (e instanceof Error ? e.message : "The register could not be rebuilt.");
  } finally {
    syncing.value = false;
  }
}
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-depreciation">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <h2 class="text-base font-semibold text-slate-900">
        Depreciation under the Income-tax Act
        <InfoTip
          title="Section 32"
          text="Depreciation for tax is computed block-wise on the written down value at the rates prescribed by the Income-tax Rules, not asset by asset at the rates used in the books."
          statutory-ref="Section 32"
          size="md"
        />
      </h2>
      <p class="text-sm text-slate-600">
        This register is maintained block-wise on the written down value. Every figure below is
        calculated for you from the fixed asset register and the statutory rate for the block —
        there is nothing to type.
      </p>

      <div class="grid gap-4 sm:grid-cols-3">
        <DerivedField
          label="Depreciation claimed under the Income-tax Act"
          :value="totalClaimed"
          kind="money"
          emphasis="strong"
          :currency="currency"
          statutory-ref="Section 32"
          explain="The total allowable depreciation across every block, including additional depreciation where it is available. This reduces business income on the right."
          testid="depreciation-total-claimed"
        />
        <DerivedField
          label="Depreciation charged in the books"
          :value="accountingAddBack"
          kind="money"
          :currency="currency"
          explain="Taken from the depreciation add-back recorded under Tax Adjustments. It is added back in full because the Income-tax Act allows its own depreciation instead."
          testid="depreciation-books"
        />
        <DerivedField
          label="Difference"
          :value="difference"
          kind="money"
          :currency="currency"
          explain="Depreciation charged in the books less depreciation allowable under the Income-tax Act. A positive figure increases taxable income."
          testid="depreciation-difference"
        />
      </div>

      <div v-if="visible('depreciation.syncFromAssets')" class="flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="btn-secondary"
          :disabled="props.disabled || syncing"
          data-testid="depreciation-rebuild"
          @click="rebuildFromAssets"
        >
          {{ syncing ? "Rebuilding…" : "Rebuild from the fixed asset register" }}
        </button>
        <p class="text-sm text-slate-600">
          Reads every capitalised asset for the year, groups it into its block and applies the
          statutory rate. Existing rows for this year are replaced.
        </p>
      </div>

      <p v-if="message" class="text-sm text-emerald-700" data-testid="depreciation-message">
        {{ message }}
      </p>
      <p v-if="errorText" class="text-sm text-red-700" data-testid="depreciation-error">
        {{ errorText }}
      </p>
    </section>

    <section class="space-y-2" data-field="depreciation.register" tabindex="-1">
      <h3 class="text-sm font-semibold text-slate-900">Block-wise register</h3>

      <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table class="w-full text-sm">
          <thead class="bg-slate-50">
            <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
              <th class="px-3 py-2 font-medium">Block of assets</th>
              <th class="px-3 py-2 text-right font-medium">
                Rate
                <InfoTip text="The statutory rate of depreciation prescribed for this block by the Income-tax Rules." size="sm" />
              </th>
              <th class="px-3 py-2 text-right font-medium">
                Opening written down value
                <InfoTip text="The closing written down value of this block at the end of the previous year, carried forward automatically." size="sm" />
              </th>
              <th class="px-3 py-2 text-right font-medium">Additions</th>
              <th class="px-3 py-2 text-right font-medium">Deletions</th>
              <th class="px-3 py-2 text-right font-medium">Depreciation allowable</th>
              <th
                v-if="visible('depreciation.additionalDepreciation')"
                class="px-3 py-2 text-right font-medium"
              >
                Additional depreciation
                <InfoTip
                  title="Section 32(1)(iia)"
                  text="An extra allowance on new plant and machinery acquired for manufacture. It is forfeited under the concessional regimes."
                  statutory-ref="Section 32(1)(iia)"
                  size="sm"
                />
              </th>
              <th class="px-3 py-2 text-right font-medium">Closing written down value</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="register in registers"
              :key="register.id"
              class="border-t border-slate-100"
              :data-testid="`depreciation-row-${register.block_code}`"
            >
              <td class="px-3 py-2 text-slate-800">{{ blockLabel(register.block_code) }}</td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-600">
                {{ formatNumber(register.rate_percent) }}%
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(register.opening_wdv) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(num(register.additions_full) + num(register.additions_half)) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(register.deletions) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums font-medium text-slate-900">
                {{ money(register.depreciation_amount) }}
              </td>
              <td
                v-if="visible('depreciation.additionalDepreciation')"
                class="px-3 py-2 text-right tabular-nums text-slate-700"
              >
                {{ money(register.additional_depreciation_amount) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(register.closing_wdv) }}
              </td>
            </tr>
            <tr v-if="!registers.length">
              <td class="px-3 py-6 text-center text-sm text-slate-500" colspan="8">
                No depreciation blocks have been prepared for this year. Rebuild from the fixed asset
                register to create them.
              </td>
            </tr>
          </tbody>
          <tfoot v-if="registers.length" class="border-t-2 border-slate-200 bg-slate-50">
            <tr class="font-medium text-slate-900">
              <td class="px-3 py-2">Total</td>
              <td class="px-3 py-2" />
              <td class="px-3 py-2 text-right tabular-nums">{{ money(totals.opening) }}</td>
              <td class="px-3 py-2 text-right tabular-nums">{{ money(totals.additions) }}</td>
              <td class="px-3 py-2 text-right tabular-nums">{{ money(totals.deletions) }}</td>
              <td class="px-3 py-2 text-right tabular-nums">{{ money(totals.depreciation) }}</td>
              <td
                v-if="visible('depreciation.additionalDepreciation')"
                class="px-3 py-2 text-right tabular-nums"
              >
                {{ money(totals.additional) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums">{{ money(totals.closing) }}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      <p class="text-xs text-slate-500">
        Closing written down value is the opening value plus additions, less deletions and the
        depreciation allowed. It becomes next year's opening value when you copy the year forward.
      </p>
    </section>
  </div>
</template>
