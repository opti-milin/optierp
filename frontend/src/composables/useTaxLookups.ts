/**
 * Every dropdown in the Income Tax workspace, fetched once per assessment year.
 *
 * The workspace and bootstrap aggregates already embed a `lookups` payload, so the
 * usual path is `set()` (adopt what the aggregate returned) rather than `load()`.
 */
import { ref, type Ref } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { TaxLookups, TaxOption } from "@/types/taxation";

/** Every list starts as an empty array so panels can read `lookups.x` before the fetch lands. */
export function emptyLookups(): TaxLookups {
  return {
    ay_code: null,
    assessment_years: [],
    entity_classes: [],
    tax_regimes: [],
    filing_types: [],
    income_heads: [],
    income_characters: [],
    adjustment_provisions: [],
    adjustment_stages: [],
    adjustment_directions: [],
    depreciation_blocks: [],
    loss_kinds: [],
    setoff_groups: [],
    challan_types: [],
    major_heads: [],
    minor_heads: [],
    bank_branch_codes: [],
    credit_kinds: [],
    deduction_sections: [],
    deductors: [],
    bank_accounts: [],
    tax_payable_accounts: [],
    tax_expense_accounts: [],
    itr_forms: [],
    verification_modes: [],
  };
}

function envelopeMessage(e: unknown): string {
  const detail = (e as ErrorEnvelope | null)?.detail;
  if (detail) return detail;
  return e instanceof Error ? e.message : String(e);
}

export interface UseTaxLookups {
  lookups: Ref<TaxLookups>;
  loading: Ref<boolean>;
  error: Ref<string>;
  load: (ayCode?: string) => Promise<TaxLookups>;
  set: (value: TaxLookups) => void;
  option: (list: keyof TaxLookups, value: string | null | undefined) => TaxOption | null;
  label: (list: keyof TaxLookups, value: string | null | undefined, fallback?: string) => string;
}

export function useTaxLookups(): UseTaxLookups {
  const lookups = ref<TaxLookups>(emptyLookups()) as Ref<TaxLookups>;
  const loading = ref(false);
  const error = ref("");

  // Per-assessment-year cache and in-flight de-duplication, scoped to this caller.
  const cache = new Map<string, TaxLookups>();
  const inFlight = new Map<string, Promise<TaxLookups>>();

  function set(value: TaxLookups): void {
    lookups.value = value;
    cache.set(value.ay_code ?? "", value);
  }

  async function load(ayCode?: string): Promise<TaxLookups> {
    const key = ayCode ?? "";
    const cached = cache.get(key);
    if (cached) {
      lookups.value = cached;
      return cached;
    }
    const pending = inFlight.get(key);
    if (pending) return pending;

    loading.value = true;
    error.value = "";
    const request = api
      .get<TaxLookups>("/tax/workspace/lookups", { params: ayCode ? { ay_code: ayCode } : {} })
      .then((resp) => {
        cache.set(key, resp.data);
        lookups.value = resp.data;
        return resp.data;
      })
      .catch((e: unknown) => {
        error.value = envelopeMessage(e);
        throw e;
      })
      .finally(() => {
        inFlight.delete(key);
        loading.value = false;
      });
    inFlight.set(key, request);
    return request;
  }

  function option(list: keyof TaxLookups, value: string | null | undefined): TaxOption | null {
    if (!value) return null;
    const entries = lookups.value[list];
    if (!Array.isArray(entries)) return null;
    return entries.find((candidate) => candidate.value === value) ?? null;
  }

  function label(
    list: keyof TaxLookups,
    value: string | null | undefined,
    fallback?: string,
  ): string {
    return option(list, value)?.label ?? fallback ?? value ?? "";
  }

  return { lookups, loading, error, load, set, option, label };
}
