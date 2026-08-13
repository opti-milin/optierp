---
name: Income Tax CA workspace
overview: Refactor the Income Tax module from a collection of CRUD screens into a single unified, CA-friendly computation workspace ? one screen per assessment year with a live result rail, section navigation with completion status, minimal data entry driven by lookups/templates/derived values, plain-English statutory terminology, context-aware forms, and a consolidated review-and-validate gate ? served by a backend BFF aggregate, a non-persisting preview compute, lookup/template/copy-forward/populate-from-books services and a structured validation service.
todos:
  - id: plan
    content: Write docs/plans/income_tax_ca_workspace.plan.md, index it in docs/plans/README.md, and settle the API contract before any parallel implementation starts
    status: completed
  - id: be-schemas
    content: "Backend: add the workspace/preview/lookup/template/validation/statement Pydantic schemas to app/schemas/taxation.py (the shared contract every other stream compiles against)"
    status: pending
  - id: be-services
    content: "Backend: new services under app/services/taxation/ ? preview.py, lookups.py, templates.py, validation.py, statement.py, books.py, workspace.py (BFF aggregate + per-AY year summaries)"
    status: pending
  - id: be-router
    content: "Backend: thin router app/api/v1/tax/workspace.py wired into app/api/v1/router.py"
    status: pending
  - id: be-tests
    content: "Backend: unit tests under tests/unit/taxation/ for preview math (Decimal-exact), templates, copy-forward, validation, lookups, statement builder"
    status: pending
  - id: fe-primitives
    content: "Frontend: shared primitives (SearchSelect, EditableGrid, DerivedField, InfoTip, CommandPalette, SectionNav, StatusPill) + terminology map + field-visibility engine + keyboard-shortcut registry"
    status: pending
  - id: fe-shell
    content: "Frontend: workspace shell ? section navigation with per-section status, sticky live result rail with per-line explain, debounced preview, autosave with dirty/saved indication"
    status: pending
  - id: fe-sections
    content: "Frontend: the fourteen section panels (overview, income, adjustments, depreciation, losses, MAT, credits, challans, reconciliation, interest, summary, review, filing, audit)"
    status: pending
  - id: fe-landing
    content: "Frontend: Income Tax landing at /tax listing assessment years with status, due dates and next action; workspaces.ts + router consolidation with redirects; delete the eight replaced CRUD views"
    status: pending
  - id: docs
    content: "Docs: update TAXATION_ARCHITECTURE.md, INCOME_TAX_USER_GUIDE.md and PROJECT.md for the new information architecture"
    status: pending
  - id: verify
    content: "Verify: ruff check, pytest tests/unit, npm run build, docker compose up -d --build, curl /health, manual click-through"
    status: pending
isProject: false
---

# Income Tax ? CA-Friendly Unified Workspace

> Successor to [itr_enterprise_rearchitecture.plan.md](itr_enterprise_rearchitecture.plan.md), which built the
> *correct* four-tier engine (Phases 1?10, migrations `0085`?`0092`). That plan delivered statutory
> correctness and auditability. It did **not** deliver usability: Phase 9's UI was a tab shell over
> ten sibling CRUD pages. This plan fixes the product layer **without weakening the audit chain**.
>
> Architecture reference: [../TAXATION_ARCHITECTURE.md](../TAXATION_ARCHITECTURE.md).
> End-user guide: [../INCOME_TAX_USER_GUIDE.md](../INCOME_TAX_USER_GUIDE.md).

## 1. Audit of the current module

### 1.1 The workflow forces page switching

`TaxWorkspaceView.vue` (236 lines) is a tab bar over five real panels plus a generic
`ResourcePanel.vue` (104 lines) that renders a **read-only summary** for depreciation, set-off, MAT,
credits and challans and then *links out* to a separate page to actually do the work:

- `/tax/computations` (227 lines) ? a second, competing list + create + run surface
- `/tax/challans` (189), `/tax/credits` (161), `/tax/depreciation` (139), `/tax/losses` (161),
  `/tax/mat-credits` (108), `/tax/calendar` (210), `/tax/filings` (293)

So the actual CA sequence ? enter profit ? add back disallowances ? claim Income-tax Act
depreciation ? set off brought-forward losses ? compare Minimum Alternate Tax ? claim credits ?
reconcile Form 26AS ? look at interest ? check the result ? costs **eight full-page navigations**,
each losing scroll position, each needing a manual return trip, and none of them updating the result.

### 1.2 Nothing updates live

There is no preview endpoint. The only way to see a number is `POST /tax/computations/{id}/runs`,
which **persists an append-only run** and supersedes the previous one. The result of that design is
that a CA experimenting with an add-back either pollutes the audit trail with a dozen throwaway runs
or works blind. `ResultTab.vue` (46 lines) is a static dump of the last persisted run, reachable only
by switching to a tenth tab.

### 1.3 Data entry is almost entirely free-text typing

Verified against the current views:

