# Taxation Architecture

> Replaces the former `ITR_GAP_AND_PLAN.md` (decommissioned in Phase 10). Master delivery plan:
> [plans/itr_enterprise_rearchitecture.plan.md](plans/itr_enterprise_rearchitecture.plan.md).
>
> **Plain-language how-to:** [INCOME_TAX_USER_GUIDE.md](INCOME_TAX_USER_GUIDE.md).

OptiReach Income Tax is a **four-tier** design. Statutory law is global and read-only to tenants;
worksheets, ledgers and filings are company-scoped under RLS.

## Tiers

```
Tier 1  statutory.*          Finance-Act versioned catalogue (SELECT-only for erp_app)
Tier 2  tax_registrations    PAN/TAN/CIN, regime elections, policy overrides
Tier 3  tax_computations*    Worksheet + append-only runs/results, challans, credits,
        tax_filings          dep/loss/MAT ledgers, ITR filings
Tier 4  services/taxation/   resolve → facts → kernel → pipeline → forms
```

Dependency arrows only point downward. A filed return pins `finance_act_version_id` and
persists `ruleset_hash` + `input_hash` on every run so recomputation is auditable.

## Package map (`backend/app/services/taxation/`)

| Package | Role |
|---------|------|
| `catalogue/` | Pack loader/validator + read accessors |
| `resolve/` | `(company, AY, class, regime)` → frozen hashed `ResolvedRuleSet` |
| `facts/` | ERP adapters (books, TDS, prior year) |
| `kernel/` | Pure Decimal math — slabs, surcharge, marginal relief, cess, rebate, MAT, set-off, 234A/B/C |
| `pipeline.py` | Ordered stages; no I/O |
| `runs.py` | Append-only persistence |
| `forms/` | `itr_field_map`-driven CBDT JSON (ITR-6 first) |
| `filings.py` | Generate / hash / acknowledge / chain Revised·Belated·Updated |
| `challans.py` / `credits.py` / `recon_26as.py` | Payments + credits + GL |
| `depreciation.py` / `loss_setoff.py` / `mat_credit.py` | Corporate depth |
| `calendar.py` / `interest.py` | Advance-tax shortfall + reminders |

## HTTP surface (`/api/v1/tax/`)

- `/tax/catalogue/*` — law browser (AYs, rates, provisions, ITR field maps)
- `/tax/registrations`, `/tax/elections`
- `/tax/computations` — CRUD, submit, `POST/GET …/runs`, explain
- `/tax/challans`, `/tax/credits`, `/tax/credits/reconcile-26as`
- `/tax/depreciation`, `/tax/losses`, `/tax/mat-credits`
- `/tax/calendar` — advance-tax + 234 preview + reminders
- `/tax/filings` — generate ITR-6, acknowledge, sandbox e-file, chain returns

## UI

Primary entry: **Tax Workspace** (`/tax/workspace`) — Heads / Adjustments / Depreciation /
Set-off / MAT / Credits / Challans / Result / Runs·Audit / Form preview.

Sibling resource pages remain under `/tax/*`. Settings live at `/income-tax-settings`
(backed by `tax_registrations`, not a SystemSetting blob).

## Data packs

Versioned JSON under `backend/data/statutory/in/<ay>/pack.json` (+ `_common.json`).
Load with `python -m scripts.load_statutory` (idempotent; owner role).

## What was decommissioned (Phase 10)

Deleted: `income_tax_engine/`, `tax_adjustment_engine/`, `income_tax_computation.py`,
`income_tax_masters.py`, `income_tax_settings.py`, `itr_export.py`, `itr_efile.py`,
legacy `/income-tax-computations` and `/income-tax-settings` routers, tenant law descriptors
(`tax-policy`, `tax-adjustment-*`, `tax-depreciation-block`), and tables dropped in
`0092_drop_legacy_itr`.

## Kernel contracts

- Money is `Decimal` only (`ROUND_HALF_UP`); floats rejected.
- s.288A / s.288B round to nearest ₹10 at the statutory points only.
- Marginal relief **re-runs** the rate schedule at the threshold (no proportional scale).
- Surcharge is character-aware (15% cap on 111A/112A/115AD).
- MAT (115JB) is compared with normal tax; 115JAA credit is ledgered.

## Follow-ons (out of Phase 10)

Individual-track depth (Salary/HP/CG schedules, per-section Chapter VI-A, Form 16, payroll
bridge) and presumptive 44AD/44ADA/44AE — models already accept them without further schema
change.
