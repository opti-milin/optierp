# Manufacturing Module — Build Plan (ERPNext-style, MSME-lean)

**Scope:** a new top-level **Manufacturing** module for OptiReach ERP, modelled on ERPNext v15
Manufacturing (https://docs.frappe.io/erpnext/manufacturing + `reference/erpnext/erpnext/manufacturing`),
with MSME simplifications for a single appliance distributor that does **light assembly / kitting**
(build a finished SKU from components + a little labour), not a multi-line factory.
**Status:** ✅ **BUILT (2026-07-05)** — lean core + convenience + reports (Phases 1–4) shipped on branch
`feat/manufacturing` (migration `0065_manufacturing`). See
[MANUFACTURING_IMPLEMENTATION.md](MANUFACTURING_IMPLEMENTATION.md) for exactly what landed + manual test
steps. Owner confirmed the business does real assembly into stock **and** sell-time bundles, so
Manufacturing (BOM → Work Order → Manufacture) was built *and* Product Bundle kept.
**Designed:** 2026-06-23. **Built:** 2026-07-05.

> Same house rules as Assets ([ASSETS_GAP_AND_PLAN.md](ASSETS_GAP_AND_PLAN.md)): plain-language first;
> **copy the value, drop the ceremony**; masters that are pure config are **engine-served**
> (`DocTypeDescriptor`), anything that **moves stock or posts to the GL** is a **bespoke service** that
> *reuses* the existing stock ledger / valuation / GL — no new posting engine.

---

## 0. Plain-language summary (read this first)

Manufacturing answers one question: *"to make X units of a finished product, what raw materials and
work does it take — and when I make it, reduce the raw stock and add the finished stock at the right
cost."*

Two core ideas:
- A **BOM (Bill of Materials)** is the **recipe**: "1 Air-Cooler Combo = 1 cooler + 1 stand + 1 remote +
  ₹200 assembly labour." It's a master; it doesn't touch stock or money by itself.
- A **Work Order** is the **act of making it**: "make 10 Air-Cooler Combos." When you finish it, the system
  **consumes** 10 coolers + 10 stands + 10 remotes from the warehouse and **produces** 10 combos into
  stock, valuing each combo at *(components consumed + labour) ÷ 10*.

That's the whole loop for an MSME assembler: **recipe → make → stock moves at the right cost.** Everything
else ERPNext offers (shop-floor job cards, workstation capacity, MRP production planning, multi-level
auto-explosion, subcontracting, downtime, plant-floor dashboards) is factory-grade machinery we can skip
until there's a real need.

**Where we stand:** the foundation already exists — **Items**, **Warehouses**, the **Stock Ledger** with
**Moving-Average valuation**, **Stock Entry** (Material Receipt/Issue/Transfer), **Material Request**, and
GL posting from stock. So Manufacturing is mostly: a **BOM** document + a **Work Order** document + one new
**"Manufacture"** Stock-Entry behaviour (consume raws, produce FG at input cost) that *reuses* the ledger
we already have. No new valuation engine, no new GL.

---

## 1. What ERPNext provides

ERPNext Manufacturing ships **~25 DocTypes**. Grouped:

| Group | DocTypes | Purpose |
|---|---|---|
| **BOM** | BOM, BOM Item, BOM Operation, BOM Scrap Item, BOM Explosion Item | The recipe: components, operations (labour), scrap/by-products, costing, multi-level explosion |
| **BOM tools** | BOM Creator (+Item), BOM Update Tool / Log / Batch, BOM Website Item/Operation | Bulk-build BOMs, replace a component across all BOMs, publish BOMs to a website |
| **Work Order** | Work Order, Work Order Item, Work Order Operation | Make N of an item per a BOM; track transferred/produced qty; operations |
| **Job Card** | Job Card (+ Item, Operation, Time Log, Scheduled Time, Scrap Item) | Shop-floor execution of each operation: who, which workstation, time logs, completed qty |
| **Shop floor** | Operation, Sub Operation, Workstation, Workstation Type, Workstation Working Hour, Plant Floor | Operation definitions + machines + capacity + hour rates + a plant-floor dashboard |
| **Routing** | Routing | A reusable ordered list of operations to attach to a BOM |
| **Production Plan** | Production Plan (+ 7 child tables) | MRP: from sales orders / forecasts, plan what to make, auto-raise Work Orders + Material Requests, sub-assembly planning |
| **Other** | Downtime Entry, Manufacturing Settings, Blanket Order (shared w/ Selling) | Machine downtime, global settings |

**The lifecycle ERPNext supports:** BOM → (Production Plan) → Work Order → Material Transfer for
Manufacture (move raws to a WIP warehouse) → Job Cards per operation → Manufacture (consume raws, produce
FG) → stock + GL updated. Plus multi-level BOM explosion, scrap/by-products, process loss, subcontracted
operations, capacity scheduling.

---

## 2. Where ERPNext is over-engineered → how we simplify

POV test: *can a 20-person appliance distributor assemble combo SKUs and get correct stock + costing,
without a Frappe consultant or a factory MES?*

| ERPNext feature | Decision |
|---|---|
| **BOM** (components + costing) | **BUILD** — the recipe. Single-level for v1. |
| **BOM operations + Workstations + Operations + Routing + hour rates** | **Simplify:** one flat **operating cost** field on the BOM/Work Order (e.g. "₹200 labour/unit") folded into FG cost. Drop the Operation/Workstation/Routing masters. |
| **Work Order** (make N) | **BUILD** — the core. |
| **Manufacture / Material-Transfer-for-Manufacture stock entries** | **BUILD** one new "Manufacture" Stock-Entry behaviour (consume raws + produce FG at input cost). WIP transfer step **optional** (default: consume straight from the source warehouse on finish — `skip_transfer`). |
| **Job Card** (shop-floor per-operation tracking, time logs) | **SKIP** — no factory floor; assembly is a single step. |
| **Production Plan** (MRP from sales orders/forecast → auto Work Orders + Material Requests) | **SKIP** v1 — create Work Orders directly; for shortfalls, reuse the existing **Material Request**. |
| **Multi-level BOM explosion / sub-assemblies** | **SKIP** v1 — single-level. A component can *reference* a sub-BOM, but no auto-explosion. (Optional later.) |
| **Scrap items / by-products / process loss** | **OPTIONAL** — a scrap output line on the Manufacture entry (valued), only if asked. |
| **Subcontracting** (send raws to a vendor to make) | **SKIP** — separate buying-side concern. |
| **BOM Creator / Update Tool / Website BOM / Plant Floor / Downtime / capacity scheduling** | **SKIP** — pure factory/enterprise ceremony. |
| **Manufacturing Settings** (dozens of flags) | **Simplify** — a couple of sensible defaults in code (e.g. default WIP/FG warehouse), no settings doctype. |

Everything marked SKIP is **explicitly out of scope** unless a real requirement reverses it.

> **Note vs Product Bundle (we already have one):** a *Product Bundle* is a **sell-time kit** — it explodes
> into lines on a Sales Invoice but is **not** stocked or manufactured. A *BOM + Work Order* actually
> **builds and stocks** a new finished SKU at a computed cost. Different tools; we keep both.

---

## 3. Data model (engine-served vs bespoke)

**Bespoke documents** (hand-written services — they move stock / compute cost):
- **BOM** — `production_item_id` (the FG), `uom`, `quantity` (batch the recipe yields, usually 1),
  `is_active`, `is_default`, `operating_cost` (flat labour/overhead per batch, optional),
  `raw_material_cost` / `total_cost` (computed), `currency`. Child **BOM Item**: `item_id`, `qty`, `uom`,
  `rate` (sourced from item valuation/last-purchase), `amount`, optional `source_warehouse_id`, optional
  `sub_bom_id`. *(No GL; bespoke because of the costing rollup + it's the spec Work Orders consume.)*
- **Work Order** — `name` (series `MFG-WO-.YYYY.-`), `production_item_id`, `bom_id`, `qty`,
  `source_warehouse_id`, `fg_warehouse_id`, optional `wip_warehouse_id`, `status`
  (Draft | Not Started | In Process | Completed | Stopped | Cancelled), `produced_qty`,
  `material_transferred_qty`, `operating_cost`, planned/actual dates, optional `sales_order_id`. Child
  **Work Order Item**: `item_id`, `required_qty`, `transferred_qty`, `consumed_qty`, `source_warehouse_id`
  (exploded from the BOM × qty at creation).
- **Manufacture Stock Entry** — **not a new doctype**: add a `"Manufacture"` purpose (+ optional
  `"Material Transfer for Manufacture"`) to the existing **Stock Entry**, with a `work_order_id` link.

**Engine masters:** none required for the lean set (Operation/Workstation are skipped). If operations are
revived later, **Operation** and **Workstation** become simple engine masters.

**Decision rule (same as accounting/assets):** moves stock or posts GL → bespoke service; pure config →
engine descriptor; read-only summary → endpoint.

---

## 4. The manufacturing mechanics (the heart)

### BOM costing (no GL — just numbers)
`raw_material_cost = Σ (component.qty × component.rate)`, where `rate` is sourced from the component item's
current valuation rate (fallback last-purchase / standard). `total_cost = raw_material_cost +
operating_cost` (the flat labour). `cost_per_unit = total_cost / quantity`. Computed on save (snapshot) and
re-computable on demand. This is the *estimated* cost; the **actual** cost is captured at manufacture time
from real consumed valuation.

### Work Order lifecycle
1. **Create** from a BOM + a target qty → explode the BOM components × qty into **required items**
   (status Draft → Submit → *Not Started*).
2. *(Optional)* **Transfer for manufacture** — a Material-Transfer stock entry moving raws source → WIP
   warehouse (status → *In Process*). Lean default: **skip** this and consume on finish.
3. **Finish** (make `n` units, `n ≤ qty`) → post a **Manufacture stock entry**:
   - **consume** each required item (`required_qty × n/qty`) out of the source/WIP warehouse at its
     current valuation (outgoing value), and
   - **produce** `n` FG units into the FG warehouse with **incoming rate =
     (Σ consumed value + operating_cost × n/qty) ÷ n** — so the finished good is valued at exactly what
     went into it.
   - update `produced_qty`; status → *In Process* (partial) or *Completed* (full).

### Manufacture Stock Entry (reuses the stock ledger + valuation we already have)
The existing Stock Entry already issues at outgoing valuation and receives at a given incoming rate, writes
Stock Ledger Entries, maintains Bin valuation (Moving Average), and posts GL. The new `"Manufacture"`
purpose is just: rows with a **source** warehouse = consumed raws; rows with a **target** warehouse = the FG,
whose incoming rate is derived from the consumed value + operating cost. **No new valuation/GL code** — the
ledger does it.

---

## 5. Stock & GL integration (reuse what exists — no new accounts/engine)

| Event | Stock | GL (via the Stock Entry, when perpetual inventory is on) |
|---|---|---|
| **Manufacture** | − raws (source whse), + FG (fg whse) at input cost | Dr Finished-Goods inventory / Cr Raw-Material inventory (+ Cr a labour/expense account for `operating_cost`) — through the existing stock-entry GL path |
| **Transfer for manufacture** *(optional)* | move raws source → WIP | none (intra-company transfer, like a normal Material Transfer) |

All postings go through the existing **Stock Entry → Stock Ledger → GL** path. Manufacturing *drives* it;
it never re-implements valuation or GL (same principle as Subscription driving Sales Invoice, and Assets
driving the JE).

---

## 6. Phased build plan

### Phase 1 — BOM *(the recipe; gating)*
- **BOM** bespoke doc + **BOM Item** child: create/activate; cost rollup (raw material + flat operating →
  total + per-unit). Rates sourced from item valuation. Mark `is_default` per FG.
- Frontend: BOM list + detail (component grid + live cost), reachable from the Item.
- *Acceptance:* a BOM "1 Combo = 1 cooler @₹4,000 + 1 stand @₹500 + ₹200 labour" shows raw cost ₹4,500,
  total ₹4,700, per-unit ₹4,700.

### Phase 2 — Work Order + Manufacture *(the core)*
- **Work Order** bespoke doc + **Work Order Item** child: create from a BOM × qty (explode required items),
  submit, **Finish** → Manufacture stock entry (consume raws, produce FG at input cost), status lifecycle,
  produced-qty tracking, partial finishes.
- Add the `"Manufacture"` purpose to **Stock Entry** (consume + produce + FG valuation + GL).
- Frontend: Work Order list + detail (required items, produced qty, Finish action) + a new Manufacturing
  module shell (launcher tile + sidebar).
- *Acceptance:* a Work Order for 10 Combos consumes 10 coolers + 10 stands, produces 10 Combos valued at
  ₹4,700 each; Stock Balance + Stock Ledger + GL all reconcile; blocking when raw stock is short.

### Phase 3 — Convenience & costing polish
- **Material availability** check on the Work Order (shortfall vs on-hand) + one-click **Material Request**
  for shortfalls (reuse the existing MR).
- *(Optional)* **WIP transfer** step (`skip_transfer` default on), **scrap/by-product** output line,
  **operating cost** breakdown.

### Phase 4 — Reports
- **Production Register** (work orders: planned vs produced, cost), **BOM where-used / BOM stock report**
  (which BOMs use an item; can I build N of a FG given current stock) — read-only endpoints + a Reports tab.

### Deliberately deferred (revisit only on a real need)
- Job Cards / Operations / Workstations / Routing / capacity scheduling · Production Plan (MRP) ·
  multi-level BOM explosion & sub-assemblies · subcontracting · BOM Creator / Update Tool / website BOM ·
  Plant Floor · Downtime · Manufacturing Settings doctype.

---

## 7. Decisions captured (owner, 2026-06-23)

1. **Use case = BOTH.** The business *does* assemble SKUs into stock **and** uses sell-time bundles. →
   **Build the Manufacturing module** (BOM → Work Order → Manufacture) for the real assembly, and **keep
   Product Bundle** for sell-time kits. The two coexist (see the note in §2).
2. **Scope (when we build) = "Just the plan for now."** Plan reviewed/approved as the design; **build is
   on hold** until the owner says go. When greenlit, default to the **lean core** (Phases 1–2) then
   availability + reports (Phases 3–4); named operations / Job Cards / MRP stay deferred unless requested.

Still-open details to confirm at build time (sensible defaults in brackets):
- **Operations/labour:** flat operating cost per unit *[default]* vs named operations.
- **WIP warehouse:** consume-on-finish *[default]* vs transfer-to-WIP first.
- **Multi-level:** single-level BOM *[default]* vs sub-assemblies (BOM-within-BOM).

> **Status:** ✅ **Built 2026-07-05** — Phases 1–4 on `feat/manufacturing` (migration `0065`). Open
> details were resolved with the plan's defaults: flat operating cost, consume-on-finish (WIP transfer
> optional), single-level BOM. Deferred items (Job Cards / MRP / multi-level / subcontracting / etc.)
> remain deferred. See [MANUFACTURING_IMPLEMENTATION.md](MANUFACTURING_IMPLEMENTATION.md).
