# Data Migration — spreadsheet (.xlsx/.csv) import from any application

**Date:** 2026-08-24 · **Branch:** `feat/ocr` → (migration work) · **Module:** 12

> Generalises Module 12 from "Tally XML import" to "**Data Migration**": the same
> staging → automap → dry run → run → rollback pipeline, fed by *any* source —
> Tally XML, Tally CSV, or a spreadsheet exported by Tally, Zoho Books, or an
> application nobody has profiled yet.

Living doc after implementation: [`docs/DATA_MIGRATION.md`](../DATA_MIGRATION.md)
(rename of `TALLY_IMPORT.md`).

---

## 1. Why this shape

The existing Tally pipeline is already source-agnostic below the parser. Everything
expensive — GL/stock posting through the real services, per-row transactions, the
durable name book, GUID-level dedupe, amendment detection, background runs with a
heartbeat reaper, and rollback-by-cancellation — sits *downstream* of one function:

```
parser.parse(raw) -> (ParsedFile, source_type, text)
```

`ParsedFile` buckets records by catalogue entity key, and each record is a plain
dict whose `data` member is Tally-XML-shaped (`ALLLEDGERENTRIES.LIST`,
`ALLINVENTORYENTRIES.LIST`, `BILLALLOCATIONS.LIST`, …).

So the whole feature is: **teach the front of the pipe to read workbooks, and emit
that same intermediate representation.** ~4,000 lines of importers, dedupe and
rollback are reused untouched.

```
upload (.xml | .csv | .xlsx)
        │
        ├─ xml/csv ──────────────► sources/tally_xml.py        (today's parser)
        │
        └─ xlsx/csv workbook ────► sources/workbook.py         read sheets
                                        │
                                   sources/detect.py           score built-in + saved profiles
                                        │
                                   sources/profiles.py         SourceProfile (sheet→entity, column→field)
                                        │
                                   sources/normalise.py        ──► ParsedFile  (same IR)
                                        │
        ┌───────────────────────────────┘
        ▼
[ staging → automap → validate → run → rollback ]   ← unchanged
```

The IR is the contract. A profile's only job is to answer "which sheet is which
entity, which column is which field, and how do header rows join to line rows".

---

## 2. Phase 0 — rename Module 12 to Data Migration

Nothing is Tally-branded any more except the Tally *source adapter*. Landed first
so every later phase is written against final names.

**Database — `0102_data_migration_rename`** (reversible)

| From | To |
|---|---|
| `tally_imports` | `migration_imports` |
| `tally_import_entities` | `migration_import_entities` |
| `tally_staging_records` | `migration_staging_records` |
| `tally_mappings` | `migration_mappings` |
| `tally_imported_documents` | `migration_imported_documents` |
| `tally_import_logs` | `migration_import_logs` |

Column renames carry the same generalisation — a Zoho record flows through these
columns too, so they stop claiming to be Tally's:

| From | To |
|---|---|
| `tally_import_id` | `migration_import_id` |
| `tally_guid` (staging, mappings, imported docs) | `source_guid` |
| `tally_name` / `tally_parent` / `tally_voucher_type` | `source_name` / `source_parent` / `source_voucher_type` |
| `tally_imports.tally_company_name` | `source_company_name` |
| `tally_imports.tally_guid` | `source_company_guid` |

Plus: rename indexes, unique constraints and the two RLS policies; `UPDATE
role_permissions SET doctype='Data Migration' WHERE doctype='Tally Import'`;
naming series `TALLY-IMP-.YYYY.-` → `MIGRATION-.YYYY.-`.

**New columns on `migration_imports`** (this phase, used from Phase 1):

- `source_app` — `Tally` · `Zoho Books` · `OptiERP Template` · `Custom`
- `source_profile` — profile key that parsed it
- `sheet_map` JSONB — the resolved (possibly user-edited) mapping, so a re-parse
  reproduces the run exactly
- `payload_encoding` — `text` | `base64`; a workbook is binary and `payload` is Text

**Backend moves**

| From | To |
|---|---|
| `app/models/tally.py` | `app/models/migration.py` |
| `app/schemas/tally.py` | `app/schemas/migration.py` |
| `app/services/tally/` | `app/services/migration/` |
| `app/services/tally/parser.py` | `app/services/migration/sources/tally_xml.py` |
| `app/api/v1/tally/` | `app/api/v1/migration/` |
| `app/jobs/tally_reaper.py` | `app/jobs/migration_reaper.py` |
| `tests/{unit,integration}/test_tally_*` | `test_migration_*` |