| Screen | What the user must type by hand today |
|---|---|
| Workspace create | `AY` as free text (`<input v-model="ayCode" placeholder="AY">`), PGBP net, book profit |
| `HeadsTab` | head code, income character code, gross, deductions, **and** net (net is not derived) |
| `AdjustmentsTab` | section code as free text ? the guide literally suggests typing `37-personal`, stage, direction, amount |
| `TaxChallansView` | BSR code, challan serial, CIN, major head `0021`, minor head `100`, deposit date, amount, two account UUIDs |
| `TaxCreditsView` | deductor TAN, deductor name, section code, credited amount, claimed amount |
| `TaxDepreciationView` | opening written-down-value overrides keyed by block code, typed as a JSON-ish map |
| `TaxLossesView` | origin assessment year, set-off group, loss kind, amount, expiry assessment year |
| `TaxCalendarView` | assessment year, estimated tax |
| `TaxFilingsView` | computation id, form code, acknowledgement number, verification mode |

Every one of those has a real source of truth already in the database or the statutory pack:
`statutory.assessment_year`, `statutory.depreciation_block.rate_percent`, `statutory.provision`,
`statutory.income_character`, prior `tax_challans.bsr_code`, prior
`tax_credit_entries.deductor_tan/deductor_name`, `tax_registrations.pan/tan`, the chart of accounts,
`tax_loss_carry_forward_ledger`, `mat_credit_ledger`. None of it is offered as a choice.

### 1.4 The UI speaks in statute shorthand

`AY`, `PY`, `WDV`, `MAT`, `115JB`, `115JAA`, `TDS`, `TCS`, `26AS`, `PGBP`, `HP`, `CG`, `OS`,
`b/f`, `Dep.`, `BSR`, `u/s`, `Chapter VI-A`, `ORDINARY`, `LTCG_112A`, `Adj.`, `Set-off`, `234A/B/C`
all appear as bare labels. A CA knows them; a CA's article clerk and the business owner do not, and
the module is sold as software a firm hands to a client.

### 1.5 Forms show every field always

`TaxComputationsView` shows `book_profit_115jb` even for entity classes and regimes where Minimum
Alternate Tax does not apply. `IncomeTaxSettingsView` shows every registration field regardless of
entity class. A company that elects the concessional Section 115BAA regime forfeits additional
depreciation and most Chapter VI-A incentives ? the UI still invites the user to enter them.

### 1.6 Validation is a single red string

`error.value = e instanceof Error ? e.message : String(e)`. The axios interceptor rejects a rich
`{detail, code, field}` envelope and every tax view throws the `code`/`field` away. There is no
pre-flight check, no distinction between "this will be rejected" and "you may want to look at this",
and no way to jump to the offending input.

### 1.7 What is already good and must not regress

- The kernel is pure `Decimal`, `ROUND_HALF_UP`, with s.288A/288B applied at the statutory points
  only, character-aware surcharge with the 15% cap, and marginal relief by **re-running** the rate
  schedule. Keep every call going through `kernel/`.
- Runs are genuinely append-only with `ruleset_hash` + `input_hash` + `engine_version` +
  `finance_act_version_id`. Preview must be **strictly non-persisting** so this stays trustworthy.
- Statutory law is a global `statutory` schema that `erp_app` can only `SELECT`. All new lookups read
  it; none of them write it.

## 2. Target information architecture

```
/tax                          Income Tax landing ? one row per assessment year
                              (status ? return due date ? net payable ? next action)
   ?
   ??? /tax/workspace/:id?section=<key>
                              THE screen. Everything happens here. No page switching.
       ??? left   Section navigation, each with completion status
       ??? centre The active section panel
       ??? right  Live result rail (sticky, always visible, always current)

/tax/catalogue                Statutory Catalogue ? read-only law browser (kept: reference, not CRUD)
/income-tax-settings          Company Tax Registration & regime elections (kept, terminology swept)
/taxation-settings            Module on/off (kept)
```

### 2.1 The fourteen sections

| Key | Navigation label | What the CA does here |
|---|---|---|
| `overview` | Setup & Basis | Entity class, regime election, audit applicability, filing type, template, copy-forward, populate-from-books |
| `income` | Statement of Income | Head-wise income lines; net derived from gross ? deductions |
| `adjustments` | Tax Adjustments | Disallowances (add) and allowances (less), picked from the statutory provision catalogue |
| `depreciation` | Depreciation (Income-tax Act) | Block-wise written-down-value register; rate, allowable depreciation and closing value all derived |
| `losses` | Brought Forward Losses & Set-off | Loss ledger with expiry, and the set-off actually applied by the last computation |
| `mat` | Minimum Alternate Tax (Section 115JB) | Book profit, Minimum Alternate Tax vs normal tax comparison, Section 115JAA credit ledger |
| `credits` | Taxes Already Paid | Tax Deducted at Source / Tax Collected at Source / advance tax credit entries |
| `challans` | Tax Payment Challans | Challan records that post to the general ledger on submit |
| `reconciliation` | Reconcile with Form 26AS | Upload the portal statement, match against books, adopt portal amounts |
| `interest` | Advance Tax & Interest | Instalment schedule, shortfall, interest under Sections 234A / 234B / 234C |
| `summary` | Statement of Total Income | The CA-format statement, plus variance against previous year and against books |
| `review` | Review & Validate | Blocking errors vs advisory warnings, each click-through to the field |
| `filing` | Return Filing (ITR-6) | Generate, hash, acknowledge, chain Revised / Belated / Updated |
| `audit` | Computation History | Append-only runs, hashes, explain |

