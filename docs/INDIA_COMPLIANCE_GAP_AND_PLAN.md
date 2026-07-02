# India Compliance — Build Plan (GST + statutory, multi-tenant SaaS)

**Scope:** make OptiReach a **multi-tenant SaaS** that keeps any Indian MSME GST-compliant — correct GST on
every document, GST-compliant invoices, the monthly **returns** (GSTR-1/3B), **e-invoice** and **e-way
bills**, **reverse charge**, **GSTR-2B reconciliation**, and the TDS/TCS already in place — **eventually
covering the full GST spectrum** (regular + composition, monthly + QRMP, B2B/B2C, SEZ/export, e-commerce
TCS), **configurable per tenant**, and with **live portal/GSP automation** as a planned phase. Modelled on
ERPNext + the Frappe **India Compliance** app.
**Status:** 🟢 **building — Phases 0–5 done (2026-07-02).** HSN + GST-invoice completeness, GST Settings,
GSTR-1/3B returns (+JSON), reverse-charge posting, e-invoice/e-way-bill JSON, and a pluggable GSP provider
abstraction (JSON fallback) are built + tested. Remaining: the concrete live GSP adapter (needs creds) and
the Phase-6 long tail.
**Designed:** 2026-06-23. **Built:** 2026-06-28 → 2026-07-02.

> **SaaS framing (owner, 2026-06-23):** this is a product for *many* MSMEs, so we **cannot permanently skip**
> a GST case — some tenant will need each one. The lever is **per-tenant configuration + sensible
> sequencing**, not omission: build the broad foundation first, gate edge cases behind per-company settings,
> and layer the **live GSP/IRP/NIC integration** (owner wants portal automation) on top of a data/JSON layer.
> House rules still hold: reuse the existing tax engine / GL / print system — no new posting engine.
> ERPNext's India GST lives in a **separate app** (`india-compliance`), so this draws on that app + GST law,
> not the core `reference/` tree (UAE/US/etc. only).

---

## 0. Plain-language summary (read this first)

"Indian compliance" for a distributor is mostly **GST** plus a bit of **TDS/TCS**. Three jobs:

1. **Put the right tax on every bill** and print a **GST-compliant invoice** (both GSTINs, HSN code per
   line, place of supply, CGST/SGST/IGST split, amount in words). *← we mostly do the tax; we don't yet
   print all the legally-required fields or carry HSN.*
2. **Hand the government the monthly numbers** — **GSTR-1** (every sale, grouped the way the portal wants)
   and **GSTR-3B** (the summary + input-tax-credit). *← not built; this is the headline gap.*
3. **Move goods legally** — an **e-way bill** when you transport > ₹50,000 of goods, and (above a turnover
   threshold) an **e-invoice** with a government IRN/QR. *← not built.*

The good news: the **hard tax math already works** — GSTIN-based auto-pick of IGST (inter-state) vs
CGST+SGST (intra-state), tax templates, MRP-inclusive tax, per-item GST slabs (Item Tax Template), and
TDS/TCS. So compliance is mostly **adding the missing fields (HSN, place of supply), a compliant invoice
print, and reading the GL/invoices into the return formats** — not re-doing tax.

**Stance on e-docs (SaaS):** build the **data/JSON layer first** (works for every tenant and is the
foundation), then add **live GSP/IRP/NIC integration** so tenants can push e-invoices, e-way bills and
returns from inside the app (the owner wants portal automation). The live layer is **pluggable** (a GSP
provider abstraction + per-tenant credentials), so different GSPs/sandbox/production can be configured —
not a permanent omission, just a later phase that sits on the data layer.

---

## 1. What an Indian MSME distributor actually needs

