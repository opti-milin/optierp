// Context-aware form engine for the Income Tax workspace.
//
// Panels ask `visible('mat.bookProfit')` instead of repeating conditions over
// entity class, regime and document state. Rules live in config/taxSections.ts.

import { computed, type ComputedRef, type Ref } from "vue";
import { FIELD_RULES, TAX_SECTIONS, type TaxSectionDef } from "@/config/taxSections";
import type { TaxWorkspaceContext } from "@/types/taxation";

export function useFieldVisibility(ctx: Ref<TaxWorkspaceContext | null>): {
  visible: (key: string) => boolean;
  sections: ComputedRef<TaxSectionDef[]>;
  forfeited: ComputedRef<string[]>;
} {
  // Fail open: an unmapped key, or a context that has not loaded yet, renders.
  function visible(key: string): boolean {
    const context = ctx.value;
    if (!context) return true;
    const rule = FIELD_RULES[key];
    if (!rule) return true;
    return rule(context);
  }

  const sections = computed<TaxSectionDef[]>(() => {
    const context = ctx.value;
    if (!context) return [...TAX_SECTIONS];
    return TAX_SECTIONS.filter((def) => !def.when || def.when(context));
  });

  const forfeited = computed<string[]>(() => ctx.value?.forfeited_incentives ?? []);

  return { visible, sections, forfeited };
}
