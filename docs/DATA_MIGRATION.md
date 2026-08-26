# Data Migration — Module 12

Bring a customer's existing books into OptiERP: masters, opening balances and
vouchers. Two ways in, one pipeline behind them:

| Source | How | Fidelity |
|---|---|---|
| **Tally.ERP 9 / TallyPrime** | XML export (or Day Book CSV) | Highest — GUIDs, bill references, godown splits, `ALTERID` change tracking |
| **Any application that exports Excel** | `.xlsx` workbook | Depends on the export; the shape is detected, reviewed, and saved for next time |

Built for two audiences at once —

- **Testers** moving off a legacy install, who need to import the same books
  here and compare screen by screen; and
- **Production customers** onboarding, for whom this is the migration path.

Both need the same thing: an import that is reviewable before it runs,
explainable when it goes wrong, and reversible afterwards.

Everything below the parser is source-agnostic. A Zoho Books workbook and a Tally
XML export become the same intermediate records, and from there the same code
posts them, dedupes them and rolls them back — which is why a spreadsheet import
is not a second-class path with its own bugs.

---

## 1. How to use it

### If you are on Tally

Export from Tally

| What | Where in Tally | Notes |
|---|---|---|
| Masters | Gateway of Tally → **Export** → *All Masters* → Format: **XML** | Do this first — vouchers reference these names. |
| Vouchers | Display → **Day Book** → *Alt+E* (Export) → Format: **XML** | Set the period. Split very large ranges by quarter. |
| Fallback | Day Book → Export → Format: **CSV/Excel** | Works, but carries no GUIDs, bill references or godown detail. |

### If you are on anything else

Export whatever your system gives you as **`.xlsx`** and upload that. Three
shapes are recognised on sight, with no setup:

| Shape | Recognised by | Notes |
|---|---|---|
| **Tally workbook dump** | `Voucher_Headers` + `Accounting_Lines` | The double entry is already in the file, so amounts are carried across verbatim. |
| **Tally flat sheet** | one `Transactions` sheet with `Debit_Ledger` / `Credit_Ledger` | Rows sharing a voucher number fold into one document. |
| **Zoho Books backup** | `Chart_of_Accounts` + `Invoices` + `Invoice_Line_Items` | Zoho ships documents, not journals, so the double entry is rebuilt — see §5. |

Anything else lands on the **Sheets & columns** step: say which sheet is what and
which column is which, press *Apply*, then save it as a profile. The next file of
that shape is recognised automatically, for the whole company.

No usable export at all? **Download the OptiERP template** (Data Migration →
Sources & Templates, or `GET /api/v1/migration/template.xlsx`), paste your data
in, and upload it back. A filled template skips the mapping step entirely.

### Import into OptiERP

`Data Migration → Imports`, or the **Import data** button on any module screen
(Customers, Items, Warehouses, Chart of Accounts, …).

The wizard is four steps — five for a spreadsheet — each its own API call so you
can stop and check something in the old system between any two:

| Step | What it does | What it writes |
|---|---|---|
| **Upload** | Parses the file into staging rows | staging only |
| **0 · Sheets & columns** *(spreadsheets only)* | Confirms which sheet is what and which column is which; re-parses on change | staging only |
| **1 · Auto-map** | Proposes an OptiERP record for every name in the file | mappings only |
| **2 · Dry run** | Reports how many documents, what is unresolved, and what you already have | nothing |
| **3 · Run** | Creates the documents, posting GL and stock | documents |
| **Roll back** | Cancels everything the run created | reversing entries |

The run happens **in the background** — a full year of vouchers takes far longer
than a request should. `/run` answers `202` immediately with status `Importing`;
poll `/status` for progress. You can close the page and come back.

### API

Upload is JSON with the file base64-encoded. That is deliberate on two counts:
Tally writes UTF-16 as often as UTF-8, so base64 hands the parser the exact bytes
to sniff; and an `.xlsx` is a binary ZIP that would not survive being treated as
text at all.

