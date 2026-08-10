/**
 * Data layer for the unified Income Tax workspace.
 *
 * One aggregate GET feeds the whole screen; editing keeps a local draft copy of the
 * income lines, adjustment lines and book profit, which drives a debounced
 * non-persisting preview (~450 ms) and a debounced autosave (~1200 ms). The preview
 * endpoint never writes, so the append-only run chain stays trustworthy — a persisted
 * run only happens when the user explicitly asks for one via `recompute()`.
 */
import { computed, onScopeDispose, ref, type ComputedRef, type Ref } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxAdjustmentLine,
  TaxComputation,
  TaxCopyPreviousYearIn,
  TaxCopyPreviousYearResult,
  TaxIncomeLine,
  TaxPopulateFromBooksIn,
  TaxPopulateFromBooksResult,
  TaxPreview,
  TaxPreviewRequest,
  TaxSectionStatusInfo,
  TaxStatement,
  TaxTemplateApplyIn,
  TaxValidation,
  TaxWorkspace,
  TaxWorkspaceBootstrap,
  TaxWorkspaceContext,
} from "@/types/taxation";

const AUTOSAVE_DELAY_MS = 1200;
const PREVIEW_DELAY_MS = 450;

const EMPTY_VALIDATION: TaxValidation = {
  ok: true,
  blocking_count: 0,
  advisory_count: 0,
  issues: [],
};

