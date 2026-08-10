<script setup lang="ts">
/**
 * The Income Tax workspace — one screen per assessment year.
 *
 * Section navigation on the left, the active section panel in the middle, the live
 * result rail on the right. Changing section swaps the panel and rewrites the
 * `?section=` query only; it never re-mounts the route or refetches the aggregate.
 */
import { computed, nextTick, onMounted, ref, unref, watch, type Component } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import CommandPalette from "@/components/shared/CommandPalette.vue";
import type { CommandAction } from "@/components/shared/CommandPalette.vue";
import InfoTip from "@/components/shared/InfoTip.vue";
import SearchSelect from "@/components/shared/SearchSelect.vue";
import SectionNav from "@/components/shared/SectionNav.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { TAX_SECTIONS } from "@/config/taxSections";
import { taxLabel } from "@/config/taxTerminology";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import { useKeyboardShortcuts } from "@/composables/useKeyboardShortcuts";
import { useTaxLookups } from "@/composables/useTaxLookups";
import { useTaxWorkspace } from "@/composables/useTaxWorkspace";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxAdjustmentLine,
  TaxIncomeLine,
  TaxLookups,
  TaxSectionKey,
  TaxSectionStatusInfo,
} from "@/types/taxation";
import { formatCurrency, formatDate } from "@/utils/format";
import ResultRail from "./tax-workspace/ResultRail.vue";
import OverviewSection from "./tax-workspace/sections/OverviewSection.vue";
import IncomeSection from "./tax-workspace/sections/IncomeSection.vue";
import AdjustmentsSection from "./tax-workspace/sections/AdjustmentsSection.vue";
import DepreciationSection from "./tax-workspace/sections/DepreciationSection.vue";
import LossesSection from "./tax-workspace/sections/LossesSection.vue";
import MatSection from "./tax-workspace/sections/MatSection.vue";
import CreditsSection from "./tax-workspace/sections/CreditsSection.vue";
import ChallansSection from "./tax-workspace/sections/ChallansSection.vue";
import ReconciliationSection from "./tax-workspace/sections/ReconciliationSection.vue";
import InterestSection from "./tax-workspace/sections/InterestSection.vue";
import SummarySection from "./tax-workspace/sections/SummarySection.vue";
import ReviewSection from "./tax-workspace/sections/ReviewSection.vue";
import FilingSection from "./tax-workspace/sections/FilingSection.vue";
import AuditSection from "./tax-workspace/sections/AuditSection.vue";

const route = useRoute();
const router = useRouter();

const {
  bootstrap,
  workspace,
  computation,
  context,
  sections,
  preview,
  statement,
  incomeLines,
  adjustmentLines,
  bookProfit,
  dirty,
  saving,
  savedAt,
  loading,
  previewing,
  running,
  error,
  loadBootstrap,
  loadWorkspace,
  reload,
  markDirty,
  saveNow,
  refreshPreview,
  loadStatement,
  recompute,
  submit,
  applyTemplate,
  copyPreviousYear,
  populateFromBooks,
} = useTaxWorkspace();

const { lookups, set: adoptLookups, load: loadLookups } = useTaxLookups();
const visibility = useFieldVisibility(context);

const SECTION_COMPONENTS: Record<TaxSectionKey, Component> = {
  overview: OverviewSection,
  income: IncomeSection,
  adjustments: AdjustmentsSection,
  depreciation: DepreciationSection,
  losses: LossesSection,
  mat: MatSection,
  credits: CreditsSection,
  challans: ChallansSection,
  reconciliation: ReconciliationSection,
  interest: InterestSection,
  summary: SummarySection,
  review: ReviewSection,
  filing: FilingSection,
  audit: AuditSection,
};

const active = ref<TaxSectionKey>("overview");
const panelRef = ref<HTMLElement | null>(null);
const paletteOpen = ref(false);
const helpOpen = ref(false);
const moreOpen = ref(false);
const livePreviewOpen = ref(false);
const cancelling = ref(false);
const viewError = ref("");
const viewErrorField = ref<string | null>(null);

