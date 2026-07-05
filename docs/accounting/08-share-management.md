# 08 · Share Management

> Sub‑module of [ACCOUNTING_GAP_AND_PLAN.md](../ACCOUNTING_GAP_AND_PLAN.md). ERPNext workspace card: **Share Management**.

## Status today — 🟢 built (2026‑06‑22)

> Implemented — see the v1 status note at the bottom of this doc. The original "not built" analysis is kept below for context.

This was a **net‑new sub‑module** and the **lowest priority** in the accounting plan — for
a private appliance distributor it's a cap table, not a trading function. Build only when the owner
actually needs to track shareholders (e.g. taking on an investor, issuing ESOPs).

## What ERPNext provides

| DocType | Purpose |
|---|---|
| `shareholder` | A shareholder party (holdings, folio). |
| `share_type` | Class of share (Equity, Preference…). |
| `share_transfer` | Issue / transfer / purchase of shares between parties. |
| `share_balance` | Running balance held per shareholder (child of Shareholder). |
| Reports: `share_balance`, `share_ledger` | Holdings per shareholder; transfer ledger. |

## Scope decision — build the cap table, skip the trading desk

This is **standalone bookkeeping of who owns what**; it does **not** post to the financial GL in
ERPNext (share capital hits the GL via a normal Journal Entry, separately). So this sub‑module is
almost entirely **engine‑served masters + one small transfer service** — no GL posting engine needed.

## Build‑out (Phase 4 — on demand)

Data model (all new, mostly engine descriptors):
- **Share Type** — engine master (name, currency/par value).
- **Shareholder** — engine master (party, folio, contact) with a **Share Balance** child grid (running holdings per share type).
- **Share Transfer** — a light document: `transfer_type (Issue/Transfer/Purchase), from_shareholder, to_shareholder, share_type, no_of_shares, rate, amount, date, company`. On submit it updates the running **Share Balance** of the affected shareholders.

Logic (bespoke, but tiny — no GL):
- Validate the `from` shareholder holds enough shares (except Issue, which mints new shares).
- Update Share Balance rows atomically.
- Optionally prompt to create the matching capital Journal Entry (Dr Bank / Cr Share Capital) — but
  keep that a normal JE, not coupled into this module.

Reports:
- **Share Balance** (holdings per shareholder per type) and **Share Ledger** (chronological transfers).

*Acceptance:* issuing 1,000 equity shares to a shareholder, then transferring 200 to another, leaves
correct running balances and a transfer ledger that reconciles.

## Engine vs bespoke
- **Engine:** Share Type, Shareholder (with Share Balance child grid).
- **Bespoke:** Share Transfer (balance‑update logic, no GL). Reports are read‑only.

## Recommendation
**Defer to last.** Ship everything else in the accounting plan first; this delivers value only to
businesses with multiple shareholders to track. One developer‑week when the need is real.

---

## v1 Implementation Plan (designed 2026‑06‑22 — ready to execute)

Grounded in the codebase patterns already in use: engine masters (like `DunningType`), a bespoke
document with a naming series + draft→submit→cancel (like invoices/payment entries), and read‑only
reports (like `financial_reports` + ReportsView tabs). **No GL** — this is a register.

### Key design call — compute balances from the ledger, don't store them
ERPNext stores a `share_balance` child grid on the Shareholder and mutates it on every transfer.
**We deviate:** a shareholder's holding is **derived** from the submitted Share Transfers (append‑only
ledger), exactly like the project derives invoice `outstanding` / AR aging from the GL rather than a
second ledger (master §3.1 anti‑drift philosophy). One source of truth, can't drift, and **cancel just
flips `docstatus`** — the balance recomputes automatically with no reversal rows. So **Shareholder has
no balance child** — it's a plain master.

### Data model
- **ShareType** — *engine master* (`/m/share-type`): `share_type_name, currency (3), par_value (Numeric),
  disabled`. `naming=field:share_type_name`.
- **Shareholder** — *engine master* (`/m/shareholder`): `shareholder_name, contact_id (FK contacts,
  nullable), folio_no (Data, nullable), disabled`. `naming=field:shareholder_name`.
