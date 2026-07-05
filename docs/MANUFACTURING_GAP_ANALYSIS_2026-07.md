# Manufacturing Module — Decision-Grade Gap Analysis

*OptiReach ERP · Lean Manufacturing (Phases 1–4)*
**Status:** Verified against ERPNext v15 source (`reference/erpnext/erpnext/manufacturing/**`) and cross-checked against a live ERPNext v15 instance (`rrd-dgn`). Dated 2026-07-05. Scoped to the target user: a ~20-person appliance distributor doing light assembly / kitting, not a multi-line factory.

---

## 1. Executive Summary

**Verdict: Fit-for-purpose today for the core kitting workflow, with four small, cheap gaps that should close before this is called "done," and one architectural gap (BOM-less Repack) that a distributor will hit in week one.**

Our lean build covers the load-bearing 80% an appliance assembler actually touches, and covers it *well*:

- **BOM** is a clean single-level recipe with real single-default enforcement, a valuation-rate cost snapshot, `Update Cost` on submitted BOMs, source-warehouse per line, and where-used / can-build reports.
- **Work Order** does make-N against a submitted BOM, explodes required items, runs a Draft→Not Started→In Process→Completed lifecycle with partial finishes, offers a genuinely useful live material-availability check (`can_finish_qty`) and a one-click shortfall Material Request that ERPNext doesn't even surface at that spot.
- **The posting core is correct.** The Manufacture Stock Entry reuses the Stock Ledger, moving-average valuation, and perpetual GL rather than re-implementing them; finished-good valuation faithfully mirrors ERPNext's `consumed cost + operating cost / finished qty`; operating (labour) cost is capitalised into FG value with a real, balanced GL credit; and cancel cleanly reverses SLE/GL and rolls back Work Order quantities. This is the highest-risk area and it is handled well — arguably more rigorously than base ERPNext's default.

**What we correctly threw away.** The overwhelming majority of ERPNext's ~40 manufacturing doctypes are factory/enterprise machinery that presupposes doctypes we deliberately never built: Operations, Workstations, Workstation Types, Routing, Sub Operations, Plant Floor, Job Cards (+ 5 child tables), Production Plan (+ **6** child tables / full MRP), Downtime Entry, the BOM Creator / Update Log / Update Batch background engine, multi-level BOM explosion, and ~13 of 21 reports. For a bench-assembly shop with no machines to schedule, no operation times to log, and no multi-tier sub-assembly tree, every one of these is ceremony. The collapse of the entire operations/routing/workstation apparatus into a single flat `operating_cost` field is the defining, correct simplification of this module.

**The real gaps** (all small, none structural):

1. **Repack (BOM-less conversion)** — the single most-used ERPNext shop-floor operation we *cannot express at all*. A distributor re-boxes, bundles, and splits packs daily without wanting a Work Order + BOM. Our valuation engine already does the exact Repack math; this is mostly plumbing. **Top priority.**
2. **Default warehouses / a minimal Settings singleton** — WIP/FG warehouses are re-keyed on every Work Order (FG is even *required* per-WO). A 5-field settings record removes daily friction.
3. **A cross-Work-Order material-shortage report** — "across everything I've committed to build, what components am I short?" We answer this per-BOM and per-WO but never in aggregate. This is the most-wanted missing report and reuses logic we already have.
4. **Two cheap safety guards** — reject a BOM listing its own FG as a component (`check_recursion`), and reject fractional qty for whole-number UOMs (you can currently book a Work Order to make 2.5 fridges).

**The one gap that punches above its "Build later" weight for an Indian MSME:** job-work / subcontracting. We skipped it entirely as "buying-side," which is architecturally right — but sending components to a fabricator is extremely common for a small appliance assembler, and it carries a **statutory GST obligation** (Rule 45 delivery challan, Form ITC-04, 1-year return rule) for which we have zero primitives. This should become a scoped Buying/Compliance build, not stay a blanket skip.

**Bottom line:** ship it. The core is production-grade. Close the four cheap gaps (Repack, default warehouses, shortage report, two guards) as a fast follow-up, and put a lean job-work/ITC-04 flow on the near-term roadmap for compliance reasons.

---

## 2. Coverage Matrix

Legend: ✅ Built · 🟡 Partial · ❌ Missing · ⬜ Deliberately Skipped

### 2.1 Doctypes (~40)