| Area | What it is | Priority for a distributor |
|---|---|---|
| **GST on invoices** | Correct CGST/SGST/IGST by place of supply | ✅ have |
| **HSN/SAC codes** | 4–8 digit goods code on every taxable line (legally required; drives GSTR-1 HSN summary) | **must** |
| **GST-compliant tax invoice** | Both GSTINs, HSN, place of supply, tax split, RCM flag, amount in words | **must** |
| **GSTR-1** | Monthly outward-supply return (B2B, B2C-large, B2C-small, HSN summary, document summary) | **must** |
| **GSTR-3B** | Monthly summary return (outward tax + eligible ITC) | **must** |
| **E-Way Bill** | Required to transport goods > ₹50k — a distributor's daily reality | **high** |
| **Reverse Charge (RCM)** | Buyer pays GST on certain inward supplies (freight/GTA, legal, unregistered) | **medium** |
| **E-Invoice (IRN + QR)** | Government-registered invoice; **mandatory only if AATO > ₹5 cr** | **threshold** |
| **GSTR-2A/2B reconciliation** | Match purchases to what suppliers filed (protect ITC) | **medium** |
| **GST on advances** | GST payable when you receive an advance before supply | **low/medium** |
| **TDS/TCS** | Withhold/collect tax at source + returns (26Q) + Form 16A | ✅ mostly have |
| **Nil/Exempt/Non-GST** | Mark non-taxable supplies so returns are correct | **must (small)** |

---

## 2. What we already have (don't rebuild)

- **GSTIN** on Company, Customer, Supplier (`tax_id`), with **state-code comparison** (`gstin[:2]`) that
  auto-selects **IGST** (inter-state) vs **CGST+SGST** (intra-state) tax templates — `resolve_tax_template`
  in `accounts_masters.py`.
- **Tax Template** (Sales/Purchase) + **Tax Category** (inter-state) + the faithful **Taxes & Totals**
  engine (per-item chaining, multi-currency, rounding), incl. **MRP-inclusive** tax.
- **Item Tax Template** — per-item GST-slab override (5/12/18% on one invoice).
- **Output CGST/SGST/IGST + Input GST** accounts in the COA.
- **TDS/TCS** — Tax Withholding Category + deduction posting (+ some reports).
- A themed **PDF/print** system (Sales/Purchase Invoice) to extend for the GST invoice format.

---

## 3. The gaps (what to build)

1. **HSN/SAC on Item** (+ optional default GST rate / `gst_treatment` = Taxable | Nil-rated | Exempt |
   Non-GST). Carried onto invoice lines. *Foundation for legal invoices + GSTR-1 HSN summary.*
2. **Place of Supply** on Sales/Purchase Invoice (a state; defaults from the party's GSTIN state, editable —
   matters for services and for GSTR-1).
3. **GST-compliant tax invoice print** — supplier+recipient GSTIN, HSN per line, place of supply,
   taxable value + CGST/SGST/IGST columns, reverse-charge marker, "amount in words", invoice-type label.
4. **GSTR-1 report** — outward supplies grouped: **B2B**, **B2C (large/small)**, **HSN-wise summary**,
   **document summary** — read from submitted Sales Invoices. + optional **GSTR-1 JSON** export.
5. **GSTR-3B report** — section 3.1 (outward taxable/zero/nil/exempt) + 3.2 (inter-state to unregistered) +
   eligible **ITC** (from Purchase Invoices) — a summary the CA files.
6. **Reverse Charge (RCM)** — a flag on Purchase Invoice: book the GST liability **and** the ITC (and a
   self-invoice for unregistered purchases) so net ITC is right.
7. **E-Way Bill** — generate the **EWB JSON payload** (transporter, vehicle, from/to, HSN, value) for upload;
   live NIC API **deferred**.
8. **E-Invoice (IRN/QR)** — generate the **e-invoice JSON** (Schema 1.1) for B2B; live IRP/GSP API **deferred**;
   only relevant above the ₹5 cr turnover threshold.
9. **GSTR-2A/2B reconciliation** — import portal data and match to the purchase register. *(Optional/defer.)*
10. **GST on advances** — GST liability on advance receipts, adjusted on the later invoice. *(Optional.)*

