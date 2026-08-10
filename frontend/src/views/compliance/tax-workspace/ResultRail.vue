<script setup lang="ts">
/**
 * Live tax preview panel.
 *
 * Opened from the workspace header (or the compact net-payable chip). Rendered as a
 * right-side drawer so it is never clipped by the app shell — the previous docked
 * column was invisible in Brave / narrower viewports.
 */
import { computed, ref, watch } from "vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import type { TaxPreview, TaxPreviewLine, TaxSectionKey, TaxWorkspaceContext } from "@/types/taxation";
import { formatCurrency } from "@/utils/format";

const props = withDefaults(
  defineProps<{
    preview: TaxPreview | null;
    context: TaxWorkspaceContext | null;
    open?: boolean;
    busy?: boolean;
    dirty?: boolean;
    savedAt?: Date | null;
    saving?: boolean;
    currency?: string;
  }>(),
  { open: false, busy: false, dirty: false, savedAt: null, saving: false, currency: "INR" },
);

const emit = defineEmits<{
  "update:open": [value: boolean];
  recompute: [];
  navigate: [section: TaxSectionKey];
}>();

const showAllLines = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) showAllLines.value = false;
  },
);

const currency = computed(() => props.context?.currency ?? props.currency);

function amount(value: string | number | null | undefined): string {
  return formatCurrency(value ?? 0, currency.value);
}

function toNumber(value: string | number | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isNaN(parsed) ? 0 : parsed;
}

const isRefund = computed(() => toNumber(props.preview?.refund_due) > 0);
const headlineLabel = computed(() => (isRefund.value ? "Refund Due" : "Net Tax Payable"));
const headlineAmount = computed(() =>
  amount(isRefund.value ? props.preview?.refund_due : props.preview?.net_payable),
);

const allLines = computed<TaxPreviewLine[]>(() => props.preview?.lines ?? []);
const visibleLines = computed<TaxPreviewLine[]>(() =>
  showAllLines.value
    ? allLines.value
    : allLines.value.filter((line) => toNumber(line.amount) !== 0 || line.kind === "total"),
);
const hiddenLineCount = computed(() => allLines.value.length - visibleLines.value.length);

function rowClass(line: TaxPreviewLine): string {
  if (line.emphasis === "total") {
    return "border-t-2 border-slate-800 pt-2 mt-1 font-semibold text-slate-900";
  }
  if (line.emphasis === "subtotal") {
    return "border-t border-slate-300 pt-1.5 mt-1 font-medium text-slate-800";
  }
  return "text-slate-600";
}

const matApplied = computed(
  () => (props.preview?.tax_applied_basis ?? "Normal").toLowerCase() !== "normal",
);
const matNote = computed(() =>
  matApplied.value
    ? "Minimum Alternate Tax applies — it exceeds tax under normal provisions."
    : "Tax under normal provisions applies — it exceeds Minimum Alternate Tax.",
);

const savedAtLabel = computed(() => {
  if (!props.savedAt) return "";
  return props.savedAt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
});

type Freshness = { tone: string; text: string; showRun: boolean };

const freshness = computed<Freshness>(() => {
  if (props.saving) {
    return { tone: "text-slate-500", text: "Saving…", showRun: false };
  }
  if (props.dirty) {
    return {
      tone: "text-amber-700",
      text: "Unsaved changes — figures below are a live preview",
      showRun: false,
    };
  }
  if (props.preview && props.preview.matches_current_run === false) {
    return {
      tone: "text-amber-700",
      text: "Live preview — differs from the last saved computation run",
      showRun: true,
    };
  }
  if (!props.preview) {
    return { tone: "text-slate-500", text: "No figures computed yet", showRun: false };
  }
  return {
    tone: "text-emerald-700",
    text: savedAtLabel.value
      ? `Matches the last saved computation run · Saved at ${savedAtLabel.value}`
      : "Matches the last saved computation run",
    showRun: false,
  };
});

const quickLinks: { key: TaxSectionKey; label: string }[] = [
  { key: "credits", label: "Taxes Already Paid" },
  { key: "interest", label: "Advance Tax & Interest" },
  { key: "summary", label: "Statement of Total Income" },
  { key: "review", label: "Review & Validate" },
];

function close(): void {
  emit("update:open", false);
}