Section state is a URL query parameter (`?section=depreciation`) so a link is shareable, but
switching sections **never** re-mounts the route or refetches the workspace.

### 2.2 Route consolidation

| Old route | New behaviour |
|---|---|
| `/tax/computations` | redirect ? `/tax` (the landing replaces the raw record table) |
| `/tax/challans` | redirect ? `/tax/workspace?section=challans` |
| `/tax/credits` | redirect ? `/tax/workspace?section=credits` |
| `/tax/depreciation` | redirect ? `/tax/workspace?section=depreciation` |
| `/tax/losses` | redirect ? `/tax/workspace?section=losses` |
| `/tax/mat-credits` | redirect ? `/tax/workspace?section=mat` |
| `/tax/calendar` | redirect ? `/tax/workspace?section=interest` |
| `/tax/filings` | redirect ? `/tax/workspace?section=filing` |
| `/income-tax`, `/income-tax/:id` | unchanged legacy redirects, now landing on `/tax` |

Eight view files are deleted, not orphaned:
`TaxComputationsView.vue`, `TaxChallansView.vue`, `TaxCreditsView.vue`, `TaxDepreciationView.vue`,
`TaxLossesView.vue`, `TaxMatCreditsView.vue`, `TaxCalendarView.vue`, `TaxFilingsView.vue`,
plus the six `tax-workspace/*Tab.vue` / `ResourcePanel.vue` files they were paired with.

## 3. API contract (frozen before implementation)

New thin router `backend/app/api/v1/tax/workspace.py`, prefix `/tax/workspace`, tag
`tax: workspace`, permission doctype `Income Tax Computation` (`read` / `write`), all logic in
`backend/app/services/taxation/`. Static paths are declared **before** the `computation/{id}` group
so there is no path-parameter ambiguity.

| Method | Path | Request | Response | Purpose |
|---|---|---|---|---|
| GET | `/tax/workspace/bootstrap` | `ay_code?` | `TaxWorkspaceBootstrapOut` | Landing + workspace cold start in one round trip |
| GET | `/tax/workspace/years` | ? | `list[TaxYearSummaryOut]` | Assessment-year list with status, due date, next action |
| GET | `/tax/workspace/lookups` | `ay_code?` | `TaxLookupsOut` | Every dropdown in the module, one call |
| GET | `/tax/workspace/templates` | `ay_code?` | `list[TaxTemplateOut]` | Entity/regime presets |
| POST | `/tax/workspace/templates/apply` | `TaxTemplateApplyIn` | `TaxComputationOut` | Create a pre-populated computation |
| GET | `/tax/workspace/computation/{id}` | ? | `TaxWorkspaceOut` | **BFF aggregate** ? replaces ten calls |
| POST | `/tax/workspace/computation/{id}/preview` | `TaxPreviewRequest` | `TaxPreviewOut` | **Non-persisting** dry-run compute |
| GET | `/tax/workspace/computation/{id}/validate` | ? | `TaxValidationOut` | Blocking vs advisory issues |
| GET | `/tax/workspace/computation/{id}/statement` | ? | `TaxStatementOut` | Statement of Total Income + variances |
| POST | `/tax/workspace/computation/{id}/copy-previous-year` | `TaxCopyPreviousYearIn` | `TaxCopyPreviousYearOut` | Carry masters, blocks, opening values, loss ledger, standing adjustments |
| POST | `/tax/workspace/computation/{id}/populate-from-books` | `TaxPopulateFromBooksIn` | `TaxPopulateFromBooksOut` | Pull profit + accounting depreciation from the general ledger |

### 3.1 Hard rules on `preview`

1. It calls `resolve_ruleset` ? `run_pipeline` ? `kernel` exactly like `runs.create_run`, then
   interest and credits, so the numbers are identical to what a real run would produce.
2. It **never** writes: no `TaxComputationRun`, no `TaxComputationResult`, no set-off entries, no
   Minimum Alternate Tax credit ledger rows, no adjustment lines. The service opens no transaction of
   its own and the router does not commit.
3. It returns `ruleset_hash` and `input_hash` so the frontend can tell the user whether the preview
   matches the last persisted run (`matches_current_run`) ? that is the honesty signal replacing the
   old "did you remember to click Run?" footgun.
4. Unsaved editor state is passed as an overlay in the request body, so live recompute works before
   autosave lands.

### 3.2 Section status contract

`TaxSectionStatusOut.status` is one of `not-started`, `in-progress`, `complete`, `has-errors`,
computed server-side in `workspace.py` from the same facts the validator uses, so the left navigation
and the Review panel can never disagree.

