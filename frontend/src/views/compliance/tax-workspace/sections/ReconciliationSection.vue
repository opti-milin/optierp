<script setup lang="ts">
/**
 * Reconcile with Form 26AS — match the tax credited against the company's Permanent
 * Account Number on the government portal with the credits recorded in the books.
 *
 * The portal statement can be attached as a file or pasted; bad input is reported as a
 * sentence, never as a raw parser exception. Every reconciliation is kept, so an earlier
 * match can always be reproduced.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import DerivedField from "@/components/shared/DerivedField.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { taxLabel } from "@/config/taxTerminology";
import type { ErrorEnvelope } from "@/types/core";
import type {
  Tax26asRecon,
  Tax26asReconRow,
  TaxSectionEmits,
  TaxSectionProps,
} from "@/types/taxation";
import { formatCurrency, formatDate } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const BUCKET_LABELS: Record<string, string> = {
  matched: "Matched with the portal statement",
  match: "Matched with the portal statement",
  mismatch: "Amount differs from the portal statement",
  mismatched: "Amount differs from the portal statement",
  only_in_books: "Recorded in the books only",
  only_in_26as: "Shown in the portal statement only",
  only_in_portal: "Shown in the portal statement only",
};

const BUCKET_ORDER = [
  "mismatch",
  "mismatched",
  "only_in_books",
  "only_in_26as",
  "only_in_portal",
  "matched",
  "match",
];

const currency = computed(() => props.workspace.context.currency || "INR");
const ayCode = computed(() => props.workspace.computation.ay_code);
const computationId = computed(() => props.workspace.computation.id);

function errorText(e: unknown, fallback = "The reconciliation could not be completed."): string {
  const detail = (e as ErrorEnvelope | null)?.detail;
  if (typeof detail === "string" && detail.length > 0) return detail;
  return e instanceof Error ? e.message : fallback;
}

const portalText = ref("");
const fileName = ref("");
const busy = ref(false);
const parseError = ref("");
const requestError = ref("");
const message = ref("");

function onFileChosen(event: Event): void {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  fileName.value = file.name;
  parseError.value = "";
  const reader = new FileReader();
  reader.onload = () => {
    portalText.value = typeof reader.result === "string" ? reader.result : "";
  };
  reader.onerror = () => {
    parseError.value = `${file.name} could not be read. Try opening it and pasting its contents below instead.`;
  };
  reader.readAsText(file);
}

function parsePortalStatement(): Record<string, unknown> | null {
  const raw = portalText.value.trim();
  if (!raw) {
    parseError.value =
      "Attach the statement downloaded from the government portal, or paste its contents in the box below.";
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      parseError.value =
        "The statement was read, but it is not in the shape the portal produces. It should be a single record of tax credited, not a list or a plain value.";
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch {
    parseError.value =
      "That does not look like a statement downloaded from the government portal. Download the annual tax statement again in its machine-readable format and attach the file unchanged.";
    return null;
  }
}

async function reconcile(): Promise<void> {
  if (busy.value) return;
  parseError.value = "";
  requestError.value = "";
  message.value = "";
  const form26as = parsePortalStatement();
  if (!form26as) return;
  busy.value = true;
  try {
    const { data } = await api.post<Tax26asRecon>("/tax/credits/reconcile-26as", {
      ay_code: ayCode.value,
      form26as,
      computation_id: computationId.value,
    });
    message.value =
      data.mismatch + data.only_in_books + data.only_in_26as === 0
        ? "Everything matched. The credits in the books agree with the government's statement."
        : `${data.mismatch + data.only_in_books + data.only_in_26as} entries need attention. They are listed below.`;
    emit("changed");
  } catch (e: unknown) {
    requestError.value = errorText(e);
  } finally {
    busy.value = false;
  }
}

const reconciliations = computed<Tax26asRecon[]>(() => props.workspace.reconciliations);
const latest = computed<Tax26asRecon | null>(() => reconciliations.value[0] ?? null);
const earlier = computed<Tax26asRecon[]>(() => reconciliations.value.slice(1));

const needsAttention = computed<number>(() => {
  const run = latest.value;
  if (!run) return 0;
  return run.mismatch + run.only_in_books + run.only_in_26as;
});

interface BucketGroup {
  bucket: string;
  label: string;
  rows: Tax26asReconRow[];
}

const groupedRows = computed<BucketGroup[]>(() => {
  const run = latest.value;
  if (!run) return [];
  const buckets = new Map<string, Tax26asReconRow[]>();
  for (const row of run.rows) {
    const list = buckets.get(row.bucket) ?? [];
    list.push(row);
    buckets.set(row.bucket, list);
  }
  return [...buckets.entries()]
    .sort((a, b) => {
      const ai = BUCKET_ORDER.indexOf(a[0]);
      const bi = BUCKET_ORDER.indexOf(b[0]);
      return (ai === -1 ? BUCKET_ORDER.length : ai) - (bi === -1 ? BUCKET_ORDER.length : bi);
    })
    .map(([bucket, rows]) => ({
      bucket,
      label: BUCKET_LABELS[bucket] ?? bucket.replace(/_/g, " "),
      rows,
    }));
});

function variance(row: Tax26asReconRow): string {
  return ((Number(row.portal_amount) || 0) - (Number(row.books_amount) || 0)).toFixed(2);
}

function isMatched(bucket: string): boolean {
  return bucket === "matched" || bucket === "match";
}

function rowTone(row: Tax26asReconRow): string {
  if (isMatched(row.bucket)) return "text-slate-700";
  return Number(variance(row)) === 0 ? "text-amber-800" : "text-red-700";
}

function statusLabel(status: string): string {
  return taxLabel(`status.${status}`, status);
}

function reconTimestamp(run: Tax26asRecon): string {
  if (!run.creation) return "Date not recorded";
  return `${formatDate(run.creation.slice(0, 10))} at ${run.creation.slice(11, 16)}`;
}
</script>

<template>
  <section class="space-y-6" data-testid="tax-section-reconciliation">
    <header class="space-y-1">
      <h2 class="text-lg font-semibold text-slate-900">
        {{ taxLabel("section.reconciliation", "Reconcile with Form 26AS") }}
      </h2>
      <p class="text-sm text-slate-600">
        {{ taxLabel("term.form26as", "Form 26AS") }} is the government's annual statement of every
        rupee of tax credited against the company's Permanent Account Number. Matching it against
        the books before filing is what stops a credit being disallowed later.
      </p>
    </header>

    <div class="space-y-3 rounded-lg border border-slate-200 p-4" data-field="reconciliation.form26as">
      <h3 class="text-sm font-semibold text-slate-800">Attach the portal statement</h3>
      <div class="flex flex-wrap items-center gap-3 text-sm">
        <input
          type="file"
          accept=".json,application/json"
          class="text-sm text-slate-600 file:mr-3 file:rounded-md file:border file:border-gray-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-gray-700"
          :disabled="disabled || busy"
          data-testid="reconciliation-file"
          @change="onFileChosen"
        />
        <span v-if="fileName" class="text-xs text-slate-500">Attached: {{ fileName }}</span>
      </div>
      <label class="form-label" for="reconciliation-paste">
        Or paste the statement contents
        <InfoTip
          text="Download the annual tax statement from the government portal in its machine-readable format, then attach the file or paste its contents here. Nothing is sent anywhere except your own server."
          title="Where this comes from"
          size="sm"
        />
      </label>
      <textarea
        id="reconciliation-paste"
        v-model="portalText"
        rows="6"
        class="form-input font-mono text-xs"
        placeholder="Paste the contents of the statement here"
        :disabled="disabled || busy"
        data-testid="reconciliation-paste"
      />
      <p v-if="parseError" class="text-sm text-red-700" data-testid="reconciliation-parse-error">
        {{ parseError }}
      </p>
      <p v-if="requestError" class="text-sm text-red-700" data-testid="reconciliation-error">
        {{ requestError }}
      </p>
      <p v-if="message" class="text-sm text-emerald-800" data-testid="reconciliation-message">
        {{ message }}
      </p>
      <button
        type="button"
        class="btn-primary"
        :disabled="disabled || busy"
        data-testid="reconciliation-run"
        @click="reconcile"
      >
        {{ busy ? "Matching…" : "Match against the books" }}
      </button>
    </div>

    <div v-if="latest" class="space-y-4" data-testid="reconciliation-result">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h3 class="text-sm font-semibold text-slate-800">Latest reconciliation</h3>
        <StatusPill :status="latest.status" :label="statusLabel(latest.status)" size="sm" />
      </div>

      <div class="grid gap-4 sm:grid-cols-3">
        <DerivedField
          label="Amount as per books"
          :value="latest.books_total"
          kind="money"
          :currency="currency"
          explain="The total tax credit recorded against this assessment year in the books."
          testid="reconciliation-books-total"
        />
        <DerivedField
          :label="`Amount as per ${taxLabel('term.form26as', 'Form 26AS')}`"
          :value="latest.portal_total"
          kind="money"
          :currency="currency"
          explain="The total tax credited against the company's Permanent Account Number in the statement you attached."
          testid="reconciliation-portal-total"
        />
        <DerivedField
          label="Difference"
          :value="latest.difference"
          kind="money"
          :currency="currency"
          emphasis="strong"
          explain="Amount as per the portal statement less the amount as per the books. A positive difference means the government has recorded more credit than the books claim."
          testid="reconciliation-difference"
        />
      </div>

      <dl class="grid gap-3 text-sm sm:grid-cols-4">
        <div class="rounded-md border border-slate-200 px-3 py-2">
          <dt class="text-slate-500">Matched</dt>
          <dd class="text-lg font-medium text-slate-900">{{ latest.matched }}</dd>
        </div>
        <div class="rounded-md border border-slate-200 px-3 py-2">
          <dt class="text-slate-500">Amount differs</dt>
          <dd class="text-lg font-medium text-slate-900">{{ latest.mismatch }}</dd>
        </div>
        <div class="rounded-md border border-slate-200 px-3 py-2">
          <dt class="text-slate-500">Only in the books</dt>
          <dd class="text-lg font-medium text-slate-900">{{ latest.only_in_books }}</dd>
        </div>
        <div class="rounded-md border border-slate-200 px-3 py-2">
          <dt class="text-slate-500">
            Only in {{ taxLabel("term.form26as", "Form 26AS") }}
          </dt>
          <dd class="text-lg font-medium text-slate-900">{{ latest.only_in_26as }}</dd>
        </div>
      </dl>

      <div
        v-if="needsAttention > 0"
        class="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      >
        <span>
          {{ needsAttention }} entries did not match. Add or correct the affected credit entries so
          the amount claimed agrees with the government's statement.
        </span>
        <button type="button" class="btn-secondary" @click="emit('navigate', 'credits')">
          Go to Taxes Already Paid
        </button>
      </div>

      <div v-for="group in groupedRows" :key="group.bucket" class="space-y-2">
        <h4 class="text-sm font-medium text-slate-700">
          {{ group.label }}
          <span class="text-slate-400">({{ group.rows.length }})</span>
        </h4>
        <div class="overflow-x-auto rounded-lg border border-slate-200">
          <table class="min-w-full text-sm">
            <thead class="bg-slate-50 text-left text-slate-600">
              <tr>
                <th class="px-3 py-2 font-medium">
                  Deductor Tax Deduction Account Number
                  <InfoTip
                    text="The account number quoted by the party that deducted tax at source before paying the company."
                    size="sm"
                  />
                </th>
                <th class="px-3 py-2 font-medium">Section</th>
                <th class="px-3 py-2 text-right font-medium">Amount as per books</th>
                <th class="px-3 py-2 text-right font-medium">
                  Amount as per {{ taxLabel("term.form26as", "Form 26AS") }}
                </th>
                <th class="px-3 py-2 text-right font-medium">Variance</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, index) in group.rows"
                :key="`${group.bucket}-${index}`"
                class="border-t border-slate-100"
                :class="isMatched(group.bucket) ? '' : 'bg-red-50/40'"
              >
                <td class="px-3 py-2 font-mono text-xs">{{ row.deductor_tan || "Not quoted" }}</td>
                <td class="px-3 py-2">{{ row.section_code || "Not quoted" }}</td>
                <td class="px-3 py-2 text-right tabular-nums">
                  {{ formatCurrency(row.books_amount, currency) }}
                </td>
                <td class="px-3 py-2 text-right tabular-nums">
                  {{ formatCurrency(row.portal_amount, currency) }}
                </td>
                <td class="px-3 py-2 text-right font-medium tabular-nums" :class="rowTone(row)">
                  {{ formatCurrency(variance(row), currency) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <p v-else class="text-sm text-slate-500" data-testid="reconciliation-empty">
      No reconciliation has been run for this assessment year yet.
    </p>

    <div v-if="earlier.length" class="space-y-2">
      <h3 class="text-sm font-semibold text-slate-800">Earlier reconciliations</h3>
      <ul class="divide-y divide-slate-100 rounded-lg border border-slate-200 text-sm">
        <li
          v-for="run in earlier"
          :key="run.id"
          class="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
        >
          <span class="text-slate-700">
            {{ reconTimestamp(run) }} — {{ statusLabel(run.status) }}, difference
            {{ formatCurrency(run.difference, currency) }}
          </span>
          <span class="flex items-center gap-2 font-mono text-xs text-slate-500">
            {{ run.payload_hash.slice(0, 12) }}…
            <InfoTip
              :text="`Fingerprint of the statement that was matched: ${run.payload_hash}. Re-running the same statement produces the same fingerprint, which is how an earlier match can be proved.`"
              title="Statement fingerprint"
              size="sm"
            />
          </span>
        </li>
      </ul>
    </div>
  </section>
</template>
