<script setup lang="ts">
/**
 * Computation History — the append-only run trail.
 *
 * Every saved run keeps its own fingerprints of the rules applied and the inputs
 * used, so a figure filed years ago can still be reproduced. Runs are never edited
 * or deleted; a recomputation supersedes the previous run and both stay on record.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import InfoTip from "@/components/shared/InfoTip.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxRun,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatCurrency, formatDate } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const currency = computed(() => context.value?.currency ?? "INR");

const expanded = ref<string>("");
const explanation = ref<Record<string, unknown> | null>(null);
const loadingExplain = ref(false);
const errorText = ref("");

const runs = computed<TaxRun[]>(() => props.workspace.runs ?? []);
const currentRunId = computed(() => props.workspace.computation.current_run_id);

function money(value: string | number | null | undefined): string {
  return formatCurrency(value ?? 0, currency.value);
}

/** The live figures come from an unsaved preview, so they can legitimately differ. */
const previewDiffers = computed(() => props.preview !== null && !props.preview.matches_current_run);

async function toggleExplain(run: TaxRun): Promise<void> {
  if (expanded.value === run.id) {
    expanded.value = "";
    explanation.value = null;
    return;
  }
  expanded.value = run.id;
  explanation.value = null;
  loadingExplain.value = true;
  errorText.value = "";
  try {
    const { data } = await api.get<Record<string, unknown>>(
      `/tax/computations/${props.workspace.computation.id}/runs/${run.id}/explain`,
    );
    explanation.value = data;
  } catch (e: unknown) {
    const detail = (e as ErrorEnvelope | null)?.detail;
    errorText.value =
      detail ?? (e instanceof Error ? e.message : "The explanation could not be loaded.");
  } finally {
    loadingExplain.value = false;
  }
}

function runStatus(run: TaxRun): string {
  if (run.id === currentRunId.value) return "complete";
  return run.superseded_at ? "not-started" : "in-progress";
}

function runStatusLabel(run: TaxRun): string {
  if (run.id === currentRunId.value) return "Current";
  return run.superseded_at ? "Superseded" : "Earlier run";
}
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-audit">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-2">
      <h2 class="text-base font-semibold text-slate-900">
        Computation history
        <InfoTip
          text="Every calculation you save is recorded permanently, with a fingerprint of the statutory rules applied and of the inputs used. Nothing here is ever edited or deleted."
          size="md"
        />
      </h2>
      <p class="text-sm text-slate-600">
        {{ runs.length }} {{ runs.length === 1 ? "run has" : "runs have" }} been saved for this year.
        Open any of them to see exactly how the figures were arrived at.
      </p>

      <p
        v-if="previewDiffers"
        class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
        data-testid="audit-preview-differs"
      >
        The figures on the right are a live calculation of the current worksheet and no longer match
        the last saved run. Save a computation run to record them.
      </p>

      <p v-if="errorText" class="text-sm text-red-700" data-testid="audit-error">{{ errorText }}</p>
    </section>

    <section class="space-y-2" data-field="audit.runs" tabindex="-1">
      <div v-if="!runs.length" class="rounded-lg border border-slate-200 bg-white px-3 py-6 text-center text-sm text-slate-500">
        No computation has been run yet. Use “Save computation run” in the header to record the
        first one.
      </div>

      <article
        v-for="run in runs"
        :key="run.id"
        class="rounded-lg border border-slate-200 bg-white"
        :data-testid="`audit-run-${run.run_no}`"
      >
        <header class="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-sm font-semibold text-slate-900">Run {{ run.run_no }}</h3>
              <StatusPill :status="runStatus(run)" :label="runStatusLabel(run)" />
              <span class="text-xs text-slate-500">{{ run.trigger }}</span>
            </div>
            <p class="mt-0.5 text-xs text-slate-500">
              {{ run.creation ? formatDate(run.creation) : "—" }}
              <span v-if="run.duration_ms"> · took {{ run.duration_ms }} milliseconds</span>
              · engine {{ run.engine_version }}
            </p>
          </div>
          <button
            type="button"
            class="btn-secondary"
            :data-testid="`audit-explain-${run.run_no}`"
            @click="toggleExplain(run)"
          >
            {{ expanded === run.id ? "Hide the working" : "Show the working" }}
          </button>
        </header>

        <dl v-if="run.result" class="grid gap-3 border-t border-slate-100 px-4 py-3 text-sm sm:grid-cols-4">
          <div>
            <dt class="text-xs uppercase tracking-wide text-slate-500">Total income</dt>
            <dd class="tabular-nums text-slate-800">{{ money(run.result.taxable_income) }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-slate-500">Tax applied on</dt>
            <dd class="text-slate-800">
              {{
                run.result.tax_applied_basis.toUpperCase() === "MAT"
                  ? "Minimum Alternate Tax"
                  : "Normal provisions"
              }}
            </dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-slate-500">Total tax and interest</dt>
            <dd class="tabular-nums text-slate-800">{{ money(run.result.total_tax) }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-slate-500">Net tax payable</dt>
            <dd class="tabular-nums font-medium text-slate-900">
              {{ money(run.result.net_payable) }}
            </dd>
          </div>
        </dl>

        <div class="border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          Rules fingerprint {{ run.ruleset_hash.slice(0, 16) }}… · Inputs fingerprint
          {{ run.input_hash.slice(0, 16) }}…
          <InfoTip
            text="Two fingerprints taken when the run was saved: one over the statutory rules that applied, one over the figures fed in. Together they let this run be reproduced exactly."
            size="sm"
          />
        </div>

        <div v-if="expanded === run.id" class="border-t border-slate-100 px-4 py-3">
          <p v-if="loadingExplain" class="text-sm text-slate-500">Loading the working…</p>
          <pre
            v-else-if="explanation"
            class="max-h-96 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700"
            :data-testid="`audit-explanation-${run.run_no}`"
            >{{ JSON.stringify(explanation, null, 2) }}</pre
          >
        </div>
      </article>
    </section>

    <p class="text-sm text-slate-600">
      Looking for the statement a reviewer signs?
      <button
        class="font-medium text-primary underline"
        type="button"
        data-testid="audit-go-summary"
        @click="emit('navigate', 'summary')"
      >
        Statement of Total Income
      </button>
      presents the current figures in reading order.
    </p>
  </div>
</template>