```bash
BASE=/api/v1/migration

curl -X POST $BASE/imports -H 'Content-Type: application/json' \
  -d '{"file_name":"masters.xml","content_base64":"<base64 of the file>"}'

curl -X POST $BASE/imports/$ID/automap
curl -X POST $BASE/imports/$ID/validate    # dry run
curl -X POST $BASE/imports/$ID/run         # 202; runs in the background
curl         $BASE/imports/$ID/status      # poll until is_running=false
curl -X POST $BASE/imports/$ID/rollback

curl         $BASE/sync-state              # ALTERID watermark per Tally company
```

Spreadsheet-only:

```bash
curl         $BASE/imports/$ID/mapping     # sheets, columns, sample values, rival profiles
curl -X PUT  $BASE/imports/$ID/mapping \
  -d '{"definition": {...}}'               # or {"profile":"zoho_books"} — either re-parses

curl         $BASE/sources                 # built-in + this company's saved shapes
curl -X POST $BASE/sources \
  -d '{"label":"Busy 21 export","migration_import_id":"<id>"}'
curl -OJ    "$BASE/template.xlsx?entities=ledger,stock_item"
```

`GET /api/v1/migration/catalogue` returns the whole mapping table below as JSON —
the UI and this document are generated from the same source
(`app/services/migration/catalogue.py`), so they cannot drift.

The old `/api/v1/tally/*` paths are gone; the UI routes `/tally/*` redirect to
their `/data-migration/*` replacements so existing bookmarks keep working.

---

## 2. Entity coverage

**Full** = imported into a real record. **Partial** = imported, but some Tally
fields have no home here. **Reference** = staged and reported, never becomes a
document. **Not yet** = no target module exists — staged and counted so the gap
is visible rather than silent.

### Masters

| In Tally | Becomes | Coverage | Notes |
|---|---|---|---|
| Currency | Currency + Currency Exchange | Full | Matched on ISO code, never invented; a stated rate becomes a Currency Exchange dated from the cut-off. |
| Payment terms | Payment Terms Template | Full | "Net 30" becomes a template with one 100% term. Split terms need one row per instalment sharing a name. |
| Unit | UOM | Full | Compound units ("Box of 12 Nos") become a UOM Conversion. |
| Group | Account (group) | Full | The 28 reserved groups fix each branch's root type. |
| Ledger | Account (leaf) | Full | Every ledger becomes a COA leaf. |
| Ledger under Sundry Debtors | **+ Customer** | Full | GSTIN → `tax_id`, plus payment terms, tax category, customer group, territory, credit limit, credit days, registered name, notes and disabled. |
| Ledger under Sundry Creditors | **+ Supplier** | Full | Same, with supplier group in place of customer group and territory. |
| Party address (inline, or its own sheet) | Address | Full | A Tally ledger carries it inline; a spreadsheet usually ships an address sheet joined by the party's id. Both land as the same rows. |
| Party contact person | Contact | Full | Name, email, phone, mobile, designation. |
| Bank account | Bank + Bank Account | Full | Account number, IFSC, and the ledger account it posts to — which is what reconciliation needs. |
| Tax rate master | Tax Template | Partial | One template per rate, with CGST/SGST/IGST rows. Imported documents keep their own tax amounts; this is for invoices raised *after* the move. |
| Fixed asset | Asset (Draft) | Partial | Cost, purchase date and accumulated depreciation to date, so book value is right from day one. Historic depreciation is **not** replayed — it is already inside the opening balances. |
| Cost Centre | Cost Center | Full | |
| Cost Category | — | Reference | Tally's second, orthogonal cost dimension; we have one Cost Center tree. |
| Stock Group | Item Group | Full | |
| Stock Category | — | Reference | Orthogonal; written to `Item.brand` when the item has none. |
| Stock Item | Item | Full | HSN/SAC + GST rate + base unit carried over. |
| Godown / Location | Warehouse | Full | |
| Price List (Price Level) | Price List + Item Price | Partial | Quantity slabs collapse to the lowest-slab rate; slabs reported. |
| Voucher Type | Tally Voucher Type Mapping | Reference | User types inherit their reserved parent's mapping. |
| Budget | Budget + Budget Account | Partial | Rows sharing a budget name fold into one Budget with a line per account. Needs a fiscal year covering the period. |

