# Contribution Margin

Two complementary surfaces share the same CM1 / CM2 / CM3 language:

| Surface | When | Input |
|---------|------|--------|
| **CM Planning Engine** (pre-sales) | Before Quotation / SO confirmation | Estimated cost drivers + scenario what-ifs |
| **CM Report** (actuals) | After GL is posted | P&L accounts tagged with `cm_class` |

Statutory Profit & Loss and Gross Profit are unchanged.

## Planning engine (primary USP)

Standalone **Contribution Margin Plan** document linked to a Quotation or Sales Order.
Seed creates a **baseline scenario**; clone scenarios with overrides for what-if analysis.

Economics are driven by a **Cost Driver framework** (migration `0083+`):

- **Allocation method** (how): `from_source`, `percentage`, `rate_times_basis`, `fixed_amount`, `pool_share`, `formula`, …
- **Allocation basis** (what): `revenue`, `quantity`, `headcount`, `weight`, … (pluggable library)
- **Driver hierarchy**: groups (e.g. Corporate Overhead → HR / Finance / IT) roll up; CM1/CM2/CM3 still use the five `cm_class` values
- **Explainability**: each `cm_plan_costs` row stores a structured `explanation` (method, basis, pool, formula_display)

MVP `% of revenue` fixed layers remain the default via method=`percentage` + basis=`revenue`.

Also computes **minimum selling price** from target CM1%. Optional submit gate on Quotation/SO (`warn` / `block`).

Design briefs: [cm_cost_driver_framework.plan.md](../plans/cm_cost_driver_framework.plan.md), [cm_planning_engine.plan.md](../plans/cm_planning_engine.plan.md).

### Planning APIs (under `/api/v1`)

| Method | Path |
|--------|------|
| `POST` | `/cm-plans/from-quotation/{id}` |
| `POST` | `/cm-plans/from-sales-order/{id}` |
| `POST` | `/cm-plans/preview` |
| `GET`/`PUT` | `/cm-plans/{id}` (PUT = draft policy + allocations) |
| `GET` | `/cm-plans/{id}/actual-comparison?from_date&to_date&scenario_id?` |
| `POST` | `/cm-plans/{id}/scenarios` |
| `PUT` | `/cm-plans/{id}/scenarios/{sid}` |
| `POST` | `/cm-plans/{id}/compare` (+ `.csv`) |
| `POST` | `/cm-plans/{id}/scenarios/{sid}/apply-to-quotation` |
| `GET`/`PUT` | `/cm-planning/settings` |
| `GET`/`PUT` | `/cm-planning/cost-structure` |
| `GET` | `/cm-planning/templates` |
| `POST` | `/cm-planning/apply-template?template_id=` |

UI: Selling → **CM Plans** / **CM Settings**. Settings key: `system_settings.cm_planning` (separate from the GL report key).

## Actuals report (GL)

Tag each **P&L leaf** account with a `cm_class`:

- `revenue` | `variable_cost` | `product_channel_fixed` | `segment_bu_fixed` | `corporate_overhead`

Industry CoA templates under `backend/data/cm_templates/` suggest mappings.

| Method | Path |
|--------|------|
| `GET` | `/api/v1/reports/contribution-margin?from_date&to_date&cost_center_id?` |
| `GET` | `/api/v1/contribution-margin/templates` |
| `POST` | `/api/v1/contribution-margin/apply-template` |
| `GET` | `/api/v1/contribution-margin/unclassified` |
| `GET`/`PUT` | `/api/v1/contribution-margin/settings` |

UI: **Reports → Contribution Margin**. CoA has a CM Class field on P&L leaves.

Settings key: `system_settings.contribution_margin`.

## Estimated vs Actual (Phase 4 lean)

`GET /cm-plans/{id}/actual-comparison` compares a plan scenario (default baseline) to the company GL CM report for a date range. Deal-level estimate vs company-period actuals — scale may differ. Voucher-level mapping deferred.

## Vs Gross Profit / P&L

- **P&L** — statutory Income vs Expense by CoA tree.
- **Gross Profit** — item-level selling vs stock valuation COGS.
- **CM Planning** — forward-looking deal economics + scenarios.
- **CM Report** — booked GL amounts bucketed by managerial cost class.
