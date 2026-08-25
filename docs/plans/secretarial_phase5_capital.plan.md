# Secretarial Phase 5 — Capital & s.186

**Date:** 2026-08-21 · **Migration:** `0100_secretarial_capital` (head was `0099_tally_import_heartbeat`)
**Living doc:** [SECRETARIAL_GAP_AND_PLAN.md](../SECRETARIAL_GAP_AND_PLAN.md) §10 Phase 5.

Phases 0–4 are built. This plan is the capital slice: who owns the shares, how ownership
moved, what paper proves it, and what the board was allowed to lend or invest.

---

## 1. The load-bearing decision

**The accounts cap table stays the single source of holdings. Secretarial wraps it; it does
not copy it.**

`app/models/accounts/share.py` already keeps an append-only `share_transfers` ledger from
which every holder's balance is *derived* (never stored). Secretarial's job is the legal
overlay the Companies Act asks for and a cap table does not carry: the SH-4 instrument, the
board approval that authorised it, the stamp duty, the distinctive share numbers, and the
certificate that gets cancelled and reissued.

So `secretarial_share_transfer_details` is a **wrapper row keyed to a `share_transfers` row**,
not a second ledger. Posting the transfer creates (or submits) the accounts-side movement in
the same transaction; reverting cancels it. There is exactly one place that knows how many
shares Priya holds, and `SecretarialMember.shares_held` becomes the *opening position* only —
as the Phase-1 docstring on that model already promised.

For a **managed** client (books not in this tenant) there is no accounts cap table to wrap.
The wrapper then stands alone: `share_transfer_id` is null, the register still balances from
the secretarial rows, and the UI says which mode it is in. That is the same honesty the
financial-facts panel already applies to ledger-derived vs manual figures.

## 2. Distinctive numbers are a serialised range allocator

Share certificates carry *distinctive numbers* — a contiguous range per share class, and the
ranges across all live certificates of a class must tile the issued capital with no gap and
no overlap. That is the same integrity mechanism as the minutes book: not `MAX()+1`, but a
locked counter row.

Reuse the shape of `ss_dates.allocate_minutes_number`: a
`secretarial_distinctive_seq(entity_id, share_class)` row taken `FOR UPDATE`, handing out
`(from, to)` and advancing. Numbers are consumed at **issue**, so a draft allotment never
burns a range. A cancelled certificate's range is *not* returned to the pool — it is
re-issued against the same range on the replacement certificate, because the range identifies
the shares themselves, not the paper.

## 3. Tables (`0100`)

| Table | Shape |
|---|---|
| `secretarial_share_transfer_details` | SH-4 wrapper. `share_transfer_id` → accounts (nullable), transferor/transferee + folios, consideration, stamp duty, SH-4 date, lodgement date, board approval (`meeting_id`/`agenda_item_id`), `distinctive_from/to`, state machine, `reverted_reason` |
| `secretarial_share_certificates` | SH-1. `certificate_no` (gap-free per entity), member, class, count, distinctive range, `issue_type` original·duplicate·renewed·split·consolidation, `status` issued·cancelled·surrendered, `supersedes_id`, `deferred` |
| `secretarial_distinctive_seq` | Locked counter per (entity, share_class) — §2 |
| `secretarial_capital_events` | Right issue · private placement · ESOP grant · bonus · buyback · dividend. Authorising meeting/circular, offer & record dates, capital before/after, allotment date, state machine, PAS-3 filing link |
| `secretarial_s186_limits` | Per (entity, FY): paid-up capital, free reserves, securities premium → the 60% limit and the 100%-of-net-worth limit, whichever is higher; `source` ledger·manual; special-resolution link that lifts the cap |
| `secretarial_s186_entries` | The s.186 register: loan · guarantee · security · investment, party, amount, rate, purpose, board/special resolution, repayment |

`company_id` + entity scope + RLS on every one, matching `0096`.

## 4. State machines

| Object | States | Gates |
|---|---|---|
| Share transfer | `draft → board_approved → issued_posted → [reverted]` | approval needs a meeting or circular; posting allocates the distinctive range, cancels the transferor's certificate and issues the transferee's **in one transaction**; revert demands a reason and realigns the register while leaving every row in place |
| Certificate | `issued → cancelled \| surrendered` | never edited; a replacement supersedes and carries the same distinctive range |
| Capital event | `draft → approved → allotted → [cancelled]` | allotment needs the authorising resolution; a dividend event is refused if it exceeds distributable profit (§5) |

DB CHECKs mirror each one, as `0096` does.

## 5. Two computed guards

**s.186 enabling limit** — free reserves and securities premium come from the ledger where the
books are here, from the manual facts row where they are not; the limit is the higher of 60% of
(paid-up + free reserves + securities premium) and 100% of free reserves, and a passed special
resolution lifts it. Recording an entry above the limit without that resolution is refused with
the numbers in the message, not a bare 422.

**Dividend distributable profit** — s.123: current-year profit after depreciation plus accumulated
profits, less accumulated losses. Declaring above it is blocked the same way.

Both return `unknown` rather than `ok` when a figure is missing — the rule the applicability
engine already follows: never tell someone they are clear because a number is absent.

## 6. Surface

- **Backend:** `models/secretarial/capital.py` · `schemas/secretarial_capital.py` ·
  `services/secretarial/capital.py` + `s186.py` · `api/v1/secretarial/capital.py`
- **Frontend:** `/secretarial/capital` (tabs: Transfers · Certificates · Capital events ·
  Dividend check) and `/secretarial/s186`; sidebar gets a **Capital** group
- **Content packs (6 new):** `share-transfer-sh4` · `share-certificate-sh1` · `right-issue` ·
  `private-placement` · `esop-grant` · `s186-resolution`, seeded `draft` behind the publish gate
- **RBAC:** four new doctypes appended to `_SECRETARIAL_DOCTYPES` in `scripts/seed.py`, so
  every existing role and every engagement projection picks them up with no new matrix rows

## 7. Demo data (the second half of this work)

`seed_secretarial_scenario.py` grows from "one row per register" to full lifecycles, so every
submodule has something true to click:

- Meetings driven **through** the chain — notice sent → held with attendance → minutes drafted
  → minutes signed with real book numbers — not left at `draft`
- A circulation with recipients at `viewed` and `acknowledged`; a circular carried by actual
  consent responses to `passed`, then ratified
- Issued CTCs, including one superseding another with a reason
- Filings that produce a **complete** evidence chain, and one deliberately gappy
- Facts for three FYs so the compliance calendar has history
- Phase 5: an authorised capital of 10,00,000; certificates for both founders with tiling
  distinctive ranges; a completed SH-4 transfer of 5,000 shares with certificate cancel/reissue;
  a right issue allotted; an ESOP grant; s.186 limits with one compliant loan and one that
  needed the special resolution

## 8. Order of work

1. Models + `0100` + `__init__` exports
2. Schemas
3. `capital.py` service (transfers, certificates, distinctive allocator, events)
4. `s186.py` service (limits, entries, dividend check)
5. Router + wiring + RBAC doctypes
6. Content packs
7. Frontend types, two views, router, sidebar
8. Deep seed
9. Unit tests + docs
