# Manufacturing Module — Implementation & Test Guide

**Status:** ✅ **Built (2026-07-05)** — lean core + convenience + reports (Phases 1–4 of
[MANUFACTURING_GAP_AND_PLAN.md](MANUFACTURING_GAP_AND_PLAN.md)). Migration `0065_manufacturing`.
Branch `feat/manufacturing`.
**Fast-follow (same day):** the gap analysis
([MANUFACTURING_GAP_ANALYSIS_2026-07.md](MANUFACTURING_GAP_ANALYSIS_2026-07.md)) BUILD-NOW items are
also in — **Repack** (BOM-less conversion, §8), **Manufacturing Settings** (default warehouses +
over-production allowance, §9), the **cross-Work-Order Material Shortage report** (§10), the **BOM
recursion guard**, the **whole-number qty guard**, and the **Manufacture single-finished-row valuation
guard**. No new migration — settings live in the existing `system_settings` JSON table.

This doc records **exactly what was implemented** and **how to test it by hand**. For the design
rationale and the ERPNext-vs-lean scope decisions, read the plan doc first.

---

## 1. What it does (plain language)

Two documents, one new stock behaviour:

- **BOM (Bill of Materials)** — the *recipe*. "1 Mixer Grinder = 1 motor winding + 1 jar set +
  ₹150 labour." It's a master: it snapshots a cost but touches no stock and no ledger.
- **Work Order** — the *act of making it*. "Make 2 Mixer Grinders." Creating it explodes the BOM
  into required components; **Finish** consumes the components out of stock and produces the
  finished good into stock, valued at *(materials consumed + operating cost) ÷ qty produced*.
- **"Manufacture" Stock Entry** — a new purpose on the existing Stock Entry. The Work Order's Finish
  posts it; it reuses the existing **Stock Ledger + Moving-Average valuation + perpetual-inventory
  GL**. No new valuation engine, no new accounts.

---

## 2. Backend — files & endpoints

### New models — `backend/app/models/manufacturing.py` (migration `0065_manufacturing`)
| Table | Purpose |
|---|---|
| `boms` | BOM header — production item, batch quantity, currency, operating cost, cost snapshot (`raw_material_cost`, `total_cost`), `is_active`, `is_default`. |
| `bom_items` | BOM components — item, qty, uom, conversion_factor, stock_qty, rate (from valuation), amount, optional source warehouse. |
| `work_orders` | Work Order — production item, bom, qty, produced_qty, source/wip/fg warehouses, `skip_transfer`, operating_cost + account, status, planned/actual dates, optional sales order. |
| `work_order_items` | Required components exploded from BOM × qty — required/transferred/consumed qty, source warehouse, rate, amount. |

### Changed — `backend/app/models/stock.py`
- `STOCK_ENTRY_PURPOSES` gains `"Manufacture"` and `"Material Transfer for Manufacture"`.
- `StockEntry` gains `work_order_id`, `operating_cost`, `operating_cost_account_id`.