Class renames `Tally*` → `Migration*`. API prefix `/api/v1/tally/*` →
`/api/v1/migration/*`.

**Careful:** `tally` is also an ordinary English word in this repo — the
secretarial circular-resolution **vote tally** (`CircularTally`, `TallyOut`,
`services/secretarial/circular.py`). Those must not be touched.

**Frontend moves**

| From | To |
|---|---|
| `types/tally.ts` | `types/migration.ts` |
| `views/tally/TallyImportView.vue` | `views/migration/MigrationImportView.vue` |
| `views/tally/TallyImportDetailView.vue` | `views/migration/MigrationImportDetailView.vue` |
| `views/tally/TallyCoverageView.vue` | `views/migration/MigrationCoverageView.vue` |
| `views/tally/TallyMappingEditor.vue` | `views/migration/MigrationMappingEditor.vue` |
| `views/tally/TallyRecordTable.vue` | `views/migration/MigrationRecordTable.vue` |
| `views/tally/TallyExportGuideView.vue` | `views/migration/TallyExportGuideView.vue` *(stays Tally — it is a Tally guide)* |
| `components/shared/ImportFromTallyButton.vue` | `components/shared/ImportDataButton.vue` |

Routes: `/tally` → `/data-migration/imports`, `/tally/imports/:id` →
`/data-migration/imports/:id`, `/tally/coverage` → `/data-migration/coverage`,
`/tally/guide` → `/data-migration/guides/tally`.

---

## 3. Phase 1 — workbook reader, source profiles, normaliser

New dependency: **`openpyxl>=3.1`** (read-only + write, pure Python, no native libs).

### 3.1 `sources/workbook.py`

`.xlsx`/`.xlsm` via openpyxl read-only; `.csv`/`.tsv` as a single-sheet workbook.
Yields `Workbook(sheets=[Sheet(name, headers, rows)])` with:

- header row detected as the first row with ≥2 non-empty cells that is followed by
  data (skips the title/banner rows exports love to prepend);
- values coerced once — `datetime` → `date`, numeric-looking strings → `Decimal`,
  `""` → `None`;
- a hard cap on cells read, with a clear "split by period" error like the XML path.

### 3.2 `sources/profiles.py` — the mapping model

```python
@dataclass(frozen=True)
class ColumnSpec:      field: str; aliases: tuple[str, ...]; required: bool = False
@dataclass(frozen=True)
class ChildSpec:       sheet: str; role: str; key: str; columns: tuple[ColumnSpec, ...]
@dataclass(frozen=True)
class SheetSpec:
    sheet: str                     # name or regex
    entity: str                    # catalogue entity key, or "voucher:auto"
    kind: str                      # master | opening | document | document_flat
    key: str | None                # id column joining header→lines
    columns: tuple[ColumnSpec, ...]
    children: tuple[ChildSpec, ...] = ()
    constants: dict[str, str] = {} # e.g. force voucher_type="Sales"
@dataclass(frozen=True)
class SourceProfile:
    key: str; label: str; app: str
    sheets: tuple[SheetSpec, ...]
    account_types: dict[str, str] = {}   # source account type → Tally reserved group
    signature: tuple[str, ...] = ()      # sheet names that identify this shape
```

**Built-in profiles**

| Key | Recognises | Notes |
|---|---|---|
| `tally_workbook` | `Voucher_Headers` + `Accounting_Lines` (+ `Inventory_Lines`, `Bill_Allocations`, `Bank_Allocations`) | Full relational Tally dump. Masters from `Groups`/`Ledger_Master`/`Stock_Items`/`Godowns`/`Cost_Centres`/`Units`. Closest to the XML; near-total fidelity. |
| `tally_flat` | single `Transactions` sheet with `Voucher_Type` + `Debit_Ledger`/`Credit_Ledger` | One row = one voucher. Rows sharing a `Voucher_Number` collapse into one document with several lines. |
| `zoho_books` | `Chart_of_Accounts` + `Invoices` + `Invoice_Line_Items` | Documents are typed per sheet (`Invoices`→Sales, `Bills`→Purchase, `Customer_Payments`→Receipt, …); double-entry is **synthesised** (see 3.4). |
| `optierp_template` | the canonical template of Phase 3 | Marker row in a `_OptiERP` sheet, so a filled template never needs detection. |

### 3.3 `sources/detect.py`

