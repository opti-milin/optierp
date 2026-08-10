<script setup lang="ts">
/**
 * Tax Payment Challans — record government tax deposits without leaving the workspace.
 *
 * Recording is grid-based (no modal per challan): the bank account and tax payable
 * account are chosen once and applied to every row, the minor head follows the payment
 * type, and a submitted challan can be claimed as a tax credit in one click so the
 * amount reaches the live result rail without a second data entry.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import DerivedField from "@/components/shared/DerivedField.vue";
import EditableGrid from "@/components/shared/EditableGrid.vue";
import type { EditableGridColumn } from "@/components/shared/EditableGrid.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import SearchSelect from "@/components/shared/SearchSelect.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { taxLabel } from "@/config/taxTerminology";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxChallan,
  TaxOption,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency, formatDate, roundMoney, toISODateLocal } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context);
const { visible } = useFieldVisibility(context);

/** Payment type → minor head, the statutory sub-classification of the deposit. */
const MINOR_HEAD_BY_PAYMENT_TYPE: Record<string, string> = {
  AdvanceTax: "100",
  SelfAssessment: "300",
  SelfAssessmentTax: "300",
  RegularAssessment: "400",
  TaxOnRegularAssessment: "400",
};

/** Payment type → the kind of credit the same money becomes once the challan is submitted. */
const CREDIT_KIND_BY_PAYMENT_TYPE: Record<string, string> = {
  AdvanceTax: "AdvanceTax",
  SelfAssessment: "SelfAssessment",
  SelfAssessmentTax: "SelfAssessment",
  RegularAssessment: "SelfAssessment",
  TaxOnRegularAssessment: "SelfAssessment",
};

const currency = computed(() => props.workspace.context.currency || "INR");
const ayCode = computed(() => props.workspace.computation.ay_code);
const computationId = computed(() => props.workspace.computation.id);

function errorText(e: unknown, fallback = "The request could not be completed."): string {
  const detail = (e as ErrorEnvelope | null)?.detail;
  if (typeof detail === "string" && detail.length > 0) return detail;
  return e instanceof Error ? e.message : fallback;
}

function text(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}

function recommended(options: TaxOption[]): string {
  return options.find((o) => o.meta?.recommended === "true")?.value ?? options[0]?.value ?? "";
}

function minorHeadFor(paymentType: string, option?: TaxOption): string {
  const fromLookup = option?.meta?.minor_head;
  if (fromLookup) return fromLookup;
  return MINOR_HEAD_BY_PAYMENT_TYPE[paymentType] ?? "100";
}

const defaultPaymentType = computed<string>(
  () => props.lookups.challan_types[0]?.value ?? "AdvanceTax",
);

/** A company pays under Corporation Tax; every other assessee under Income Tax. */
const defaultMajorHead = computed<string>(() => {
  const options = props.lookups.major_heads;
  const flagged = options.find((o) => o.meta?.recommended === "true");
  if (flagged) return flagged.value;
  const preferred = props.workspace.context.entity_class_code === "Company" ? "0020" : "0021";
  return options.some((o) => o.value === preferred) ? preferred : options[0]?.value ?? preferred;
});

const bankAccountId = ref<string>(recommended(props.lookups.bank_accounts));
const taxPayableAccountId = ref<string>(recommended(props.lookups.tax_payable_accounts));

const rows = ref<Record<string, unknown>[]>([]);

function newRow(): Record<string, unknown> {
  const paymentType = defaultPaymentType.value;
  return {
    challan_type: paymentType,
    bsr_code: "",
    challan_serial: "",
    cin: "",
    deposit_date: toISODateLocal(new Date()),
    amount: "",
    major_head: defaultMajorHead.value,
    minor_head: minorHeadFor(paymentType),
  };
}