---

## 4. Per-tenant configuration & sequencing (SaaS: cover all cases, sequenced)

Nothing is permanently cut — each case is **gated by a per-company GST Settings flag** and **sequenced**,
so a tenant that needs it can turn it on without re-architecting.

| Case | How it's handled |
|---|---|
| **Registration type** (Regular vs **Composition**) | Per-tenant flag; Composition tenants get composition invoice/return behaviour (later phase, off by default). |
| **Filing cadence** (Monthly vs **QRMP**) | Per-tenant flag; returns surface honours it. |
| **E-Invoice (IRN/QR)** | Per-tenant `e_invoice_applicable` flag (set by turnover band). JSON generator first, **live IRP/GSP** later. |
| **E-Way Bill** | Per-tenant `e_way_bill_applicable`. JSON generator first, **live NIC** later. |
| **GSP/IRP/NIC live API** (auto e-invoice/e-way/return push) | **Pluggable provider** + per-tenant credentials; built on the data/JSON layer (later phase). |
| **GSTR-2A/2B reconciliation** | Build the matcher; portal **pull** via GSP in the live phase, **file upload** before that. |
| **SEZ / Export (with/without payment)**, **e-commerce TCS u/s 52**, **RCM** | Modelled as GST-treatment/flow flags on the document; phased in. |
| **e-invoice cancel/amend, e-way Part-B vehicle update** | Supported via the live API phase; before that, manage on the portal. |
| **GST Settings** | A real **per-company settings** record (not a few stray fields) — see §5. |

The only genuine *omissions* are non-GST statutory areas with no module yet: **payroll (PF/ESI/PT)** —
out of scope until an HR/Payroll module exists.

---

## 5. Data model (fields + masters + reports)

**Per-company GST Settings** (a record per tenant — the SaaS config surface):
`registration_type` (Regular | Composition), `gst_state` (from GSTIN), `filing_cadence` (Monthly | QRMP),
`e_invoice_applicable`, `e_way_bill_applicable`, `is_sez`, GSP provider + credentials (later phase).

**Fields (small migrations):**
- **Item:** `hsn_sac_code` (Data), `gst_treatment` (Taxable | Nil-rated | Exempt | Non-GST).
- **Sales/Purchase Invoice:** `place_of_supply` (state), `is_reverse_charge` (Check, purchase),
  `gst_category` (Registered | Unregistered | SEZ | Export | Composition), `hsn_sac_code` snapshot on the
  **invoice item** (copied from Item at billing).

**Reports (read-only services + a Compliance reports surface):** GSTR-1, GSTR-3B, HSN summary, (later)
GSTR-2B reconciliation, TDS 26Q.

**Bespoke vs engine:** the GST math/RCM posting + return computation are **bespoke** (read GL/invoices,
reuse the tax engine + GL). HSN/`gst_treatment` are just **fields on existing masters/docs**. E-way-bill /
e-invoice JSON builders are **bespoke read-only generators**.

**Decision rule:** posts GL / computes statutory figures → bespoke; a field on a master → add the column;
a filing artifact → read-only generator/report.

---

## 6. Phased build plan

### Phase 1 — Invoice GST completeness *(foundation; gating)* — ✅ DONE (2026-06-29)
- **HSN/SAC** + `gst_treatment` on Item, snapshotted onto invoice lines. **Built:** `Item.hsn_sac_code`
  (`String(8)`) + `Item.gst_treatment` (Taxable | Nil-Rated | Exempt | Non-GST, default Taxable);
  `hsn_sac_code` snapshotted onto Sales/Purchase Invoice lines with a **line-level override winning over
  the item master**.
- **Place of Supply** on Sales/Purchase Invoice (default from party GSTIN state). **Built:**
  `place_of_supply` + `is_reverse_charge` on both invoices; POS defaults to the **customer** state for
  sales (fallback company), the **company** state for purchases, via `gst_state_label_of()` →
  `"27-Maharashtra"`. Migration `0059_gst_invoice_fields` (down_revision `0058_disposal_sales_invoice`).