// Empty-state (no computation yet) form model
const newAyCode = ref("");
const selectedTemplate = ref("");
const copyPreviousYearOnCreate = ref(true);
const populateFromBooksOnCreate = ref(false);

const busy = computed(() => loading.value || saving.value || running.value);
const isDraft = computed(() => context.value?.is_draft ?? false);
/** Submitting freezes the worksheet and posts to the ledger, so blocking issues bar it. */
const blockingCount = computed(() => workspace.value?.validation?.blocking_count ?? 0);
const submitBlockedReason = computed(() => {
  if (!isDraft.value) return "";
  if (blockingCount.value === 0) return "";
  return `${blockingCount.value} issue(s) must be resolved in Review & Validate before this computation can be submitted.`;
});

/** Single gate for the header button, the command palette and the keyboard shortcut. */
async function attemptSubmit(): Promise<void> {
  if (submitBlockedReason.value) {
    viewError.value = submitBlockedReason.value;
    viewErrorField.value = null;
    active.value = "review";
    return;
  }
  await submit();
}
const panelDisabled = computed(() => !isDraft.value || loading.value || running.value);
const activeComponent = computed<Component>(() => SECTION_COMPONENTS[active.value]);

const livePreviewHeadline = computed(() => {
  const refund = Number(preview.value?.refund_due ?? 0);
  if (refund > 0) {
    return {
      label: "Refund Due",
      amount: formatCurrency(refund, context.value?.currency ?? "INR"),
    };
  }
  return {
    label: "Net Tax Payable",
    amount: formatCurrency(preview.value?.net_payable ?? 0, context.value?.currency ?? "INR"),
  };
});

const banner = computed<{ detail: string; field: string | null } | null>(() => {
  if (viewError.value) return { detail: viewError.value, field: viewErrorField.value };
  if (error.value) return { detail: error.value, field: null };
  return null;
});

function showError(e: unknown): void {
  const envelope = e as ErrorEnvelope | null;
  viewError.value = envelope?.detail ?? (e instanceof Error ? e.message : String(e));
  viewErrorField.value = envelope?.field ?? null;
}

function dismissError(): void {
  viewError.value = "";
  viewErrorField.value = null;
  error.value = "";
}

/* ---------------------------------------------------------------- sections */

function isSectionKey(value: unknown): value is TaxSectionKey {
  return typeof value === "string" && TAX_SECTIONS.some((def) => def.key === value);
}

const visibleSectionKeys = computed<Set<string>>(() => {
  const keys = new Set<string>();
  for (const entry of unref(visibility.sections)) {
    keys.add(typeof entry === "string" ? entry : entry.key);
  }
  return keys;
});

function fallbackStatus(key: TaxSectionKey, label: string): TaxSectionStatusInfo {
  return {
    key,
    label,
    status: "not-started",
    summary: null,
    row_count: 0,
    blocking_count: 0,
    advisory_count: 0,
  };
}

const navSections = computed<TaxSectionStatusInfo[]>(() => {
  const fromServer = new Map(sections.value.map((entry) => [entry.key, entry]));
  const allowed = visibleSectionKeys.value;
  const rows: TaxSectionStatusInfo[] = [];
  for (const def of TAX_SECTIONS) {
    if (allowed.size > 0 && !allowed.has(def.key)) continue;
    rows.push(fromServer.get(def.key) ?? fallbackStatus(def.key, def.label));
  }
  return rows;
});

const navGroups = computed<{ title: string; keys: string[] }[]>(() => {
  const order: string[] = [];
  const grouped = new Map<string, string[]>();
  const visible = new Set(navSections.value.map((entry) => entry.key));
  for (const def of TAX_SECTIONS) {
    if (!visible.has(def.key)) continue;
    let bucket = grouped.get(def.group);
    if (!bucket) {
      bucket = [];
      grouped.set(def.group, bucket);
      order.push(def.group);
    }
    bucket.push(def.key);
  }
  return order.map((group) => ({
    title: taxLabel(`group.${group}`, group),
    keys: grouped.get(group) ?? [],
  }));
});

