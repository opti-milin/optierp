# Income Tax Return (ITR) — Gap Analysis & Build Plan (India, multi-tenant SaaS)

**Scope:** make OptiReach help an Indian MSME **compute** its business-entity income-tax liability
from the books it already keeps, and eventually **export / hand off** an ITR pack (ITR-6 first for
companies) — a **net-new India compliance USP**. Not a re-implementation of ERPNext: upstream has
**no** entity ITR compute/file feature (verified). Modelled on the same sequencing as
[INDIA_COMPLIANCE_GAP_AND_PLAN.md](INDIA_COMPLIANCE_GAP_AND_PLAN.md) (data/JSON layer first, live
portal later) and the MCA idea in [USP_AND_FUTURE_SCOPE.md](USP_AND_FUTURE_SCOPE.md) §4.
**Status:** 🟢 **Phases 0–6 lean built, plus IndividualHeads extension, rule-engine refactor
(`0077_income_tax_engine`), and Tax Adjustment Engine (`0084_tax_adjustment_engine`).** Phase 0–1
worksheet + UI; Phase 2 ITR JSON/CSV; Phase 3 advance-tax calendar; Phase 4 entity-gated ITR-3/5
packs (lean worksheets); Phase 5 26AS JSON upload reconcile; Phase 6 pluggable e-file
(`none` / `sandbox` stub — no portal HTTPS / DSC yet). Follow-on: `Individual` + slab rates,
`IndividualHeads` ITR-1 export, lean Form 16 PDF, payroll bridge stubs. **Tax math** is
policy-driven (`FlatRate` / `SlabBased` / `RuleBased`) with separate masters for slabs,
surcharge (+ marginal relief), cess, 87A rebate, and special rates — see §5.1.
**Books → taxable** is now a metadata-driven **tax adjustment engine** (provisions + AY rule
packs + evaluators) — see §5.2.
**Designed:** 2026-07-19. **Built (0–6 lean):** 2026-07-19. **Rule engine:** 2026-07-29.
**Adjustment engine:** 2026-07-31.

> Cursor briefs: [plans/itr_gap_and_plan.plan.md](plans/itr_gap_and_plan.plan.md),
> [plans/taxation_module_itr.plan.md](plans/taxation_module_itr.plan.md),
> [plans/income_tax_engine_refactor.plan.md](plans/income_tax_engine_refactor.plan.md),
> [plans/tax_adjustment_engine.plan.md](plans/tax_adjustment_engine.plan.md).

> **SaaS framing:** many tenants will need entity ITR help; we cannot permanently skip
> proprietor/firm forms, but we **sequence** them. Phase 1 targets **companies (ITR-6)** — matches
> the Company tenant model and the MCA USP. Proprietor / partnership / LLP (ITR-3 / ITR-5) ride on
> per-company `entity_type` later. House rules: reuse P&L / GL / TDS — **no new posting engine** for
> tax computation; computation is a worksheet that *reads* books and optionally records advance-tax
> credits. Actual DSC e-filing is a later pluggable phase (same pattern as GSP).

---

## 0. Plain-language summary (read this first)

"Income tax return" for the **business** is different from **GST returns** and from **TDS** the
company withholds on suppliers:

1. **Books** — P&L and Balance Sheet for the financial year (we already have these).
2. **Taxable income** — start from book profit, then **add back / disallow** items the Income-tax
   Act does not allow as deductions, adjust depreciation (books vs Income-tax Act), claim Chapter
   VI-A deductions where applicable. *← not built; this is the headline gap.*
3. **Tax** — apply corporate (or firm/individual) rates, surcharge, cess; subtract **TDS/TCS
   receivable** and **advance tax** already paid; show tax payable or refundable.
4. **File** — put the numbers into the right **ITR form** (ITR-6 for companies) on the Income-tax
   portal (DSC for companies). *← not built; export pack first, live e-file later.*

What people confuse this with (and what we / ERPNext **do** have):

