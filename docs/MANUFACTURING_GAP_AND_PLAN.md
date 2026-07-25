# Manufacturing (MRP) — Gap Analysis & Build Plan

**Status:** 🟢 **Phase 8 done** (Planning Dashboard + SO/Quotation fulfillment check).
Phases 0–7 parity + USP planning engines complete. True finite-capacity APS remains out of scope.
**Designed:** 2026-07-20. **Phase 0–6 closed:** 2026-07-20/22. **Phase 7 closed:** 2026-07-22.
**Phase 8 closed:** 2026-07-22.

> **Scope:** bring OptiReach's Manufacturing module to ERPNext v15 "MRP-I" parity — BOM,
> Work Order, Job Card, Production Plan, Subcontracting, Quality gates — while deliberately
> simplifying the ceremony (§5) and deferring true APS/finite-capacity/forecast features to
> a later USP phase (§9, out of scope for parity). Modelled on the same
> plain-language-first → gap-matrix → phased structure as
> [ITR_GAP_AND_PLAN.md](ITR_GAP_AND_PLAN.md).

---

## 0. Plain-language summary (read this first)

"Manufacturing" in an ERP means: *turn raw materials into a finished good, and know exactly
what it cost.* Four ideas, usually confused with each other:

1. **Recipe (BOM)** — what it takes to make 1 (or N) units of a finished good: which
   components, how much of each, plus any flat labour/overhead cost. A BOM never moves
   stock or money by itself — it's a spec.
2. **Order to build (Work Order)** — "make 20 units of X, by BOM Y." This is the thing that
   actually consumes raw material stock and creates finished-good stock when you **Finish**
   it (optionally via a **WIP transfer** step first, if you want raws staged on the shop
   floor before consumption).
3. **Shop floor tracking (Job Card / Operations)** — if making X takes multiple *operations*
   (cut → weld → paint), each on a *workstation*, with its own time/labour — that's tracked
   separately from the Work Order so you know where in the process an order actually is.
   **OptiReach has this** when a BOM defines operations — Job Cards are created on Work
   Order submit; Finish without operations stays a single atomic step (Phase 2).
4. **Plan ahead (Production Plan)** — "I have these Sales Orders / forecasts, what Work
   Orders and raw-material purchases do I need to cover them?" **OptiReach doesn't have this
   yet** — Work Orders are created one at a time, and shortfalls are checked *after* creating
   one, not planned across many.

What people confuse this with (and what OptiReach already has elsewhere, so it isn't
rebuilt here):

| Nearby thing | What it is | Not the same as |
|---|---|---|
| **Stock Entry (Material Transfer/Receipt/Issue)** | Generic stock movement | A Work Order's Manufacture entry is a *purpose* of Stock Entry, driven by a BOM |
| **Repack Stock Entry** | BOM-less "consume these, produce those" (e.g. break a case into units) | Not tied to a Work Order/BOM; exists in the service layer today but has **no UI** |
| **Material Request** | "We need to buy/move this" | The Work Order shortfall flow raises one, but as a generic `Purchase` request — there's no dedicated `Manufacture` MR type yet (ERPNext has one) |
| **Item costing (valuation rate)** | Moving-average cost per unit already tracked by the Stock module | BOM costing *reads* this for its component rate estimate; Work Order Finish uses the *actual* consumed valuation, not the BOM estimate |
| **Purchase Order / subcontracting** | Buying finished goods, or sending materials to a job-work vendor | Subcontracting (Phase 4) is a distinct, not-yet-built flow |

**Stance:** the core "recipe → order → consume/produce → cost" loop is real and postable to
the GL today. Everything a shop floor supervisor or planner would recognize as "MRP" beyond
that single-level, single-step loop — routing, capacity, planning across orders, scrap,
subcontract, quality gates — is still ahead of us.

---

## 1. What a discrete manufacturer actually needs

| Area | What it is | Priority |
|---|---|---|
| **BOM (flat)** | Recipe + cost rollup for a finished good | **have** |
| **Work Order lifecycle** | Draft → Not Started → In Process → Completed/Stopped; Finish posts stock+GL | **have** |
| **WIP transfer (optional)** | Stage raws in a WIP warehouse before consuming | **have** |
| **Material availability + shortfall MR** | "Can I finish? If not, raise a purchase request" | **have (lean)** |
| **Correct GL/valuation on Manufacture/Repack** | Consumed cost pool + operating cost → FG value; cancel reverts | **have (just landed)** |
| **Serial/batch tracked manufacturing** | FG or components carry serials/batches through Finish | **must, blocked today** |
| **Multi-level BOM** | A component is itself a BOM (sub-assembly); explode N levels deep | **must** |
| **BOM scrap / by-product** | Expected wastage or secondary output per batch | **high** |
| **Phantom BOM** | A sub-assembly BOM that explodes straight through (never stocked on its own) | **medium** |
| **BOM Explorer** | Visualize/flatten a nested BOM | **medium** |
| **Operation / Workstation / Routing** | Named process steps, machines/benches, and their sequence per BOM | **must** (gates Job Card) |
| **Job Card** | Per-operation, per-Work-Order shop floor doc: start/stop, qty, time, labour | **must** |
| **Material Consumption entry** | Consume raws mid-process without finishing (separate from Finish) | **high** |
| **Production Plan** | Aggregate demand (Sales Orders / manual) → proposed Work Orders + Material Requests | **must** |
| **Subcontracting** | Send raws to a job-work vendor, receive back the finished/processed good | **high** |
| **Quality Inspection touchpoints** | Inline QC at Receipt / Delivery / Manufacture | **medium** (needs Quality module) |
| **Capacity (soft)** | Warn on Workstation overload at Work Order submit — not true finite scheduling | **medium** |
| **Reports** | Production Register, Material Shortage, BOM Where-Used, BOM Stock (**have**); BOM Explorer, WO Summary, Production Analytics (**absent**) | **medium** |
| **True APS / ATP / CTP / forecast / reverse scheduling** | Advanced planning — neither ERPNext nor OptiReach has this | **later, USP (§9)** |

---