function highlightField(field: string): boolean {
  const root = panelRef.value;
  if (!root) return false;
  const target = root.querySelector<HTMLElement>(`[data-field="${CSS.escape(field)}"]`);
  if (!target) return false;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  const focusable = target.matches("input, select, textarea, button")
    ? target
    : target.querySelector<HTMLElement>("input, select, textarea, button");
  focusable?.focus();
  const ring = ["ring-2", "ring-primary", "ring-offset-2", "rounded"];
  target.classList.add(...ring);
  window.setTimeout(() => target.classList.remove(...ring), 2000);
  return true;
}

async function goToSection(section: TaxSectionKey, field?: string): Promise<void> {
  if (active.value !== section) {
    active.value = section;
    await router.replace({ query: { ...route.query, section } });
  }
  if (!field) return;
  await nextTick();
  // The panel may still be fetching its own rows — one short retry is enough.
  if (!highlightField(field)) {
    window.setTimeout(() => highlightField(field), 250);
  }
}

function applySectionFromRoute(): void {
  const wanted = route.query.section;
  active.value = isSectionKey(wanted) ? wanted : "overview";
}

function onSelectSection(key: string): void {
  if (isSectionKey(key)) void goToSection(key);
}

/* ----------------------------------------------------------- panel wiring */

function onDirty(): void {
  markDirty();
}

async function onChanged(): Promise<void> {
  await reload();
  await refreshPreview();
}

function onNavigate(section: TaxSectionKey, field?: string): void {
  void goToSection(section, field);
}

function onIncomeLines(lines: TaxIncomeLine[]): void {
  incomeLines.value = lines;
  markDirty();
}

function onAdjustmentLines(lines: TaxAdjustmentLine[]): void {
  adjustmentLines.value = lines;
  markDirty();
}

function onBookProfit(value: string): void {
  bookProfit.value = value;
  markDirty();
}

function onLoadStatement(): void {
  void loadStatement();
}

/* --------------------------------------------------------------- actions */

async function cancelComputation(): Promise<void> {
  const comp = computation.value;
  if (!comp) return;
  moreOpen.value = false;
  const confirmed = window.confirm(
    "Cancel this computation? It stays in the history for audit but can no longer be edited.",
  );
  if (!confirmed) return;
  cancelling.value = true;
  try {
    await api.post(`/tax/computations/${comp.id}/cancel`, {});
    await reload();
    await refreshPreview();
  } catch (e: unknown) {
    showError(e);
  } finally {
    cancelling.value = false;
  }
}

async function createFromTemplate(): Promise<void> {
  if (!selectedTemplate.value || !newAyCode.value) return;
  try {
    const created = await applyTemplate({
      template_code: selectedTemplate.value,
      ay_code: newAyCode.value,
      copy_from_previous_year: copyPreviousYearOnCreate.value,
      populate_from_books: populateFromBooksOnCreate.value,
      filing_type: "Original",
    });
    await router.replace({
      path: `/tax/workspace/${created.id}`,
      query: { section: "overview" },
    });
  } catch (e: unknown) {
    showError(e);
  }
}

async function runCopyPreviousYear(): Promise<void> {
  try {
    await copyPreviousYear();
  } catch (e: unknown) {
    showError(e);
  }
}

async function runPopulateFromBooks(): Promise<void> {
  try {
    await populateFromBooks();
  } catch (e: unknown) {
    showError(e);
  }
}

/* ------------------------------------------------------- keyboard + palette */

const { shortcuts } = useKeyboardShortcuts(() => [
  {
    combo: "mod+s",
    description: "Save the working draft",
    allowInInput: true,
    handler: () => {
      void saveNow();
    },
  },
  {
    combo: "mod+enter",
    description: "Save a computation run",
    allowInInput: true,
    handler: () => {
      void recompute();
    },
  },
  {
    combo: "mod+k",
    description: "Open the command palette",
    allowInInput: true,
    handler: () => {
      paletteOpen.value = true;
    },
  },
  {
    combo: "mod+shift+r",
    description: "Refresh the live preview",
    allowInInput: true,
    handler: () => {
      void refreshPreview();
    },
  },
  {
    combo: "mod+shift+p",
    description: "Open or close the live preview",
    allowInInput: true,
    handler: () => {
      livePreviewOpen.value = !livePreviewOpen.value;
    },
  },
  {
    combo: "?",
    description: "Show keyboard shortcuts",
    handler: () => {
      helpOpen.value = !helpOpen.value;
    },
  },
]);

