# 03 · Financial Reports

> Sub‑module of [ACCOUNTING_GAP_AND_PLAN.md](../ACCOUNTING_GAP_AND_PLAN.md). ERPNext workspace card: **Financial Reports**.

## Status today — 🟢 seven reports live

All in [financial_reports.py](../../backend/app/services/financial_reports.py), surfaced as tabs in
[ReportsView.vue](../../frontend/src/views/accounts/ReportsView.vue):

General Ledger · Trial Balance · Balance Sheet · Profit & Loss · Accounts Receivable (aged) ·
Accounts Payable (aged) · Cash Flow · Bank Reconciliation (read‑only).

These cover the statutory core. The gaps are **operational/analytical** reports.

## Gaps vs ERPNext (57 reports → the ones worth porting)

| # | Report | State | P | Why |
|---|---|---|---|---|
| 1 | **Sales Register / Purchase Register** | 🔴 | 2 | Every invoice with tax breakup — the daily "what did we bill" report; basis for GST returns. |
| 2 | **Gross Profit** | 🔴 | 2 | Margin per invoice/item — pulls COGS from stock valuation. Core to a trading business. |
| 3 | **Customer / Supplier Ledger Summary** | 🔴 | 2 | Per‑party opening/movement/closing — the "account statement" backbone. |
| 4 | **Item‑wise Sales / Purchase Register** | 🟡 | 3 | Register exploded per line item. |
| 5 | **Accounts Receivable / Payable Summary** | 🟡 | 3 | Summarized (per‑party totals) vs our detailed aging. |
| 6 | **Budget Variance** | 🔴 | 3 | Budget vs actual — see [07-budget.md](07-budget.md). |
| 7 | **Sales / Purchase Invoice Trends** | 🔴 | 4 | Period‑over‑period trend (we have a 12‑month sales trend in workspace stats — generalize it). |
| 8 | **Payment Period Based on Invoice Date** | 🔴 | 4 | How fast customers actually pay. |
| 9 | **TDS reports** (withholding details, computation summary) | 🔴 | 3 | Gate on TDS build — see [05-taxes.md](05-taxes.md). |
| 10 | **Consolidated Financial Statement, Financial Ratios, Profitability Analysis** | 🔴 | 5 | Multi‑company / advanced analytics. **Defer.** |

## Simplifications

- **Skip multi‑company consolidation** (single company — master §3.2). No `consolidated_financial_statement`.
- **Skip the audit/health reports** (`invalid_ledger_entries`, `calculated_discount_mismatch`,
  `general_and_payment_ledger_comparison`) — our invariants make them moot (master §3.3).
- **One reporting pattern**: every new report is a read‑only endpoint in `financial_reports.py`
  returning rows + totals, rendered as a new tab in `ReportsView.vue`. Never engine‑served.

## Build‑out

### Phase 1 — Operational registers & margin
- ✅ **Sales Register / Purchase Register** DONE (2026‑06‑19): submitted invoices in a date window
  with net / tax / grand / outstanding + totals row. `financial_reports.sales_register` /
  `purchase_register` (a shared `_register` helper) → `/reports/sales-register` &
  `/reports/purchase-register` → tabs in `ReportsView.vue`. Verified live (25 invoices, net
  ₹626,941 / tax ₹101,969).
- ✅ **Customer / Supplier Ledger Summary** DONE (2026‑06‑19): per‑party opening / period
  debit‑credit / closing from GL party rows (`_party_ledger_summary`, handles opening‑only parties)
  → `/reports/customer-ledger-summary` & `/reports/supplier-ledger-summary` → tabs. Verified live.
- ✅ **Gross Profit** DONE (2026‑06‑19): per‑item selling (invoice net) vs COGS, where COGS uses each
  item's **latest moving‑average stock valuation** (`StockLedgerEntry`, `DISTINCT ON item_id`). Excludes
  returns + opening invoices; items sold without a recorded stock cost show 0 / 100% margin (flagged in
  the UI). `financial_reports.gross_profit` → `/reports/gross-profit` → Gross Profit tab. Verified live.
  *Simplification:* current valuation, not the exact cost at each sale (which would need the delivery's
  SLE) — good enough for "which items/customers earn margin".
- ✅ **Contribution Margin** DONE (2026‑07‑30): CM1/CM2/CM3 waterfall from `Account.cm_class`;
  industry templates; optional Cost Center filter. See [contribution-margin.md](contribution-margin.md).
  `GET /reports/contribution-margin` + Reports tab.
### Phase 3 — Summaries & trends
- AR/AP **Summary** variants, **Invoice Trends**, **Payment Period** report.
- *Acceptance:* AR summary totals equal the detailed AR aging totals for the same date.

## Notes
- Reuse the existing report response shape (`rows` + `summary`) and the `?tab=` routing so each new
  report is a tab, not a new page.
- Gross Profit is the one report with a cross‑module dependency (stock valuation) — confirm the
  valuation read path before building.
</content>