## 2. What ERPNext v15 has (baseline)

ERPNext's Manufacturing module ("MRP-I", not a true APS) covers:

- **BOM** — multi-level, with child tables for Operations and Scrap Items; phantom BOM;
  alternative item substitution; "Update Cost" recursive recompute; BOM Explorer report.
- **Operation**, **Workstation**, **Routing** — masters describing shop-floor process steps,
  machines/benches (with hour-rate costing), and named step sequences reusable across BOMs.
- **Work Order** — created manually or from a Production Plan; carries Required Items
  *and* Operations (copied from the BOM/Routing); soft capacity check against Workstation
  load at submit (a warning, not a hard scheduler).
- **Job Card** — one per Work Order × Operation; tracks time logs, operator, qty completed,
  and (optionally) triggers Material Consumption / Transfer Stock Entries per operation.
- **Stock Entry purposes** — Material Transfer for Manufacture, Manufacture, Material
  Consumption for Manufacture, Repack, Send to Subcontractor — all reusing the same Stock
  Ledger/valuation/GL engine.
- **Subcontracting Order / Subcontracting Receipt** — a full PO-like ceremony: raise an SCO,
  transfer raw materials to the subcontractor, receive back the finished good with a
  Subcontracting Receipt that consumes the transferred materials + service cost.
  (Older ERPNext also has a Subcontract flag on a plain Purchase Order — deprecated path.)
- **Manufacturing Settings** — a proper Doctype (Single) with ~15 fields: default WIP/FG
  warehouses, backflush-from-WIP toggle, over-production %, capacity planning toggle,
  default FG valuation basis, etc.
- **Quality Inspection** — an optional gate wired into Stock Entry submission (and
  Purchase Receipt / Delivery Note) when an item is flagged "Inspection Required".
- **Reports** — BOM Explorer, BOM Stock Report, Work Order Summary, Production Analytics,
  Production Planning Report, Cost of Poor Quality, and others.
- **No true APS** — no automatic finite-capacity scheduling, no CTP (capable-to-promise)
  delivery-date engine, no statistical demand forecast, no reverse/backward scheduling
  solver. Production Plan is a demand-aggregation + WO-proposal tool, not a scheduler.

---

## 3. What OptiReach already has (current state, 2026-07-20)

Built in migration **`0065_manufacturing`** ([`backend/migrations/versions/0065_manufacturing.py`](../backend/migrations/versions/0065_manufacturing.py)):
`boms` / `bom_items`, `work_orders` / `work_order_items`, plus `work_order_id`,
`operating_cost`, `operating_cost_account_id` added to `stock_entries`. Company-scoped,
filtered explicitly in services (no RLS on these tables, same pattern as accounting/assets).

| Piece | File(s) | What it does today |
|---|---|---|
| **BOM service** | [`bom.py`](../backend/app/services/bom.py) | Flat recipe: components + flat operating cost → `raw_material_cost` / `total_cost` / `cost_per_unit`. Component rate defaults from live valuation ([`resolve_valuation_rate`](../backend/app/services/manufacturing_common.py)); "Update Cost" re-sources it on demand. Rejects a BOM containing its own FG. One default BOM per production item, enforced. Draft → Submit (`is_active`) → Cancel (blocked while a submitted Work Order still uses it) → Activate/Deactivate. |
| **Work Order service** | [`work_order.py`](../backend/app/services/work_order.py) | Create explodes BOM × qty into required items (Draft) → Submit (Not Started) → **Finish** builds+submits a `Manufacture` Stock Entry consuming required items proportionally and producing the FG, then advances `produced_qty`/`consumed_qty`/status → Stop/Resume → Cancel (blocked once anything produced/transferred — cancel the Stock Entries first, which auto-reverts the WO via `revert_manufacture_entry` / `revert_transfer_entry`). Over-production % allowance from Manufacturing Settings. Optional WIP transfer step (`transfer_for_manufacture`) when `skip_transfer=false`; lean default is `skip_transfer=true` (consume straight from source). |
| **Manufacture/Repack GL** | [`stock_entry.py`](../backend/app/services/stock_entry.py) `_submit_manufacture_or_repack` / `_post_manufacture_gl` | **Landed.** Consumes source rows at real SLE valuation, pools consumed value + operating cost, allocates the pool across finished rows (by value-weight if given, else by qty). Under perpetual inventory: credits each source warehouse's inventory account, debits each target's, credits the operating-cost **Expense** account (validated via `require_expense_account` — must be an enabled, non-group Expense account, e.g. *Expenses Included In Valuation*), and posts any rounding residual to the company's **Stock Adjustment** account. Cancel reverses SLE + GL and rolls the Work Order's produced/consumed/transferred qty back in the same transaction. |
| **Repack purpose** | [`stock_entry.py`](../backend/app/services/stock_entry.py) `create_stock_entry` / `_validate_repack_create` | BOM-less "consume N, produce M" with the same GL treatment as Manufacture. **Backend-only** — see §4, no frontend form exposes it yet (only `POST /stock-entries` with `purpose: "Repack"`). |
| **Material availability + shortfall MR** | `material_availability` / `create_shortfall_material_request` in [`work_order.py`](../backend/app/services/work_order.py) | Per-component on-hand vs pending-required, plus "can finish now" (min across components); one click raises a **`Purchase`**-type Material Request for every short component (no dedicated `Manufacture` MR type — see §4). |
| **Manufacturing Settings** | `MFG_SETTINGS_*` in [`manufacturing_common.py`](../backend/app/services/manufacturing_common.py) | A 4-field `SystemSetting` JSON blob (default source/WIP/FG warehouse, over-production %) — sanitized on every read, not a Doctype/migration. |
| **Reports (4)** | [`manufacturing_reports.py`](../backend/app/services/manufacturing_reports.py) | Production Register (planned vs produced + cost per WO), Material Shortage (aggregate demand across all open WOs vs stock), BOM Where-Used, BOM Stock Report (buildable qty for N units). |
| **Frontend** | [`BomView.vue`](../frontend/src/views/manufacturing/BomView.vue) / [`BomDetailView.vue`](../frontend/src/views/manufacturing/BomDetailView.vue) / [`WorkOrderView.vue`](../frontend/src/views/manufacturing/WorkOrderView.vue) / [`WorkOrderDetailView.vue`](../frontend/src/views/manufacturing/WorkOrderDetailView.vue) / [`ManufacturingReportsView.vue`](../frontend/src/views/manufacturing/ManufacturingReportsView.vue) / [`ManufacturingSettingsView.vue`](../frontend/src/views/manufacturing/ManufacturingSettingsView.vue) | Create/list BOM, create/list/detail Work Order with Finish/Transfer-to-WIP/Stop/Resume/Cancel actions and a live material-availability table + "raise MR for shortfall" button, the 4 reports, and the settings form. |
| **Blocked deliberately** | `_blocked_tracking` in [`work_order.py`](../backend/app/services/work_order.py) | A Work Order **cannot** be created if the production item *or any component* has `has_serial_no` / `has_batch_no` set — fails loudly rather than silently mishandling serial/batch allocation through Finish. |