### 3.3 No schema migration

Every new capability is derived from existing tables or from the read-only statutory catalogue.
Templates are **system** presets (law-adjacent, not tenant data) held as frozen dataclasses in
`services/taxation/templates.py`; they are not a tenant master, so per the machine-first rule they
get neither a table nor a descriptor. Alembic head stays `0092_drop_legacy_itr`.

## 4. Minimal data entry ? the mapping

| Was typed | Now |
|---|---|
| Assessment year free text | Searchable select from `statutory.assessment_year`, defaulted to the current year |
| Entity class, regime | Selects from `statutory.assessee_class` / `statutory.tax_regime`, defaulted from `tax_registrations` and the year's election |
| Income head, income character | Selects with plain-English labels; character defaults to Ordinary Business Income |
| Income line **net** | Derived `gross ? deductions`, read-only, with an explain tooltip |
| Adjustment section code | Searchable select over `statutory.provision` (section number + title + stage + default effect) |
| Adjustment direction | Defaulted from the provision's `default_effect` |
| Depreciation block + rate | Select over `statutory.depreciation_block`; rate, opening written-down value, allowable depreciation and closing value all derived |
| Opening written-down value | Carried from the previous year's closing value by copy-forward |
| Challan major/minor head | Selects (`0020` Company Deducted, `0021` Other Than Company / `100` Advance Tax, `300` Self-Assessment, `400` Regular Assessment) |
| Challan bank + tax payable account | Selects over the chart of accounts, defaulted to the company's cash/bank and tax-payable accounts |
| Bank Branch Code (BSR) | Select over codes already used on this company's challans, still free-entry for a new bank |
| Deductor Tax Deduction Account Number and name | Select over deductors already seen in `tax_credit_entries`; picking one fills the name |
| Tax Deducted at Source section | Select over the common section list (194A/194C/194H/194I/194J/194Q/195/206C?) |
| Loss kind, set-off group, expiry year | Selects; expiry year derived from `income_character.loss_carry_years` |
| Estimated tax on the calendar | Derived from the latest computation result |
| Advance-tax instalment amount | Derived shortfall, offered as a one-click "record this challan" |
| Interest under 234A/234B/234C | Derived from statutory rules and real challan deposit dates |
| Surcharge, cess, marginal relief, rebate | Derived by the kernel, never enterable |
| Minimum Alternate Tax credit available | Derived from the ledger |
| Book profit | Offered from the general ledger net profit via populate-from-books |
| Return due date | Derived from `statutory.due_date_rule` + audit applicability |

Templates shipped: `PVT_LTD_NORMAL` (Private Limited Company ? normal provisions),
`COMPANY_115BAA` (Company opting for Section 115BAA concessional rate),
`COMPANY_115BAB` (New manufacturing company under Section 115BAB),
`SMALL_COMPANY` (Small company / MSME ? turnover-based rate).

## 5. Terminology

One TypeScript map, `frontend/src/config/taxTerminology.ts`, exporting `TAX_TERMS` (code ? `{ label,
short, statutoryRef?, help? }`) plus `taxLabel()` / `taxHelp()` helpers. Every user-visible string in
the module resolves through it. Dense grid headers may use `short`, but only with the full term in an
`InfoTip`. See ?3 of the final report for the before?after table.

## 6. Context-aware forms

`frontend/src/config/taxSections.ts` declares, per field group, a `when` predicate over a single
typed context object (`TaxWorkspaceContext`: entity class, regime, `concessionalRegime`,
`matApplicable`, `auditApplicable`, `presumptive`, `isDraft`, `isSubmitted`, `filingType`).
Panels ask `visible('mat.bookProfit')`, never `v-if="doc.assessee_class_code === 'Company' && ?"`.
Concessional-regime elections set `forfeitedIncentives`, which hides additional depreciation and the
forfeited Chapter VI-A groups and shows one explanatory note instead.

## 7. Phases

1. **Contract** ? schemas in `app/schemas/taxation.py` and this document. *(Everything else compiles
   against this; it lands first and alone.)*
2. **Backend services + router + tests** ? `preview.py`, `lookups.py`, `templates.py`,
   `validation.py`, `statement.py`, `books.py`, `workspace.py`, `api/v1/tax/workspace.py`.
3. **Frontend primitives** ? `SearchSelect`, `EditableGrid`, `DerivedField`, `InfoTip`,
   `CommandPalette`, `SectionNav`, `StatusPill`, terminology map, visibility engine, shortcut registry.
4. **Workspace shell** ? section navigation with status, sticky result rail with per-line explain,
   debounced preview, autosave with dirty/saved indication.
5. **Section panels** ? the fourteen panels.
6. **Consolidation** ? landing view, router redirects, `workspaces.ts`, delete replaced views.
7. **Verify** ? `ruff check`, `pytest tests/unit`, `npm run build`, container rebuild, `/health`,
   manual click-through.