const columns = computed<EditableGridColumn[]>(() => {
  const list: EditableGridColumn[] = [
    {
      key: "challan_type",
      label: taxLabel("term.challanType", "Payment Type"),
      type: "select",
      options: props.lookups.challan_types,
      required: true,
      help: "Whether this deposit is advance tax, self-assessment tax paid before filing, or tax paid on a regular assessment raised by the department.",
      onSelect: (row: Record<string, unknown>, option: TaxOption) => {
        row.minor_head = minorHeadFor(option.value, option);
      },
    },
    {
      key: "bsr_code",
      label: taxLabel("term.bankBranchCode", "Bank Branch Code"),
      short: "Bank Branch Code",
      type: "select",
      options: props.lookups.bank_branch_codes,
      allowFree: true,
      required: true,
      placeholder: "Seven digits",
      help: "The seven-digit Basic Statistical Return code of the bank branch that accepted the payment. It is printed on the stamped challan counterfoil, next to the date of deposit.",
    },
    {
      key: "challan_serial",
      label: "Challan Serial Number",
      short: "Challan Serial Number",
      type: "text",
      required: true,
      help: "The serial number the bank stamped on the challan counterfoil on the day of payment.",
    },
    {
      key: "cin",
      label: "Challan Identification Number",
      short: "Challan Identification Number",
      type: "text",
      placeholder: "Optional",
      help: "The Challan Identification Number combines the bank branch code, the date of deposit and the challan serial number. Leave it blank if the counterfoil does not show one — it is not required to record the payment.",
    },
    {
      key: "deposit_date",
      label: "Date of Deposit",
      type: "date",
      required: true,
      help: "The date the bank accepted the money. Interest for short payment of advance tax is measured from this date, so it must match the counterfoil.",
    },
    {
      key: "amount",
      label: "Amount",
      type: "money",
      align: "right",
      required: true,
      help: "The amount actually deposited, including any interest and cess paid along with the tax.",
    },
  ];
  if (visible("challans.majorHead")) {
    list.push({
      key: "major_head",
      label: "Major Head",
      short: "Major Head",
      type: "select",
      options: props.lookups.major_heads,
      help: "The head of account the payment is credited to. A company pays under Corporation Tax; every other assessee pays under Income Tax (Other Than Companies).",
    });
  }
  list.push({
    key: "minor_head",
    label: "Minor Head",
    short: "Minor Head",
    type: "select",
    options: props.lookups.minor_heads,
    help: "The sub-classification of the payment. It follows the payment type you chose — Advance Tax is 100, Self-Assessment Tax is 300, Tax on Regular Assessment is 400 — and you can override it if the counterfoil says otherwise.",
  });
  return list;
});

const enteredTotal = computed<string>(() =>
  rows.value
    .reduce((sum, row) => sum + (Number(text(row, "amount")) || 0), 0)
    .toFixed(2),
);

const saving = ref(false);
const rowErrors = ref<{ row: number; message: string }[]>([]);
const message = ref("");
const actionError = ref("");

function rowProblem(row: Record<string, unknown>): string | null {
  if (!text(row, "bsr_code").trim()) return "Enter the bank branch code shown on the counterfoil.";
  if (!text(row, "challan_serial").trim()) return "Enter the challan serial number.";
  if (!text(row, "deposit_date")) return "Enter the date the bank accepted the payment.";
  const amount = Number(text(row, "amount"));
  if (!Number.isFinite(amount) || amount <= 0) return "Enter the amount deposited.";
  if (visible("challans.glAccounts") && !bankAccountId.value) {
    return "Choose the bank account the payment left, above the grid.";
  }
  if (visible("challans.glAccounts") && !taxPayableAccountId.value) {
    return "Choose the tax payable account, above the grid.";
  }
  return null;
}

function payloadFor(row: Record<string, unknown>): Record<string, unknown> {
  const paymentType = text(row, "challan_type") || defaultPaymentType.value;
  return {
    ay_code: ayCode.value,
    challan_type: paymentType,
    bsr_code: text(row, "bsr_code").trim(),
    challan_serial: text(row, "challan_serial").trim(),
    deposit_date: text(row, "deposit_date"),
    amount: roundMoney(text(row, "amount")),
    cin: text(row, "cin").trim() || null,
    major_head: text(row, "major_head") || defaultMajorHead.value,
    minor_head: text(row, "minor_head") || minorHeadFor(paymentType),
    bank_account_id: bankAccountId.value || null,
    tax_payable_account_id: taxPayableAccountId.value || null,
    computation_id: computationId.value,
  };
}

