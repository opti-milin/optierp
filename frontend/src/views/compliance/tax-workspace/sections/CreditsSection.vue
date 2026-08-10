<script setup lang="ts">
/**
 * Taxes Already Paid — tax deducted at source, tax collected at source, advance tax
 * and self-assessment tax claimed against this year's liability.
 *
 * Deductors come from the entries already on file rather than being retyped, the
 * deduction section comes from the statutory catalogue, and the amount claimed
 * defaults to the amount credited. The ledger is append-only: a wrong entry is
 * voided, never deleted.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import DerivedField from "@/components/shared/DerivedField.vue";
import EditableGrid from "@/components/shared/EditableGrid.vue";
import type { EditableGridColumn } from "@/components/shared/EditableGrid.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { taxLabel } from "@/config/taxTerminology";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxCreditEntry,
  TaxOption,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const currency = computed(() => context.value?.currency ?? "INR");
const ayCode = computed(() => props.workspace.computation.ay_code);
const computationId = computed(() => props.workspace.computation.id);

/** Only tax deducted or collected at source names a deductor and a section. */
const DEDUCTOR_KINDS = new Set(["TDS", "TCS"]);

const saving = ref(false);
const voiding = ref("");
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

function errorFrom(e: unknown, fallback: string): string {
  const detail = (e as ErrorEnvelope | null)?.detail;
  return detail ?? (e instanceof Error ? e.message : fallback);
}

/* ------------------------------------------------------------- live totals */

const entries = computed<TaxCreditEntry[]>(() => props.workspace.credits ?? []);
const liveEntries = computed(() => entries.value.filter((entry) => entry.docstatus !== 2));

function totalOf(kinds: string[]): number {
  return liveEntries.value
    .filter((entry) => kinds.includes(entry.credit_kind))
    .reduce((total, entry) => total + num(entry.amount_claimed), 0);
}

const deductedTotal = computed(() => totalOf(["TDS"]));
const collectedTotal = computed(() => totalOf(["TCS"]));
const advanceTotal = computed(() => totalOf(["AdvanceTax"]));
const selfAssessmentTotal = computed(() => totalOf(["SelfAssessment"]));

/* -------------------------------------------------------------- entry grid */

const rows = ref<Record<string, unknown>[]>([]);

const defaultKind = computed(() => props.lookups.credit_kinds[0]?.value ?? "TDS");

function newRow(): Record<string, unknown> {
  return {
    credit_kind: defaultKind.value,
    deductor_tan: "",
    deductor_name: "",
    section_code: "",
    amount_credited: "0",
    amount_claimed: "",
    remarks: "",
  };
}