**Honest gaps inside "Phase 0" itself** (not yet done, despite the core loop working):

- Serial/batch tracked manufacturing is **blocked**, not supported — see above.
- Manufacturing Settings is a lean 4-field blob, not the ~15-field ERPNext doctype (no
  backflush-from-WIP toggle, no default valuation-basis choice, no capacity-planning flag).
- No `default_bom_id` / manufacturing-related fields on the Item master yet.
- Material Request type `Manufacture` is used by the Work Order shortfall flow.
- **Repack has no frontend form** — [`StockEntryFormView.vue`](../frontend/src/views/stock/StockEntryFormView.vue)'s purpose dropdown only offers Material Receipt/Issue/Transfer; Repack must be created via the API directly today.

---

## 4. Gap matrix — Present / Partial / Absent

| Capability | ERPNext v15 | OptiReach today | Note |
|---|---|---|---|
| BOM (single level, cost rollup) | ✅ | 🟢 **Present** | [`bom.py`](../backend/app/services/bom.py) |
| BOM operations (child rows) | ✅ | 🟢 **Present** | time × hour_rate folded into `operating_cost` |
| BOM scrap items | ✅ | 🟢 **Present** | `bom_scrap_items`; recovery netted into cost; Finish produces scrap |
| Multi-level BOM explosion | ✅ | 🟢 **Present** | phantoms flatten; stocked sub-assemblies stay as one WO line |
| Phantom BOM | ✅ | 🟢 **Present** | `boms.is_phantom` |
| Alternate item substitution | ✅ | 🟢 **Present** | Finish `substitute_item_id` when `allow_alternative_item` + **Item Alternative** master |
| BOM Explorer report | ✅ | 🟢 **Present** | `GET /manufacturing-reports/bom-explorer` |
| Update BOM Cost | ✅ (recursive) | 🟢 **Present** | nested child BOM costs refreshed depth-first |
| Operation / Workstation / Routing masters | ✅ | 🟢 **Present** | descriptors at `/m/operation`, `/m/workstation`, `/m/routing` |
| Work Order lifecycle (Draft→…→Completed/Stopped) | ✅ | 🟢 **Present** | [`work_order.py`](../backend/app/services/work_order.py) |
| Work Order operations / capacity check | ✅ | 🟢 **Present** | ops copied from BOM; soft capacity warnings on submit |
| Job Card | ✅ | 🟢 **Present** | [`job_card.py`](../backend/app/services/job_card.py); time logs + qty |
| Material Transfer for Manufacture (WIP) | ✅ | 🟢 **Present** | optional, `skip_transfer` default true |
| Material Consumption for Manufacture (mid-process, non-Finish) | ✅ | 🟢 **Present** | `POST /work-orders/{id}/consume` |
| Manufacture Stock Entry (Finish) | ✅ | 🟢 **Present** | |
| Manufacture/Repack valuation + GL (consumed pool + operating cost) | ✅ | 🟢 **Present** | landed in [`stock_entry.py`](../backend/app/services/stock_entry.py) |
| Cancel Manufacture SE reverts Work Order qty | ✅ | 🟢 **Present** | `revert_manufacture_entry` / `revert_transfer_entry` / `revert_consumption_entry` |
| Repack Stock Entry | ✅ | 🟡 **Partial** | backend complete, **no frontend form** |
| Serial/batch tracked manufacturing | ✅ | 🔴 **Absent (blocked)** | `_blocked_tracking` fails loudly at WO create |
| Scrap / by-product output on Finish | ✅ | 🟢 **Present** | scaled from BOM scrap rows |
| Over-production % allowance | ✅ | 🟢 **Present** | Manufacturing Settings |
| Manufacturing Settings | ✅ (~15-field doctype) | 🟡 **Partial** | 4-field JSON blob |
| Material availability check | ✅ | 🟢 **Present** | per-component on-hand vs pending |
| Shortfall → Material Request | ✅ (`Manufacture` MR type) | 🟡 **Partial** | raises a generic `Purchase` MR, no dedicated type |
| Production Plan | ✅ | 🟢 **Present** | Phase 3 — `/production-plans` |
| Subcontracting Order / Receipt | ✅ | 🔴 **Absent** | Phase 4 (simplified — §5) |
| Quality Inspection touchpoints | ✅ | 🔴 **Absent** | Phase 5, needs Quality module |
| Production Register report | ✅ (Work Order Summary) | 🟢 **Present** | |
| Material Shortage report | ✅ | 🟢 **Present** | aggregate across open WOs |
| BOM Where-Used report | ✅ | 🟢 **Present** | |
| BOM Stock Report (buildable qty) | ✅ | 🟢 **Present** | |
| Production Analytics / Cost of Poor Quality reports | ✅ | 🔴 **Absent** | low priority |
| Soft capacity warning (Workstation load) | ✅ | 🟢 **Present** | gated by `capacity_planning_enabled` in settings |
| True finite-capacity scheduling / APS | ❌ (neither has it) | 🔴 **Absent** | Out of scope; soft board is 7.5 |
| Soft capacity board (WS load snapshot) | ❌ | 🟢 **Present (7.5)** | `GET /manufacturing-reports/capacity-board` |
| What-if CTP overrides | ❌ | 🟢 **Present (7.4)** | `POST /manufacturing-reports/what-if-ctp` |
| ATP / CTP delivery-date engine | ❌ | 🟢 **Present (7.0)** | `GET /manufacturing-reports/capable-to-promise` |
| Statistical demand forecast | ❌ | 🟢 **Present (7.3)** | `GET /manufacturing-reports/demand-forecast` (moving avg) |
| Reverse/backward scheduling | ❌ | 🟢 **Present (7.1)** | `GET /manufacturing-reports/reverse-schedule` + procurement order-by |