### Services
- `services/bom.py` — create / update (draft) / **update-cost** / submit / cancel / activate/deactivate; cost rollup; single-default enforcement.
- `services/work_order.py` — create (explode BOM), submit, cancel, stop/resume, **finish** (build + submit the Manufacture entry, advance produced/consumed qty + status), **material_availability**, **create_shortfall_material_request**, optional **transfer_for_manufacture** (WIP), and `revert_manufacture_entry` (called when a Manufacture entry is cancelled).
- `services/stock_entry.py` — `submit_stock_entry` gets a **Manufacture branch** (FG valued at consumed value + operating cost; GL = Cr each raw's inventory account, Dr the FG inventory account, Cr the operating-cost account); `cancel_stock_entry` rolls the Work Order back.
- `services/manufacturing_common.py` — naming series, `resolve_valuation_rate` (component's live moving-average from Bins → item valuation → last purchase → standard), `item_available_qty`.
- `services/manufacturing_reports.py` — production register, BOM where-used, BOM stock report.
- `services/module_workspace.py` — `get_manufacturing_workspace` (dashboard stats).

### API (registered in `api/v1/router.py`)
| Method & path | Action |
|---|---|
| `POST /boms` · `GET /boms` · `GET /boms/{id}` | create / list / get BOM |
| `PATCH /boms/{id}` | edit a draft BOM (fields + full component replace) |
| `POST /boms/{id}/update-cost` | re-source rates from valuation, refresh cost |
| `POST /boms/{id}/submit` · `/cancel` · `/activate` · `/deactivate` | lifecycle |
| `POST /work-orders` · `GET /work-orders` · `GET /work-orders/{id}` | create / list / get Work Order |
| `GET /work-orders/{id}/material-availability` | on-hand vs pending per component + can-finish qty |
| `POST /work-orders/{id}/submit` · `/cancel` · `/stop` · `/resume` | lifecycle |
| `POST /work-orders/{id}/finish` | **manufacture** — consume raws, produce FG |
| `POST /work-orders/{id}/transfer` | optional WIP material transfer |
| `POST /work-orders/{id}/material-request` | raise a Purchase MR for shortfalls |
| `GET /manufacturing-reports/production-register` | planned-vs-produced + cost |
| `GET /manufacturing-reports/bom-where-used?item_id=` | BOMs that use an item |
| `GET /manufacturing-reports/bom-stock?bom_id=&for_qty=` | can-I-build-N |
| `GET /manufacturing/workspace` | dashboard stats |

### Permissions (`scripts/seed.py`)
New doctypes `BOM` and `Work Order`. New roles **Manufacturing Manager** (full) and **Manufacturing
User** (no cancel) + read on Item/Warehouse/Stock Entry/Stock Ledger and MR create for shortfalls.
**Stock Manager** also gets full BOM/Work Order access (lean MSME: the stock lead runs production).
System Manager (the default admin) already bypasses permission checks.

---

## 3. The mechanics (what actually posts)

### BOM cost
```
raw_material_cost = Σ (component.stock_qty × component.rate)   # rate = live valuation
total_cost        = raw_material_cost + operating_cost
cost_per_unit     = total_cost / quantity
```
Computed on create/update and on **Update Cost**. This is the *estimate*; the *actual* cost is
captured at finish time from the real consumed valuation.

### Finish → Manufacture Stock Entry (n units of an N-qty order)
- **Consume** each required item `required_qty × n/N` out of the source (or WIP) warehouse at its
  current moving-average valuation.
- **Produce** n finished units into the FG warehouse at
  `incoming_rate = (Σ consumed value + operating_cost × n/N) ÷ n`.
- **GL (perpetual inventory on):** `Cr` each raw's inventory account (consumed value), `Dr` the FG
  inventory account (consumed + operating), `Cr` the operating-cost account (the labour capitalised
  into the FG). Balances exactly. If the FG and raws share an inventory account, the entry nets to
  the operating-cost reclass only.
- Work Order `produced_qty` and each component's `consumed_qty` advance; status → *In Process*
  (partial) or *Completed* (full). Cancelling the Manufacture entry reverses the stock + GL **and**
  rolls the Work Order back.

**Operating-cost account:** required only when perpetual inventory is on *and* operating cost > 0.
Use a clearing/expense account — **"Expenses Included In Valuation"** is the natural choice (the same
account ERPNext uses for costs capitalised into inventory).

---

## 4. Frontend — files

- `config/workspaces.ts` — `MANUFACTURING` workspace (sidebar + cards) + registered in `WORKSPACES`.
- `views/dashboard/LauncherView.vue` — 🏭 Manufacturing launcher tile.
- `router/index.ts` — routes: `/manufacturing` (workspace), `/bom`, `/bom/:id`, `/work-orders`,
  `/work-orders/:id`, `/manufacturing-reports`.
- `components/shared/StatusBadge.vue` — Work Order states (Not Started / In Process / Stopped).
- `types/manufacturing.ts` — DTOs.
- Views under `views/manufacturing/`: **BomView** (list + create), **BomDetailView** (cost cards +
  components + submit/cancel/refresh-cost/activate), **WorkOrderView** (list + create),
  **WorkOrderDetailView** (Finish + availability + stop/resume/cancel + shortfall MR),
  **ManufacturingReportsView** (tabbed: Production Register / BOM Where-Used / BOM Stock).

---

## 5. Manual test steps (UI)

Prereq: some stock items exist and raw components have on-hand stock (the demo seed's
`RM-*` items + `MIXER-GRINDER-X200` work well). Perpetual inventory is on in the demo company.

1. **BOM.** Manufacturing → BOM → *New BOM*. Finished good = *Mixer Grinder X200*; batch qty 1;
   operating cost 150; add components *Copper Motor Winding 750W* (1) and *Stainless Steel Jar Set*
   (1); tick *default*; **Create draft**.
   → detail shows *Raw material cost* (from live valuation), *+ ₹150 operating*, *Total* and
   *per-unit* cost. Click **Submit** → status Submitted, Active.
2. **Work Order.** Manufacturing → Work Order → *New Work Order*. BOM = the one above; qty 2;
   source & FG warehouse = *Main Store*; operating cost account = *Expenses Included In Valuation*;
   **Create draft**.
   → detail shows Operating cost ₹300, required components with **Available** and **Can finish now**.
   Click **Submit** → *Not Started*.
3. **Finish.** Click **Finish…**, keep qty 2, **Confirm & manufacture**.
   → status **Completed**, "Manufactured 2 — posted MAT-STE-…", each component's *Consumed* = 2 and
   *Available* drops by 2.
4. **Verify stock & GL.**
   - Stock → Stock Balance: the finished good now has +2 at its valued input cost; each raw −2.
   - Reports → General Ledger: the Manufacture Stock Entry balances (Cr raw inventory, Dr FG
     inventory, Cr Expenses Included In Valuation for the ₹300 labour).
5. **Reports.** Manufacturing → Reports → **Production Register** shows the Work Order (Produced 2,
   Est. cost). **BOM Where-Used** for a component lists the BOM. **BOM Stock** for the BOM with a
   target qty shows buildable qty + per-component shortfall.
6. **Shortfall (optional).** Create a Work Order whose qty exceeds on-hand stock → the detail shows a
   red **Shortfall**; **Raise Material Request for shortfall** creates a Purchase MR for the gap.
7. **Reverse (optional).** Cancel the Manufacture Stock Entry (Stock → Stock Entries → the
   `Manufacture` row → Cancel) → raws restored, FG removed, the Work Order rolls back to *Not
   Started* / produced 0.

---

## 6. Automated tests

- **Integration** `backend/tests/integration/test_manufacturing.py` (7 tests): BOM costing;
  Work Order → Manufacture stock + GL reconciliation (FG valued at materials + labour, operating
  account credited, trial balance balanced); partial finish + over-produce block; material
  availability + shortfall MR; Manufacture-cancel rolls the Work Order back; BOM-cancel blocked
  while a Work Order uses it; Phase-4 reports.
- **Regression:** stock moving-average + supply-chain suite unaffected (`stock_entry.py` changes are
  an additive Manufacture branch).

Run (Postgres required — see `docs`/memory for the `erp_test` setup):
```bash
cd backend
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/erp_test"
export MIGRATIONS_DATABASE_URL="$DATABASE_URL"; export TEST_DATABASE_URL="$DATABASE_URL"
./.venv/Scripts/python.exe -m pytest tests/integration/test_manufacturing.py -q   # 7 passed
./.venv/Scripts/python.exe -m pytest tests/unit -q                                # 132 passed
./.venv/Scripts/python.exe -m ruff check app/ scripts/                            # clean
```
Frontend: `cd frontend && node_modules/.bin/vue-tsc -b` (clean).

---

## 7. Repack (BOM-less conversion) — fast-follow

The most-used shop-floor operation for a distributor: re-box / bundle / split packs **without** a
BOM or Work Order. A new `"Repack"` purpose on the ordinary Stock Entry:

- **Rows:** tick **Finished** on the rows the repack *produces*; every other row is *consumed*.
  At least one of each. Consumed rows go out at moving-average valuation; finished rows are valued
  at **consumed value + additional cost**. With several finished rows the pool is split by the
  per-row **value weight** (`qty × basic_rate`); if no weights are given, per unit of quantity.
- **Additional cost** (labour / freight) + its account — credited under perpetual inventory,
  exactly like the Manufacture operating cost.
- **GL:** Cr consumed inventory / Dr finished inventory / Cr additional-cost account. Nets to
  nothing when both sides share one inventory account and there's no additional cost.
- **Guard added to Manufacture:** a Manufacture entry now *asserts* exactly one finished row (its
  full-pool valuation is only correct for the single FG a Work Order finish emits).

**Manual test:** Stock → Stock Entries → New → purpose **Repack**. Consume From/Produce Into =
Main Store. Row 1: a bundle item, qty 1 (leave Finished unticked = consumed). Rows 2–3: two
component items, tick **Finished**, weights e.g. 700/300. Save → Submit → Stock Balance shows the
bundle −1 and the components +1 each, values split 70/30 of the bundle's valuation.

## 8. Manufacturing Settings — fast-follow

`GET/PUT /api/v1/manufacturing/settings`, UI at **Manufacturing → Setup → Manufacturing Settings**
(`/manufacturing-settings`). Four fields, stored as one `system_settings` JSON row per company:

| Field | Effect |
|---|---|
| Default source warehouse | Prefills new Work Orders (payload still overrides) |
| Default FG warehouse | Prefills new Work Orders; a WO no longer *requires* an explicit FG warehouse if this is set |
| Default WIP warehouse | Prefills the optional WIP step |
| Over-production allowance % | A Work Order may finish up to `qty × (1 + pct/100)` |

**Manual test:** set source+FG defaults, create a Work Order leaving both warehouse fields empty →
it resolves to the defaults. Set allowance 10%, make a 10-unit order → finishing 11 works,
finishing a 12th is blocked.

## 9. Material Shortage report — fast-follow

**Manufacturing → Reports → Material Shortage** (`/manufacturing-reports?tab=shortage`, API
`GET /manufacturing-reports/material-shortage?only_short=`). Aggregates the still-to-consume
demand (`required − consumed`) of **every open Work Order** per (component, consume-from
warehouse), nets it against on-hand stock, and lists the Work Orders that need each component.
Shortfall rows sort first; an *Only shortfalls* toggle filters the rest.

## 10. Safety guards — fast-follow

- **BOM recursion:** a BOM listing its own finished good as a component is rejected (422) at
  create/update — it would feed the FG's own valuation into its cost on every Update Cost.
- **Whole numbers:** Work Order create *and* finish reject fractional quantities when the finished
  good's stock UOM is flagged `must_be_whole_number` (Nos/Unit/Pair/Set/Box in the seed) — no more
  2.5 appliances.

## 11. Adversarial review hardening (2026-07-05)

A 22-agent adversarial review of the fast-follow diff confirmed 17 findings (12 unique);
**all were fixed the same day**:

| Fix | Detail |
|---|---|
| Negative-bin GL balance | Producing into a zero/negative bin resets the moving-average rate, so the finished rows' actual ledger movement can differ from the consumed pool. The residual now posts to **Stock Adjustment** (previously the voucher went out of balance and submit hard-failed). |
| Weight = `qty × basic_rate` | The Repack value weight is the row's **line-UOM** amount — using stock_qty double-counted the UOM conversion factor for pack-UOM rows. |
| Mixed weights rejected | Weighting some finished rows and leaving others blank would have booked the blank rows at **zero value**; now rejected at create *and* submit ("all weights or none"). |
| Expense-account enforcement | The operating/additional-cost account must be a real, enabled, non-group **Expense** account of the company — validated at Stock Entry create, Work Order create, and finish (blocks fabricated credits to Sales/GST/bank accounts via the manufacturing GL leg, and 500s/stuck drafts from bad ids). Cost > 0 also requires the account at create. |
| Settings reads sanitized | The generic `PUT /settings` can write arbitrary JSON to the `manufacturing_settings` key — every read now re-validates: warehouse ids must parse as UUIDs (else null), the over-production % is clamped to [0, 100]. |
| Settings-sourced warehouse errors | A stale/disabled default warehouse now fails Work Order creation with a message that names **Manufacturing Settings** (previously an opaque "Warehouse is disabled"). |
| Explicit-null source honored | Sending `source_warehouse_id: null` explicitly means "no order-level source" (per-BOM-row warehouses only); only an *omitted* field falls back to the settings default. |
| WIP transfer allowance | `transfer` now honours the same over-production allowance as finish (previously the extra raws could never reach WIP). |
| Actual values written back | After submit, Manufacture/Repack rows carry the **actual** moved values (rate/amount/total) instead of raw weights/estimates/zeros. |
| `operating_cost` bounded | Schema `le` bound keeps it inside `Numeric(21,6)` (was an unhandled 500 on overflow). |
| Frontend | Purpose switches reset per-row weight/Finished state (no silent leak into other purposes); Save is gated when an additional cost lacks its account; the Settings page surfaces load errors and blocks saving a blank form over stored values. |

## 12. Deliberately deferred (unchanged from the plan)

Serial/batch-tracked finished goods & components (blocked with a clear error in v1) · Job Cards /
Operations / Workstations / Routing / capacity scheduling · Production Plan (MRP) · multi-level BOM
explosion / sub-assemblies · subcontracting · BOM Creator / Update Tool / website BOM · Plant Floor ·
Downtime · a Manufacturing Settings doctype · scrap / by-products / process loss.