async function saveChallans(): Promise<void> {
  if (rows.value.length === 0 || saving.value) return;
  saving.value = true;
  rowErrors.value = [];
  message.value = "";
  actionError.value = "";
  const unsaved: Record<string, unknown>[] = [];
  let saved = 0;
  for (const [index, row] of rows.value.entries()) {
    const problem = rowProblem(row);
    if (problem) {
      rowErrors.value.push({ row: index + 1, message: problem });
      unsaved.push(row);
      continue;
    }
    try {
      await api.post<TaxChallan>("/tax/challans", payloadFor(row));
      saved += 1;
    } catch (e: unknown) {
      rowErrors.value.push({ row: index + 1, message: errorText(e) });
      unsaved.push(row);
    }
  }
  rows.value = unsaved;
  saving.value = false;
  if (saved > 0) {
    message.value =
      saved === 1
        ? "One challan was recorded as a draft. Submit it to post the payment to the books."
        : `${saved} challans were recorded as drafts. Submit them to post the payments to the books.`;
    emit("changed");
  }
}

const busyChallanId = ref("");

async function runChallanAction(id: string, action: "submit" | "cancel"): Promise<void> {
  busyChallanId.value = id;
  actionError.value = "";
  message.value = "";
  try {
    await api.post<TaxChallan>(`/tax/challans/${id}/${action}`);
    message.value =
      action === "submit"
        ? "The challan was submitted and posted to the books. It now counts as tax already paid."
        : "The challan was cancelled and its posting to the books was reversed.";
    emit("changed");
  } catch (e: unknown) {
    actionError.value = errorText(e);
  } finally {
    busyChallanId.value = "";
  }
}

function creditKindFor(challan: TaxChallan): string {
  return CREDIT_KIND_BY_PAYMENT_TYPE[challan.challan_type] ?? "SelfAssessment";
}

function alreadyClaimed(challan: TaxChallan): boolean {
  return props.workspace.credits.some((c) => c.challan_id === challan.id && c.docstatus !== 2);
}

async function claimAsCredit(challan: TaxChallan): Promise<void> {
  busyChallanId.value = challan.id;
  actionError.value = "";
  message.value = "";
  try {
    await api.post("/tax/credits", {
      ay_code: challan.ay_code,
      credit_kind: creditKindFor(challan),
      amount_credited: roundMoney(challan.amount),
      amount_claimed: roundMoney(challan.amount),
      challan_id: challan.id,
      computation_id: computationId.value,
    });
    message.value =
      "The payment is now claimed as a credit against this computation, so the net tax payable on the right has come down.";
    emit("changed");
  } catch (e: unknown) {
    actionError.value = errorText(e);
  } finally {
    busyChallanId.value = "";
  }
}

const challans = computed<TaxChallan[]>(() => props.workspace.challans);

const submittedTotal = computed<string>(() =>
  challans.value
    .filter((c) => c.docstatus === 1)
    .reduce((sum, c) => sum + (Number(c.amount) || 0), 0)
    .toFixed(2),
);

const advanceTaxShortfall = computed<string>(() => {
  const instalments = props.workspace.advance_tax?.instalments ?? [];
  return instalments.reduce((sum, i) => sum + (Number(i.shortfall) || 0), 0).toFixed(2);
});

function docstatusLabel(docstatus: number): string {
  if (docstatus === 1) return "Submitted";
  if (docstatus === 2) return "Cancelled";
  return "Draft";
}

function paymentTypeLabel(code: string): string {
  const option = props.lookups.challan_types.find((o) => o.value === code);
  return option?.label ?? taxLabel(`challanType.${code}`, code);
}

