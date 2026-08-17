# Tally Import — Module 12 (Data Migration)

Bring a Tally.ERP 9 / TallyPrime company into OptiERP: masters, opening balances
and vouchers. Built for two audiences at once —

- **Testers** moving off a legacy Tally install, who need to import the same
  books here and compare screen by screen; and
- **Production customers** onboarding, for whom this is the migration path.

Both need the same thing: an import that is reviewable before it runs,
explainable when it goes wrong, and reversible afterwards.

---

## 1. How to use it

### Export from Tally

| What | Where in Tally | Notes |
|---|---|---|
| Masters | Gateway of Tally → **Export** → *All Masters* → Format: **XML** | Do this first — vouchers reference these names. |
| Vouchers | Display → **Day Book** → *Alt+E* (Export) → Format: **XML** | Set the period. Split very large ranges by quarter. |
| Fallback | Day Book → Export → Format: **CSV/Excel** | Works, but carries no GUIDs, bill references or godown detail. |

### Import into OptiERP

`Data Migration → Tally Imports`, or the **Import from Tally** button on any
module screen (Customers, Items, Warehouses, Chart of Accounts, …).

The wizard is four steps, each its own API call so you can stop and check
something in Tally between any two:

| Step | What it does | What it writes |
|---|---|---|
| **Upload** | Parses the file into staging rows | staging only |
| **1 · Auto-map** | Proposes an OptiERP record for every Tally name | mappings only |
| **2 · Dry run** | Reports how many documents, and what is unresolved | nothing |
| **3 · Run** | Creates the documents, posting GL and stock | documents |
| **Roll back** | Cancels everything the run created | reversing entries |

### API

Upload is JSON with the file base64-encoded — Tally writes UTF-16 as often as
UTF-8, and base64 gives the parser the exact bytes to sniff.

```bash
curl -X POST /api/v1/tally/imports -H 'Content-Type: application/json' \
  -d "{\"file_name\":\"masters.xml\",\"content_base64\":\"$(base64 -w0 masters.xml)\"}"

curl -X POST /api/v1/tally/imports/$ID/automap
curl -X POST /api/v1/tally/imports/$ID/validate    # dry run
curl -X POST /api/v1/tally/imports/$ID/run
curl -X POST /api/v1/tally/imports/$ID/rollback
```

`GET /api/v1/tally/catalogue` returns the whole mapping table below as JSON —
the UI and this document are generated from the same source
(`app/services/tally/catalogue.py`), so they cannot drift.

---

## 2. Entity coverage

**Full** = imported into a real record. **Partial** = imported, but some Tally
fields have no home here. **Reference** = staged and reported, never becomes a
document. **Not yet** = no target module exists — staged and counted so the gap
is visible rather than silent.

### Masters

| In Tally | Becomes | Coverage | Notes |
|---|---|---|---|
| Currency | Currency | Full | Matched on ISO code; unknown symbols reported, not invented. |
| Unit | UOM | Full | Compound units ("Box of 12 Nos") become a UOM Conversion. |
| Group | Account (group) | Full | The 28 reserved groups fix each branch's root type. |
| Ledger | Account (leaf) | Full | Every ledger becomes a COA leaf. |
| Ledger under Sundry Debtors | **+ Customer** | Full | GSTIN → `tax_id`; mailing details → Address + Contact. |
| Ledger under Sundry Creditors | **+ Supplier** | Full | Same. |
| Cost Centre | Cost Center | Full | |
| Cost Category | — | Reference | Tally's second, orthogonal cost dimension; we have one Cost Center tree. |
| Stock Group | Item Group | Full | |
| Stock Category | — | Reference | Orthogonal; written to `Item.brand` when the item has none. |
| Stock Item | Item | Full | HSN/SAC + GST rate + base unit carried over. |
| Godown / Location | Warehouse | Full | |
| Price List (Price Level) | Price List + Item Price | Partial | Quantity slabs collapse to the lowest-slab rate; slabs reported. |
| Voucher Type | Tally Voucher Type Mapping | Reference | User types inherit their reserved parent's mapping. |
| Budget | Budget | Partial | Ledger-wise amounts import; cost-centre budgets reported as unsupported. |

### Opening balances

| In Tally | Becomes | Notes |
|---|---|---|
| Ledger opening balance | Journal Entry (opening) | One balanced entry against Temporary Opening. Party ledgers with bill-wise detail become Opening Invoices so ageing survives. |
| Stock item opening balance | Stock Reconciliation (opening) | Qty + value per item/godown at the cut-off date. |

### Vouchers

| Tally voucher type | Becomes | Coverage |
|---|---|---|
| Sales | Sales Invoice | Full |
| Credit Note | Sales Invoice (return) | Full |
| Purchase | Purchase Invoice | Full |
| Debit Note | Purchase Invoice (return) | Full |
| Receipt | Payment Entry (Receive) | Full |
| Payment | Payment Entry (Pay) | Full |
| Contra | Payment Entry (Internal Transfer) | Full |
| Journal | Journal Entry | Full |
| Reversing Journal | Journal Entry | Partial — auto-reversal on the applicable date is not reproduced. |
| Sales Order | Sales Order | Full |
| Purchase Order | Purchase Order | Full |
| Delivery Note | Delivery Note | Full |
| Receipt Note | Purchase Receipt | Full |
| Rejections In | Delivery Note (return) | Full |
| Rejections Out | Purchase Receipt (return) | Full |
| Stock Journal | Stock Entry | Full — purpose follows which sides are present. |
| Manufacturing Journal | Stock Entry (Repack) | Full — Tally's BOM is not carried over. |
| Physical Stock | Stock Reconciliation | Full |
| Memorandum | — | Reference — non-accounting in Tally; staged, never posted. |
| Payroll / Attendance | — | **Not yet** — no HR module. Remap the voucher type to import the net journal. |