Scores each profile against the workbook: exact signature-sheet hits, then
header-overlap per candidate sheet. Returns ranked `(profile, confidence,
matched_sheets, unmatched_sheets)`. Below a floor it proposes the **`custom`**
profile — every sheet unassigned, for the wizard to fill in.

### 3.4 `sources/normalise.py` — workbook + profile → `ParsedFile`

Emits exactly what `parse_xml` emits.

- **Masters** → `{_entity_key, _sequence, guid, name, parent, data}` where `data`
  is filled with the Tally tag names the master importers already read
  (`OPENINGBALANCE`, `PARENT`, `GSTREGISTRATIONNUMBER`, `BASEUNITS`, …).
- **Opening balances** — a `Opening_Balances` sheet, or a master's opening column,
  becomes an `opening_ledger` / `opening_stock` record, as `_opening_record` does.
- **Documents** — header row + joined child rows become
  `ALLLEDGERENTRIES.LIST` / `ALLINVENTORYENTRIES.LIST` /
  `BILLALLOCATIONS.LIST` / `BANKALLOCATIONS.LIST`, honouring Tally's sign rule
  (**negative = debit**) so `_ledger_rows` reads them unchanged.
- **Synthesised double entry** (Zoho, and any flat source): a source that ships no
  ledger lines gets them built from the document's own totals —
  party Dr `total`, revenue/expense Cr `sub_total` per line account,
  tax Cr per `cgst`/`sgst`/`igst` against the profile's tax accounts. The balance
  is asserted before the record is emitted; a row that will not balance is staged
  with an `Error` and its difference, never silently rounded.
- **Identity** — no source GUID means no dedupe. Synthesise a stable
  `source_guid` = `sha1(profile, entity, natural key)` so re-importing the same
  workbook is still recognised. Records with no natural key say so in their log
  line, exactly as the CSV path does today.

**Deliberately not imported** (staged, counted, reported — never silently dropped,
per the existing rule): Zoho `Projects`/`Project_Tasks`/`Time_Entries`,
`Recurring_Invoices`/`Recurring_Expenses`, `Retainer_Invoices`, `Audit_Trail`,
`Bank_Transactions`; Tally `Employees`/`Pay_Heads`/`Payroll_Lines`/
`Attendance_Lines` (no HR module), `Scenarios`, `Budgets` beyond ledger-wise.

---

## 4. Phase 2 — mapping wizard + saved profiles

New table **`migration_source_profiles`** — a company-scoped, named, reusable
mapping ("Our Busy export", "Marg trial balance"), so the second file from the same
application needs no work. Same durability argument as `migration_mappings`.