- **GST-compliant tax-invoice print** (both GSTINs, HSN, POS, CGST/SGST/IGST split, RCM marker, amount in
  words) — extended the existing PDF/print slice (sales + purchase templates: HSN column, Place of Supply,
  reverse-charge marker; the CGST/SGST↔IGST split reuses the existing tax loop).
- *Acceptance (verified):* an inter-state invoice (27→29) prints **IGST** with **HSN 84182100** per line,
  **Place of Supply 29-Karnataka** (auto-derived), both GSTINs, and amount in words. Intra-state prints
  CGST+SGST. Tests: 9 integration (`test_gst_invoice.py` — item GST fields, HSN snapshot + override,
  POS intra/inter defaulting, reverse-charge flag, purchase POS from company) + Playwright UI pass.

### Phase 2 — GST returns *(the headline value)* — ✅ DONE (2026-07-02)
- **GSTR-1** report: B2B, B2C (large/small), **HSN-wise summary**, document summary, plus **CDNR/CDNUR**
  credit/debit notes — from submitted Sales Invoices; + **GSTR-1 JSON** export (portal offline-tool schema).
- **GSTR-3B** report: §3.1 outward tax (a…e) + §3.2 inter-state to unregistered + §4 eligible ITC from
  Purchase Invoices, with a net-payable line.
- A **Compliance → GST Returns** reports surface (`/gst-returns`, month picker, GSTR-1/3B tabs, drill-down,
  JSON download). **Built:** service `app/services/gst_returns.py` (tax amounts read from the invoices'
  actual tax rows → tie to the Output GST ledger; per-line HSN/taxable split allocated by taxable×rate);
  schemas in `app/schemas/compliance.py`; router `app/api/v1/compliance/returns.py`
  (`/gst-returns/gstr-1`, `/gstr-1/json`, `/gstr-3b`); frontend `views/compliance/GstReturnsView.vue`
  + `types/compliance.ts` + nav. Tests: `test_gst_returns.py` (3) — buckets + totals tie to the invoices;
  JSON export carries the portal keys.
- *Acceptance (verified):* for a month, GSTR-1 B2B/B2CS/B2CL/HSN totals and GSTR-3B §3.1 tie to the
  submitted invoices; HSN summary sums to taxable value; §3.2 = the inter-state B2C subset.

### Phase 3 — Reverse charge + advances — 🟢 RCM DONE (2026-07-02); advances deferred
- **RCM** on a Purchase Invoice → **self-assessed GST**: booked as both ITC (`Input GST`, an *Add* tax row →
  Dr) and liability (`Output CGST/SGST` intra or `Output IGST` inter, *Deduct* rows → Cr), netting the
  payable to the base (supplier charges no GST). **Built:** `accounts_common.reverse_charge_tax_rows`
  (self-contained; leaves `auto_gst_from_items` untouched), wired into `create_purchase_invoice` when
  `is_reverse_charge` and no explicit taxes; GSTR-3B splits input(ITC 4A3)/output(liability 3.1d) heads.
  Reuses the existing taxes-and-totals engine + GL posting (no new posting engine). Test `test_gst_rcm.py`:
  GL posts Dr ITC / Cr liability, payable = base, returns pick up 3.1(d) + ITC 4(A)(3).
- **GST on advances** *(optional)* — **deferred** (liability on advance receipts adjusted at invoicing;
  belongs with the Payment-Entry advance flow — lands in the long tail when demanded).