---

## 3. Ledger group → account classification

Tally ships 28 reserved ledger groups. Where a ledger sits decides what kind of
account it becomes — and whether it is *also* a Customer or a Supplier. Groups
you created yourself inherit the classification of the nearest reserved group
above them, so "Debtors – North" under "Sundry Debtors" still yields Customers.

| Tally group | Root type | Account type | Also becomes |
|---|---|---|---|
| Capital Account | Equity | Equity | |
| Reserves & Surplus *(alias Retained Earnings)* | Equity | Equity | |
| Current Liabilities | Liability | | |
| Duties & Taxes | Liability | Tax | |
| Provisions | Liability | | |
| **Sundry Creditors** | Liability | Payable | **Supplier** |
| Loans (Liability) / Secured / Unsecured Loans | Liability | | |
| Bank OD A/c *(alias Bank OCC A/c)* | Liability | Bank | |
| Branch / Divisions | Liability | | |
| Suspense A/c | Liability | Temporary | |
| Current Assets | Asset | | |
| Bank Accounts | Asset | Bank | |
| Cash-in-Hand | Asset | Cash | |
| Deposits (Asset) / Loans & Advances (Asset) | Asset | | |
| Stock-in-Hand | Asset | Stock | |
| **Sundry Debtors** | Asset | Receivable | **Customer** |
| Fixed Assets | Asset | Fixed Asset | |
| Investments / Misc. Expenses (ASSET) | Asset | | |
| Sales Accounts | Income | Income Account | |
| Direct Incomes / Indirect Incomes | Income | Income Account | |
| Purchase Accounts | Expense | Cost of Goods Sold | |
| Direct Expenses | Expense | Cost of Goods Sold | |
| Indirect Expenses | Expense | | |

A reserved group that the export never ships as a `GROUP` record (common in Day
Book exports) is **created on demand** under the right root, so the imported
Chart of Accounts keeps Tally's shape instead of collapsing to the root.

---

## 4. Design decisions worth knowing

**Documents go through the normal services.** An imported Sales Invoice is
created by `create_sales_invoice` and submitted by `submit_sales_invoice` — the
same code the UI calls. It posts GL, stock and GST identically. Nothing writes
into `gl_entries` directly. That is why an imported book reconciles.

**Tax amounts are copied, not recomputed.** Tally's tax ledger amounts become
`Actual` tax rows. An import must reproduce the customer's books to the paisa,
not improve on them.

**Sign convention.** In Tally's ledger entries a *negative* amount is a debit.
The parser converts once, up front, into explicit debit/credit.

**One transaction per record.** Each staging row commits on success and rolls
back on failure, so one malformed voucher out of 4,000 fails alone with a
readable reason and the other 3,999 still land.

**Nothing is silently dropped.** Every record in the file gets a staging row.
Unsupported entities are staged, counted and reported with the reason.

**Mappings are durable.** "Tally ledger X is our Customer Y" is stored per
company. Correcting one locks it, so a re-import — or the next file from the
same Tally company — keeps your decision.

**Rollback cancels, it does not delete.** Cancellation writes the reversing GL
and stock entries, so the audit trail shows what the import did *and* undid.
Master records are deliberately left in place: deleting them would orphan
anything created against them since.

**Fiscal years open automatically.** A migration is historical by definition; a
company created today has one fiscal year. The run opens the years its period
needs and logs which ones.

---

## 5. Known limits

- **Payroll / Attendance** — no HR module. Staged and counted, not posted.
- **Cost Categories** — one Cost Center tree here vs Tally's two dimensions.
- **Price slabs** — quantity-slab pricing collapses to the lowest-slab rate.
- **Credit/Debit notes without a traceable original** — Tally does not always
  record which invoice a note reverses. When no match is found the note is
  imported as a Journal Entry with the same ledger effect, and flagged.
- **CSV imports** — no GUIDs, no bill-wise references, no godown split. Every
  CSV import says so in its log.
- **Reversing Journals** — imported as ordinary journals; Tally's auto-reversal
  on the applicable date is not reproduced.

---

## 6. Where the code lives

| Path | Role |
|---|---|
| `app/services/tally/catalogue.py` | The mapping tables. Everything else reads them. |
| `app/services/tally/parser.py` | Tally XML + CSV → plain dicts. Encoding, entities, repeated tags. |
| `app/services/tally/mapping.py` | Which OptiERP record a Tally name means; auto-map + the run's lookup book. |
| `app/services/tally/context.py` | Per-run state: counters, messages, per-row transaction. |
| `app/services/tally/importers/masters.py` | Groups, ledgers, parties, units, items, godowns, cost centres, price lists. |
| `app/services/tally/importers/vouchers.py` | Invoices, payments, journals, stock notes, orders, stock journals. |
| `app/services/tally/runner.py` | The pipeline + entity ordering + rollback. |
| `app/api/v1/tally/` | `imports.py`, `mappings.py`, `catalogue.py`. |
| `app/models/tally.py`, migration `0093_tally_import` | Sessions, staging, mappings, logs. |
| `frontend/src/views/tally/` | Wizard, mapping editor, coverage matrix. |
| `tests/unit/test_tally_parser.py` | 41 tests — encoding, signs, classification, matching. |
| `tests/integration/test_tally_import.py` | 19 tests — full upload → run → rollback. |

See [TALLY_IMPORT_HOW_IT_WORKS.md](TALLY_IMPORT_HOW_IT_WORKS.md) for the
plain-language walkthrough of the same machinery.