function go(section: TaxSectionKey): void {
  emit("navigate", section);
  emit("update:open", false);
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex justify-end"
      data-testid="tax-result-rail"
    >
      <button
        type="button"
        class="absolute inset-0 bg-slate-900/30"
        aria-label="Close live preview"
        data-testid="tax-live-preview-backdrop"
        @click="close"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="tax-live-preview-title"
        class="relative flex h-full w-full max-w-md flex-col border-l border-slate-200 bg-white shadow-xl"
        data-testid="tax-live-preview-panel"
      >
        <div class="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div class="min-w-0">
            <h2 id="tax-live-preview-title" class="text-sm font-medium text-slate-800">
              Live preview
            </h2>
            <p class="mt-0.5 truncate text-xs text-slate-500">
              {{ context?.ay_label ?? "" }}
            </p>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <span v-if="busy" class="animate-pulse text-xs text-slate-400">updating…</span>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-sm text-slate-500 hover:bg-slate-100 hover:text-slate-800"
              data-testid="tax-live-preview-close"
              @click="close"
            >
              Close
            </button>
          </div>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto">
          <div class="px-4 py-4" :class="busy ? 'animate-pulse' : ''">
            <p class="text-xs uppercase tracking-wide text-slate-500">{{ headlineLabel }}</p>
            <p
              class="mt-1 text-3xl font-semibold tabular-nums"
              :class="isRefund ? 'text-emerald-700' : 'text-slate-900'"
              data-testid="tax-net-payable"
            >
              {{ headlineAmount }}
            </p>
            <p class="mt-2 text-xs" :class="freshness.tone">{{ freshness.text }}</p>
            <button
              v-if="freshness.showRun"
              type="button"
              class="btn-primary mt-2 w-full !py-1.5 text-xs"
              @click="emit('recompute')"
            >
              Save this computation run
            </button>
          </div>

          <div v-if="visibleLines.length" class="space-y-1 px-4 pb-3 text-sm">
            <div
              v-for="line in visibleLines"
              :key="line.key"
              class="flex items-baseline justify-between gap-2"
              :class="rowClass(line)"
            >
              <span class="flex min-w-0 items-center gap-1">
                <span class="truncate">{{ line.label }}</span>
                <InfoTip
                  v-if="line.explain || line.statutory_ref"
                  size="sm"
                  :title="line.label"
                  :text="line.explain ?? line.label"
                  :statutory-ref="line.statutory_ref ?? null"
                />
              </span>
              <span class="shrink-0 tabular-nums">{{ amount(line.amount) }}</span>
            </div>
            <button
              v-if="hiddenLineCount > 0 || showAllLines"
              type="button"
              class="pt-1 text-xs text-primary hover:underline"
              @click="showAllLines = !showAllLines"
            >
              {{
                showAllLines
                  ? "Show only lines with a value"
                  : `Show all lines (${hiddenLineCount} more)`
              }}
            </button>
          </div>

          <div
            v-if="preview && preview.tax_mat !== null && preview.tax_mat !== undefined"
            class="mx-4 mb-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm"
          >
            <p class="font-medium text-slate-800">Minimum Alternate Tax comparison</p>
            <div class="mt-1 flex items-baseline justify-between gap-2 text-slate-600">
              <span class="min-w-0">Tax under normal provisions</span>
              <span class="shrink-0 tabular-nums">{{ amount(preview.tax_normal) }}</span>
            </div>
            <div class="flex items-baseline justify-between gap-2 text-slate-600">
              <span class="min-w-0">Minimum Alternate Tax</span>
              <span class="shrink-0 tabular-nums">{{ amount(preview.tax_mat) }}</span>
            </div>
            <p class="mt-1.5 text-xs text-slate-600">{{ matNote }}</p>
            <button
              type="button"
              class="mt-1 text-xs text-primary hover:underline"
              @click="go('mat')"
            >
              Open Minimum Alternate Tax (Section 115JB)
            </button>
          </div>

          <div v-if="preview?.notes?.length" class="mx-4 mb-3 space-y-0.5 text-xs text-slate-500">
            <p v-for="(note, index) in preview.notes" :key="index">{{ note }}</p>
          </div>

          <div class="flex flex-wrap gap-x-3 gap-y-1 px-4 pb-3">
            <button
              v-for="link in quickLinks"
              :key="link.key"
              type="button"
              class="text-xs text-slate-600 hover:text-primary hover:underline"
              @click="go(link.key)"
            >
              {{ link.label }}
            </button>
          </div>
        </div>

        <p class="border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          This is a preview and is not recorded. Choose “Save computation run” to record it in the
          computation history.
        </p>
      </aside>
    </div>
  </Teleport>
</template>