| Nearby thing | What it is | Not the same as |
|---|---|---|
| **TDS 26Q / Form 16A** | Tax *you withheld* on vendor payments | Your own ITR |
| **GSTR-1 / 3B** | GST returns | Income tax |
| **Employee income tax / Form 16** | Payroll TDS on salaries | Entity ITR |
| **Schedule III P&L/BS** | Companies-Act presentation of financials | Tax computation |

**Stance:** treat entity ITR as a **net-new differentiator**. ERPNext + india-compliance stop at
TDS + GST (+ Schedule III templates). We already hold the ledgers — we can pre-fill a computation
worksheet and an export pack so the owner / CA is not re-keying from scratch.

---

## 1. What an Indian MSME actually needs (entity income tax)

| Area | What it is | Priority |
|---|---|---|
| **PAN / TAN on Company** | Identity for ITR header + TDS returns | **must** |
| **Books → taxable income worksheet** | P&L + adjustments (40(a), 43B, depreciation, etc.) | **must** |
| **Tax computation** | Rates / surcharge / cess for the AY; tax payable | **must** |
| **Credit for TDS / TCS / advance tax** | Against liability | **must** |
| **ITR-6 pack (companies)** | Schedules / JSON or CSV for offline utility / CA | **must** (Phase 2) |
| **Advance-tax calendar** | 15 Jun / 15 Sep / 15 Dec / 15 Mar instalments | **high** |
| **ITR-3 / ITR-5** | Proprietor / firm / LLP | **sequenced** (entity_type) |
| **Form 26AS / AIS reconcile** | Match credits claimed vs portal | **medium** |
| **Live e-filing (DSC)** | Push to income-tax portal | **later** (pluggable) |
| **Employee Form 16 / 24Q** | Payroll | **bridge ready**; full Payroll still pending |

---

## Payroll linkage

The original lean ITR build stopped at entity filing. The current extension adds
`IndividualHeads` worksheets, Form 16 fields, and a **Payroll bridge contract**
so HR/Payroll can auto-seed ITR without redesigning Taxation.

### What exists now

- `IncomeTaxComputation` supports:
  - `assessee_mode = EntityBooks | IndividualHeads`
  - salary / house-property / other-sources / capital-gains heads
  - Form 16 fields (`employer_*`, `employee_*`, salary totals, deducted tax)
  - bridge fields: `seed_source`, `employee_id`, `payroll_entry_id`, `salary_slip_ids`
- `backend/app/services/payroll_income_tax_bridge.py` defines:
  - `Form16Slice`
  - `form16_slices_from_salary_slips(...)` stub
  - `seed_individual_computation_from_payroll(...)`
- `POST /income-tax-computations/seed-from-payroll` documents the intended handoff.
  Until HR/Payroll exists it returns `PAYROLL_NOT_ENABLED`.

### Implementation checklist for HR/Payroll

When building HR/Payroll, **must**:

1. Reuse `seed_individual_computation_from_payroll(...)` instead of rebuilding salary-tax math.
2. Implement `form16_slices_from_salary_slips(...)` from real `Salary Slip` / `Payroll Entry` data.
3. Trigger the bridge on Payroll Entry close and/or an explicit “Seed from Payroll” action.
4. Map Payroll fields as follows:
   - gross salary → `gross_salary` and `salary_income`
   - exemptions → `exemptions_total`
   - taxable salary → `taxable_salary`
   - salary TDS → `tax_deducted` and `salary_tds`
   - employer identity → `employer_name`, `employer_tan`, `employer_address`
5. Prefer one active `IndividualHeads` computation per `(company, assessment_year, employee_id)`.
6. Leave seeded computations in **Draft** for human review.
7. Never overwrite a submitted computation; cancel + recreate or refuse.

### Important design note

Payroll should **not** reimplement slabs, rebate `87A`, cess, or export math.
Those belong in the existing income-tax services so EntityBooks and IndividualHeads
stay consistent.

---

## 2. What ERPNext / india-compliance / HRMS actually have (verified)

Inspected **2026-07-19** against upstream git trees (not assumptions).

### 2.1 `frappe/erpnext` `develop` (~5429 blobs)