### Opening balances

| In Tally | Becomes | Notes |
|---|---|---|
| Ledger opening balance | Journal Entry (opening) | One balanced entry booked on the migration cut-off date. Party rows carry their Customer/Supplier, so the balance reaches the ageing reports. Any difference squares off against Temporary Opening and is logged. |
| Stock item opening balance | Stock Reconciliation (purpose: Opening Stock) | Qty + value **per godown**, from the item's `BATCHALLOCATIONS.LIST`. Items with no godown need a default warehouse on the import. |

**Not yet:** per-bill opening ageing. Tally's bill-wise opening detail is not
split into Opening Invoices — a party's opening lands as one balance against the
party, so the total and the party attribution are right but the invoice-level
ageing buckets are not.

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
| Quotation | Quotation | Full — needs its item lines, like any order. |
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
| (bank statement lines) | Bank Transaction | Partial — reconciliation input, not an accounting entry. Posts nothing, arrives Unreconciled, runs last so the payments it will match already exist. |

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

**One intermediate shape, four front doors.** Tally XML, Tally CSV, a workbook
and a filled template all become the same records before anything else runs:
entity-keyed dicts whose payload is Tally-XML-shaped
(`ALLLEDGERENTRIES.LIST`, `ALLINVENTORYENTRIES.LIST`, `BILLALLOCATIONS.LIST`).
That is not elegance for its own sake — it is why a Zoho Books import gets the
same GL posting, the same duplicate detection and the same rollback as a Tally
one, rather than a parallel implementation of each with its own bugs. The cost is
that a new adapter has to speak Tally's vocabulary; `sources/builders.py` exists
so nobody has to remember that a debit is a negative `AMOUNT`.

**A rebuilt double entry is checked, never nudged.** Some sources ship documents
rather than journals: a Zoho invoice knows its customer, its lines and its tax,
but not its postings. Those are reconstructed from the document's own totals —
party, revenue per line account, GST per head, TDS withheld, discount — and then
*asserted to balance*. Sub-paisa rounding lands in an explicit Round Off row;
anything larger makes the row an `Error` carrying the exact difference, reported
by the dry run before anything posts. An importer that silently absorbed a
one-rupee gap would produce a wrong trial balance nobody finds for months.

**Accounts a rebuilt entry needs are created, and said so.** A flat Tally sheet
carries GST in columns, not ledgers — so there is no "CGST Output" account to
post to. Those accounts are created under the right group (`Duties & Taxes`, so
GST still reaches the returns) and the parse log names every one of them. The
alternative — resolving to nothing and dropping the amount — is an import that
"succeeded" with a hole in it.

**A spreadsheet has no GUIDs, so identity is derived.** Every record gets a
deterministic `source_guid` from its natural key (voucher number + date, invoice
id), prefixed `x-` so it is visibly synthetic in the staging table. Re-uploading
the same workbook therefore hits the same duplicate detection a second Tally
export would. Change the voucher numbers in the file and that guarantee is gone,
which is the honest limit of the technique.

**The mapping is stored on the session, not looked up.** Re-parsing reads the
session's own `sheet_map`, so it reproduces the mapping the tester reviewed
rather than re-detecting and possibly deciding differently. Saving it as a
company profile is a separate, deliberate act.

**The wizard can only describe what the importer can build.** Sheet shapes,
fields and line-sheet roles all come from one table in
`sources/profiles.py`, which the API serves to the UI. A tester cannot map a
sheet into something the normaliser has no code for, so "the mapping looked fine
and then the import failed" is not a state the feature can reach.

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