### Phase 0 (cross-cutting) — per-company **GST Settings** — ✅ DONE (2026-06-28)
A tenant config record everything below reads. **Built:** `GstSettings` stored as a per-company JSON blob
under the `gst_settings` `SystemSetting` key (no new table — mirrors the print/branding profile) with
`registration_type` (Regular | Composition), `filing_cadence` (Monthly | QRMP), `e_invoice_applicable`,
`e_way_bill_applicable`, `is_sez`. **GSTIN + place-of-supply state are derived from `Company.tax_id` on
read** (single source of truth) via a new `app/core/gst_states.py` (the 37 GST state codes + GSTIN→state /
`NN-State` helpers, reused by Phase 1 place-of-supply). `GET`/`PUT /gst-settings`; frontend
`/gst-settings` page (Settings + Accounting→Taxes links). Tests: 4 unit (state derivation) + 3 integration
(defaults/save/reload, validation, GSTIN-derived state). The GSP-credentials slot lands with Phase 5.

### Phase 4 — E-documents (data/JSON layer) — ✅ DONE (2026-07-02)
- **E-Way Bill JSON** generator (transporter/vehicle/from-to/HSN/value), gated by `e_way_bill_applicable`.
- **E-Invoice JSON** (Schema 1.1) generator for B2B, gated by `e_invoice_applicable`.
- **Built:** `app/services/e_documents.py` (`e_invoice_json`, `e_way_bill_json` — per-line GST from the
  effective HSN rate, IGST vs CGST+SGST by company↔POS state; UQC mapping; graceful pincode/URP defaults);
  router `app/api/v1/compliance/e_documents.py` (GET endpoints, each gated by the GST-settings flag; e-invoice
  is B2B-only); frontend **download buttons** on a submitted Sales Invoice (`InvoiceFormView.vue`). Tests:
  `test_e_documents.py` (4) — e-invoice B2B payload + gating + B2C refusal, e-way-bill payload, push fallback.

### Phase 5 — Live GSP/IRP/NIC integration *(portal automation — owner wants this)* — 🟢 ABSTRACTION DONE; live push needs creds
- A **pluggable GSP provider** abstraction (`app/services/gsp.py`: `GspProvider` protocol, `NullProvider`
  default, `register_provider`/`get_provider` keyed by `GstSettings.gsp_provider`) + a **credentials slot**
  (provider name in settings; secrets held out-of-band in a secure store, never in the settings blob).
- **Push endpoints** `POST /e-documents/.../e-invoice|e-way-bill/push` built on the Phase-4 layer: with a GSP
  configured they push (IRN/QR, EWB no.); **with none they degrade to returning the JSON** for manual upload
  (verified in `test_e_documents.py`). Frontend: a **GSP provider** field on GST Settings.
- **Remaining for a live tenant (needs real GSP/NIC sandbox credentials — cannot be exercised here):** the
  concrete HTTPS adapter(s) implementing `GspProvider` (auth, IRN/QR, EWB Part-B cancel/amend, GSTR-1/3B
  filing, GSTR-2B pull) + secure per-tenant credential storage. The seams are in place to drop these in.