| ERPNext doctype | Status | Note |
|---|---|---|
| BOM | ✅ Built | Single-level recipe; `is_active`/`is_default` enforced; valuation-rate costing |
| BOM Item | ✅ Built | item/qty/uom/conv/stock_qty/rate/amount/source_warehouse; stock-item-only |
| BOM Explosion Item | ⬜ Skipped | No multi-level explosion |
| BOM Operation | ⬜ Skipped | No operations/routing; collapsed to flat `operating_cost` |
| BOM Scrap Item | ❌ Missing | No scrap/by-product output; disputed relevance (see §3) |
| BOM Website Item / BOM Website Operation | ⬜ Skipped | No integrated webshop |
| BOM Creator / BOM Creator Item | ⬜ Skipped | Visual multi-level tree builder — moot without nested BOMs |
| BOM Update Tool | 🟡 Partial | Single-BOM `update_bom_cost` exists; no all-BOMs sweep |
| BOM Update Log / BOM Update Batch | ⬜ Skipped | 7000-BOM background batch engine — enterprise scale only |
| Work Order | ✅ Built | make-N, lifecycle, availability check, shortfall MR |
| Work Order Item | 🟡 Partial | transferred/consumed tracked; no `returned_qty` |
| Work Order Operation | ⬜ Skipped | No named operations |
| Job Card (+ Time Log, Item, Scrap Item, Operation, Scheduled Time) | ⬜ Skipped | No shop-floor execution layer |
| Operation | ⬜ Skipped | No named steps |
| Sub Operation | ⬜ Skipped | — |
| Workstation | ⬜ Skipped | No machine/station modelling |
| Workstation Type | ⬜ Skipped | — |
| Workstation Working Hour | ⬜ Skipped | No capacity/holiday scheduling |
| Routing | ⬜ Skipped | Nothing to sequence |
| Plant Floor | ⬜ Skipped | No shop-floor dashboard |
| Production Plan (+ 6 child tables¹) | ⬜ Skipped | Full MRP engine; WOs created directly |
| Material Request Plan Item | ⬜ Skipped | Reuse existing Material Request |
| Downtime Entry | ⬜ Skipped | No workstations to track downtime against |
| Manufacturing Settings | ❌ Missing | No settings singleton; defaults hard-coded/per-doc |
| Subcontracting Order (+ Item, Service Item, Supplied Item) | ❌ Missing | No job-work order flow (4 doctypes) |
| Subcontracting Receipt (+ Item, Supplied Item) | ❌ Missing | No FG-back-from-job-worker receipt (3 doctypes) |
| Subcontracting BOM | ❌ Missing | Skip — convenience auto-fill master |
| Blanket Order | ✅ Built | Lives in Selling/Buying as agreed-rate contract (correct home) |
| Stock Entry — Manufacture | ✅ Built | Posting core; correct valuation + GL + cancel |
| Stock Entry — Material Transfer for Manufacture | ✅ Built | Optional WIP staging |
| Stock Entry — Repack | ❌ Missing | BOM-less conversion — genuine gap (see §3) |
| Stock Entry — Disassemble | ❌ Missing | Skip — manual Issue+Receipt covers it |
| Stock Entry — Material Consumption for Manufacture | ⬜ Skipped | Two-step consumption; partial-finish covers need |
| Stock Entry — Send to Subcontractor | ❌ Missing | Part of job-work gap |

¹ Production Plan children: `production_plan_item`, `production_plan_item_reference`, `production_plan_material_request`, `production_plan_material_request_warehouse`, `production_plan_sales_order`, `production_plan_sub_assembly_item`.

**Quality Inspection — attribution correction.** `Quality Inspection` and its `_template` / `_parameter` / `_parameter_group` / `_reading` children are **Stock-module** doctypes, not Manufacturing. They attach to Work Order / Job Card only through the `inspection_required` / `quality_inspection` link fields. **Status: ⬜ Skipped** (metrology/sampling engine over-engineered for this persona). A future lean pass/fail QC (see §3, Build later) would reuse the existing Stock doctype via those link fields, not introduce a new Manufacturing doctype.

### 2.2 Reports (21)

