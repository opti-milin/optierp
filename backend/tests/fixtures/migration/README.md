# Real export fixtures

These are shaped like what TallyPrime actually writes, not like hand-written
sample XML. That distinction has cost real bugs.

The spreadsheet side follows the same rule from the other direction: the
built-in workbook profiles were written against the genuine 500-transaction
sample exports in [`docs/migration/`](../../../../docs/migration/), and
`tests/unit/test_migration_spreadsheet.py` reads those files directly rather than
a fixture written to agree with the profile.

Every defect found while testing Module 12 against a customer's genuine export
passed the hand-written fixtures first:

| What broke | Why the old fixtures missed it |
|---|---|
| Company name erased | No `<COMPANY><REMOTECMPINFO.LIST>` trailer |
| Every reserved group its own parent | No `RESERVEDNAME` attribute on groups |
| Purchase GST subtracted instead of added | No tax ledger on any purchase voucher |
| Party addresses lost | Address written flat, not in `LEDMAILINGDETAILS.LIST` |
| Invoice due dates missing | No `BILLCREDITPERIOD` on bill allocations |
| Opening balances silently ignored | No `OPENINGBALANCE` anywhere |
| Item's own ledger unchecked | No nested `ACCOUNTINGALLOCATIONS.LIST` |

So the rule for this directory: **shapes come from real exports.** If you are
tempted to simplify one because it looks noisy, that noise is the point.

## Files

| File | Contents |
|---|---|
| `masters.xml` | Currency, the reserved group tree, ledgers with nested GST and mailing blocks, a stock item with per-godown opening stock |
| `transactions.xml` | A cancelled voucher and a purchase invoice with IGST, bill-wise reference and credit period |

Stored as UTF-8 so they stay diffable in review. Tally itself writes UTF-16LE
with a BOM; `tests/unit/test_tally_real_export.py` encodes them to UTF-16 to
exercise that path, so the encoding is covered without an unreadable blob in
the repository.