Phases 3?6 are file-disjoint and were run in parallel; phase 1 was completed and frozen first.

## 8. Testing

- `tests/unit/taxation/test_preview.py` ? preview kernel path is Decimal-exact and byte-identical to
  the run path for the same inputs; overlay lines override persisted lines; preview writes nothing.
- `tests/unit/taxation/test_templates.py` ? every shipped template resolves against every shipped
  statutory pack; unavailable regimes are reported, not silently dropped.
- `tests/unit/taxation/test_validation.py` ? blocking vs advisory classification, section routing.
- `tests/unit/taxation/test_lookups.py` ? option shape, plain-English labels, no abbreviation-only labels.
- `tests/unit/taxation/test_statement.py` ? Statement of Total Income row order and subtotal arithmetic.
- Existing kernel golden vectors stay untouched and must keep passing.

---

## 9. Appendix ? frozen frontend contract

Written before implementation so the parallel streams (primitives / shell / panels / consolidation)
could not drift. Every signature below is normative.

### 9.1 File ownership

| Stream | Owns |
|---|---|
| Backend | `app/schemas/taxation.py`, `app/services/taxation/{preview,lookups,templates,validation,statement,books,workspace}.py`, `app/api/v1/tax/workspace.py`, `app/api/v1/router.py`, `tests/unit/taxation/test_{preview,templates,validation,lookups,statement}.py` |
| Primitives | `components/shared/{SearchSelect,EditableGrid,DerivedField,InfoTip,StatusPill,SectionNav,CommandPalette}.vue`, `config/taxTerminology.ts`, `config/taxSections.ts`, `composables/{useFieldVisibility,useKeyboardShortcuts}.ts`, `types/taxation.ts`, `docs/FRONTEND_TAX_PRIMITIVES.md` |
| Shell | `views/compliance/TaxWorkspaceView.vue`, `views/compliance/tax-workspace/ResultRail.vue`, `composables/{useTaxWorkspace,useTaxLookups}.ts`; deletes `composables/useTaxComputation.ts` and `views/compliance/tax-workspace/{Heads,Adjustments,Result,RunsAudit,FormPreview}Tab.vue` + `ResourcePanel.vue` |
| Panels | `views/compliance/tax-workspace/sections/*.vue` (fourteen files) |
| Consolidation | `views/compliance/IncomeTaxHomeView.vue`, `router/index.ts`, `config/workspaces.ts`, `views/compliance/{IncomeTaxSettingsView,StatutoryCatalogueView,TaxationSettingsView}.vue`; deletes the eight replaced CRUD views |

### 9.2 Shared types (`types/taxation.ts`)

Mirrors the backend schemas one-for-one, money as `string`. Additionally exports the section
contract every panel implements:

```ts
export type TaxSectionKey =
  | "overview" | "income" | "adjustments" | "depreciation" | "losses" | "mat"
  | "credits" | "challans" | "reconciliation" | "interest" | "summary"
  | "review" | "filing" | "audit";

export interface TaxSectionProps {
  workspace: TaxWorkspace;
  lookups: TaxLookups;
  disabled: boolean;
  preview: TaxPreview | null;
  statement: TaxStatement | null;
  incomeLines: TaxIncomeLine[];
  adjustmentLines: TaxAdjustmentLine[];
  bookProfit: string;
}

export type TaxSectionEmits = {
  changed: [];                                        // server-side change ? shell reloads the aggregate
  dirty: [];                                          // local draft change ? shell autosaves + re-previews
  navigate: [section: TaxSectionKey, field?: string]; // Review panel click-through
  "update:incomeLines": [lines: TaxIncomeLine[]];
  "update:adjustmentLines": [lines: TaxAdjustmentLine[]];
  "update:bookProfit": [value: string];
  "load-statement": [];
};
```

Every panel declares `defineProps<TaxSectionProps>()` and `defineEmits<TaxSectionEmits>()` ? the
full set, even where unused ? so the shell can render all fourteen uniformly and `vue-tsc` stays clean.

### 9.3 Primitive signatures

`SearchSelect` ? searchable/typeahead select over `TaxOption[]`, grouped, keyboard-navigable,
optional free entry.
`EditableGrid` ? paste-friendly inline grid; `Enter` moves to the next row and appends one on the
last row; per-column `derive`/`explain` for read-only computed cells; footer totals.
`DerivedField` ? read-only value with an "Auto" chip, statutory reference and explain tooltip.
`InfoTip` ? the full term behind a short grid header.
`StatusPill` ? section status / docstatus pill.
`SectionNav` ? status-aware left navigation.
`CommandPalette` ? `Ctrl/? K` action list.
`useFieldVisibility(ctx)` ? `{ visible(key), sections, forfeited }`, driven by `FIELD_RULES` in
`config/taxSections.ts` ? no inline `v-if` thickets in panels.
`useKeyboardShortcuts(() => ShortcutSpec[])` ? window-level registry, combos `mod+s`, `mod+k`,
`mod+enter`, `alt+n`, `?`.

### 9.4 Terminology map (`config/taxTerminology.ts`)