---

## 5. Design simplifications (commit to these)

OptiReach will reach the *same functional coverage* as ERPNext's MRP-I with **less
ceremony**. These are deliberate, permanent product decisions, not shortcuts to revisit:

1. **Subcontracting as one "Subcontract Job" (or a Work Order type) + Send Stock Entry +
   Receipt** — not ERPNext's dual Subcontracting Order + Subcontracting Receipt (or the
   even older PO-with-subcontract-flag ceremony). One document raises the job, one Stock
   Entry sends materials out, one receipt brings the finished good back and consumes them.
2. **Job Cards are optional** — only generated when a BOM actually has operations. A BOM
   with no operations behaves exactly like today: Finish is one atomic step.
3. **One Production Plan** to start, with a visual Planning Board layered on later — not a
   family of planning reports/tools from day one.
4. **Scrap as a BOM child row** (expected wastage qty + optional recovery value) — ERPNext
   v16's "secondary/co-product" concept on Work Order is deferred; a simple scrap child is
   enough for v1.
5. **Settings stay a `SystemSetting` JSON blob** — richer (closer to ERPNext's ~15 fields)
   but still not a new Doctype/migration, consistent with how GST/TDS/Assets settings work.
6. **Inline serial/batch** — no separate "Serial and Batch Bundle" document; serials/batches
   are picked directly on the Stock Entry line, same pattern already used elsewhere in Stock.
7. **Discrete / light-assembly manufacturing is the primary target.** Process manufacturing
   (byproducts, yield %, process loss accounting) is a later extension, not blocking v1.

---

## 6. Data model + machine-first classification (PROJECT.md §6)

| Artifact | Decision |
|---|---|
| **Operation** (name, default hour rate) | **Simple master → descriptor.** No logic. |
| **Workstation** (name, hour rate, working hours) | **Simple master → descriptor.** No logic. |
| **Routing** (named ordered list of Operations) | **Master + light validate → descriptor + hook** (unique operation sequence numbers). |
| **BOM Operation / BOM Scrap Item** (child rows on BOM) | Extend the existing **bespoke** BOM service — costing math changes (add operation time-cost + net off scrap recovery value). |
| **Multi-level BOM explosion** | **Bespoke** — recursive CTE over `bom_items` where a component's `item_id` has its own active/default BOM; cycle-guarded. |
| **Job Card** | **Transaction / heavy logic → bespoke service**; `docstatus` 0/1/2; time logs as a child table. |
| **Production Plan** (+ its proposed Work Orders / Material Request items) | **Bespoke** — it posts nothing itself but drives creation of Work Orders/MRs; `docstatus` 0/1/2 like any transaction so it can be cancelled cleanly. |
| **Subcontract Job** + **Send** / **Receipt** Stock Entry purposes | **Bespoke** — reuses the Stock Entry engine for the Send/Receipt legs (like Manufacture/Repack today), new lightweight header doc for the job itself. |
| **Manufacturing Settings** (richer blob) | Stays `SystemSetting` JSON — **not** a new master table. |
| **Quality Inspection** (linked from Manufacture Stock Entry) | Depends on the Quality module's own classification; Manufacturing side is just an optional gate check before submit. |

```
Operation / Workstation / Routing  ──►  BOM (+ operations, scrap)  ──►  Work Order (+ ops)
      (engine descriptors)                  (bespoke)                  │        │
                                                                        ▼        ▼
                                                                   Job Card   Stock Entry
                                                                   (bespoke)  (Manufacture/
                                                                              Repack/Consume/
                                                                              Send-to-Subcontractor)
Production Plan (bespoke) ──► proposes Work Orders + Material Requests (Manufacture type)
```

**Decision rule (unchanged from PROJECT.md):** posts GL / moves stock / computes a live cost
→ bespoke service; a name/rate list with no posting → descriptor; a field on an existing
master → column; a per-company toggle blob → `SystemSetting`.

---

## 7. Phased build plan

### Phase 0 — Correctness *(done)*
- ✅ Manufacture/Repack posts real GL: consumed cost pool + operating cost, cancel reverts.
- ✅ WIP transfer API + UI; material availability + shortfall MR; 4 reports; settings blob.
- ✅ Serial/batch on Finish / Transfer (pick serials/batches on consumed + FG rows).
- ✅ Item master: `default_bom_id`, `include_item_in_manufacturing` (migration `0069`).
- ✅ Material Request type `Manufacture` (shortfall raises this type).
- ✅ Repack frontend on Stock Entry form (Finished checkbox + operating cost).

### Phase 1 — BOM depth *(done)*
- ✅ Multi-level BOM explosion (cycle-guarded): phantoms flatten on WO create; stocked
  sub-assemblies stay as one required line; Explorer can flatten all levels to leaves.
- ✅ Phantom BOM flag (`boms.is_phantom`).
- ✅ BOM scrap child rows (`bom_scrap_items`) — recovery value netted into cost; Finish
  posts scrap as extra finished Stock Entry rows.
- ✅ Alternate item substitution on Finish (`allow_alternative_item` on BOM/WO item +
  `substitute_item_id` on the finish payload; allowed pairs from **Item Alternative**
  master at `/m/item-alternative`).
- ✅ BOM Explorer report (`GET /manufacturing-reports/bom-explorer`).

### Phase 2 — Shop floor *(done)*
- ✅ **Operation**, **Workstation**, **Routing** masters via descriptors (`0072_shop_floor`).
- ✅ BOM Operation child rows (time + hour-rate cost, added into `operating_cost`).
- ✅ **Job Card**: one per Work Order × Operation on submit; time logs, qty completed.
- ✅ Material Consumption for Manufacture Stock Entry purpose (consume raws mid-process,
  independent of Finish; Finish only consumes remaining pending qty).
- ✅ Soft capacity check: warn (not block) on Workstation overload at Work Order submit
  when `capacity_planning_enabled` is on.

### Phase 3 — Production Plan (MRP-I) *(done)*
- ✅ Aggregate demand from Sales Orders (and/or manual rows) across a date range.
- ✅ Propose Work Orders (respecting existing BOMs) and raw-material Material Requests
  (using the `Manufacture` MR type) for anything short.
- ✅ One Production Plan document (`docstatus` 0/1/2); a visual Planning Board is a later
  UI-only layer on the same data, not a new phase. Migration `0073_production_plan`.

### Phase 4 — Subcontracting *(done)*
- ✅ Subcontract Job referencing a BOM + vendor + supplier warehouse.
- ✅ Send Stock Entry (`Send to Subcontractor`) — materials source → supplier WH.
- ✅ Receipt (`Subcontract Receipt`) — FG in, consuming sent materials + service cost
  folded into FG valuation (same cost-pool path as Manufacture). Migration
  `0074_subcontracting`.

### Phase 5 — Quality gates *(done)*
- ✅ `Item.inspection_required` + lean Quality Inspection (Accepted/Rejected) against
  Work Order / Subcontract Job. Finish / Receive block until Accepted qty covers the
  batch. Migration `0075_quality_inspection`.

### Phase 6 — Parity polish + feature flag *(done)*
- ✅ Work Order Summary + Production Analytics reports.
- ✅ Per-company `module_flags.manufacturing` SystemSetting — hides Manufacturing from
  launcher + global nav when off (toggle on Manufacturing Settings).

### Phase 7+ — USP (net-new, beyond ERPNext parity)
- ✅ **Phase 7.0:** lead-time rollup + capable-to-promise estimate
  (`GET /manufacturing-reports/capable-to-promise`).
- ✅ **Phase 7.1:** reverse/backward schedule
  (`GET /manufacturing-reports/reverse-schedule`) + procurement order-by dates.
- ✅ **Phase 7.2:** demand → supply pegging timeline
  (`GET /manufacturing-reports/pegging`) — open SO demand vs stock / WOs / MRs + CTP
  for uncovered qty.
- ✅ **Phase 7.3:** light demand forecast
  (`GET /manufacturing-reports/demand-forecast`) — moving average of SO delivery qty
  over lookback → project horizon months.
- ✅ **Phase 7.4:** what-if CTP
  (`POST /manufacturing-reports/what-if-ctp`) — temporary extra stock / lead-time
  overrides; nothing persisted.
- ✅ **Phase 7.5:** soft capacity board
  (`GET /manufacturing-reports/capacity-board`) — open WO planned mins vs workstation
  daily hours (same soft model as submit warnings).
- ✅ **UI:** Manufacturing → Reports → **Planning (CTP / Forecast)** *(superseded by Phase 8)*.
- ✅ **Demo seed kit:** `--manufacturing-topup` also creates **FG-GEARBOX** (ops BOM),
  **RAW-ALUM** (lead 7d, ~5 on hand), **RAW-BOLT** (lead 3d, stocked), and an open
  Sales Order for 10× FG-GEARBOX.
- ⬜ Out of scope: true finite-capacity APS / detailed Gantt scheduling.

### Phase 8 — Planning Dashboard + order fulfillment *(done)*
- ✅ **Planning Dashboard** at `/manufacturing-planning` — context = Finished Item /
  Sales Order / Quotation / Production Plan; panels for Demand, Supply, Capacity,
  Timeline, Decision support (composes Phase 7 APIs).
- ✅ **Context resolver** `GET /manufacturing/planning/context`.
- ✅ **Order fulfillment check** (`order_fulfillment.py`) — CTP + BOM cost / margin for
  Quotation and Sales Order; endpoints `POST …/check-fulfillment` (+ preview).
- ✅ **Submit gate** via Manufacturing Settings `order_fulfillment_mode`:
  `off` | `warn` (default) | `block`.
- ✅ **Selling UI** — Check Fulfillment panel + deep-link to Planning Dashboard.
- ⬜ Still out of scope: finite APS / Gantt Planning Board.

---

## 8. Manual test guide (Phase 0)

All steps assume a logged-in session against a company with **perpetual inventory enabled**
and an Indian COA (so *Expenses Included In Valuation* and *Stock Adjustment* accounts
exist by default).

### Seed the test masters (do this first)

If `RAW-STEEL` / `FG-BRACKET` / warehouses are missing, run the idempotent manufacturing
top-up (safe to re-run):

```bash
docker compose exec backend python -m scripts.seed_demo --manufacturing-topup \
  --database-url "postgresql+asyncpg://erp_owner:milin@postgres:5432/erp" \
  --admin-email admin@example.com
```

That creates:

| Master | What you get |
|---|---|
| Warehouses | **Main Store** (source), **WIP Store**, **Finished Goods** |
| Items | **RAW-STEEL** (lead 5d, valuation ₹50), **FG-BRACKET** |
| Phase 7 kit | **FG-GEARBOX**, **RAW-ALUM** (lead 7d, ~5 stock), **RAW-BOLT** (lead 3d, stocked), Operation Machine @ CNC-1, open SO ×10 |
| Stock | 100 RAW-STEEL; CTP kit stock as above |
| BOM | FG-BRACKET flat; FG-GEARBOX with 96-min Machine op |
| Settings | Manufacturing defaults pointed at the three warehouses |

Log in as the seeded admin and continue from step 4 below (BOM already submitted).

### A) Happy path: BOM → Work Order → Finish (skip transfer)

1. **(Skip if seeded)** Receive stock: Stock → Stock Entries → New → purpose *Material Receipt* → target =
   Main Store → item `RAW-STEEL`, qty e.g. `100`, rate e.g. `50` → Save → Submit.
2. **(Skip if seeded)** Create a BOM: Manufacturing → Bill of Materials → New BOM → production item
   `FG-BRACKET`, batch quantity `1`, operating (labour) cost per batch e.g. `20`, one
   component row: `RAW-STEEL` qty `2` → Create draft → **Submit**.
3. Confirm on the BOM detail: raw material cost ₹100, total cost ₹120, cost/unit ₹120.
4. **Create a Work Order**: Manufacturing → Work Orders → New Work Order → pick the FG-BRACKET BOM,
   quantity to make `10`, Source = Main Store, Finished-goods = Finished Goods,
   **leave "Use WIP transfer step" unchecked**, operating
   cost account = *Expenses Included In Valuation* → Create draft.
5. Open the Work Order → **Submit** (status becomes "Not Started").
6. Check the **Required materials** table: `RAW-STEEL` required qty should be `20` (2 × 10).
7. Click **Finish…**, quantity to produce = `10`, confirm today's date → **Confirm &
   manufacture**. You should see a notice with the posted Stock Entry number and the
   Work Order status flips to "Completed", Produced = `10`.
8. **Verify FG valuation**: Stock → Stock Balance (or Items → `FG-BRACKET`) — on-hand qty
   `10` at Finished Goods, valuation rate should equal
   `(consumed RAW-STEEL value + operating cost) ÷ produced qty`. The Work Order scales
   BOM labour: `20 × 10 = ₹200` operating cost for the order. So
   `(20 × 50 + 200) ÷ 10` = **₹120/unit**, total value ₹1,200.
9. **Verify GL**: Accounting → Reports → General Ledger (or Trial Balance), filtered to
   the Manufacture Stock Entry's posting date. You should see: Main Store inventory
   **credited** ₹1,000 (20 units consumed at ₹50), Finished Goods inventory
   **debited** ₹1,200, and *Expenses Included In Valuation* **credited**
   ₹200 (the operating cost). Debits and credits should balance to zero net.

### B) Cancel Manufacture Stock Entry rolls back the Work Order

1. From the Work Order created in (A) (or a fresh partially-finished one), open the
   **Manufacture Stock Entry** it posted (Stock → Stock Entries, filter purpose
   *Manufacture*, or follow the link from the Work Order).
2. Click **Cancel** on the Stock Entry.
3. Go back to the Work Order — `produced_qty` and each component's `consumed_qty` should
   have dropped back by exactly what that entry produced/consumed, and status should have
   recomputed (e.g. back to "Not Started" if nothing else was produced).
4. Re-check Stock Balance and the General Ledger — both the stock and GL postings from
   that entry should be fully reversed (net zero for that voucher).

### C) WIP path: Work Order with "Use WIP transfer step"

1. Create a new Work Order as in (A) step 4, but **check "Use WIP transfer step"** and pick
   a WIP warehouse.
2. Submit it. Note the "Required materials" table now shows a **Transferred** column
   alongside Required/Consumed.
3. On the detail page, click **Transfer to WIP…**, enter the FG-equivalent quantity to
   transfer (e.g. `5` of the `10` ordered) → Confirm transfer. This posts a *Material
   Transfer for Manufacture* Stock Entry moving raws from Source → WIP.
4. Verify: Stock Balance shows the raws now sitting in the WIP warehouse, not Source; the
   Work Order's "Transferred to WIP" tile updates; status becomes "In Process".
5. Click **Finish…** for some quantity ≤ what's been transferred. Confirm the consumed
   rows on the resulting Manufacture entry pull from the **WIP** warehouse, not Source.
6. (Optional) Try clicking **Finish…** for more than the transferred amount — the backend
   should still let you finish the exact WIP holding (or reject it, if you try to exceed
   the over-production allowance) — either way, confirm the error/behavior is sensible
   rather than posting negative stock.

### D) Repack stock entry (backend-only today — no dedicated UI)

There is currently **no Repack option in the Stock Entry form** ([`StockEntryFormView.vue`](../frontend/src/views/stock/StockEntryFormView.vue)'s
purpose dropdown only lists Material Receipt/Issue/Transfer). Exercise it via the API
directly (e.g. the FastAPI docs at `/docs`, or `curl`/Postman) until Phase 0 adds a form:

1. `POST /api/v1/stock-entries` with `purpose: "Repack"`, one consumed row
   (`item_id` = some stocked item, `source_warehouse_id` set, `is_finished_item: false`)
   and one finished row (`item_id` = a different item, `target_warehouse_id` set,
   `is_finished_item: true`). Optionally set `operating_cost` +
   `operating_cost_account_id` (an Expense account).
2. `POST /api/v1/stock-entries/{id}/submit`.
3. Verify in Stock Balance: the consumed item's qty dropped at its source warehouse, the
   finished item's qty rose at its target warehouse, valued at
   `(consumed value + operating cost)`, split across finished rows by value-weight (or
   evenly by qty if no weight was given).
4. Verify GL the same way as (A) step 9 — same posting shape as Manufacture.

### E) Shortfall Material Request

1. Create a Work Order whose required raw-material quantity **exceeds** what's on hand
   (e.g. order `100` units of `FG-BRACKET` needing `200` of `RAW-STEEL` when only `20` are
   in stock). Submit it.
2. Open the Work Order detail page — the **Required materials** table should show a red
   **Shortfall** figure for `RAW-STEEL`, and a "Raise Material Request for shortfall" link
   should appear above the table.
3. Click it. You should get a notice with the new Material Request's number.
4. Open Stock → Material Requests and confirm a new request exists, type `Manufacture`,
   with a line for the shortfall quantity
   of `RAW-STEEL` at the expected source warehouse.

---

## 8a. Manual test guide (Phase 3 — Production Plan)

Assumes Phase 0–2 seed is available (`--manufacturing-topup`): **FG-BRACKET** BOM submitted,
**RAW-STEEL** in stock, warehouses set. You also need at least one **submitted Sales Order**
that orders a manufacturable item (has an active BOM) with a delivery date in range and
undelivered qty.

### Seed / prep

```bash
docker compose exec backend python -m scripts.seed_demo --manufacturing-topup \
  --database-url "postgresql+asyncpg://erp_owner:milin@postgres:5432/erp" \
  --admin-email admin@example.com
```

1. Confirm Manufacturing Settings has default FG + source warehouses.
2. Create a Customer (if needed) and a **Sales Order**:
   - Item = `FG-BRACKET` (or another item with a submitted default BOM)
   - Qty e.g. `5`
   - Delivery date = today (or within the plan window you will use)
   - **Submit** the Sales Order (status should be open: To Deliver / To Deliver and Bill)

### Happy path

1. **Manufacturing → Production Plan → New Production Plan**
   - Posting date = today
   - Demand from / to = a range that includes the Sales Order delivery date
   - FG / source warehouses = defaults (or pick explicitly)
   - **Create draft**
2. On the plan detail, click **Get items from Sales Orders**.
   - Finished-goods table should show `FG-BRACKET`, planned qty `5`, linked Sales Order name, and a BOM.
3. Click **Get raw materials**.
   - Raw material shortfalls table appears (e.g. `RAW-STEEL` required `10` if BOM is 2× per unit).
   - Shortfall = required − available (may be `0` if you have enough stock — then the shortfall table may be empty when only-shortfall mode is on; re-run after reducing stock, or leave empty and skip Create MR).
4. Click **Submit**. Status → Submitted.
5. Click **Create Work Orders**.
   - Notice lists the new WO name(s). Finished-goods row shows Ordered qty and an **Open** link to the Work Order.
6. If shortfalls existed, click **Create Material Requests**.
   - Opens a Manufacture-type Material Request for the shortfall lines.
7. Open the Work Order → Submit → Finish as in Phase 0 to complete the loop.

### Edge checks

| Check | Expected |
|---|---|
| Get items with no SO in date range | Finished-goods table empty (no error) |
| Item on SO with no BOM | Skipped (not listed) |
| Cancel after Create Work Orders | Blocked with error |
| Cancel before creating WOs/MRs | Allowed; status Cancelled |
| Create Work Orders twice | Second run creates nothing new (ordered already) |

---

## 8b. Manual test guide (Phase 4 — Subcontracting)

Assumes Phase 0 seed (`RAW-STEEL`, `FG-BRACKET` BOM, Main Store / Finished Goods). You need a
**Supplier** and a **supplier warehouse** (any leaf warehouse used as the job-work staging
location — create one named e.g. "Job Work" under Stock → Warehouses if you don't have it).

### Happy path

1. Ensure stock of `RAW-STEEL` in **Main Store** (enough for 2 × qty; e.g. qty `5` needs 10 RAW).
2. **Manufacturing → Subcontract Jobs → New Subcontract Job**
   - BOM = FG-BRACKET's submitted BOM
   - Supplier = any active supplier
   - Qty = `5`
   - Source = Main Store, Supplier warehouse = Job Work, FG = Finished Goods
   - Service cost = e.g. `500` (whole job), service cost account = *Expenses Included In Valuation*
   - **Create draft**
3. Confirm **Required materials** shows `RAW-STEEL` required `10`.
4. **Submit** → status **Open**.
5. **Send materials…** → qty `5` → Confirm send.
   - Notice shows a Stock Entry number (purpose *Send to Subcontractor*).
   - Status → **Materials Sent**; RAW-STEEL moves Main Store → Job Work.
6. **Receive FG…** → qty `5` → Confirm receive.
   - Stock Entry purpose *Subcontract Receipt*; FG-BRACKET lands in Finished Goods.
   - Status → **Completed**; RAW consumed from Job Work.
7. Check Stock Balance / valuation: FG rate ≈ (raw value consumed + service cost) / qty.

### Edge checks

| Check | Expected |
|---|---|
| Receive before Send | Error — send materials first |
| Receive more than sent | Error |
| Cancel job after Send | Blocked — cancel Stock Entries first |
| Cancel Send SE | Job sent_qty rolls back |

---

## 8c. Manual test guide (Phase 5 — Quality gates)

1. Open **Items → FG-BRACKET** → enable **Inspection required before manufacture / subcontract receipt** → Save.
2. Create & **Submit** a Work Order for FG-BRACKET qty `2` (skip WIP as usual).
3. Click **Finish** without a QI → expect error `ERR_QI_REQUIRED` (inspection covering at least 2).
4. **Manufacturing → Quality Inspections → New Inspection**
   - Reference type = Work Order
   - Reference ID = the Work Order's UUID (from the browser URL `/work-orders/<uuid>`)
   - Item = FG-BRACKET, Qty = `2`
   - Create draft → **Accept**
5. Finish the Work Order again → succeeds; produced qty = 2.
6. (Optional) Same gate on Subcontract Job **Receive**: Accept a QI against the Subcontract Job first.

### Edge checks

| Check | Expected |
|---|---|
| Inspection required off | Finish works without QI |
| Rejected QI only | Finish still blocked |
| Partial: Accepted qty `1` then Finish `2` | Blocked until Accepted covers produced+batch |

---

## 8d. Manual test guide (Phase 6 — reports + feature flag)

### Reports

1. Complete at least one Work Order (Phase 0 or 5).
2. **Manufacturing → Reports**
   - **Work Order Summary** — roll-up by status (count, qty, produced, cost).
   - **Production Analytics** — monthly completed qty/cost (needs `actual_end_date` on Completed WOs).

### Feature flag

1. **Manufacturing → Manufacturing Settings**
2. Uncheck **Show Manufacturing module in launcher and navigation** → Save.
3. Go **Home** — Manufacturing tile should be gone; global sidebar should omit Manufacturing.
4. Re-enable and Save — tile / nav return. (Direct URL `/manufacturing` still works; this is a UI gate.)

---

## 8e. Manual test guide — Phase 7 regression (all prior phases + CTP)

Use this as the single checklist when verifying Manufacturing after a deploy. Seed first:

```bash
docker compose exec backend python -m scripts.seed_demo --manufacturing-topup \
  --database-url "postgresql+asyncpg://erp_owner:milin@postgres:5432/erp" \
  --admin-email admin@example.com
```

Log in as admin. Prefer a company with perpetual inventory + India COA.

### Automated (CI / local)

```bash
# Unit (no DB)
cd backend && python -m pytest tests/unit/test_lead_time.py -q

# Integration (needs TEST_DATABASE_URL → erp_test)
# From host with Docker Postgres, or inside a container that has tests copied in:
pytest tests/integration/test_manufacturing.py tests/integration/test_manufacturing_phases.py -q
```

`test_manufacturing.py` = Phase 0–1. `test_manufacturing_phases.py` = Phase 2–7.0.

### Manual smoke (UI) — work through in order

| # | Phase | Steps | Pass if |
|---|---|---|---|
| 1 | 0 | BOM → WO → Finish 10 FG-BRACKET (skip WIP). Check Stock Balance FG @ ₹120/unit; GL balances. | Completed WO; stock + GL OK |
| 2 | 0 | Cancel the Manufacture SE → WO produced_qty back to 0. | SE cancelled; WO Not Started |
| 3 | 0 | New WO with **Use WIP transfer** → Transfer → Finish. Confirm Finish consumes from **WIP**, not Source. | WIP emptied; FG in FG WH |
| 4 | 0 | Shortfall WO → Raise MR → type **Manufacture**. | MR created for short qty |
| 5 | 1 | Phantom / multi-level BOM (or seeded Phase 1 BOMs) → WO required lines flatten phantoms. BOM Explorer flattens. | Leaves on WO; explorer OK |
| 6 | 2 | Operation + Workstation masters → BOM with operation → Submit WO → Job Cards appear → Start/Complete → optional Consume → Finish. | Job Card qty + Finish OK |
| 7 | 3 | Sales Order for FG → Production Plan → Get items → Get raw materials → Submit → Create WOs (and MRs if short). | WO linked; cancel blocked after |
| 8 | 4 | Subcontract Job → Submit → Send → Receive. FG valued at raws + service cost. | Status Completed; GL OK |
| 9 | 5 | Item `inspection_required` on → Finish blocked → Accept QI → Finish works. | ERR_QI_REQUIRED then success |
| 10 | 6 | Reports → Work Order Summary + Production Analytics. Settings → hide Manufacturing module → Home tile gone → re-enable. | Reports + flag OK |
| 11 | 7.0–7.5 | Seed top-up → Reports → **Planning (CTP / Forecast)** → pick **FG-GEARBOX** qty 10 → CTP, reverse (+21d), pegging, forecast, what-if (extra RAW-ALUM), capacity board. | Shortfall on RAW-ALUM; SO on pegging; what-if shortens promise; CNC-1 on board |

### Phase 7.0 CTP via Swagger / curl

1. Open `http://localhost:8000/docs` → authorize with Bearer token.
2. `GET /manufacturing-reports/capable-to-promise` with `item_id`, `qty`, optional `as_of`, `warehouse_id`.
3. Read `procurement_days`, `manufacturing_days`, `earliest_promise_date`, and `components[]` shortfalls.

Example (after login):

```bash
curl -s "http://localhost:8000/api/v1/manufacturing-reports/capable-to-promise?item_id=<FG_UUID>&qty=10&as_of=2026-07-01" \
  -H "Authorization: Bearer <token>"
```

### Known fix verified in Phase 7 testing

WIP-path Finish previously preferred each line's original source warehouse over WIP.
Finish / Consume / material-availability now consume from **WIP** when
`skip_transfer=false` (regression covered by `test_wip_transfer_then_finish`).

---

## 9. Decisions captured

1. **Correctness before breadth** — Phase 0 finishes the single-level loop's valuation/GL
   and unblocks serial/batch before any multi-level BOM or Job Card work starts.
2. **Simplify ceremony, not capability** — every ERPNext MRP-I capability in the gap matrix
   (§4) is planned; §5's simplifications change *how many documents* it takes, not *whether
   it's covered*.
3. **Settings stay JSON, masters stay descriptors** — Operation/Workstation/Routing need no
   bespoke code; Job Card/Production Plan/Subcontract Job do (they post stock/GL or drive
   other documents).
4. **True APS is out of scope for parity** — Phase 7+ is an explicit, separate USP track,
   not a parity gap; do not block Phases 1–6 on it.
5. **Build order:** Phase 0 (finish correctness) → 1 (BOM depth) → 2 (shop floor) →
   3 (Production Plan) → 4 (Subcontracting) → 5 (Quality) → 6 (polish/flag) → 7+ (USP).

---

## 10. Status

| Phase | Status |
|---|---|
| BOM (flat) + Work Order lifecycle + Finish | ✅ built |
| WIP transfer API + UI | ✅ built |
| Material availability + shortfall MR (as `Purchase`) | ✅ built (lean) |
| Manufacturing Settings (4-field blob) + 4 reports | ✅ built |
| Manufacture/Repack GL (consumed pool + operating cost + cancel-revert) | ✅ landed |
| Phase 0 (correctness + serial/batch + Item fields + MR Manufacture + Repack UI) | ✅ done |
| Phase 1 — BOM depth (multi-level, phantom, scrap, alternate, explorer) | ✅ done |
| Phase 2 — Shop floor (Operation/Workstation/Routing, Job Card, Material Consumption) | ✅ done |
| Phase 3 — Production Plan | ✅ done |
| Phase 4 — Subcontracting | ✅ done |
| Phase 5 — Quality gates | ✅ done |
| Phase 6 — Parity polish + feature flag | ✅ done |
| Phase 7.0 — Lead-time / CTP estimate | ✅ done |
| Phase 7.1 — Reverse schedule + procurement order-by dates | ✅ done |
| Phase 7.2 — Pegging timeline | ✅ done |
| Phase 7.3 — Light demand forecast | ✅ done |
| Phase 7.4 — What-if CTP | ✅ done |
| Phase 7.5 — Soft capacity board | ✅ done |
| True finite-capacity APS / Gantt | ⬜ out of scope |
