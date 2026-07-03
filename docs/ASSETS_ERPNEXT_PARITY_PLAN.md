# Assets — ERPNext Parity Gap Analysis & Plan

**Goal:** measure our Assets module against the *full* ERPNext v15 Assets module (per
https://docs.frappe.io/erpnext/assets and the `reference/erpnext/erpnext/assets` source),
and lay out what to build to reach the level you want.

**Source of truth:** ERPNext ships **~22 asset DocTypes**. This doc lists every one, says
whether we have it, and — for the gaps — gives a plain recommendation: **BUILD** (real value
for an appliance distributor), **SKIP** (enterprise ceremony that contradicts the MSME-lean
thesis), or **OPTIONAL** (build only if you actually need it).

> Reminder of the house rule for this project: *copy the value, drop the ceremony.* You also
> just had me **merge** Asset Maintenance + Asset Repair because two near-identical screens was
> redundant — full ERPNext parity would re-split them and add teams/SLAs. I flag those tensions
> below rather than blindly cloning.

---

## 1. Where we stand (Phases 1–3 + extras, all built & verified)

| Capability | Status |
|---|---|
| Asset Category (method, life, salvage, 3 GL accounts) | ✅ (simplified: accounts on the category, not a per-book child) |
| Location | ✅ (simplified: flat, no tree/geo) |
| Asset register: create / submit / cancel | ✅ |
| Depreciation: **Straight Line**, **Written Down Value**, **Manual** | ✅ |
| Depreciation schedule + idempotent daily posting job + manual "depreciate now" | ✅ |
| Disposal: **Sell** / **Scrap** → gain/loss Journal Entry, halts depreciation | ✅ |
| Asset Movement (location/custodian + history) | ✅ (simplified: one asset per move) |
| Asset Maintenance **& Repair** (one log master, "Repair" is a type) | ✅ (merged per your call) |
| Asset Value Adjustment (impairment **and** appreciation → Revaluation Surplus) | ✅ |
| Non-depreciable categories (Land / freehold, held at cost) | ✅ |
| `is_fixed_asset` Item → auto-create draft Asset from a Purchase Invoice line | ✅ |
| Opening accumulated depreciation (mid-life asset onboarding) | ✅ |

---

## 2. Full ERPNext DocType list vs us

| # | ERPNext DocType | Us | Verdict |
|---|---|---|---|
| 1 | Asset | ✅ | done |
| 2 | Asset Category | ✅ | done (simplified) |
| 3 | Asset Category Account (per-company/book accounts child) | ➖ | **SKIP** — single company/book; accounts sit on the category |
| 4 | Asset Depreciation Schedule (submittable, per finance book) | ◑ | we have the schedule as a child of Asset — enough for one book |
| 5 | Asset Finance Book (parallel schedules per book) | ❌ | **OPTIONAL** — dual-book (Companies Act vs IT Act) depreciation |
| 6 | Location | ✅ | done (simplified) |
| 7 | Asset Movement (+ Movement Item child) | ✅ | done (single-asset; no batch child) |
| 8 | Asset Maintenance + Task + Log + Team + Team Member | ◑ | merged to **one log**; scheduling = **BUILD**, teams/SLAs = **SKIP** |
| 9 | Asset Repair (+ Consumed Item child) | ◑ | merged into maintenance; stock-consumption + capitalisation = **OPTIONAL** |
| 10 | Asset Value Adjustment | ✅ | done |
| 11 | Asset Capitalization (+ stock/asset/service item children) | ❌ | **BUILD** — assemble an asset from parts + labour |
| 12 | Asset Shift Allocation + Shift Factor | ❌ | **SKIP** — factory shift depreciation, not for a distributor |
| 13 | Asset Activity (system event timeline) | ❌ | **SKIP** — our audit log already records every change |
| 14 | Linked Location | ❌ | **SKIP** — part of the location-tree machinery |

Plus these ERPNext *capabilities* (not separate DocTypes):

| Capability | Us | Verdict |
|---|---|---|
| **CWIP** (Capital Work in Progress — cost accrues until ready-for-use, then capitalise) | ❌ | **BUILD** (pairs with Capitalization) |
| Depreciation method **Double Declining Balance** | ❌ | **OPTIONAL** — niche method |
| **Daily pro-rata** first/last period (asset bought mid-month) | ❌ | **BUILD** — real accuracy gap |
| Explicit **Rate of Depreciation %** input for WDV (vs derived from salvage) | ❌ | **BUILD** — small, India WDV blocks use fixed rates |
| Disposal via **Sales Invoice** (tax invoice + GST on used-asset sale) | ◑ | **BUILD** — we only post a plain JE today |
| **Cancel / restore** posted depreciation (reverse the JEs) | ❌ | **BUILD** — small but expected |
| **Reports**: Fixed Asset Register / Net Block, Depreciation Ledger, Asset-wise depreciation | ❌ | **BUILD** — this is the originally-planned Phase 4 |
| Multiple assets from one purchase line (qty > 1) | ➖ | **SKIP** — one Asset per line (offer split later if asked) |

---

## 3. Recommended plan (high value first)

### Phase 4 — Reports *(do first; already the planned next step)*
- **Fixed Asset Register / Net Block** — per asset/category: gross, accumulated depreciation,
  book value, as of any date.
- **Asset Depreciation Ledger** — every posted depreciation entry (date, amount, JE link).
- **Asset-wise depreciation** — the schedule across assets, projected vs posted.
- Read-only endpoints + a Reports surface in the Assets workspace.

### Phase 5 — Acquisition completeness — ◑ DONE (capitalization); SI-disposal deferred
- ✅ **Asset Capitalization** (`POST /assets/capitalize`): build a new submitted asset from costed
  components — Dr the category's Fixed Asset account (total) / Cr each component's source account.
  The sources can be **CWIP** (so this also clears Capital-Work-in-Progress), **Stock In Hand**
  (parts consumed), a **labour/expense** account, **Bank**, etc. Creates the asset live with its
  depreciation schedule. Frontend `AssetCapitalizeView` at `/asset-capitalize` (component grid).
- ✅ **CWIP** — covered *by* capitalization: an asset under construction accrues cost in a CWIP
  account (via normal purchases/JEs), then you capitalize crediting that CWIP account → Fixed Asset.
  No separate flag/lifecycle needed (lean).
- ⏸️ **Disposal via Sales Invoice** — **deferred with rationale.** The existing Sell disposal already
  books the receivable/proceeds + gain/loss vs book value correctly. Wiring the *asset removal* into a
  GST Sales Invoice requires modifying the **core Sales-Invoice GL** to special-case fixed-asset-sale
  lines (credit the asset, not income) — a cross-module change with real regression risk, for an
  occasional event. Recommendation: for GST on a used-asset sale, raise a normal Sales Invoice and use
  the Dispose (Sell) action for the asset removal; revisit a wired SI-disposal only if it becomes routine.
- *Tests:* +1 integration (capitalize from 2 components → asset + balanced "Asset Capitalization" JE).
  Verified live (₹2L Cold Storage Unit from ₹180k parts + ₹20k labour). Also added a "show first 12 /
  show all" collapse to the depreciation-schedule table (long schedules like a 30-year building).

### Phase 6 — Depreciation accuracy — ✅ DONE (migration 0056)
- ✅ **Daily pro-rata** (`daily_prorata` on the category) — Straight-Line periods are weighted by
  their actual day count (Feb < Jan) instead of an equal split; total still equals the base.
- ✅ **Explicit WDV rate %** (`rate_of_depreciation` on the category) — used directly (e.g. IT-Act
  15%/40% blocks) instead of deriving from salvage; works with 0% salvage too.
- ✅ **Cancel/restore depreciation** (`POST /assets/{id}/cancel-depreciation`) — writes reversing GL
  entries for every posted depreciation JE, clears the rows' posted flags, reopens the asset
  (status → Submitted) so the schedule can be re-posted. Ledger stays append-only.
- ⏸️ **Double Declining Balance** — left out (niche; WDV with an explicit rate covers most needs).
- *Tests:* +3 unit (explicit rate, pro-rata day-weighting, rate>0) + 2 integration (cancel+re-post,
  WDV explicit-rate category). Verified live (forklift depreciation cancelled → status Submitted,
  accumulated ₹0, rows reopened).

### Phase 7 — Maintenance scheduling *(kept ONE doctype)* — ✅ DONE (migration 0057)
- ✅ Asset Maintenance gains `periodicity` (One-time/Monthly/Quarterly/Half-yearly/Yearly),
  `next_due_date` and `assigned_to` — preventive maintenance can be scheduled, still on the single
  engine-served master (no teams/members/SLAs).
- ✅ **Maintenance Due report** (`GET /asset-reports/maintenance-due`, `only_overdue` filter) — Open
  scheduled maintenance by next-due date, **overdue first** with a days-overdue figure; a third tab in
  the Asset Reports view (overdue rows highlighted) + a sidebar link.
- *Tests:* +1 integration (overdue vs upcoming + only_overdue filter). Verified live (a Forklift
  preventive service 22 days overdue surfaced in red).
- *Note:* auto-rolling the next-due date on completion was left out (lean) — log the service done and
  set the next due date, or create the next occurrence. Easy to add later if it becomes routine.

---

## ✅ Recommended BUILD set complete

All four phases of the agreed scope are built, tested, verified live and pushed on
`feat/assets-phase-1`: **P4 Reports**, **P6 depreciation accuracy**, **P5 capitalization (+CWIP)**,
**P7 maintenance scheduling**. Deliberately skipped (enterprise ceremony): multiple finance books,
shift-based depreciation, maintenance teams/SLAs, location tree+geo, asset-activity timeline,
multi-asset movement, Double Declining Balance. Deferred with rationale: disposal via Sales Invoice
(cross-module GL risk for an occasional event).

### Deliberately SKIPPED (enterprise ceremony — revisit only on a real need)
- **Multiple Finance Books** + Asset Category Account child (dual-book parallel depreciation).
- **Shift-based depreciation** (Shift Allocation / Shift Factor).
- **Maintenance Teams / Members / SLA tasks**, **Asset Activity** timeline, **Location tree +
  geolocation**, **multi-asset Movement batches**.

---

## 4. Optional (only if you say so)
- **Multiple Finance Books** — if you need Companies-Act *and* Income-Tax-Act depreciation
  computed inside the ERP (most MSMEs let their CA do the IT-Act block separately). This is the
  single biggest structural change (asset gets N schedules instead of one) — worth a dedicated
  decision.
- **Asset Repair with consumed stock + capitalisation** — if repairs routinely consume tracked
  inventory and you want the repair to add to the asset's value.

---

## 5. Suggested sequencing
1. **Phase 4 (Reports)** — fast, high value, finishes the original plan.
2. **Phase 6 (pro-rata + WDV rate + cancel depreciation)** — correctness, mostly backend.
3. **Phase 5 (CWIP + Capitalization + SI disposal)** — the biggest functional additions.
4. **Phase 7 (maintenance scheduling)** — quality-of-life.
5. Finance Books / shift depreciation only if a real requirement appears.

Each phase ships the same way as 1–3: migration + service + engine/bespoke + frontend +
unit/integration tests + Playwright verification + a commit, with manual verification steps.