function headLabel(kind: "majorHead" | "minorHead", code: string): string {
  const options = kind === "majorHead" ? props.lookups.major_heads : props.lookups.minor_heads;
  const option = options.find((o) => o.value === code);
  return option?.label ?? taxLabel(`${kind}.${code}`, code);
}
</script>

<template>
  <section class="space-y-6" data-testid="tax-section-challans" data-field="challans.rows">
    <header class="space-y-1">
      <h2 class="text-lg font-semibold text-slate-900">
        {{ taxLabel("section.challans", "Tax Payment Challans") }}
      </h2>
      <p class="text-sm text-slate-600">
        A challan is the receipt for tax you have already paid to the government. Recording one here
        keeps a draft; submitting it posts the payment to the books and counts the amount as tax
        already paid against this assessment year.
      </p>
    </header>

    <p
      v-if="message"
      class="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800"
      data-testid="challans-message"
    >
      {{ message }}
    </p>
    <p
      v-if="actionError"
      class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
      data-testid="challans-error"
    >
      {{ actionError }}
    </p>

    <div
      v-if="Number(advanceTaxShortfall) > 0"
      class="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
    >
      <span>
        Advance tax of {{ formatCurrency(advanceTaxShortfall, currency) }} is still short against
        the instalment schedule. Paying it before the next instalment date reduces interest for
        short payment of advance tax.
      </span>
      <button type="button" class="btn-secondary" @click="emit('navigate', 'interest')">
        See the instalment schedule
      </button>
    </div>

    <div v-if="visible('challans.glAccounts')" class="grid gap-4 sm:grid-cols-2">
      <div data-field="challans.bank_account_id">
        <label class="form-label">
          Bank account the payment was made from
          <InfoTip
            text="Chosen once and applied to every challan you record below. Submitting a challan credits this account and debits the tax payable account."
            title="Bank account"
            size="sm"
          />
        </label>
        <SearchSelect
          v-model="bankAccountId"
          :options="lookups.bank_accounts"
          :disabled="disabled"
          placeholder="Choose the bank account"
          clearable
          testid="challans-bank-account"
        />
      </div>
      <div data-field="challans.tax_payable_account_id">
        <label class="form-label">
          Tax payable account
          <InfoTip
            text="The liability account the payment is set against in the books. It is defaulted to the account your chart of accounts marks for income tax payable."
            title="Tax payable account"
            size="sm"
          />
        </label>
        <SearchSelect
          v-model="taxPayableAccountId"
          :options="lookups.tax_payable_accounts"
          :disabled="disabled"
          placeholder="Choose the tax payable account"
          clearable
          testid="challans-payable-account"
        />
      </div>
    </div>

    <div class="space-y-3" data-field="challans.bsr_code">
      <h3 class="text-sm font-semibold text-slate-800">Record payments</h3>
      <EditableGrid
        v-model="rows"
        :columns="columns"
        :new-row="newRow"
        :disabled="disabled"
        add-label="Add a challan"
        empty-text="No payments entered yet. Add a challan to record a deposit you have already made."
        :total-keys="['amount']"
        show-row-numbers
        testid="challans-grid"
      />
      <ul v-if="rowErrors.length" class="space-y-1 text-sm text-red-700">
        <li v-for="issue in rowErrors" :key="issue.row">Row {{ issue.row }}: {{ issue.message }}</li>
      </ul>
      <div class="flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="btn-primary"
          :disabled="disabled || saving || rows.length === 0"
          data-testid="challans-save"
          @click="saveChallans"
        >
          {{ saving ? "Saving…" : "Save challans" }}
        </button>
        <DerivedField
          label="Total being recorded"
          :value="enteredTotal"
          kind="money"
          :currency="currency"
          explain="The sum of the amounts entered in the grid above. Nothing is saved until you choose Save challans."
          testid="challans-entered-total"
        />
      </div>
    </div>

    <div class="space-y-3">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <h3 class="text-sm font-semibold text-slate-800">Payments already recorded</h3>
        <DerivedField
          label="Submitted payments counted as tax already paid"
          :value="submittedTotal"
          kind="money"
          :currency="currency"
          emphasis="strong"
          explain="Only submitted challans reduce the net tax payable. Draft and cancelled challans are ignored by the computation."
          testid="challans-submitted-total"
        />
      </div>

      <div class="overflow-x-auto rounded-lg border border-slate-200">
        <table class="min-w-full text-sm" data-testid="challans-table">
          <thead class="bg-slate-50 text-left text-slate-600">
            <tr>
              <th class="px-3 py-2 font-medium">Challan</th>
              <th class="px-3 py-2 font-medium">Payment Type</th>
              <th class="px-3 py-2 font-medium">
                Bank Branch Code
                <InfoTip
                  text="The seven-digit Basic Statistical Return code of the bank branch that accepted the payment."
                  size="sm"
                />
              </th>
              <th class="px-3 py-2 font-medium">Date of Deposit</th>
              <th class="px-3 py-2 font-medium">Heads of Account</th>
              <th class="px-3 py-2 text-right font-medium">Amount</th>
              <th class="px-3 py-2 font-medium">Status</th>
              <th class="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="challan in challans" :key="challan.id" class="border-t border-slate-100">
              <td class="px-3 py-2">
                <div class="font-medium text-slate-800">{{ challan.name }}</div>
                <div class="text-xs text-slate-500">
                  Serial {{ challan.challan_serial }}
                  <span v-if="challan.cin"> · Identification {{ challan.cin }}</span>
                </div>
              </td>
              <td class="px-3 py-2">{{ paymentTypeLabel(challan.challan_type) }}</td>
              <td class="px-3 py-2 font-mono text-xs">{{ challan.bsr_code }}</td>
              <td class="px-3 py-2">{{ formatDate(challan.deposit_date) }}</td>
              <td class="px-3 py-2 text-xs text-slate-600">
                {{ headLabel("majorHead", challan.major_head) }} ·
                {{ headLabel("minorHead", challan.minor_head) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums">
                {{ formatCurrency(challan.amount, currency) }}
              </td>
              <td class="px-3 py-2">
                <StatusPill
                  :status="String(challan.docstatus)"
                  :label="taxLabel(`status.${challan.docstatus}`, docstatusLabel(challan.docstatus))"
                  size="sm"
                />
              </td>
              <td class="px-3 py-2">
                <div class="flex flex-wrap justify-end gap-2">
                  <button
                    v-if="challan.docstatus === 0"
                    type="button"
                    class="btn-primary text-xs"
                    :disabled="disabled || busyChallanId === challan.id"
                    data-testid="challans-submit"
                    @click="runChallanAction(challan.id, 'submit')"
                  >
                    Submit
                  </button>
                  <button
                    v-if="challan.docstatus === 1"
                    type="button"
                    class="btn-secondary text-xs"
                    :disabled="disabled || busyChallanId === challan.id"
                    @click="runChallanAction(challan.id, 'cancel')"
                  >
                    Cancel
                  </button>
                  <button
                    v-if="challan.docstatus === 1 && !alreadyClaimed(challan)"
                    type="button"
                    class="btn-secondary text-xs"
                    :disabled="disabled || busyChallanId === challan.id"
                    data-testid="challans-claim-credit"
                    @click="claimAsCredit(challan)"
                  >
                    Claim this payment as a credit
                  </button>
                  <span
                    v-else-if="challan.docstatus === 1"
                    class="text-xs text-slate-500"
                    data-testid="challans-claimed"
                  >
                    Claimed as a credit
                  </span>
                </div>
              </td>
            </tr>
            <tr v-if="challans.length === 0">
              <td colspan="8" class="px-3 py-6 text-center text-slate-500">
                No tax payments recorded for this assessment year yet.
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="text-xs text-slate-500">
        A submitted challan is posted to the books and its amount counts as tax already paid, so it
        reduces the net tax payable shown alongside. Claiming it as a credit links the payment to
        this computation, which is what the return and the Form 26AS reconciliation read.
      </p>
    </div>
  </section>
</template>