**A voucher can only be imported once.** Every imported transaction is recorded
against its Tally GUID in `tally_imported_documents`, unique per company. Export
Apr–Jun, import it, then export Apr–Sep and import that: the April–June vouchers
are recognised and skipped, not posted twice. The dry run reports the overlap
*before* you commit, so "900 records" reads honestly as "588 new, 312 you already
have". Rolling back releases those identities, so the
import → compare → roll back → fix → import loop still works.

**An edited voucher is flagged, never silently rewritten.** Tally bumps `ALTERID`
on every change. Same GUID with a higher `ALTERID` means the voucher changed in
Tally *after* you imported it — the two copies genuinely disagree. That is
reported separately from an ordinary repeat, and the document here is left
untouched: correcting a posted document is a decision with reversing entries
attached, not something an importer should do behind your back.

**The run is a background task, and an abandoned one is recoverable.** A run
that dies with its process would otherwise sit in `Importing` forever — and
rollback refuses that status, so it could be neither finished nor undone. The run
writes a heartbeat; a reaper retires runs that stop beating (at startup and every
10 minutes), leaving them `Failed`, explained, and rollback-able.

**Rollback cancels each document once, not once per row that names it.**
Opening balances are deliberately many staging rows against a *single* Journal
Entry — they are one balanced entry by design. Unwinding by staging row therefore
asked to cancel that entry once per ledger: the first call succeeded and every
one after it failed with "Document is not submitted", so a clean rollback
reported failures it had not had. The unwind now claims each target document
once, and the rows behind it are marked rolled back with it. (Found by the
spreadsheet path, but it was never Tally-specific.)

**Rollback cancels, it does not delete.** Cancellation writes the reversing GL
and stock entries, so the audit trail shows what the import did *and* undid.
Master records are deliberately left in place: deleting them would orphan
anything created against them since.

**Fiscal years open automatically.** A migration is historical by definition; a
company created today has one fiscal year. The run opens the years its period
needs and logs which ones.

---

## 5. Known limits

### Not imported at all

Per source, with the reason on every row — the Sheets & columns step and the
parse log both name them, so the gap is visible rather than silent.

Each reason below names the specific thing OptiERP lacks. The Sheets & columns
step shows the same sentence against the sheet itself, so nobody has to read this
file to find out why something was skipped.

**No module to receive it.**

- **Payroll / Attendance** — no Employee, Salary Structure or Payroll Entry.
  Staged and counted; the net payroll journal can still come across by mapping
  the voucher sheet as a Journal.
- **Projects, tasks and time entries** — no Projects or Timesheets module. The
  project id on an invoice or expense is kept on the staging row and dropped
  from the document.

**No concept to receive it.**

- **Cost categories, stock categories and reporting tags** — a second,
  independent classification axis. There is one tree for each of these here, and
  folding two dimensions into one would make both wrong.
- **Tally scenarios** — provisional books layered over the real ones.
- **Retainer invoices** — an invoice for an advance against no specific bill. The
  money is not lost: the matching payment imports with its unallocated amount
  intact, which is what the ledger cares about.
- **Recurring invoice / expense profiles** — a rule that *generates* future
  documents. The documents it has already raised are in the ordinary invoice and
  expense sheets and do import; recreate the schedule under Subscriptions.
- **PAN** (`INCOMETAXNUMBER`) — Customer and Supplier have no field for it. Read
  and discarded rather than stuffed into a notes box where nothing can query it.

**Nothing missing here — the file does not carry it.**

- **Quotes, sales orders and purchase orders in a Zoho backup** — Quotation,
  Sales Order and Purchase Order all exist and import fully, *with* their lines.
  That particular export ships order headers and no line-item sheet, and the
  lines are what the order is: importing the totals alone would create a
  document nobody can fulfil or match a receipt against. If your export does
  include the lines, map the sheet as an order and point it at them.

**Deliberate, and it should stay that way.**