### Phase 6 — Reconciliation & the long tail (per-tenant, as demand appears)
- **GSTR-2B reconciliation** — 🟢 **DONE (2026-07-03, slice 6.1).** `app/services/gstr2b_recon.py`
  (`reconcile_gstr2b`) matches the submitted **Purchase Invoices** in a window against an **uploaded portal
  GSTR-2B JSON** (parses `b2b` + `cdnr`, tolerant of the `data.docdata`/`docdata`/top-level wrappers, sums
  item taxes, accepts igst/cgst/sgst or iamt/camt/samt keys). Match key = (supplier GSTIN, normalised
  invoice no = our `bill_no` vs the 2B `inum`); buckets **Matched / Mismatch / Only in Books (ITC at risk) /
  Only in 2B** with a summary (books ITC, 2B ITC, matched ITC, at-risk ITC). Only registered-supplier
  purchases are reconciled (unregistered can't appear in 2B). RCM bills count only the Input head as ITC.
  `POST /api/v1/gst-returns/gstr-2b/reconcile` (Purchase-Invoice report perm); frontend **GSTR-2B recon**
  tab (upload the 2B JSON → summary cards + problems-first table). Test `test_gstr2b_recon.py`; verified live
  on the demo (books ITC ₹46,085.96 vs 2B ₹28,003.24 → ₹18,773.12 at risk). **Deferred:** the live portal
  **pull** of 2B (via a GSP — Phase 5).
- **TDS Form 26Q + Form 16A** — 🟢 **DONE (2026-07-03, slice 6.2).** `app/services/tds_returns.py`
  (`tds_26q`, `form_16a`) reads submitted Purchase Invoices carrying a TDS `tax_withholding_category`:
  base = `base_net_total`, tax = `tax_withholding_amount`, **section** (194C/194J/…) parsed from the
  category name, **deductee PAN derived from the supplier GSTIN** (chars 3-12). **26Q** groups by
  (deductee × section) with a summary; **16A** is the per-deductee certificate. `GET /api/v1/tds-returns/26q`
  + `/16a?supplier_id=` (Purchase-Invoice report perm); frontend `views/compliance/TdsReturnsView.vue`
  (`/tds-returns`, quarter picker, 26Q table + inline Form-16A certificate). Test `test_tds_returns.py`;
  verified live (194C, Duff Components, PAN BBBBB0001B, ₹50,000 → TDS ₹1,000). **Not captured yet:** the
  deductor **TAN** (Company has no TAN field — the filer completes it); **TCS 27EQ** is a separate return.
- Remaining long tail: **Composition** scheme flows, **QRMP**, **SEZ/Export** (with/without payment),
  **e-commerce TCS u/s 52**, **TCS 27EQ**, **GST on advances**.

### Out of scope (no module yet)
- Payroll statutory (PF / ESI / Professional Tax) — needs an HR/Payroll module first.

---

## 7. Decisions captured (owner, 2026-06-23)
1. **This is a multi-tenant SaaS for MSMEs → cover all GST cases**, configurable per tenant — don't
   permanently omit e-invoice, composition, QRMP, SEZ/export, e-commerce TCS; gate them behind per-company
   **GST Settings** and sequence them (Phase 6 long tail).
2. **E-invoice is in scope** (not deferred on turnover) — every tenant can enable it via
   `e_invoice_applicable`; the JSON generator (Phase 4) lands first, the live IRP push (Phase 5) after.
3. **Portal/GSP automation is wanted** → Phase 5 builds a pluggable GSP/IRP/NIC integration on top of the
   Phase-4 data layer (e-invoice IRN/QR, e-way bill, GSTR push + GSTR-2B pull).
4. **Build = "just the plan for now"** — plan approved as the design; **build is on hold** until the owner
   says go.

> **Recommended build order when greenlit:** Phase 0 (GST Settings) → Phase 1 (HSN + place of supply + GST
> invoice print) → Phase 2 (GSTR-1 + GSTR-3B, file-ready) → Phase 3 (RCM) → Phase 4 (e-invoice + e-way JSON)
> → Phase 5 (live GSP/IRP/NIC) → Phase 6 (reconciliation + composition/QRMP/SEZ/TCS long tail). Foundation
> is broad and shared; tenant-specific cases ride on per-company settings.

> **Status:** 🟢 **Phases 0–5 built (0/1 on 2026-06-28/29; 2, 3-RCM, 4, 5-abstraction on 2026-07-02).**
> GST returns (GSTR-1/3B + JSON), reverse-charge posting, e-invoice/e-way-bill JSON generators, and the
> pluggable GSP provider abstraction (with JSON fallback) are live and tested. **Remaining:** Phase 5's
> concrete GSP HTTPS adapter + secure credential storage (needs real GSP/NIC sandbox creds); Phase 6 long
> tail (GSTR-2B reconciliation, composition/QRMP, SEZ/export, e-commerce TCS, TDS 26Q/Form 16A) and the
> optional GST-on-advances — phased in per tenant demand.
