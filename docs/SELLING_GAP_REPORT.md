# Selling Module — ERPNext vs OptiERP Gap Report

> **Method (hybrid):** for each screen we diffed three sources — ERPNext **source**
> (`reference/erpnext`, `version-15` doctype JSON), the **live** ERPNext desk
> (captured to `e2e/out/erpnext/`), and **our** implementation. ERPNext version matches
> the staging instance (v15).
>
> **Scope:** Selling transaction documents (Quotation, Sales Order, Sales Invoice,
> Delivery Note) + Selling masters + the new product direction (data-entry modes,
> goods/services naming, previously-deferred features now in scope).

---

## 1. Executive summary

Our transaction forms are **faithful on the core happy path** (party, dates, an editable
items grid, totals strip, document-flow actions) but implement only a **thin slice** of
each ERPNext document — roughly:

| Document | Our coverage | Biggest gaps |
|---|---|---|
| **Quotation** | ~20% of fields | Currency/Price List, Taxes editor, Additional Discount, full Totals, Quotation-To (Lead), secondary tabs stubbed |
| **Sales Order** | ~15% | same as above + per-row warehouse, payment terms, Advance Paid |
| **Sales Invoice** | ~15% (has a basic taxes editor) | Payments/POS tab, Is Return (Credit Note), Additional Discount, full Totals, currency |
| **Delivery Note** | ~5–10% (**view-only, no create form**) | no authoring form at all; taxes/discount/totals; per-row warehouse; SO linkage; transporter info |

**Nothing is broken** — the gaps are unbuilt breadth. The high-leverage insight: the missing
pieces are **the same four commercial blocks on every document**, so they should be built
**once as shared components** and dropped into all forms.

---

## 2. New requirements (this round)

- **Now in scope** (were deferred): **Taxes & Charges**, **Import (Download/Upload)**,
  **Additional Discount**, **full Totals** (grand total, rounding adjustment, rounded total,
  in-words). **Still excluded:** Scan Barcode.
- **Data entry = 3 modes:** Manual · Import (Tally / CSV) · OCR (API-backed). See §6.
- **Goods + Services naming:** the app must read correctly for both. See §5.

---

## 3. Cross-cutting gaps → build these **once**, reuse everywhere

The same blocks are missing across Quotation / Sales Order / Sales Invoice / Delivery Note.
Build each as a shared component and include it in every transaction form:

| Shared block | What it contains | Today | Target |
|---|---|---|---|
| **CurrencyPriceList.vue** | currency, exchange rate, selling price list, price-list currency, ignore pricing rule | display-only in detail | collapsible section on every form |
| **TaxesCharges.vue** | tax_category, taxes_and_charges template, the editable taxes grid, shipping_rule, incoterm, total_taxes_and_charges | only Sales Invoice has a partial manual editor | promote SI's editor to shared; add template + category |
| **AdditionalDiscount.vue** | apply_discount_on, additional_discount_percentage, discount_amount, coupon_code | read-only display of `discount_amount` only | editable section on every form |
| **DocumentTotals.vue** | net_total, total_taxes_and_charges, grand_total, rounding_adjustment, rounded_total, disable_rounded_total, in_words | grid Total Qty/Total only; detail mislabels `rounded_total` as "Grand Total" | full computed totals block |
| **DataEntry control** | Manual / Import / OCR (see §6) | "Add row" + "Add multiple" + "Get Items From" | add Import + OCR modes |
| **ItemsGrid columns** | + UOM, + optional Warehouse, + description | item/qty/rate/amount | extend the existing configurable grid |

These four commercial blocks + the data-entry control turn ~15% coverage into the bulk of
ERPNext parity across all four documents at once.

---

## 4. Per-document gap detail

### 4.1 Quotation
**Have:** Series (display), Date, Order Type (UI only — *not sent on save*), Customer, Company
(display), Valid Till, items grid (Item/Qty/Rate/Amount), Total Qty/Total, Remarks, Submit →
Create Sales Order.
**Gaps:**
- **Currency and Price List** section — missing entirely.
- **Quotation To** = Customer **or Lead** — we hardwire Customer (no Lead doctype yet).
- **Taxes & Charges** — no editor on the form (resolved server-side, shown read-only in detail).
- **Additional Discount** — missing.
- **Totals** — only grid sum; missing grand_total/rounding/in_words.
- Grid missing **UOM** column. `order_type` is rendered but never persisted (**bug-ish**).
- Tabs **Address & Contact / Terms / More Info** are placeholder stubs.

### 4.2 Sales Order
**Have:** core header + per-row **Delivery Date** column (good), document flow (Create
Delivery Note / Invoice).
**Gaps:** all four shared blocks (currency, taxes, discount, totals) + **per-row warehouse**,
header **Set Source Warehouse**, **po_no/po_date** (customer PO), **payment terms**,
**Advance Paid**, commission/sales-team, and the three stubbed tabs. Per-row delivery date is
shown on the create grid but **not** in the read-only detail table.

### 4.3 Sales Invoice
**Have:** party + quick-add, posting/due dates, items grid, **a working manual Taxes editor**
(correct charge-type enum), totals strip with **Outstanding** (good, ERPNext-faithful intent).
**Gaps:**
- **Payments tab** (Include Payment / POS, payments table, advances, write-off, loyalty) — absent.
- **Is Return (Credit Note)** + `return_against` — no credit-note flow.
- **Additional Discount**, **currency/price-list**, full **Totals** (rounding, in_words, base_* ).
- Taxes editor missing the **template link**, **tax_category**, per-row description, item tax template.
- Grid missing **warehouse** (an ERPNext list-view column), **UOM**.
- 4 of 5 tabs stubbed.