const columns = computed<EditableGridColumn[]>(() => [
  {
    key: "credit_kind",
    label: "Kind of credit",
    help: "Whether the tax was deducted at source by a payer, collected at source by a seller, or paid by you as advance tax or self-assessment tax.",
    type: "select",
    options: props.lookups.credit_kinds,
    required: true,
    width: "15rem",
  },
  {
    key: "deductor_tan",
    label: "Deductor's Tax Deduction Account Number",
    short: "Deductor",
    help: "The Tax Deduction and Collection Account Number of the party who deducted the tax. Choosing one you have used before fills in their name.",
    type: "select",
    options: props.lookups.deductors,
    allowFree: true,
    width: "18rem",
    onSelect: (row: Record<string, unknown>, option: TaxOption) => {
      row.deductor_name = option.meta?.name ?? option.label;
    },
  },
  {
    key: "deductor_name",
    label: "Deductor's name",
    short: "Name",
    type: "text",
    placeholder: "Filled in from the account number",
    help: "The name of the party who deducted or collected the tax, as it appears on Form 26AS.",
  },
  {
    key: "section_code",
    label: "Section of deduction",
    short: "Section",
    help: "The section of the Income-tax Act under which the tax was deducted or collected — for example Section 194C for payments to contractors.",
    type: "select",
    options: props.lookups.deduction_sections,
    allowFree: true,
    width: "16rem",
  },
  {
    key: "amount_credited",
    label: "Amount credited",
    short: "Credited",
    type: "money",
    align: "right",
    width: "11rem",
    help: "The tax credited to you, as it appears on the certificate or in Form 26AS.",
  },
  {
    key: "amount_claimed",
    label: "Amount claimed",
    short: "Claimed",
    type: "money",
    align: "right",
    width: "11rem",
    placeholder: "Same as credited",
    help: "Leave blank to claim the whole amount credited. Enter a smaller figure only where part of the credit belongs to another year.",
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

const pending = computed(() => rows.value.filter((row) => num(row.amount_credited) > 0));

async function saveEntries(): Promise<void> {
  if (!pending.value.length) return;
  saving.value = true;
  errorText.value = "";
  try {
    for (const row of pending.value) {
      const kind = text(row.credit_kind);
      const claimed = text(row.amount_claimed);
      await api.post("/tax/credits", {
        ay_code: ayCode.value,
        computation_id: computationId.value,
        credit_kind: kind,
        amount_credited: num(row.amount_credited).toFixed(2),
        amount_claimed: claimed === "" ? null : num(claimed).toFixed(2),
        deductor_tan: DEDUCTOR_KINDS.has(kind) ? text(row.deductor_tan) || null : null,
        deductor_name: DEDUCTOR_KINDS.has(kind) ? text(row.deductor_name) || null : null,
        section_code: DEDUCTOR_KINDS.has(kind) ? text(row.section_code) || null : null,
        remarks: text(row.remarks) || null,
      });
    }
    rows.value = [];
    emit("changed");
  } catch (e: unknown) {
    errorText.value = errorFrom(e, "The credit entry could not be saved.");
  } finally {
    saving.value = false;
  }
}

async function voidEntry(entry: TaxCreditEntry): Promise<void> {
  const confirmed = window.confirm(
    "Void this credit entry? It stays in the ledger for audit but stops counting towards the taxes you have paid.",
  );
  if (!confirmed) return;
  voiding.value = entry.id;
  errorText.value = "";
  try {
    await api.post(`/tax/credits/${entry.id}/void`, {});
    emit("changed");
  } catch (e: unknown) {
    errorText.value = errorFrom(e, "The credit entry could not be voided.");
  } finally {
    voiding.value = "";
  }
}

function reconciliationTone(status: string): string {
  if (status === "Matched") return "complete";
  if (status === "Mismatch") return "has-errors";
  return "not-started";
}
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-credits">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <h2 class="text-base font-semibold text-slate-900">Taxes already paid</h2>
      <p class="text-sm text-slate-600">
        Everything already credited to you for this year — tax deducted at source by your payers,
        tax collected at source, advance tax and self-assessment tax. These reduce the net amount
        payable on the right as soon as they are recorded.
      </p>

      <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <DerivedField
          label="Tax Deducted at Source"
          :value="deductedTotal"
          kind="money"
          :currency="currency"
          explain="Tax your payers deducted before paying you, and deposited with the government on your behalf."
          testid="credits-tds-total"
        />
        <DerivedField
          label="Tax Collected at Source"
          :value="collectedTotal"
          kind="money"
          :currency="currency"
          explain="Tax collected from you by a seller on specified transactions and deposited with the government."
          testid="credits-tcs-total"
        />
        <DerivedField
          label="Advance Tax paid"
          :value="advanceTotal"
          kind="money"
          :currency="currency"
          explain="Tax you paid in instalments during the year, before the year ended."
          testid="credits-advance-total"
        />
        <DerivedField
          label="Self-Assessment Tax paid"
          :value="selfAssessmentTotal"
          kind="money"
          :currency="currency"
          explain="Tax you paid after the year ended and before filing the return."
          testid="credits-self-assessment-total"
        />
        <DerivedField
          label="Total taxes already paid"
          :value="props.preview?.credits_total ?? null"
          kind="money"
          emphasis="strong"
          :currency="currency"
          explain="Every credit claimed for this year. It is deducted from the total tax and interest to arrive at the net amount payable."
          testid="credits-total"
        />
      </div>

      <p class="text-sm text-slate-600">
        Paid tax by challan?
        <button
          class="font-medium text-primary underline"
          type="button"
          data-testid="credits-go-challans"
          @click="emit('navigate', 'challans')"
        >
          Record it under Tax Payment Challans
        </button>
        and claim it here in one click — you will not have to type the amount twice. To match your
        records against the department's,
        <button
          class="font-medium text-primary underline"
          type="button"
          data-testid="credits-go-reconciliation"
          @click="emit('navigate', 'reconciliation')"
        >
          reconcile with Form 26AS
        </button>.
      </p>
    </section>

    <section class="space-y-2">
      <h3 class="text-sm font-semibold text-slate-900">Credits claimed for this year</h3>
      <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table class="w-full text-sm">
          <thead class="bg-slate-50">
            <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
              <th class="px-3 py-2 font-medium">Kind</th>
              <th class="px-3 py-2 font-medium">Deductor</th>
              <th class="px-3 py-2 font-medium">Section</th>
              <th class="px-3 py-2 text-right font-medium">Credited</th>
              <th class="px-3 py-2 text-right font-medium">Claimed</th>
              <th class="px-3 py-2 font-medium">Form 26AS</th>
              <th class="px-3 py-2 text-right font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="entry in entries"
              :key="entry.id"
              class="border-t border-slate-100"
              :class="entry.docstatus === 2 ? 'text-slate-400 line-through' : ''"
            >
              <td class="px-3 py-2">
                {{ taxLabel(`creditKind.${entry.credit_kind}`, entry.credit_kind) }}
              </td>
              <td class="px-3 py-2 text-slate-700">
                {{ entry.deductor_name || entry.deductor_tan || "—" }}
              </td>
              <td class="px-3 py-2 text-slate-600">{{ entry.section_code ?? "—" }}</td>
              <td class="px-3 py-2 text-right tabular-nums text-slate-700">
                {{ money(entry.amount_credited) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums font-medium text-slate-900">
                {{ money(entry.amount_claimed) }}
              </td>
              <td class="px-3 py-2">
                <StatusPill
                  :status="reconciliationTone(entry.reconciliation_status)"
                  :label="entry.reconciliation_status"
                />
              </td>
              <td class="px-3 py-2 text-right">
                <button
                  v-if="entry.docstatus !== 2"
                  type="button"
                  class="text-sm text-red-600 hover:underline disabled:opacity-50"
                  :disabled="props.disabled || voiding === entry.id"
                  :data-testid="`credits-void-${entry.id}`"
                  @click="voidEntry(entry)"
                >
                  {{ voiding === entry.id ? "Voiding…" : "Void" }}
                </button>
                <span v-else class="text-xs text-slate-400">Voided</span>
              </td>
            </tr>
            <tr v-if="!entries.length">
              <td class="px-3 py-6 text-center text-sm text-slate-500" colspan="7">
                No taxes paid or deducted have been claimed for this year yet.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-xs text-slate-500">
        This ledger is append-only. A wrong entry is voided rather than deleted, so the history stays
        complete.
      </p>
    </section>

    <section class="space-y-2" data-field="credits.entries" tabindex="-1">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-900">
          Record taxes already paid
          <InfoTip
            text="Press Enter on the last row to add another. You can paste a whole tax deducted at source schedule straight from a spreadsheet."
            size="sm"
          />
        </h3>
        <button
          type="button"
          class="btn-primary"
          :disabled="props.disabled || saving || pending.length === 0"
          data-testid="credits-save"
          @click="saveEntries"
        >
          {{ saving ? "Saving…" : `Claim ${pending.length || ""} ${pending.length === 1 ? "credit" : "credits"}`.replace(/\s+/g, " ").trim() }}
        </button>
      </div>

      <EditableGrid
        :model-value="rows"
        :columns="columns"
        :new-row="newRow"
        :disabled="props.disabled"
        add-label="Add a credit"
        empty-text="Nothing to add yet. Add a row, or paste the schedule from your working papers."
        :total-keys="['amount_credited', 'amount_claimed']"
        show-row-numbers
        testid="credits-grid"
        @update:model-value="onRows"
      />

      <p v-if="errorText" class="text-sm text-red-700" data-testid="credits-error">
        {{ errorText }}
      </p>
    </section>
  </div>
</template>