const shortcutList = computed(() => unref(shortcuts));

const isApplePlatform =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.userAgent);

function prettyCombo(combo: string): string {
  return combo
    .split("+")
    .map((part) => {
      if (part === "mod") return isApplePlatform ? "⌘" : "Ctrl";
      if (part === "alt") return isApplePlatform ? "⌥" : "Alt";
      if (part === "shift") return "Shift";
      if (part === "enter") return "Enter";
      if (part.length === 1) return part.toUpperCase();
      return part;
    })
    .join(" + ");
}

function paletteRun(action: () => void): () => void {
  return () => {
    paletteOpen.value = false;
    action();
  };
}

const paletteActions = computed<CommandAction[]>(() => {
  const actions: CommandAction[] = navSections.value.map((entry) => ({
    id: `go-${entry.key}`,
    label: `Go to ${entry.label}`,
    group: "Go to section",
    keywords: entry.key,
    run: paletteRun(() => {
      if (isSectionKey(entry.key)) void goToSection(entry.key);
    }),
  }));

  actions.push(
    {
      id: "save",
      label: "Save the working draft",
      group: "Computation",
      shortcut: "mod+s",
      run: paletteRun(() => {
        void saveNow();
      }),
    },
    {
      id: "recompute",
      label: "Save a computation run",
      hint: "Records an append-only run in the computation history",
      group: "Computation",
      shortcut: "mod+enter",
      run: paletteRun(() => {
        void recompute();
      }),
    },
    {
      id: "live-preview",
      label: livePreviewOpen.value ? "Close the live preview" : "Open the live preview",
      group: "Computation",
      shortcut: "mod+shift+p",
      run: paletteRun(() => {
        livePreviewOpen.value = !livePreviewOpen.value;
      }),
    },
    {
      id: "submit",
      label: "Submit the computation",
      group: "Computation",
      run: paletteRun(() => {
        void attemptSubmit();
      }),
    },
    {
      id: "copy-previous-year",
      label: "Copy from the previous assessment year",
      group: "Computation",
      run: paletteRun(() => {
        void runCopyPreviousYear();
      }),
    },
    {
      id: "populate-from-books",
      label: "Populate from the books of account",
      group: "Computation",
      run: paletteRun(() => {
        void runPopulateFromBooks();
      }),
    },
    {
      id: "generate-return",
      label: "Generate the ITR-6 return",
      group: "Filing",
      run: paletteRun(() => {
        void goToSection("filing");
      }),
    },
    {
      id: "reconcile-26as",
      label: "Reconcile with Form 26AS",
      group: "Filing",
      run: paletteRun(() => {
        void goToSection("reconciliation");
      }),
    },
    {
      id: "open-catalogue",
      label: "Open the Statutory Catalogue",
      group: "Elsewhere",
      run: paletteRun(() => {
        void router.push("/tax/catalogue");
      }),
    },
    {
      id: "open-registration",
      label: "Open Company Tax Registration",
      group: "Elsewhere",
      run: paletteRun(() => {
        void router.push("/income-tax-settings");
      }),
    },
    {
      id: "back-to-landing",
      label: "Back to the Income Tax landing",
      group: "Elsewhere",
      run: paletteRun(() => {
        void router.push("/tax");
      }),
    },
  );
  return actions;
});

/* ----------------------------------------------------------------- header */

const assessmentYearHeading = computed(() => {
  const label = context.value?.ay_label ?? computation.value?.ay_code ?? "";
  if (!label) return "";
  return label.toLowerCase().includes("assessment") ? label : `Assessment Year ${label}`;
});