| ERPNext report | Status | Note |
|---|---|---|
| Work Order Summary | ✅ Built | production-register (no chart/grouping) |
| Open Work Orders | ✅ Built | production-register with status filter |
| Work Orders in Progress | 🟡 Partial | Filterable; no transferred-vs-produced columns |
| Completed Work Orders | ✅ Built | Filter + workspace card |
| Production Planning Report | ❌ Missing | Cross-WO material shortage — **build now** |
| BOM Stock Report | ✅ Built | bom-stock (required vs on-hand) |
| BOM Stock Calculated | ✅ Built | Merged into bom-stock (buildable_qty) |
| BOM Explorer | 🟡 Partial | Reverse (where-used) delivered via our bom-where-used; forward tree (ERPNext `bom_tree.js`) not built |
| BOM Operations Time | ⬜ Skipped | No routing/operation times |
| BOM Variance Report | ⬜ Skipped | BOM-basis backflush = zero variance |
| Issued Items Against Work Order | 🟡 Partial | Data exists; no dedicated report |
| Work Order Consumed Materials | 🟡 Partial | Data exists; no dedicated report |
| Work Order Stock Report | 🟡 Partial | bom-stock not scoped to WO pending qty |
| Production Analytics | 🟡 Partial | Fixed 12-mo trend; no interactive grouping |
| Production Plan Summary | ⬜ Skipped | No Production Plan |
| Exponential Smoothing Forecasting | ⬜ Skipped | Spreadsheet-forecast persona |
| Job Card Summary | ⬜ Skipped | No Job Card |
| Cost of Poor Quality | ⬜ Skipped | No Quality Inspection |
| Downtime Analysis | ⬜ Skipped | No Downtime Entry |
| Process Loss Report | ⬜ Skipped | No process-loss modelling |
| Quality Inspection Summary | ⬜ Skipped | No Quality Inspection |

*(ERPNext has no standalone "BOM Where Used" or "Completed Operation" report — where-used is delivered through BOM Explorer / `bom_tree.js`; both are folded into the rows above.)*

---

## 3. Prioritized Recommendations

### 🟢 BUILD NOW — real MSME gaps, cheap to close

> **✅ ALL FIVE SHIPPED (2026-07-05, same-day fast-follow)** — plus Risk #1's single-finished-row
> guard on Manufacture. See [MANUFACTURING_IMPLEMENTATION.md](MANUFACTURING_IMPLEMENTATION.md) §7–10.
> Verified: 14 manufacturing integration tests, 31 supply-chain regression, live-UI smoke (weighted
> multi-output Repack posted + value-conserved in the ledger).

| # | Item | MSME rationale | Effort |
|---|---|---|---|
| 1 | **Repack purpose on Stock Entry** (BOM-less conversion) | A distributor re-boxes / bundles / splits packs constantly and does *not* want a full BOM + Work Order for a re-box. Today there is *no way to express this* — the schema regex only permits Receipt/Issue/Transfer. Our Manufacture branch already computes exactly the Repack valuation (`consumed cost / FG qty`); this is mostly enabling a work-order-less, explicit-FG-row entry on code we already have. **Highest-value gap in the whole review.** | Low–Med |
| 2 | **Default WIP/FG warehouse (+ tiny Settings singleton)** | FG warehouse is *required on every Work Order* and WIP is re-keyed daily for identical repeat builds. A minimal 5-field settings record (`default_wip_warehouse`, `default_fg_warehouse`, `default_scrap_warehouse`, `overproduction_percentage_for_work_order`, `backflush_raw_materials_based_on`) removes constant friction. Don't build ERPNext's 18-flag singleton — build exactly this slice. | Low |
| 3 | **Cross-Work-Order material shortage report** (Production Planning Report, lean) | "Given everything I've committed to build, what components am I short?" is the report an assembler with several open builds most wants. We answer per-BOM and per-WO but never in aggregate. Buildable from existing WorkOrder + BOMItem + stock data. | Low–Med |
| 4 | **`check_recursion` guard** | One-line reject: a component's `item_id` cannot equal the BOM's `production_item_id`. Prevents a real footgun (FG as its own component feeds its own valuation into its cost at Update Cost time). | Trivial |
| 5 | **Whole-number qty guard on Work Order** | Appliances are discrete Nos; today you can create a WO to make 2.5 air-coolers and finish fractional qty. Validate `qty` against the item's `must_be_whole_number` UOM flag. | Trivial |

### 🟡 BUILD LATER — genuine value, not launch-critical