- **Users** — a login is an access decision, not accounting data. Importing one
  would create an account nobody chose to grant, with roles guessed from another
  system's vocabulary. Invite them from Settings.
- **Company / Organization setup** — the company already exists, with its own
  GSTIN, PAN and fiscal year, before the import starts. Overwriting that from a
  file would silently repoint every document already in the books.
- **Audit trails** — OptiERP writes its own, and every row in it is one it saw
  happen. Importing another system's history would mean recording changes made
  by users who do not exist here, at times nothing in these books can
  corroborate — which is exactly what an audit trail must never contain. Keep
  the source export as the record of the period before the move.
- **Computed summaries** (GST summary, outstanding bills, transaction counts,
  batch movement, bank reconciliation state) — recomputed here from the
  vouchers being imported. A second copy could only ever disagree with the
  ledger.

### Imported, but less than Tally holds

- **Opening bill-wise ageing** — a party's opening balance imports as one figure
  against the party. The total and the party attribution are right; the
  per-invoice ageing buckets are not. Tally's bill-wise opening detail is not
  split into Opening Invoices.
- **Tax ledger rate and head** — `GSTDUTYHEAD` and `RATEOFTAXCALCULATION` are not
  copied onto the account. Voucher tax *amounts* are imported verbatim, so the
  books are right; the ledger master just carries less than Tally's does.
- **Fixed assets** — cost and accumulated depreciation arrive, and the asset is
  a Draft with the right book value. Its depreciation *schedule* restarts from
  the migration cut-off, because everything before it is already in the opening
  balances; replaying it would depreciate the asset twice. Set the category's
  three GL accounts before submitting.
- **Tax templates** — one template per rate with CGST/SGST/IGST rows, built for
  invoices raised after the move. Imported documents are untouched by it: they
  carry their own tax amounts across, line by line.
- **Bank statement lines** — imported unreconciled, and the source's own
  "matched" flag is not honoured, because the voucher it was matched to is a
  different record here. Reconcile them with the Bank Reconciliation tool.
- **Masters the source implies but does not ship** — a Tax Category behind a
  `gst_treatment`, a Customer Group behind a party type. These are matched to an
  existing master by name and created when there is none, and every create is
  reported as a warning on the row that caused it.
- **Price slabs** — quantity-slab pricing collapses to the lowest-slab rate.
- **Credit/Debit notes without a traceable original** — Tally does not always
  record which invoice a note reverses. When no match is found the note is
  imported as a Journal Entry with the same ledger effect, and flagged.
- **CSV imports** — no GUIDs, no bill-wise references, no godown split, and no
  `ALTERID`, so neither duplicate detection nor amendment detection can work on
  them. Every CSV import says so in its log. Use XML for anything you may import
  more than once.
- **Spreadsheets** — no `ALTERID`, so a voucher edited in the source system after
  you imported it is not flagged as an amendment the way a Tally re-export is.
  Duplicate detection still works (see §4), but only while the document numbers
  stay stable.
- **Rebuilt double entry** — where a source ships documents rather than journals,
  the postings are OptiERP's reconstruction, not the source's own ledger. The
  totals reconcile to the paisa and every rebuilt row is marked as such in its
  staging payload, but if the source booked something to an account its export
  does not mention, that detail is not recoverable from the file.
- **A combined tax figure** — a document that gives one `tax_total` and no
  CGST/SGST/IGST split is posted to the integrated (IGST) head, because which
  heads it splits into is not knowable from the file. Check the GST returns after
  importing from a source that exports tax this way.
- **Reversing Journals** — imported as ordinary journals; Tally's auto-reversal
  on the applicable date is not reproduced.

### Needs something from you

- **Opening balances need a cut-off date.** Set *Opening date* on the import, or
  every opening is skipped with that reason on its row. It defaults to the
  export's period start when Tally wrote one.
- **Opening stock needs a warehouse.** Tally splits opening stock per godown; if
  the export names none and the company has more than one warehouse, set a
  default warehouse on the import rather than have the importer guess.