const docStatusLabel = computed(() => {
  const ctx = context.value;
  if (!ctx) return "Draft";
  if (ctx.is_cancelled) return "Cancelled";
  if (ctx.is_submitted) return "Submitted";
  return "Draft";
});

const docStatusKey = computed(() => docStatusLabel.value.toLowerCase());

const templates = computed(() => bootstrap.value?.templates ?? []);

/* ------------------------------------------------------------- lifecycle */

function hasOptions(value: TaxLookups): boolean {
  return Object.values(value).some((entry) => Array.isArray(entry) && entry.length > 0);
}

watch(
  () => workspace.value?.lookups,
  (value) => {
    if (value && hasOptions(value)) adoptLookups(value);
  },
);

watch(
  () => route.query.section,
  () => {
    const wanted = route.query.section;
    const next: TaxSectionKey = isSectionKey(wanted) ? wanted : "overview";
    if (next !== active.value) active.value = next;
  },
);

watch(
  () => route.params.id,
  (id) => {
    if (typeof id === "string" && id && id !== computation.value?.id) void loadWorkspace(id);
  },
);

// A deep link can point at a section this entity/regime does not use (Minimum
// Alternate Tax on a concessional regime, say) — fall back rather than render it.
watch(navSections, (rows) => {
  if (!workspace.value || !rows.length) return;
  if (rows.some((entry) => entry.key === active.value)) return;
  const first = rows[0];
  if (isSectionKey(first.key)) void goToSection(first.key);
});

onMounted(async () => {
  applySectionFromRoute();
  const id = typeof route.params.id === "string" ? route.params.id : "";
  await loadBootstrap();
  const boot = bootstrap.value;
  if (boot) {
    if (hasOptions(boot.lookups)) {
      adoptLookups(boot.lookups);
    } else {
      await loadLookups(boot.default_ay_code).catch(() => undefined);
    }
    if (!newAyCode.value) newAyCode.value = boot.default_ay_code;
    const recommended = boot.templates.find((entry) => entry.recommended && entry.available);
    selectedTemplate.value = recommended?.code ?? "";
  }
  if (id) {
    await loadWorkspace(id);
    return;
  }
  // Reached through /tax/workspace or one of the redirects from the old record screens.
  // Open the year the user is most likely to want rather than the "start a year" form,
  // and put its id in the URL so the section deep link stays shareable.
  const opening = boot?.years.find((year) => year.is_current && year.computation_id)
    ?? boot?.years.find((year) => year.computation_id);
  if (opening?.computation_id) {
    await router.replace({
      name: "tax-workspace",
      params: { id: opening.computation_id },
      query: route.query,
    });
    await loadWorkspace(opening.computation_id);
  }
});
</script>