Endpoints (all under `/api/v1/migration`):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/sources` | Built-in + saved profiles for this company |
| `GET` | `/imports/{id}/mapping` | Detected profile, per-sheet assignment, per-column proposal, 5 sample rows per sheet, unmatched sheets/columns |
| `PUT` | `/imports/{id}/mapping` | Override sheet→entity and column→field; **re-parses** into fresh staging |
| `POST` | `/imports/{id}/mapping/save` | Save the current mapping as a named company profile |
| `GET` | `/template.xlsx` | Phase 3 |

The wizard becomes step **0** of the existing four, and only appears for
spreadsheet uploads. A confidently-detected workbook skips straight through it with
a "detected Zoho Books — review mapping" banner.

---

## 5. Phase 3 — canonical OptiERP templates

`GET /api/v1/migration/template.xlsx?entities=…` builds a workbook **from the
catalogue**, so it cannot drift from what the importer accepts:

- one sheet per entity, headers = the canonical field names,
- a frozen header row, column notes for required/format ("YYYY-MM-DD", "Dr/Cr"),
- two example rows per sheet,
- a `_OptiERP` sheet carrying version + profile marker so an upload of a filled
  template is detected with 100% confidence,
- a `README` sheet explaining sheet order (masters before vouchers) and the
  sign convention.

---

## 6. Phase 4 — frontend

- Upload accepts `.xlsx`/`.xlsm` alongside `.xml`/`.csv`; the base64 body already
  carries binary correctly.
- **Detection banner** on the detail view: "Zoho Books workbook · 21 sheets · 17
  mapped · 4 not imported (why)".
- **`MigrationMappingStep.vue`** — sheet list with entity dropdowns, column table
  with field dropdowns and live sample values, unmapped-column count, "Save as
  profile", "Re-parse".
- "Download OptiERP template" on the migration home + coverage views.
- `ImportDataButton` (renamed) keeps its per-module deep link.

---

## 7. Phase 5 — tests + docs

**Unit** — `test_migration_workbook.py` (header sniffing, coercion, caps),
`test_migration_profiles.py` (detection scores the three samples correctly),
`test_migration_normalise.py` (IR equivalence: a Tally workbook and the equivalent
XML produce the same records; synthesised entries balance; sign convention).

**Integration** — `test_migration_spreadsheet.py`: upload each of the three sample
workbooks → automap → validate → run → assert GL balances, party balances and
stock quantities → rollback → assert flat. Plus dedupe: import the same workbook
twice, second run posts nothing.

**Fixtures** — cut-down (~20-row) copies of the three sample workbooks under
`backend/tests/fixtures/migration/`. The 500-row originals stay in `docs/migration/`.

**Docs** — `TALLY_IMPORT.md` → `DATA_MIGRATION.md` with a new §"Importing a
spreadsheet" (supported apps, the mapping wizard, the template, what a synthesised
entry means for reconciliation); `TALLY_IMPORT_HOW_IT_WORKS.md` gains the
workbook path; `PROJECT.md` + `docs/README.md` + `docs/plans/README.md` updated.

---

## 7b. Phase 6 — close the coverage gaps the first pass left

Phases 0—5 built the machinery and proved it on three real exports. This phase came
from a different question: not "does a workbook import" but "of the things a
workbook *carries*, how many actually land". The answer was a lot fewer than it
looked, and for one reason — the profile vocabulary could not name a field that
had a real column waiting for it.

**Ten targets existed all along and had no path from a spreadsheet.** Address,
Contact, PaymentTermsTemplate, Bank + BankAccount, BankTransaction, TaxTemplate,
Currency + CurrencyExchange, PriceList, Budget + BudgetAccount, Asset +
AssetCategory. Each becomes a `KindSpec`, a `_shape_*`, an `EntitySpec` and an
importer — the same four pieces every time, which is the sign the layering held.

**Party columns.** `payment_terms_template_id`, `tax_category_id`,
`customer_group_id`, `territory_id`, `notes`, `disabled`, `customer_type`,
`credit_limit` and `credit_days` are all columns on Customer/Supplier that no
profile could name. `credit_days` is the instructive one: it was in the field
vocabulary and mapped in the Tally profile from day one, and `_shape_ledger_master`
never emitted it. Mapped-but-never-read is the failure mode this layering makes
easy, so `test_every_importable_shape_has_a_sheet_in_the_downloadable_template`
and the reverse sweep (which columns of an imported sheet do we still not read)
are both worth re-running when a profile changes.

**A correctness bug, found by that sweep.** Tally's `Is_Optional` marks a
provisional voucher deliberately outside the books. The XML path read it and the
runner excludes it; no workbook shaper set it, so a Tally *workbook* posted its
optional vouchers as real ones — 10 of them in the sample dump.

**Orders.** A new `order` kind, plus Quotation as a voucher entity, so
quotations and sales/purchase orders import *with their lines*. They post
nothing, so there is no double entry to rebuild — and therefore nothing to fall
back on when the line sheet is missing. A row with no lines is staged blocked
rather than becoming an order nobody can fulfil. Zoho's own order sheets stay
unimported for exactly that reason, and say so.

**The "why not" text became a first-class output.** Every `reference` sheet's
reason now names which of four things is true — no module, no concept, the file
does not carry it, or deliberately refused — and the wizard renders it. It was
already on the wire and rendered nowhere, which is how someone came to ask why
the audit trail was skipped: the answer existed, three layers down in
`builtin.py`. Two reasons were also simply wrong (they claimed a cost category
was folded into the cost centre's name; nothing does that).

## 8. Risks

| Risk | Mitigation |
|---|---|
| Rename touches 40 backend + 20 frontend files | Land Phase 0 alone, run the full suite before Phase 1 starts. `tally`-the-verb in secretarial is explicitly excluded. |
| Synthesised double entry can silently misstate books | Balance asserted per record before staging; unbalanced rows become `Error` rows carrying the difference. Dry run reports how many documents were synthesised vs. carried verbatim. |
| No GUIDs in spreadsheets → double-posting on re-import | Deterministic `source_guid` from the natural key; sources with no natural key log the limitation, as CSV does today. |
| openpyxl memory on large workbooks | `read_only=True` streaming + a cell cap with a "split by period" error. |