interface ComputationUpdatePayload {
  income_lines?: TaxIncomeLine[];
  adjustment_lines?: TaxAdjustmentLine[];
  book_profit_115jb?: string | null;
  remarks?: string | null;
  filing_type?: string;
  return_filed_date?: string | null;
  audit_applicable?: boolean;
  itr_due_date_override?: string | null;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export interface UseTaxWorkspace {
  bootstrap: Ref<TaxWorkspaceBootstrap | null>;
  workspace: Ref<TaxWorkspace | null>;
  computation: ComputedRef<TaxComputation | null>;
  context: ComputedRef<TaxWorkspaceContext | null>;
  validation: ComputedRef<TaxValidation>;
  sections: ComputedRef<TaxSectionStatusInfo[]>;
  preview: Ref<TaxPreview | null>;
  statement: Ref<TaxStatement | null>;
  incomeLines: Ref<TaxIncomeLine[]>;
  adjustmentLines: Ref<TaxAdjustmentLine[]>;
  bookProfit: Ref<string>;
  dirty: Ref<boolean>;
  saving: Ref<boolean>;
  savedAt: Ref<Date | null>;
  loading: Ref<boolean>;
  previewing: Ref<boolean>;
  running: Ref<boolean>;
  error: Ref<string>;
  loadBootstrap: (ayCode?: string) => Promise<void>;
  loadWorkspace: (id: string) => Promise<void>;
  reload: () => Promise<void>;
  markDirty: () => void;
  saveNow: () => Promise<void>;
  refreshPreview: () => Promise<void>;
  loadStatement: () => Promise<void>;
  recompute: () => Promise<void>;
  submit: () => Promise<void>;
  applyTemplate: (payload: TaxTemplateApplyIn) => Promise<TaxComputation>;
  copyPreviousYear: (payload?: TaxCopyPreviousYearIn) => Promise<TaxCopyPreviousYearResult>;
  populateFromBooks: (payload?: TaxPopulateFromBooksIn) => Promise<TaxPopulateFromBooksResult>;
}

export function useTaxWorkspace(): UseTaxWorkspace {
  const bootstrap = ref<TaxWorkspaceBootstrap | null>(null) as Ref<TaxWorkspaceBootstrap | null>;
  const workspace = ref<TaxWorkspace | null>(null) as Ref<TaxWorkspace | null>;
  const preview = ref<TaxPreview | null>(null) as Ref<TaxPreview | null>;
  const statement = ref<TaxStatement | null>(null) as Ref<TaxStatement | null>;

  const incomeLines = ref<TaxIncomeLine[]>([]) as Ref<TaxIncomeLine[]>;
  const adjustmentLines = ref<TaxAdjustmentLine[]>([]) as Ref<TaxAdjustmentLine[]>;
  const bookProfit = ref("");

  const dirty = ref(false);
  const saving = ref(false);
  const savedAt = ref<Date | null>(null);
  const loading = ref(false);
  const previewing = ref(false);
  const running = ref(false);
  const error = ref("");

  const computation = computed<TaxComputation | null>(() => workspace.value?.computation ?? null);
  const context = computed<TaxWorkspaceContext | null>(() => workspace.value?.context ?? null);
  const validation = computed<TaxValidation>(() => workspace.value?.validation ?? EMPTY_VALIDATION);
  const sections = computed<TaxSectionStatusInfo[]>(() => workspace.value?.sections ?? []);

  let saveTimer: ReturnType<typeof setTimeout> | null = null;
  let previewTimer: ReturnType<typeof setTimeout> | null = null;
  // Bumped by every draft edit; a save only clears `dirty` when nothing changed
  // underneath it while the request was in flight.
  let draftRevision = 0;
  // Monotonic token so a slow preview response can never overwrite a newer one.
  let previewToken = 0;

  function setError(e: unknown): void {
    const detail = (e as ErrorEnvelope | null)?.detail;
    error.value = detail ?? (e instanceof Error ? e.message : String(e));
  }

  function clearTimers(): void {
    if (saveTimer !== null) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    if (previewTimer !== null) {
      clearTimeout(previewTimer);
      previewTimer = null;
    }
  }

  function seedDraft(source: TaxComputation): void {
    incomeLines.value = clone(source.income_lines ?? []);
    // Only the manually entered rows are editable. Rows carrying a run_id were written by
    // the engine as the audit record of a saved run; replaying them here would count the
    // same adjustment once per historic run, both in the live preview and on save.
    adjustmentLines.value = clone(
      (source.adjustment_lines ?? []).filter((line) => !line.run_id),
    );
    bookProfit.value = source.book_profit_115jb ?? "";
  }

  function bookProfitPayload(): string | null {
    const raw = bookProfit.value.trim();
    return raw === "" ? null : raw;
  }

  /** GET the aggregate. Draft refs are re-seeded only when the user has no pending edits. */
  async function fetchWorkspace(id: string, reseedDraft: boolean): Promise<void> {
    const { data } = await api.get<TaxWorkspace>(`/tax/workspace/computation/${id}`);
    workspace.value = data;
    if (reseedDraft) seedDraft(data.computation);
  }

  async function loadBootstrap(ayCode?: string): Promise<void> {
    loading.value = true;
    error.value = "";
    try {
      const { data } = await api.get<TaxWorkspaceBootstrap>("/tax/workspace/bootstrap", {
        params: ayCode ? { ay_code: ayCode } : {},
      });
      bootstrap.value = data;
    } catch (e: unknown) {
      setError(e);
    } finally {
      loading.value = false;
    }
  }

  async function loadWorkspace(id: string): Promise<void> {
    clearTimers();
    loading.value = true;
    error.value = "";
    dirty.value = false;
    draftRevision += 1;
    statement.value = null;
    try {
      await fetchWorkspace(id, true);
    } catch (e: unknown) {
      setError(e);
      loading.value = false;
      return;
    }
    loading.value = false;
    await refreshPreview();
  }

  async function reload(): Promise<void> {
    const id = computation.value?.id;
    if (!id) return;
    try {
      await fetchWorkspace(id, !dirty.value);
    } catch (e: unknown) {
      setError(e);
    }
  }

  function markDirty(): void {
    dirty.value = true;
    draftRevision += 1;
    if (previewTimer !== null) clearTimeout(previewTimer);
    if (saveTimer !== null) clearTimeout(saveTimer);
    previewTimer = setTimeout(() => {
      previewTimer = null;
      void refreshPreview();
    }, PREVIEW_DELAY_MS);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      void saveNow();
    }, AUTOSAVE_DELAY_MS);
  }

  async function saveNow(): Promise<void> {
    const comp = computation.value;
    if (!comp) return;
    if (comp.docstatus !== 0) {
      dirty.value = false;
      return;
    }
    if (saveTimer !== null) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    const revisionAtSend = draftRevision;
    saving.value = true;
    error.value = "";
    try {
      const payload: ComputationUpdatePayload = {
        income_lines: incomeLines.value,
        adjustment_lines: adjustmentLines.value,
        book_profit_115jb: bookProfitPayload(),
      };
      await api.put<TaxComputation>(`/tax/computations/${comp.id}`, payload);
      savedAt.value = new Date();
      // The user may have typed while the request was in flight — keep their edits.
      if (draftRevision === revisionAtSend) dirty.value = false;
      await fetchWorkspace(comp.id, !dirty.value);
    } catch (e: unknown) {
      setError(e);
    } finally {
      saving.value = false;
    }
  }

  /** Non-persisting compute against the CURRENT draft, so the rail is live before a save lands. */
  async function refreshPreview(): Promise<void> {
    const comp = computation.value;
    if (!comp) return;
    if (previewTimer !== null) {
      clearTimeout(previewTimer);
      previewTimer = null;
    }
    const token = (previewToken += 1);
    previewing.value = true;
    try {
      // When the draft matches the saved worksheet, ask for a bare preview so the
      // fingerprint lines up with the last computation run. Sending the editor rows
      // back always takes the overlay path and can drift on optional fields.
      const body: TaxPreviewRequest = dirty.value
        ? {
            income_lines: incomeLines.value,
            adjustment_lines: adjustmentLines.value,
            book_profit_115jb: bookProfitPayload(),
            apply_book_profit: true,
          }
        : {};
      const { data } = await api.post<TaxPreview>(
        `/tax/workspace/computation/${comp.id}/preview`,
        body,
      );
      if (token !== previewToken) return; // stale response — a newer preview is in flight
      preview.value = data;
    } catch (e: unknown) {
      if (token === previewToken) setError(e);
    } finally {
      if (token === previewToken) previewing.value = false;
    }
  }

  async function loadStatement(): Promise<void> {
    const comp = computation.value;
    if (!comp) return;
    try {
      const { data } = await api.get<TaxStatement>(
        `/tax/workspace/computation/${comp.id}/statement`,
      );
      statement.value = data;
    } catch (e: unknown) {
      setError(e);
    }
  }

  /** The only call that writes a run. Everything else on this screen is a preview. */
  async function recompute(): Promise<void> {
    const comp = computation.value;
    if (!comp) return;
    if (dirty.value) await saveNow();
    running.value = true;
    error.value = "";
    try {
      await api.post(`/tax/computations/${comp.id}/runs`, { trigger: "manual" });
      await fetchWorkspace(comp.id, !dirty.value);
    } catch (e: unknown) {
      setError(e);
    } finally {
      running.value = false;
    }
    await refreshPreview();
  }

  async function submit(): Promise<void> {
    const comp = computation.value;
    if (!comp) return;
    if (dirty.value) await saveNow();
    running.value = true;
    error.value = "";
    try {
      await api.post(`/tax/computations/${comp.id}/submit`, { post_provision: true });
      await fetchWorkspace(comp.id, !dirty.value);
    } catch (e: unknown) {
      setError(e);
    } finally {
      running.value = false;
    }
    await refreshPreview();
  }

  async function applyTemplate(payload: TaxTemplateApplyIn): Promise<TaxComputation> {
    running.value = true;
    error.value = "";
    try {
      const { data } = await api.post<TaxComputation>("/tax/workspace/templates/apply", payload);
      return data;
    } catch (e: unknown) {
      setError(e);
      throw e;
    } finally {
      running.value = false;
    }
  }

  async function copyPreviousYear(
    payload: TaxCopyPreviousYearIn = {},
  ): Promise<TaxCopyPreviousYearResult> {
    const comp = computation.value;
    if (!comp) throw new Error("Open an assessment year before copying from the previous year.");
    running.value = true;
    error.value = "";
    try {
      const { data } = await api.post<TaxCopyPreviousYearResult>(
        `/tax/workspace/computation/${comp.id}/copy-previous-year`,
        payload,
      );
      await fetchWorkspace(comp.id, true);
      dirty.value = false;
      return data;
    } catch (e: unknown) {
      setError(e);
      throw e;
    } finally {
      running.value = false;
      await refreshPreview();
    }
  }

  async function populateFromBooks(
    payload: TaxPopulateFromBooksIn = {},
  ): Promise<TaxPopulateFromBooksResult> {
    const comp = computation.value;
    if (!comp) throw new Error("Open an assessment year before populating from the books.");
    running.value = true;
    error.value = "";
    try {
      const { data } = await api.post<TaxPopulateFromBooksResult>(
        `/tax/workspace/computation/${comp.id}/populate-from-books`,
        payload,
      );
      await fetchWorkspace(comp.id, true);
      dirty.value = false;
      return data;
    } catch (e: unknown) {
      setError(e);
      throw e;
    } finally {
      running.value = false;
      await refreshPreview();
    }
  }

  onScopeDispose(clearTimers);

  return {
    bootstrap,
    workspace,
    computation,
    context,
    validation,
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
  };
}