<template>
  <div class="mx-auto max-w-[110rem] space-y-4" data-testid="tax-workspace">
    <!-- Header strip -->
    <header
      v-if="computation && context"
      class="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm"
    >
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="text-lg font-semibold text-slate-900 truncate">{{ computation.name }}</h1>
          <StatusPill :status="docStatusKey" :label="docStatusLabel" size="sm" />
        </div>
        <p class="mt-1 text-sm text-slate-600">
          {{ assessmentYearHeading }} · {{ context.entity_class_label }}
        </p>
        <p class="mt-0.5 flex flex-wrap items-center gap-1 text-sm text-slate-600">
          <span>{{ context.regime_label }}</span>
          <InfoTip
            v-if="context.regime_statutory_ref"
            size="sm"
            title="Tax regime"
            :text="context.regime_label"
            :statutory-ref="context.regime_statutory_ref"
          />
          <span class="text-slate-400">·</span>
          <span>Return due {{ formatDate(context.return_due_date) }}</span>
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="btn-secondary !items-start !py-1.5 text-left"
          data-testid="tax-ws-live-preview"
          :aria-expanded="livePreviewOpen"
          @click="livePreviewOpen = !livePreviewOpen"
        >
          <span class="block text-[11px] font-normal uppercase tracking-wide text-slate-500">
            {{ livePreviewHeadline.label }}
          </span>
          <span class="block text-sm font-semibold tabular-nums text-slate-900">
            {{ livePreviewHeadline.amount }}
          </span>
          <span class="mt-0.5 block text-[11px] font-medium text-primary">
            {{ livePreviewOpen ? "Hide live preview" : "Live preview" }}
          </span>
        </button>
        <button
          type="button"
          class="btn-secondary"
          data-testid="tax-ws-command-palette-trigger"
          @click="paletteOpen = true"
        >
          Search actions
          <span class="ml-2 text-xs text-slate-400">{{ prettyCombo("mod+k") }}</span>
        </button>
        <button
          type="button"
          class="btn-primary"
          :disabled="!isDraft || busy"
          data-testid="tax-ws-recompute"
          @click="recompute()"
        >
          Save computation run
        </button>
        <button
          type="button"
          class="btn-secondary"
          :disabled="!isDraft || busy || blockingCount > 0"
          :title="submitBlockedReason"
          data-testid="tax-ws-submit"
          @click="attemptSubmit()"
        >
          Submit
        </button>
        <div class="relative">
          <button
            type="button"
            class="btn-secondary !px-3"
            aria-label="More actions"
            @click="moreOpen = !moreOpen"
          >
            …
          </button>
          <div v-if="moreOpen" class="fixed inset-0 z-10" @click="moreOpen = false" />
          <div
            v-if="moreOpen"
            class="absolute right-0 z-20 mt-1 w-56 rounded-md border border-slate-200 bg-white py-1 shadow-lg"
          >
            <button
              type="button"
              class="block w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              :disabled="!isDraft || cancelling"
              @click="cancelComputation"
            >
              Cancel this computation
            </button>
            <button
              type="button"
              class="block w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"
              @click="
                moreOpen = false;
                helpOpen = true;
              "
            >
              Keyboard shortcuts
            </button>
            <RouterLink
              class="block px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
              to="/tax"
              @click="moreOpen = false"
            >
              Back to Income Tax
            </RouterLink>
          </div>
        </div>
      </div>
    </header>

    <!-- Error banner -->
    <div
      v-if="banner"
      class="flex items-start justify-between gap-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
      data-testid="tax-ws-error"
    >
      <p>
        {{ banner.detail }}
        <span v-if="banner.field" class="text-red-600">— field: {{ banner.field }}</span>
      </p>
      <button type="button" class="text-red-500 hover:text-red-700" @click="dismissError">
        Dismiss
      </button>
    </div>

    <!-- Workspace body: section nav + panel. Live preview opens from the header button. -->
    <div
      v-if="workspace"
      class="grid min-w-0 gap-5 lg:grid-cols-[13rem_minmax(0,1fr)]"
    >
      <div
        class="min-w-0 overflow-x-auto lg:overflow-visible lg:sticky lg:top-4 lg:h-fit"
        data-testid="tax-section-nav"
      >
        <SectionNav
          :sections="navSections"
          :active="active"
          :groups="navGroups"
          @select="onSelectSection"
        />
      </div>

      <section ref="panelRef" class="min-w-0" data-testid="tax-section-panel">
        <component
          :is="activeComponent"
          :key="active"
          :workspace="workspace"
          :lookups="lookups"
          :disabled="panelDisabled"
          :preview="preview"
          :statement="statement"
          :income-lines="incomeLines"
          :adjustment-lines="adjustmentLines"
          :book-profit="bookProfit"
          @dirty="onDirty"
          @changed="onChanged"
          @navigate="onNavigate"
          @update:incomeLines="onIncomeLines"
          @update:adjustmentLines="onAdjustmentLines"
          @update:bookProfit="onBookProfit"
          @load-statement="onLoadStatement"
        />
      </section>
    </div>

    <ResultRail
      v-if="workspace"
      v-model:open="livePreviewOpen"
      :preview="preview"
      :context="context"
      :busy="previewing || loading"
      :dirty="dirty"
      :saving="saving"
      :saved-at="savedAt"
      @recompute="recompute()"
      @navigate="onNavigate"
    />

    <!-- Empty state: no computation open yet -->
    <div v-else class="space-y-5">
      <div class="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <h1 class="text-xl font-semibold text-slate-900">Income Tax workspace</h1>
        <p class="mt-1 max-w-3xl text-sm text-slate-600">
          One screen for the whole assessment year: income, tax adjustments, depreciation under the
          Income-tax Act, brought forward losses, Minimum Alternate Tax, taxes already paid,
          reconciliation with Form 26AS, interest, and the return itself — with the result updating
          live as you work.
        </p>
        <RouterLink class="mt-3 inline-block text-sm text-primary hover:underline" to="/tax">
          Back to the Income Tax landing
        </RouterLink>
      </div>

      <div class="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <p class="form-label">Choose an assessment year</p>
        <div class="max-w-sm">
          <SearchSelect
            v-model="newAyCode"
            :options="lookups.assessment_years"
            placeholder="Search assessment years"
            testid="tax-ws-ay"
          />
        </div>

        <h2 class="mt-5 text-sm font-medium text-slate-800">Start from a template</h2>
        <div class="mt-2 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <button
            v-for="template in templates"
            :key="template.code"
            type="button"
            class="rounded-lg border px-4 py-3 text-left transition"
            :class="[
              template.available
                ? 'border-slate-200 hover:border-primary hover:shadow-sm'
                : 'border-slate-200 bg-slate-50 opacity-60 cursor-not-allowed',
              selectedTemplate === template.code ? 'border-primary ring-1 ring-primary' : '',
            ]"
            :disabled="!template.available"
            @click="selectedTemplate = template.code"
          >
            <div class="flex items-start justify-between gap-2">
              <span class="font-medium text-slate-900">{{ template.title }}</span>
              <span
                v-if="template.recommended && template.available"
                class="rounded bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700"
              >
                Recommended
              </span>
            </div>
            <p class="mt-1 text-sm text-slate-600">{{ template.description }}</p>
            <p v-if="template.regime_statutory_ref" class="mt-1 text-xs text-slate-500">
              {{ template.regime_statutory_ref }}
            </p>
            <ul v-if="template.notes.length" class="mt-2 space-y-0.5 text-xs text-slate-500">
              <li v-for="(note, index) in template.notes" :key="index">• {{ note }}</li>
            </ul>
            <p v-if="!template.available" class="mt-2 text-xs text-amber-700">
              {{ template.unavailable_reason ?? "Not available for this assessment year." }}
            </p>
          </button>
          <p v-if="!templates.length" class="text-sm text-slate-500">
            {{ loading ? "Loading templates…" : "No templates are available yet." }}
          </p>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 text-sm text-slate-700">
            <input v-model="copyPreviousYearOnCreate" type="checkbox" class="rounded" />
            Copy from the previous assessment year
          </label>
          <label class="flex items-center gap-2 text-sm text-slate-700">
            <input v-model="populateFromBooksOnCreate" type="checkbox" class="rounded" />
            Populate from the books of account
          </label>
        </div>

        <button
          type="button"
          class="btn-primary mt-4"
          :disabled="!selectedTemplate || !newAyCode || busy"
          data-testid="tax-ws-create"
          @click="createFromTemplate"
        >
          Start this computation
        </button>
      </div>
    </div>

    <!-- Keyboard shortcut help -->
    <div
      v-if="helpOpen"
      class="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/20 p-4"
      @click.self="helpOpen = false"
    >
      <div class="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-4 shadow-xl">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-slate-800">Keyboard shortcuts</h2>
          <button type="button" class="text-slate-400 hover:text-slate-600" @click="helpOpen = false">
            Close
          </button>
        </div>
        <dl class="mt-3 space-y-2 text-sm">
          <div v-for="entry in shortcutList" :key="entry.combo" class="flex justify-between gap-3">
            <dt class="text-slate-600">{{ entry.description }}</dt>
            <dd class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700">
              {{ prettyCombo(entry.combo) }}
            </dd>
          </div>
        </dl>
      </div>
    </div>

    <CommandPalette
      :open="paletteOpen"
      :actions="paletteActions"
      placeholder="Search sections and actions"
      @close="paletteOpen = false"
    />
  </div>
</template>
