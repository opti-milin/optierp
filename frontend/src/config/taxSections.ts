// Declarative information architecture of the Income Tax workspace.
//
// TAX_SECTIONS is the fourteen-section navigation, in the order a chartered
// accountant works through the year. FIELD_RULES is the context-aware form
// engine: a panel asks `visible('mat.bookProfit')` instead of growing an inline
// condition over entity class, regime and document state.
//
// Every rule is a pure predicate over TaxWorkspaceContext — no DOM, no store.

import type { TaxSectionKey, TaxWorkspaceContext } from "@/types/taxation";
import { taxHelp, taxLabel, taxShort } from "@/config/taxTerminology";

export interface TaxSectionDef {
  key: TaxSectionKey;
  label: string;
  short: string;
  help: string;
  group: string;
  when?: (ctx: TaxWorkspaceContext) => boolean;
}

function section(key: TaxSectionKey, group: string, when?: (ctx: TaxWorkspaceContext) => boolean): TaxSectionDef {
  const code = `section.${key}`;
  return {
    key,
    label: taxLabel(code),
    short: taxShort(code),
    help: taxHelp(code) ?? "",
    group,
    ...(when ? { when } : {}),
  };
}

export const TAX_SECTIONS: readonly TaxSectionDef[] = [
  section("overview", "Compute"),
  section("income", "Compute"),
  section("adjustments", "Compute"),
  section("depreciation", "Compute"),
  section("losses", "Compute"),
  // Minimum Alternate Tax is a company-only floor tax; it is hidden entirely
  // for entity classes and regimes where Section 115JB does not apply.
  section("mat", "Compute", (ctx) => ctx.mat_applicable),
  section("credits", "Payments"),
  section("challans", "Payments"),
  section("reconciliation", "Payments"),
  section("interest", "Payments"),
  section("summary", "Finalise"),
  section("review", "Finalise"),
  section("filing", "Finalise"),
  section("audit", "Finalise"),
];

/**
 * Per-field-group visibility. An unmapped key is visible (fail open), so a panel
 * can ask about a field before its rule is written without disappearing.
 */
export const FIELD_RULES: Record<string, (ctx: TaxWorkspaceContext) => boolean> = {
  // --- Setup & Basis ----------------------------------------------------------
  // Book profit is only an input where the Minimum Alternate Tax comparison runs.
  "overview.bookProfit": (ctx) => ctx.mat_applicable,
  // A presumptive assessee is outside the audit requirement, so the toggle is noise.
  "overview.auditApplicable": (ctx) => !ctx.presumptive,
  // Presumptive schemes are not open to companies.
  "overview.presumptive": (ctx) => ctx.entity_class_code !== "Company",
  // Only a revised, belated or updated return points back at an earlier computation.
  "overview.revisesComputation": (ctx) => ctx.filing_type !== "Original",
  // The due date can only be overridden where audit moves it in the first place.
  "overview.itrDueDateOverride": (ctx) => ctx.audit_applicable,
  // An irrevocable election cannot be changed once made; show the notice instead.
  "overview.regimeElection": (ctx) => ctx.is_draft && !ctx.regime_irrevocable,
  // One explanatory note replaces every incentive field the election forfeits.
  "overview.forfeitedNotice": (ctx) => ctx.concessional_regime || ctx.forfeited_incentives.length > 0,

  // --- Statement of Income ----------------------------------------------------
  // Presumptive income is ordinary business income throughout; the column is dead weight.
  "income.characterColumn": (ctx) => !ctx.presumptive,
  // A company has no income under the head salaries.
  "income.headSalary": (ctx) => ctx.entity_class_code !== "Company",

  // --- Tax Adjustments --------------------------------------------------------
  // Hidden when the election forfeits Chapter VI-A incentive deductions.
  "adjustments.chapterViaStage": (ctx) => !ctx.forfeited_incentives.includes("ChapterVIA"),
  // A concessional election forfeits additional depreciation.
  "adjustments.additionalDepreciation": (ctx) => !ctx.concessional_regime,
  // The disclosure standards bind assessees keeping mercantile books — companies and audited entities.
  "adjustments.icdsStage": (ctx) => ctx.entity_class_code === "Company" || ctx.audit_applicable,

  // --- Depreciation -----------------------------------------------------------
  // A concessional election forfeits additional depreciation.
  "depreciation.additionalDepreciation": (ctx) => !ctx.concessional_regime,
  // Rebuilding the register from the fixed asset module overwrites entered rows.
  "depreciation.syncFromAssets": (ctx) => ctx.is_draft,

  // --- Brought Forward Losses -------------------------------------------------
  // Unabsorbed depreciation only arises where depreciation is claimed at all.
  "losses.unabsorbedDepreciation": (ctx) => !ctx.presumptive,

  // --- Minimum Alternate Tax --------------------------------------------------
  "mat.section": (ctx) => ctx.mat_applicable,
  "mat.bookProfit": (ctx) => ctx.mat_applicable,
  // Under a concessional election the ledger stays visible read-only, to explain
  // that brought forward credit lapses rather than silently vanishing.
  "mat.creditLedger": (ctx) => ctx.mat_applicable || ctx.concessional_regime,

  // --- Taxes Already Paid -----------------------------------------------------
  // Always relevant; the grid gates the column per row on the credit kind.
  "credits.deductorTan": () => true,
  "credits.section": () => true,

  // --- Challans ---------------------------------------------------------------
  // A company always deposits under corporation tax, so the head is derived.
  "challans.majorHead": (ctx) => ctx.entity_class_code !== "Company",
  // Posting accounts are meaningless once the year is cancelled.
  "challans.glAccounts": (ctx) => !ctx.is_cancelled,

  // --- Advance Tax & Interest -------------------------------------------------
  // Interest for late filing only arises once a return exists or the return is late.
  "interest.interest234a": (ctx) => ctx.is_submitted || ctx.filing_type !== "Original",

  // --- Return Filing ----------------------------------------------------------
  // Chaining a revised, belated or updated return needs a filed original.
  "filing.chain": (ctx) => ctx.is_submitted || ctx.filing_type !== "Original",
  // Only a submitted computation may be transmitted to the filing provider.
  "filing.efileSandbox": (ctx) => ctx.is_submitted && !ctx.is_cancelled,

  // --- Review -----------------------------------------------------------------
  // The submit gate is only actionable while the computation is still a draft.
  "review.submitGate": (ctx) => ctx.is_draft,
};