`TAX_TERMS: Record<string, TaxTerm>` where
`TaxTerm = { label: string; short?: string; statutoryRef?: string; help?: string }`, plus
`taxLabel()`, `taxShort()`, `taxHelp()`, `taxStatutoryRef()`, `taxTerm()`. Namespaced keys:
`head.*`, `character.*`, `stage.*`, `direction.*`, `regime.*`, `filingType.*`, `creditKind.*`,
`challanType.*`, `lossKind.*`, `majorHead.*`, `minorHead.*`, `status.*`, `section.*`, `term.*`.
`label` is always the full business term; `short` is only for dense grid headers and must always be
paired with an `InfoTip` carrying `label`.

---

## 10. Implementation progress log

Appended as each increment lands, so a resumed session can pick up without re-deriving state.

### Backend ? complete
- `services/taxation/lookups.py` ? every dropdown option list, business-labelled.
- `services/taxation/templates.py` ? presets, `apply_template`, `copy_previous_year`.
- `services/taxation/books.py` ? `populate_from_books` off the general ledger.
- `services/taxation/preview.py` ? non-persisting dry-run compute for the live result rail.
- `services/taxation/validation.py` ? `WorkspaceFacts` snapshot + blocking/advisory classification.
- `services/taxation/statement.py` ? Statement of Total Income, variance vs previous year and vs books.
- `services/taxation/workspace.py` ? BFF aggregate, per-section status, landing-page year summaries.
- `api/v1/tax/workspace.py` ? thin router, registered in `api/v1/router.py`.