| Item | MSME rationale |
|---|---|
| **Job-work / subcontracting flow + ITC-04** (Buying/Compliance, not Manufacturing core) | *Punches above "later."* Sending components to a fabricator is common for small appliance assemblers and is a **statutory GST obligation** (Rule 45 delivery challan, Form ITC-04, 1-year return). We have zero primitives. Build the *lean* version — challan-out of raws to a job-worker warehouse + a service-cost receipt reusing our Manufacture valuation + a "goods with job-worker" aging report — **not** ERPNext's 8-doctype backflush machinery. |
| **`Closed` Work Order status + Close action** | Without it, a WO where you built 8 of 10 and abandoned the rest sits "In Process" forever, skewing open-WO reports. Pairs cheaply with a "block further finish" guard. |
| **Serial/batch finished goods** | Appliances carry serials for warranty. We currently *block loudly* at WO creation (correct interim), but a distributor serialising assembled units will hit this wall. Depends on stock-module serial support. |
| **Open-PO / reserved-qty netting in `material_availability`** | Today shortfall math nets only against raw on-hand at one warehouse — it ignores already-ordered qty and reserved-for-production, so two concurrent WOs each "see" full stock (double-count) and a shortfall MR can over-order when a PO is in flight. Add open-PO/open-MR netting. |
| **Scrap / by-product output** *(disputed — see below)* | Adds `scrap_material_cost` credit to `total_cost` and a scrap output row on the Manufacture entry. Resolved to **Build later, Medium**. |
| **`additional_operating_cost` at finish** | The one genuinely useful ERPNext cost field for an MSME: "this batch had ₹500 extra outsourced work." Cheap — an optional amount on the finish payload folded into the Manufacture entry's operating cost. |
| **Bulk "Update Cost across all BOMs" button** | Component prices move; stale BOM costs mislead margins/quoting. For dozens of flat BOMs this is a trivial synchronous loop over the existing `update_bom_cost` — **no** level-wise background batch engine. |
| **`rm_cost_as_per` = Last Purchase Rate toggle** | Reasonable future option for volatile-priced imported components. Price-List mode stays skipped. |
| **Auto-promote sole BOM to default** | Small UX nicety so a Work Order default-BOM lookup never finds zero defaults. |
| **BOM versioning / `amended_from`** | MSMEs revise recipes; per-item versioned names + amendment lineage give "which BOM version made which batch" traceability. Not urgent. |
| **WO-status donut chart + "This month produced" card** | Cheap, high-signal additions on data we already aggregate. |
| **Lean pass/fail Quality Inspection + `inspection_required` flag** | A simple "QC passed?" gate on the assembled FG (powers on / no damage / accessories present) with a soft *warn* on posting. **Reuses the existing Stock-module Quality Inspection doctype via the `inspection_required`/`quality_inspection` link fields** — *not* the template/parameter/10-reading metrology engine, and not a new Manufacturing doctype. |
| **Non-stock component cost lines** | Appliance kits include packaging/consumables; for now lumped into `operating_cost`, but allowing non-stock cost lines is a natural enhancement. |

### 🔴 SKIP — factory/enterprise ceremony

Operations / Workstations / Workstation Types / Workstation Working Hours / Routing / Sub Operations / Plant Floor · Job Card + all 5 child tables + capacity scheduling / overlap detection / corrective job cards · Multi-level BOM explosion / exploded_items / BOM Creator / BOM Update Log + Batch background engine · Production Plan / full MRP / demand-pull-from-SO-MR / sub-assembly routing / reserved-qty CSV · Exponential Smoothing Forecasting / MPS / Sales Forecast · Downtime Entry & OEE · Quality Inspection template/parameter/reading metrology engine (Stock module) · Subcontracting BOM master / dual-mode backflush / auto-chain settings · BOM website doctypes · Disassemble · Material Consumption for Manufacture · Send-to-WIP `from_wip_warehouse` toggle · `allow_alternative_item` / Item Alternative · `has_variants` / make_variant_bom · Process loss % / qty · Pick List · Make-BOM-from-WO · Multi-currency BOM (`conversion_rate` / `base_*`) · Price-List BOM costing.

*Common thread:* every item here presupposes a factory floor, machines-as-constrained-resources, multi-tier sub-assembly trees, or 10k+ BOM scale — none of which a 20-person bench-assembly kitter exercises. These are correctly and defensibly out of scope.

**Disagreement resolved — Scrap items.** The BOM-advanced review rated this *High / Build now* (appliance assembly/repacking yields saleable cartons, damaged sub-units, metal offcuts that should be valued and credited); the BOM-core and Manufacture-Stock-Entry reviews rated it *Low / Skip* (discrete kitting rarely produces valued by-products; waste is expensed, not stocked). **Resolution: Build later, Medium.** The economic argument (FG over-absorbs cost when scrap isn't credited) is real, but two of three reviewers judged the frequency low for this persona, and the fix is modest. It is not a launch blocker but should not be a permanent skip. When built, it must ship *with* the `total_cost = raw + operating − scrap` formula fix and the extra valued target row (see Risk #1).

---

## 4. Risks & Divergences

> **✅ Update (2026-07-05):** Risks #1–#3 and #5–#6 below are FIXED (fast-follow + a same-day
> 22-agent adversarial review of that diff, which confirmed 12 further unique findings — all fixed;
> see [MANUFACTURING_IMPLEMENTATION.md](MANUFACTURING_IMPLEMENTATION.md) §11). #4 and #7–#13 remain
> documented keep-as-is / build-later divergences.

