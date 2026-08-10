<script setup lang="ts">
/**
 * Review and Validate — the gate before submission.
 *
 * Blocking errors stop the computation being submitted; advisory warnings never do.
 * Every issue names the section and field that caused it, so one click puts the
 * cursor on the offending input rather than leaving the user to hunt for it.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import InfoTip from "@/components/shared/InfoTip.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { TAX_SECTIONS } from "@/config/taxSections";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxSectionEmits,
  TaxSectionKey,
  TaxSectionProps,
  TaxValidation,
  TaxValidationIssue,
  TaxWorkspaceContext,
} from "@/types/taxation";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const { visible } = useFieldVisibility(context);

const revalidating = ref(false);
const latest = ref<TaxValidation | null>(null);
const errorText = ref("");

const validation = computed<TaxValidation>(() => latest.value ?? props.workspace.validation);

const blocking = computed<TaxValidationIssue[]>(() =>
  validation.value.issues.filter((issue) => issue.severity === "blocking"),
);

const advisory = computed<TaxValidationIssue[]>(() =>
  validation.value.issues.filter((issue) => issue.severity === "advisory"),
);

const canSubmit = computed(
  () => blocking.value.length === 0 && (context.value?.is_draft ?? false),
);

function sectionLabel(key: string): string {
  return TAX_SECTIONS.find((def) => def.key === key)?.label ?? key;
}

function isSectionKey(value: string): value is TaxSectionKey {
  return TAX_SECTIONS.some((def) => def.key === value);
}

function goToIssue(issue: TaxValidationIssue): void {
  if (!isSectionKey(issue.section)) return;
  emit("navigate", issue.section, issue.field ? `${issue.section}.${issue.field}` : undefined);
}

async function revalidate(): Promise<void> {
  revalidating.value = true;
  errorText.value = "";
  try {
    const { data } = await api.get<TaxValidation>(
      `/tax/workspace/computation/${props.workspace.computation.id}/validate`,
    );
    latest.value = data;
  } catch (e: unknown) {
    const detail = (e as ErrorEnvelope | null)?.detail;
    errorText.value =
      detail ?? (e instanceof Error ? e.message : "The computation could not be validated.");
  } finally {
    revalidating.value = false;
  }
}

const headline = computed(() => {
  if (blocking.value.length > 0) {
    const n = blocking.value.length;
    return `${n} ${n === 1 ? "issue" : "issues"} must be resolved before this computation can be submitted.`;
  }
  if (advisory.value.length > 0) {
    const n = advisory.value.length;
    return `Nothing is blocking submission. ${n} ${n === 1 ? "point is" : "points are"} worth a look first.`;
  }
  return "Everything checks out. This computation is ready to be submitted.";
});
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-review">
    <section
      class="rounded-lg border p-4 space-y-3"
      :class="
        blocking.length
          ? 'border-red-200 bg-red-50'
          : advisory.length
            ? 'border-amber-200 bg-amber-50'
            : 'border-emerald-200 bg-emerald-50'
      "
    >
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-slate-900">Review and validate</h2>
          <p class="mt-1 text-sm text-slate-700" data-testid="review-headline">{{ headline }}</p>
        </div>
        <div class="flex items-center gap-2">
          <StatusPill
            :status="blocking.length ? 'has-errors' : 'complete'"
            :label="`${blocking.length} blocking`"
            size="md"
          />
          <StatusPill
            :status="advisory.length ? 'in-progress' : 'complete'"
            :label="`${advisory.length} advisory`"
            size="md"
          />
          <button
            type="button"
            class="btn-secondary"
            :disabled="revalidating"
            data-testid="review-revalidate"
            @click="revalidate"
          >
            {{ revalidating ? "Checking…" : "Check again" }}
          </button>
        </div>
      </div>
      <p v-if="errorText" class="text-sm text-red-700" data-testid="review-error">{{ errorText }}</p>
    </section>

    <section class="space-y-2" data-testid="review-blocking">
      <h3 class="text-sm font-semibold text-slate-900">
        Blocking issues
        <InfoTip
          text="These prevent the computation being submitted and the return being filed. Each one links to the field that caused it."
          size="sm"
        />
      </h3>
      <ul v-if="blocking.length" class="space-y-2">
        <li
          v-for="(issue, index) in blocking"
          :key="`${issue.code}-${index}`"
          class="rounded-lg border border-red-200 bg-white px-3 py-2"
          :data-testid="`review-issue-${issue.code}`"
        >
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div class="min-w-0">
              <p class="text-sm font-medium text-slate-900">{{ issue.message }}</p>
              <p v-if="issue.hint" class="mt-0.5 text-sm text-slate-600">{{ issue.hint }}</p>
              <p class="mt-1 text-xs text-slate-500">In {{ sectionLabel(issue.section) }}</p>
            </div>
            <button
              type="button"
              class="btn-secondary shrink-0"
              :data-testid="`review-goto-${issue.code}`"
              @click="goToIssue(issue)"
            >
              Fix this
            </button>
          </div>
        </li>
      </ul>
      <p v-else class="rounded-lg border border-slate-200 bg-white px-3 py-4 text-sm text-slate-500">
        Nothing is blocking submission.
      </p>
    </section>

    <section class="space-y-2" data-testid="review-advisory">
      <h3 class="text-sm font-semibold text-slate-900">
        Advisory warnings
        <InfoTip
          text="Worth checking, but they do not stop the return being submitted. Use your judgement."
          size="sm"
        />
      </h3>
      <ul v-if="advisory.length" class="space-y-2">
        <li
          v-for="(issue, index) in advisory"
          :key="`${issue.code}-${index}`"
          class="rounded-lg border border-amber-200 bg-white px-3 py-2"
          :data-testid="`review-advisory-${issue.code}`"
        >
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div class="min-w-0">
              <p class="text-sm text-slate-800">{{ issue.message }}</p>
              <p v-if="issue.hint" class="mt-0.5 text-sm text-slate-600">{{ issue.hint }}</p>
              <p class="mt-1 text-xs text-slate-500">In {{ sectionLabel(issue.section) }}</p>
            </div>
            <button
              type="button"
              class="text-sm text-primary hover:underline shrink-0"
              @click="goToIssue(issue)"
            >
              Take a look
            </button>
          </div>
        </li>
      </ul>
      <p v-else class="rounded-lg border border-slate-200 bg-white px-3 py-4 text-sm text-slate-500">
        No advisory warnings.
      </p>
    </section>

    <section
      v-if="visible('review.submitGate')"
      class="rounded-lg border border-slate-200 bg-white p-4 space-y-2"
      data-field="review.submitGate"
      tabindex="-1"
    >
      <h3 class="text-sm font-semibold text-slate-900">Submit the computation</h3>
      <p class="text-sm text-slate-600">
        Submitting freezes the worksheet and posts the current tax provision to the general ledger.
        The computation can still be cancelled afterwards, and every run stays in the history.
      </p>
      <p v-if="!canSubmit" class="text-sm text-red-700" data-testid="review-submit-blocked">
        Submission is blocked until the issues above are resolved.
      </p>
      <p class="text-xs text-slate-500">
        Use the Submit button in the header, or press the keyboard shortcut shown under keyboard
        shortcuts. Blocking issues are enforced by the server as well, so nothing can slip through.
      </p>
    </section>
  </div>
</template>