### 4.4 Delivery Note — **structural gap**
**Have:** a **read-only viewer** (`FulfilmentView.vue`, shared with Purchase Receipt); DNs are
only generated from a Sales Order via a "Create Delivery Note" qty dialog.
**Gaps:** **no standalone create/edit form exists.** Plus taxes/discount/totals, **per-row
warehouse**, **`against_sales_order`** link not surfaced, transporter info, and the generic
`ItemsGrid` is **not even wired** into this screen (it renders a static table). Highest
structural lift of the four.

---

## 5. Goods + Services naming

ERPNext keeps **one `Item` doctype** for goods *and* services; a service is simply an Item with
**`is_stock_item = 0`** (which hides all inventory/valuation/batch sections), usually filed under
a **"Services"** Item Group. We already use the same flag model — only our **labels** are
stock-centric.

**Recommendation (labels only — no model/API/DB rename, stays ERPNext-compatible):**

| Place | Today | Recommended |
|---|---|---|
| Menu + list/page title | "Item" / "Items" | **"Products & Services"** |
| Create button / record | "New Item" | **"New Product / Service"** |
| Transaction grid column header | "Item Code" | **"Item / Service"** (or keep "Item") |
| Items section heading | "Items" | **"Products & Services"** |
| Goods-vs-service switch | `is_stock_item` ("Maintain stock") | **keep as-is**; surface a derived "Type: Stock / Service / Asset" badge (already half-built in `ItemsView.vue`) |

Do **not** add a separate "is service" boolean (two sources of truth). All edits are centralized:
`workspaces.ts`, `ItemsView.vue`, and the `GridColumn.label` config in the form views.

---

## 6. Data entry — 3 modes (replaces "Download/Upload")

A single **Data Entry** control above the items grid, with three modes:

1. **Manual** *(default, exists)* — the editable `ItemsGrid` (Add row / Add multiple / Get Items From).
2. **Import** *(build now)* — dialog to bring lines in from a file:
   - **CSV** — download a template (ERPNext's `allow_import` pattern), fill, upload → parse → populate grid.
   - **Tally** — accept Tally's export (XML or CSV) and map vouchers/masters → our line items.
   - One parser interface, two adapters (CSV, Tally), so more sources slot in later.
3. **OCR** — upload an invoice/PO image or PDF → `POST /api/v1/ocr/extract` (OpenAI-compatible
   vision) → match against the item master → review extracted rows → populate the grid.
   See [`OCR.md`](OCR.md). Set `OCR_API_KEY` to enable; the UI disables Extract until then.

This subsumes ERPNext's Download/Upload and keeps "Get Items From" (pull from another document)
as a fourth, document-to-document path.

---

## 7. Prioritized build roadmap

**Phase A — Shared commercial blocks (biggest parity win, all docs at once)**
1. `DocumentTotals.vue` (net → taxes → grand → rounding → rounded → in-words).
2. `TaxesCharges.vue` (promote Sales Invoice's editor to shared; add template + tax_category).
3. `AdditionalDiscount.vue`.
4. `CurrencyPriceList.vue`.
5. Extend `ItemsGrid` with UOM + optional Warehouse columns.
   → Drop all into Quotation, Sales Order, Sales Invoice.

**Phase B — Naming + data entry**
6. Goods/Services relabel (§5).
7. Data-entry modes: Manual (done) + Import (CSV, then Tally) + OCR extract API (§6).

**Phase C — Document-specific**
8. **Standalone Delivery Note create form** (wire `ItemsGrid`, per-row warehouse, SO linkage).
9. Sales Invoice **Payments tab** + **Is Return (Credit Note)**.
10. Sales Order per-row warehouse, customer PO, payment terms, Advance Paid.

**Phase D — Secondary tabs & masters**
11. Real **Address & Contact / Terms / More Info** tabs (shared across docs).
12. Master gaps: Sales Team, Sales Partner Type, Industry Type, Party Specific Item,
    Installation Note, proper Customer Credit Limit (per-company child table), Selling Settings.

**Excluded (per direction):** Scan Barcode.

---

## 8. Selling masters coverage

| ERPNext Selling master | In our app? | Notes |
|---|---|---|
| Customer | ✅ descriptor | fewer fields than ERPNext |
| Customer Group | ✅ descriptor (tree) | |
| Territory | ✅ descriptor (tree) | |
| Sales Person | ✅ descriptor (tree) | |
| Sales Partner | ✅ descriptor | `partner_type` flattened to free text |
| Campaign | ✅ descriptor | engine pilot |
| Product Bundle (+Item) | ✅ descriptor | part of pricing engine |
| **Sales Partner Type** | ❌ | collapsed into a Data field on Sales Partner |
| **Industry Type** | ❌ | not modeled |
| **Sales Team** | ❌ | no sales attribution split |
| **Installation Note** (+Item) | ❌ | not present |
| **Party Specific Item** | ❌ | not present |
| **Customer Credit Limit** | ⚠️ inline field | ERPNext's per-company child table not modeled |
| **Selling Settings** | ⚠️ generic /settings | not a real settings doctype |
| SMS Center | ❌ (intentional) | messaging utility |

---

## 9. References
- Source: `reference/erpnext/erpnext/{selling,accounts,stock}/doctype/*` (v15)
- Live captures: `e2e/out/erpnext/*.png`
- Our forms: `frontend/src/views/trade/OrderFormView.vue`, `frontend/src/views/accounts/InvoiceFormView.vue`, `frontend/src/views/trade/FulfilmentView.vue`, `frontend/src/components/shared/ItemsGrid.vue`