Ordered by severity.

| # | Risk | Detail & trigger | Recommendation |
|---|---|---|---|
| 1 | **Latent multi-target Manufacture over-valuation** | The submit branch values *every* target-warehouse row at the **full** `consumed + operating` total. Today masked because `finish_work_order` always emits exactly one FG row. The moment scrap/by-product or multi-FG Repack is added, a two-target entry silently double-counts valuation. | **Guard now:** assert single target row, or split the cost pool correctly — make the invariant explicit before it's exercised. |
| 2 | **No `check_recursion`** | A user can add the FG itself as a component; at Update Cost time this feeds the FG's own valuation into its cost. Data-integrity hole. | Build now (§3.4). |
| 3 | **Fractional Work Order qty** | `qty` is `Decimal(gt=0)` with no whole-number check → can book/finish 2.5 discrete appliances. | Build now (§3.5). |
| 4 | **`produced_qty` mutated imperatively, not recomputed from entries** | We increment/decrement counters rather than deriving from submitted Manufacture entries (ERPNext's source-of-truth model). If a Manufacture entry is ever cancelled by a path that bypasses `revert_manufacture_entry`, counters drift. Cancel *is* currently routed correctly. | Keep as-is; consider a recompute-from-entries helper for robustness. |
| 5 | **Operating-cost account not type-validated** | Any account passed is accepted for the labour credit (GL still balances). A user could capitalise labour by crediting a bank or balance-sheet account, distorting the P&L. | Low-risk hardening: validate account root/type is an expense/valuation account. |
| 6 | **Stopped WO can be cancelled directly** | ERPNext forces unstop-before-cancel. Minor lifecycle divergence; a Stopped order with nothing produced can be cancelled. | Add a Stopped guard to cancel for parity; low real-world impact. |
| 7 | **`BOMItem.rate` semantics differ** | Ours = per **stock** unit; ERPNext = per **BOM-item UOM** (`valuation × conversion_factor`). **Amounts and totals match**; only the displayed per-line rate diverges when `conversion_factor ≠ 1`. Appliance components are almost always Nos (factor = 1), so they coincide. | Keep as-is; **document the convention.** Revisit only if pack-UOM components appear. |
| 8 | **`skip_transfer` default flipped to `True`** | Deliberate lean choice (consume from source at finish). Behaviourally correct, but numbers/behaviour won't match a stock ERPNext export 1:1. | Keep as-is (documented divergence). |
| 9 | **Flat `operating_cost` vs ERPNext's planned/actual/additional/corrective split** | Intentional and correct for kitting, but labour figures won't map 1:1 to an ERPNext Work Order, and there is no planned-vs-actual variance. | Keep as-is; state as known divergence. |
| 10 | **`resolve_valuation_rate` skips the last-SLE fallback** | For an out-of-stock component with recent purchase history, we drop straight to item-master rate instead of the last SLE valuation (ERPNext's step 2). Slightly less accurate cost snapshots; excluding zero/negative bins also shifts the average marginally. | Build later — add the last-SLE fallback for accuracy. |
| 11 | **Silent zero-value consumption** | A component with no ledger value is consumed at zero with no warning (ERPNext raises unless `allow_zero_valuation_rate` is set). FG then absorbs only operating cost. | Keep as-is; a future "component has no valuation" warning would be a nice safety net. |
| 12 | **Operating-cost rounding drift across partial finishes** | Each partial finish quantizes to 6dp; the sum can drift sub-paise from planned. Economically immaterial. | Keep as-is. |
| 13 | **`is_default` no auto-promote; no `Item.default_bom`** | ERPNext auto-promotes the sole BOM to default; we don't, so a WO default-BOM lookup can find zero defaults until the user ticks it. | Build later (UX polish). |

**No risky correctness divergence exists in the core finish/cancel path** — the highest-risk lifecycle area (Manufacture valuation, perpetual GL, cancel/rollback) is sound. Risk #1 is the one sharp edge to neutralise proactively; risks #2 and #3 are the two cheap guards already in Build Now.