---

## 6. Where the code lives

| Path | Role |
|---|---|
| `app/services/migration/catalogue.py` | The mapping tables. Everything else reads them. |
| `app/services/migration/sources/ir.py` | The intermediate shape every adapter produces, plus value coercion. |
| `app/services/migration/sources/tally_xml.py` | Tally XML + CSV → the pipeline's records. Encoding, entities, repeated tags. |
| `app/services/migration/sources/workbook.py` | `.xlsx`/`.csv` → sheets of rows. Header sniffing, coercion, size cap. No opinion about meaning. |
| `app/services/migration/sources/profiles.py` | What a sheet/column mapping *is*: shapes, fields, line-sheet roles, (de)serialisation. |
| `app/services/migration/sources/builtin.py` | The shapes we ship knowing: Tally workbook, Tally flat, Zoho Books, the OptiERP template. |
| `app/services/migration/sources/detect.py` | Scoring a workbook against every profile; the custom fallback. |
| `app/services/migration/sources/builders.py` | Constructing the IR — sign convention, quantities, bill refs, the balance check. |
| `app/services/migration/sources/normalise.py` | Folding a workbook into the IR, one shape at a time. |
| `app/services/migration/sources/templates.py` | Generating the OptiERP import template from the catalogue. |
| `app/services/migration/workbooks.py` | The mapping step: wizard payload, re-parse on edit, saved company profiles. |
| `app/services/migration/mapping.py` | Which OptiERP record a Tally name means; auto-map + the run's lookup book. |
| `app/services/migration/context.py` | Per-run state: counters, messages, per-row transaction. |
| `app/services/migration/importers/masters.py` | Groups, ledgers, parties, units, items, godowns, cost centres, price lists. |
| `app/services/migration/importers/vouchers.py` | Invoices, payments, journals, stock notes, orders, stock journals. |
| `app/services/migration/runner.py` | The pipeline + entity ordering + rollback. |
| `app/api/v1/migration/` | `imports.py` (+ `/mapping`), `mappings.py`, `sources.py` (+ `template.xlsx`), `catalogue.py`. |
| `app/services/migration/importers/openings.py` | Opening balances: ledger -> one Journal Entry, stock -> one Stock Reconciliation. |
| `app/services/migration/background.py` | Running a run off the request thread; the stale-run reaper. |
| `app/jobs/migration_reaper.py` | Scheduled sweep for abandoned runs. |
| `app/models/migration.py`, migrations `0093`, `0098`, `0099`, `0102` | Sessions, staging, mappings, logs, imported-document identities, heartbeat, saved source profiles. |
| `frontend/src/views/migration/` | Wizard, sheet/column mapping step, name-mapping editor, coverage matrix, sources & templates, and the in-app Tally export guide. |
| `tests/fixtures/migration/` | Fixtures shaped like a real TallyPrime export — see the README there for why. |
| `tests/unit/test_migration_tally_real_export.py` | Drives those fixtures; a failure here means a real customer's import is broken. |
| `tests/unit/test_migration_tally_parser.py` | Encoding, signs, classification, matching, tax direction, reserved masters. |
| `tests/unit/test_migration_workbook.py` | Header sniffing under banner rows, value coercion, format sniffing, the size cap. |
| `tests/unit/test_migration_spreadsheet.py` | Profile round-trip, sign convention, rebuilt entries balancing, TDS, opening de-duplication, template round-trip — and the three real sample exports in `docs/migration/`. |
| `tests/integration/test_migration_import.py` | Full upload → run → rollback, plus openings, idempotency and background execution. |

The sample workbooks the built-in profiles were written against live in
[`docs/migration/`](migration/) and are exercised by the unit tests above, so
"we support Zoho Books" means a specific file shape a test actually imports.

See [DATA_MIGRATION_HOW_IT_WORKS.md](DATA_MIGRATION_HOW_IT_WORKS.md) for the
plain-language walkthrough of the same machinery.