- **ShareTransfer** — *bespoke document* (naming series `ACC-SHT-.YY.-`, docstatus lifecycle):
  `name, transfer_type (Issue|Transfer|Buyback), from_shareholder_id (nullable), to_shareholder_id
  (nullable), share_type_id (FK), no_of_shares (Integer), rate (Numeric), amount (= shares×rate, stored),
  transfer_date, company`.
  - **Issue** — company mints new shares → `to` required, `from` null (increases total issued).
  - **Transfer** — holder→holder → both required.
  - **Buyback** — holder→company, shares retired → `from` required, `to` null (decreases total issued).
  - (ERPNext's "Purchase" is folded into Buyback for clarity.)

### Service (`services/share_transfer.py`) — bespoke, tiny, no GL
- `create / get / list / submit / cancel` (submit sets docstatus=1; cancel→2).
- **On submit, validate**: `no_of_shares > 0`; for **Transfer/Buyback** the `from` shareholder's
  *computed* balance of that share type ≥ `no_of_shares`; `from != to`; the right shareholders are
  set per `transfer_type`.
- `shareholder_balance(db, company_id, *, shareholder_id=None, share_type_id=None)` — SUM over
  submitted transfers: `+shares where to=X` − `shares where from=X`, grouped by (shareholder, share_type).
- `cap_table(db, company_id)` — all holdings + each holder's **% of that share type's total issued**.

### Endpoints (`api/v1/accounts/share_transfers.py` + reports)
- `POST/GET /share-transfers`, `GET /share-transfers/{id}`, `POST .../submit`, `POST .../cancel`.
- `GET /reports/share-balance` (holdings per shareholder per type, with %) and
  `GET /reports/share-ledger` (chronological submitted transfers). Reuse the reports router.
- `seed.py`: new permissions for `Share Type`, `Shareholder`, `Share Transfer` (grant to Accounts
  Manager; optionally a dedicated `Share Manager` role).

### Migration
One migration (after Subscription's): `share_types`, `shareholders`, `share_transfers`. Company‑scoped,
no RLS (explicit company filter, like the rest of the accounting tables).

### Frontend
- Share Type + Shareholder = engine forms (`/m/share-type`, `/m/shareholder`) — free.
- Share Transfer = a small bespoke list+form view (`ShareTransferView.vue`): pick transfer type →
  show the right from/to pickers → shares + rate → submit/cancel.
- **Cap table**: a "Share Balance" + "Share Ledger" tab pair (in ReportsView, or a small standalone
  "Cap Table" view). A new sidebar group "Share Management" (Share Type, Shareholder, Share Transfer,
  Cap Table) — likely its own workspace card rather than under Accounting → Payments.

### Key decisions / risks
- **Whole shares** (`no_of_shares` = Integer). If fractional/partly‑paid shares are ever needed, widen
  to Numeric — decide at build time.
- **% ownership** uses total *issued* of that share type (Issues − Buybacks). Show par value × shares as
  nominal capital, informational.
- **Capital JE stays separate**: optionally offer a "create matching Journal Entry" convenience
  (Dr Bank / Cr Share Capital) but keep it a normal JE, **not** coupled into this module (spec §Logic).
- Lowest‑priority module — only build when there are real shareholders to track.

### Acceptance
Issue 1,000 equity shares to A, transfer 200 A→B → balances A=800, B=200, total issued 1,000; the
ledger lists both transfers; cancelling the transfer restores A=1,000/B=0 with no reversal rows
(balance is derived). A Buyback of 100 from A leaves A=700 and total issued 900.

> **Status:** ✅ **implemented** (2026‑06‑22). Migration `0050_share_management`; engine masters
> `Share Type` (`/m/share-type`) + `Shareholder` (`/m/shareholder`); bespoke `Share Transfer`
> (`services/share_transfer.py`, `api/v1/accounts/share_transfers.py`, `ShareTransferView.vue` at
> `/share-transfers`); **no GL**. Balances are **derived** from submitted transfers (no stored
> balance) — cancel just flips `docstatus` and the cap table recomputes. Cap Table + Share Ledger are
> read‑only ReportsView tabs (`/reports/share-balance`, `/reports/share-ledger`); UI lives in a new
> "Share Management" group under the Accounting workspace. Integration tests in
> `tests/integration/test_share_management.py` cover issue → transfer → cap‑table % → cancel‑restores,
> buyback reducing total issued, and the insufficient‑shares / transfer‑type guards. Share capital still
> hits the GL via a normal Journal Entry, separately. See [[document-delivery-plan]] and [[accounting-plan-status]].
</content>