Frozen HTTP surface (all under `/api/v1`):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/tax/workspace/bootstrap` | Landing screen cold start |
| GET | `/tax/workspace/lookups` | Option lists for every dropdown |
| GET | `/tax/workspace/templates` | Starting presets |
| POST | `/tax/workspace/templates/apply` | Create a computation from a preset |
| GET | `/tax/workspace/computation/{id}` | BFF aggregate |
| POST | `/tax/workspace/computation/{id}/preview` | Live recompute, persists nothing |
| GET | `/tax/workspace/computation/{id}/validate` | Blocking vs advisory issues |
| GET | `/tax/workspace/computation/{id}/statement` | Statement of Total Income |
| POST | `/tax/workspace/computation/{id}/copy-previous-year` | Carry last year forward |
| POST | `/tax/workspace/computation/{id}/populate-from-books` | Seed from the general ledger |

Per-computation routes sit under an explicit `/computation/` segment so a path
parameter can never shadow `/lookups`, `/templates` or `/bootstrap`.

### Frontend ? complete
Shared primitives, terminology map, section config, field-visibility and keyboard
composables, types, landing view, workspace shell and result rail. All fourteen section
panels are on disk under `views/compliance/tax-workspace/sections/`: Overview, Income,
Adjustments, Depreciation, Losses, Minimum Alternate Tax, Credits, Challans,
Reconciliation, Interest, Summary, Review, Filing, Audit.

Route consolidation done: `/tax` landing, `/tax/workspace/:id?`, and redirects from the
eight retired CRUD routes into the matching workspace section. The replaced views are
deleted and nothing imports them. Terminology sweep applied to the settings and catalogue
views; abbreviations survive only as internal codes, resolved through `taxTerminology.ts`.

### Defects found by browser testing and fixed
- `statement.py` raised `MissingGreenlet`: the preview's defensive `db.rollback()` expired
  the ORM instances that `collect_facts` had loaded. The preview now runs first.
- Live result rail double-counted adjustments. The editor seeded its draft from *all*
  adjustment rows including the engine's per-run audit rows, so every historic run replayed
  its adjustments. Fixed in `useTaxWorkspace.seedDraft`, and enforced server-side in
  `adjustment_facts_from_overlay` plus both write paths in `computations.py`
  (`run_id` added to `TaxComputationAdjustmentLineIn` so the server can tell).
- Submit was not gated: a computation with a blocking issue could be submitted. Now refused
  in `submit_computation` (422, `code="validation_blocked"`) before the provision is posted,
  and the header button is disabled with a reason.
- Brought-forward losses reported a negative carry-forward, because `amount_remaining` is
  already net of the set-off and was being netted again.
- `/tax/workspace` with no id showed the "start a year" form even when the current year
  existed; it now opens the current assessment year and rewrites the URL.
- Two labels repeated the financial year ("? (Financial Year 2025-26) (Financial Year
  2025-26)") on the landing cards and the Setup panel.

### Session 4 (resumed) ? audit, residual sweep, re-verification

State re-established before any edit: ?10 above was itself stale in the previous session, so
this session verified against the filesystem rather than the log. All fourteen panels, the
seven new backend services, the workspace router, the route consolidation and the deletion of
the eight replaced CRUD views were already on disk and green.

Dangling-reference sweep for the Phase 10 deletions across `backend/` and `frontend/src/`:
**clean**. Every surviving textual hit is one of ? a historical Alembic revision that must keep
its `down_revision` id (`0077_income_tax_engine`, `0084_tax_adjustment_engine`), a superseded
plan document under `docs/plans/`, or the live `itr_efile_provider` registration column, which
is a field name and not the deleted `itr_efile.py`. The four runtime-only files called out as
high-risk ? `core/scheduler.py`, `api/v1/router.py`, `jobs/`, `migrations/env.py` ? carry no
reference to any deleted module.

Residual minimal-data-entry gap closed in `IncomeTaxSettingsView.vue`, the last screen in the
module still holding free-text inputs:
- Default assessment year and the regime-election year are now selects over
  `/tax/catalogue/assessment-years`, labelled `2025-26 (previous year 2024-25)`.
- The election year now falls back to the newest catalogue year instead of a hardcoded
  `2025-26`, so it can never sit on a code the statutory pack does not carry.
- Electronic filing provider is a two-option select with self-describing labels instead of a
  text box hinting `none / sandbox`.

This document was re-encoded from Windows-1252 to UTF-8. It had been written with cp1252
em dashes plus a stray `0x9D` byte, so every dash rendered as `?` on GitHub and in the editor,
and the file could not be edited by exact-string match.

#### Defect found by browser testing this session, and fixed

**Every section switch was remounting the whole workspace.** `layouts/AppShell.vue` keyed the
`RouterView` on `route.fullPath`, which includes the query string. Because the active section
lives in `?section=`, each switch produced a *new* component instance: `onMounted` re-ran, so
`bootstrap` + `computation/{id}` + `preview` were refetched on every click (eight section
switches produced twenty-four API calls), the panel blanked to the "Start from a template"
cold-start screen mid-reload, and ? worst ? unsaved editor rows and scroll position were
discarded. This silently violated ?2.1 of this plan, which requires that switching sections
never re-mount the route or refetch the aggregate.

Fixed by keying on `route.path`. The original comment's concern ? not reusing an instance
across `/quotations/:id` ? `/sales-orders/new` ? is satisfied by the path alone, since the path
differs in exactly those cases. The bug affected the whole application, not just this module:
any view driving its own query string (report tabs, list filters, stock ledger tabs) was
remounting and refetching on every interaction.

One view depended on the old behaviour: `views/assets/AssetReportsView.vue` read `?tab=` only
at setup, and the sidebar links `/asset-reports`, `?tab=ledger` and `?tab=maintenance` share a
path. It now watches `route.query.tab`, matching the pattern already used by `ReportsView`,
`StockBalanceView` and `ManufacturingReportsView`. Every other query-reading view either
already watches the query or is only ever entered from a different path.

#### Seeded demo text was character-corrupted in the database

The Statement of Total Income rendered "Cash purchase of scrap and consumables above **?**10,000"
and "traffic penalties **?** not allowable". The database physically stored byte `0x3f` ? an ASCII
question mark ? where `?` (`E2 82 B9`) and `?` (`E2 80 94`) belong, confirmed with
`encode(convert_to(description,'UTF8'),'hex')`.

Root cause: the same Windows-1252 problem that made this plan document unreadable.
`scripts/seed_income_tax_msme_demo.py` is correct UTF-8 on disk *today*, so the seed must have
been run while the script was still cp1252-mangled; the stale rows outlived the fix. Re-seeding
from the current script produces correct text, so no code change was needed.

Repaired in place ? 9 rows, 3 distinct strings, all in
`public.tax_computation_adjustment_lines.description`; a scan of every `varchar`/`text` column on
every `tax%` table found no other corruption. The repair SQL was written with `chr(8377)` and
`chr(8212)` rather than literal characters, so that piping it through PowerShell could not
re-introduce the very corruption being fixed. Amounts were never touched, so no computation
value changed and append-only run history is unaffected.

#### Remaining terminology leaks closed
- `DepreciationSection.vue` printed the raw `block_code` (`BUILDING_10`, `PLANT_15`,
  `PLANT_30`) in the "Block of assets" column. It now resolves the code through the statutory
  catalogue: "Buildings ? other", "Plant and machinery ? general", "Motor cars other than
  hire". `_block_options` in `services/taxation/lookups.py` gained `meta["title"]` so a grid
  with its own rate column can show the bare business name instead of the rate-suffixed
  dropdown label, covered by `tests/unit/taxation/test_lookup_options.py`.
- `IncomeTaxSettingsView.vue` prior-elections list showed the raw `regime_code` and the bare
  word "irrevocable"; it now shows the catalogue title and "cannot be withdrawn once made".
- The Taxation sidebar ? visible on every Income Tax screen ? had a bare "TDS" group heading and
  "TDS Returns (26Q / 16A)". Spelled out in `config/workspaces.ts` to "Tax Deducted at Source" /
  "Tax Deducted at Source Returns (Form 26Q / Form 16A)". The Accounting workspace's
  "TDS / TCS Category" master link was left alone: it is a different workspace, never visible
  while working in Income Tax.

A scripted sweep of all fourteen sections for `ERPNext`, `Frappe`, and bare `AY`, `PY`, `WDV`,
`MAT`, `u/s`, `b/f`, `BSR`, `TDS`, `TCS` now returns **zero** findings, and zero 4xx/5xx API calls.

#### Note for whoever picks this up next

A **second agent session was editing this tree concurrently** ? `services/taxation/validation.py`,
`workspace.py`, `kernel/money.py` and `tests/unit/taxation/test_workspace_statement.py` were all
written while this session was running, and the unit-test count rose from 257 to 262 without this
session adding those tests. It was also driving the same Playwright browser, which is the real
explanation for the "section drift" chased at length here: the active section changed between tool
calls because another agent was clicking. Nothing was spontaneously navigating. A transient
`ruff` failure (an unused `ROUND_HALF_UP` import left in `workspace.py` after rounding moved to
`kernel/money.py`) came from that session mid-edit and was cleaned up here.

### Session 5 (resumed after a host networking failure)

The previous run did not die on a code fault. `wslrelay.exe` held `[::1]:8000` and `[::1]:8080`
with dead `CLOSE_WAIT` bindings while Docker's proxy served the `0.0.0.0` and `[::]` wildcards.
Because Windows resolves `localhost` to `::1` first, every `localhost` request timed out while
`127.0.0.1` answered normally. `docker compose down` + `up` did not release it; clearing the
stuck relay did, and it respawns on demand. Worth knowing: if the stack looks dead but
`127.0.0.1` works, this is why ? nothing needs rebuilding.

Dangling-reference check re-run against the four runtime-wired files, since neither `ruff` nor
`vue-tsc` can see a string-based reference. All three `SCHEDULED_JOBS` targets in
`core/scheduler.py` resolve through `importlib` (`subscription`, `assets`, `tax_reminders`);
`app.main` imports with all 360 routes registered, including the ten workspace routes;
`migrations/env.py` populates `Base.metadata` fully ? 191 tables, every `tax_*` table present
and no `income_tax_*` table left behind. The only textual hits anywhere in `backend/app` are
the live `itr_efile_provider` column, which is a registration field, not the deleted module.

#### Money in prose was unformatted
Section-navigation summaries and one validation message interpolated `Decimal` directly, so the
navigation read `gross total 12135000.00` and the Review panel `short by 4581389.02`, while the
rest of the module rendered `?1,21,35,000`. Added `rupees()` to `kernel/money.py` ? Indian
lakh/crore grouping, paise dropped unless non-zero ? and used it across all eleven summaries in
`workspace.py` and the advance-tax advisory in `validation.py`.

The Form 26AS summary also printed a bare negative (`-?41,000 still unexplained`), which does
not say which side is short. `difference` is Form 26AS less the books, so the sign is now read
and phrased: "books claim ?41,000 more than Form 26AS supports".

#### Internal catalogue codes were reaching the Statement of Total Income
`build_adjustments_section` prefixed `Section ` onto the raw catalogue code, printing
`(Section 37-CSR)` and `(Section 37-penalty)`. Those codes are qualified so that two distinct
rules under one section stay separable ? the qualifier is an internal key. `_section_reference`
now keeps only the statutory part, giving `(Section 37)`.

Two statutory pack titles and six seeded adjustment descriptions still carried abbreviations,
and the seed text repeated the section the UI already renders. Both fixed at source
(`data/statutory/in/_common.json`, `scripts/seed_income_tax_msme_demo.py`), the packs reloaded,
and the existing demo rows updated so the running stack matches.

#### Verified this session
Blocking validation is enforced by the server, not merely by a disabled button: `POST
/tax/computations/{id}/submit` returns `422` with
`{"detail","code":"validation_blocked","field"}` and names the offending issue, while the
advisory advance-tax shortfall does not block. Rebuilding the depreciation register moved
Depreciation ?20,12,100 ? ?19,41,475, Total Income ?1,18,78,900 ? ?1,19,49,530 and Net Tax
Payable ?22,38,133.15 ? ?22,59,757.93 with no refresh; typing an adjustment amount moved Gross
Total Income by exactly the amount typed. All eight retired routes redirect into the matching
workspace section carrying the current year's id. A scripted pass over all fourteen sections
found zero banned strings, zero console errors and zero failed requests.

#### Cross-section live updates ? verified, not assumed
Editing one income line by ?10,00,000 moved, in one debounced non-persisting preview:
Gross Total Income and Total Income (+?10,00,000), Tax on Total Income (+?2,50,000), surcharge,
Health and Education Cess, tax under normal provisions, the Minimum Alternate Tax comparison,
interest under both Section 234B and Section 234C, and Net Tax Payable
(?19,31,974.05 ? ?22,38,133.15). Submitting a challan from the Challans section cleared that
section's blocking issue and moved Net Tax Payable by the interest effect; claiming it as a
credit moved Taxes Already Paid from 9 entries/?12,75,500 to 10 entries/?13,25,500 and
Net Tax Payable from ?22,41,901.18 to ?21,91,901.18 ? with no page navigation or manual refresh.