- **No** paths matching `itr`, `income_tax`, `form_16`, `tax_return`, or `self_assessment`.
- **Has (related — TDS/TCS, not entity ITR):**
  - [`erpnext/accounts/doctype/tax_withholding_category/`](https://github.com/frappe/erpnext/tree/develop/erpnext/accounts/doctype/tax_withholding_category)
  - siblings: `tax_withholding_rate`, `tax_withholding_account`, `tax_withholding_entry`, `tax_withholding_group`
  - report [`erpnext/accounts/report/tax_withholding_details/`](https://github.com/frappe/erpnext/tree/develop/erpnext/accounts/report/tax_withholding_details)
  - regional [`erpnext/regional/doctype/lower_deduction_certificate/`](https://github.com/frappe/erpnext/tree/develop/erpnext/regional/doctype/lower_deduction_certificate)
- **No India ITR under regional** — `erpnext/regional/` has UAE / US / Italy / Australia / etc.;
  India GST was moved out of core.

**Verdict:** ERPNext does **not** compute or file business-entity Income Tax Returns.

### 2.2 `frappe/hrms` `develop`

- Employee payroll tax only:
  - [`hrms/payroll/doctype/income_tax_slab/`](https://github.com/frappe/hrms/tree/develop/hrms/payroll/doctype/income_tax_slab)
  - reports [`income_tax_computation`](https://github.com/frappe/hrms/tree/develop/hrms/payroll/report/income_tax_computation) /
    `income_tax_deductions` (`ref_doctype: Salary Slip`)
- **No** Form 16 print format, Form 24Q, or entity ITR in the tree.

**Verdict:** HRMS income tax = **salary** TDS/computation, not company ITR.

### 2.3 `resilient-tech/india-compliance` `develop`

- README / product surface is **GST** (GSTR-1/3B, e-invoice, e-way, 2A/2B).
- Module [`india_compliance/income_tax_india/`](https://github.com/resilient-tech/india-compliance/tree/develop/india_compliance/income_tax_india):
  - TDS section seed data (`data/tds_details.json`)
  - overrides for Tax Withholding Category + Company
  - reports: Tax Withholding Details (India), TDS Computation Summary
  - **Schedule III** P&L / Balance Sheet financial-report templates
  - **`doctype/` is empty** — **zero `itr` paths**

**Verdict:** india-compliance “Income Tax India” = **TDS + Schedule III presentation**, not ITR filing.

### 2.4 Distinction (upstream vs OptiReach today)

| Capability | Upstream | OptiReach today |
|---|---|---|
| Entity ITR compute / file | **None** | **None** (this plan) |
| TDS/TCS on invoices | ERPNext withholding | ✅ + 26Q / 16A |
| GST returns / e-docs | india-compliance | ✅ Phases 0–5+ |
| Employee income tax / Form 16 | HRMS slabs + salary slip | Out of scope until Payroll |
| Schedule III financials | india-compliance templates | P&L/BS exist; not Schedule III-shaped |

---

## 3. What we already have (don't rebuild)

- **P&L / Balance Sheet** — [`backend/app/services/financial_reports/statements.py`](../backend/app/services/financial_reports/statements.py)
- **Fiscal Year**, immutable **GL**, **Period Closing**
- **TDS/TCS** — Tax Withholding Category + [`tds_returns.py`](../backend/app/services/tds_returns.py) (26Q / 16A)
- **Compliance UI pattern** — [`frontend/src/views/compliance/`](../frontend/src/views/compliance/)
  (`GstReturnsView`, `TdsReturnsView`, `GstSettingsView`)
- **Per-company settings blob** — GST Settings via `SystemSetting` key (reuse for Income Tax Settings)

---

## 4. The gaps (what to build)

1. **Company PAN / TAN** — today only `tax_id` (GSTIN). Needed for ITR header and to complete 26Q.
2. **Books → taxable-income bridge** — ✅ **Tax Adjustment Engine** (`0084`): provision catalogue
   + AY rule packs + evaluators (PercentOfBase, ScheduleCap, PaymentTiming, DiffTwoSources, …).
   ERP adapters best-effort; missing facts → `NeedsInput`. Full ICDS measurement / MAT compare
   remain informational stubs (`phase_d`).
3. **Corporate tax computation** — AY rate table (rate / surcharge / cess); tax on taxable income;
   MAT depth **deferred** (catalogue stub only).
4. **Credits** — TDS receivable from books + advance-tax payments; net payable / refundable.
5. **ITR-6 export pack** — schedule-oriented JSON/CSV for offline utility / CA (Phase 2).
6. **Advance-tax calendar + reminders** (Phase 3).
7. **ITR-3 / ITR-5** behind `entity_type` (Phase 4).
8. **26AS / AIS import reconcile** — file upload first (Phase 5).
9. **Live e-filing** — pluggable provider + DSC (Phase 6; optional).

---

## 5. Data model + machine-first classification (PROJECT.md §6)

| Artifact | Decision |
|---|---|
| **Income Tax Settings** (`entity_type`, regime, default AY, flags) | Config blob like GST Settings — **not** a new master table |
| **Company** `pan`, `tan` | Columns on existing Company |
| **Income Tax Rate Table** (AY × flat `tax_rate` only — Company/Firm/LLP) | **Simple master → descriptor** |
| **Income Tax Slab Set** + child bands | **Descriptor** (Individual / Proprietor) |
| **Surcharge Rule Set** + brackets (+ marginal relief flag) | **Descriptor** |
| **Health & Education Cess Rule** | **Descriptor** |
| **Rebate Rule** (e.g. 87A) | **Descriptor** |
| **Special Income Tax Rate** (CG / lottery / crypto) | **Descriptor** |
| **Tax Policy** (method + links to packs per AY × entity × regime) | **Descriptor** |
| **Tax Adjustment Provision** (section taxonomy / stage / effect) | **Descriptor** |
| **Tax Adjustment Rule Pack** + child **Rules** (method + JSON params) | **Descriptor** + eval in `tax_adjustment_engine/` |
| **Tax Depreciation Block** (IT Act WDV) | **Descriptor** |
| **Tax Adjustment Category** (legacy Add/Deduct alias → provision) | **Descriptor** (compat) |
| **Income Tax Computation** (one per company × assessment year) | **Transaction / heavy logic → bespoke service**; `docstatus` 0/1/2 |
| **Computation adjustment lines** (child, audited) | Child of Computation (bespoke) |
| **Special-income lines** (child) | Child of Computation (bespoke) |
| **Advance Tax Payment** | Phase 3 — light submittable doc or child rows on Computation |
| **ITR export (JSON/CSV)** | Read-only **bespoke generator** (mirror GSTR JSON) — no DocType |
| Employee Form 16 / 24Q | **Out of scope** until HRMS Payroll (lean Form 16 PDF exists) |

```
Books (P&L, TDS) ──► Income Tax Computation (bespoke) ──► ITR export
         ▲                        ▲
   IT Settings              Tax Policy → Rate / Slabs / Surcharge / Cess / Rebate
   (SystemSetting)          + Tax Adjustment Engine (provisions / rule packs)
                            + Special Rates (descriptors)
                            Engines: income_tax_engine/ + tax_adjustment_engine/
```

**Decision rule:** posts GL / computes statutory tax figures → bespoke; a field on Company → column;
a filing artifact → read-only generator; a rate/category list with no posting → descriptor.

### 5.1 Rule-engine pipeline (data-driven)

```
taxable income (books + adjustments)
  → Tax Policy selects FlatRate | SlabBased | RuleBased
  → base tax (+ special-rate lines for RuleBased)
  → rebate (e.g. 87A from Rebate Rule)
  → surcharge brackets + optional marginal relief
  → Health & Education Cess
  → less TDS / TCS / advance tax → payable / refund
```

All statutory numbers live in masters (AY + optional effective dates). Submitted computations
snapshot `policy_id`, `computation_method`, and `tax_breakdown` JSON for audit.

### 5.2 Tax adjustment engine (books → taxable)

```
Books / heads / ERP facts
  → Tax Adjustment Rule Pack (AY × entity × regime)
  → methods: Manual | PercentOfBase | ThresholdDisallow | PaymentTiming
             | DiffTwoSources | ScheduleCap | FormulaSafe | PriorYearReversal
  → audited adjustment lines (status Computed / NeedsInput / Overridden / …)
  → net_adjustments → existing tax pipeline (§5.1)
```

Catalogue seed: `backend/data/tax_adjustment_packs/`. Recompute:
`POST /income-tax-computations/{id}/recompute-adjustments`. Legacy **Tax Adjustment Category**
remains as a thin alias keyed to provisions.

---

## 6. Phased build plan

### Phase 0 — Settings + identity *(foundation)* 
- `pan` / `tan` on Company.
- Income Tax Settings blob (`entity_type`: Company | Proprietor | Firm | LLP; filing regime;
  assessment-year default).
- Compliance nav entry for Income Tax.
- Migration after `0065_manufacturing` → `0066_*`.

### Phase 1 — Computation pack *(headline value)*
- Descriptors: **Income Tax Rate Table**, **Tax Adjustment Category**.
- Bespoke **Income Tax Computation**: FY window → seed book profit from P&L; adjustment lines;
  apply rate table; credit TDS from submitted purchase invoices (same basis as 26Q); show
  tax payable / refundable.
- API under `/api/v1/compliance/…`; UI `/income-tax` mirroring GST/TDS returns views.
- Unit tests for tax math + adjustment roll-up.

### Phase 2 — ITR-6 export
- Schedule-oriented JSON/CSV pack for offline utility / CA handoff (not live portal).

### Phase 3 — Advance tax calendar
- Instalment dates + amounts; reminders via existing notification/scheduler seams;
  credit against Computation.

### Phase 4 — ITR-3 / ITR-5
- Gated by `entity_type`; adapt worksheets (business income heads, partner share for firms).

### Phase 5 — 26AS / AIS reconcile
- Upload portal file; match credits claimed vs Computation (mirror GSTR-2B recon pattern).

### Phase 6 — Live e-filing *(optional)*
- Pluggable provider abstraction (sandbox/production); DSC out-of-band — same posture as GSP.

### Out of scope (initially)
- Full MAT / AMT depth, international tax, trust **ITR-7**, employee Form 16 / 24Q,
  replacing the CA for tax audit / Form 3CD.

---

## 7. Decisions captured

1. **Entity ITR is net-new** — not an ERPNext parity port; cite upstream gaps in §2.
2. **Company / ITR-6 first** — proprietor/firm sequenced via `entity_type`.
3. **Computation before filing** — worksheet + export before any portal push.
4. **Reuse books** — P&L / TDS; no parallel books ledger for tax.
5. **Build order when greenlit:** Phase 0 → 1 → 2 → 3 → 4 → 5 → 6.

> **Recommended build order:** Phase 0 (PAN/TAN + settings) → Phase 1 (computation pack + UI) →
> Phase 2 (ITR-6 export) → Phase 3 (advance tax) → Phase 4 (ITR-3/5) → Phase 5 (26AS) →
> Phase 6 (live e-file).

---

## 8. Status

| Phase | Status |
|---|---|
| Research (upstream verify) | ✅ 2026-07-19 |
| This plan document | ✅ |
| Phase 0 — settings + PAN/TAN | ✅ 2026-07-19 |
| Phase 1 — computation pack | ✅ 2026-07-19 |
| Phase 2 — ITR-6 export | ✅ 2026-07-19 (JSON/CSV handoff) |
| Phase 3 — advance tax calendar | ✅ 2026-07-19 (dates + suggested amounts; reminders later) |
| Phase 4 — ITR-3 / ITR-5 | ✅ 2026-07-19 lean (`GET …/itr` + business_income / partner_share heads) |
| Phase 5 — 26AS / AIS reconcile | ✅ 2026-07-19 (JSON upload) |
| Phase 6 — live e-filing | ✅ 2026-07-19 abstraction + `sandbox` stub (portal HTTPS / DSC later) |
