# Module 13 — Company Secretarial & Governance: gap & plan

**Status:** Phases 0–5 engineering **BUILT** (migrations `0094`–`0101`; Phase 4 tables shipped in `0096`, Phase 5 in `0100`). Phase 6 not started. Demo click-through: `scripts.seed_secretarial_demo` + `scripts.seed_secretarial_scenario` + `scripts.seed_secretarial_deep`.
**Source brief:** [cosecoffice-technical-teardown.md](cosecoffice-technical-teardown.md) — competitor teardown; §§11–12, 17–19 there are recommendations, not observed internals.
**Migrations:** `0094` onwards. **Branch:** `develop` / `feat/ocr` (Module 13 landed on `develop`).

This is the *living* doc. Update the phase checkboxes here as work lands; treat the
teardown as the frozen design brief.

---

## 1. What this module is

A Company Secretarial workspace: the statutory side of running an Indian company —
who the directors and members are, what the board resolved and when, which registers
say what, which ROC forms are due, and the paper that proves all of it.

**Practice-first** (decided 2026-08-17): the primary buyer is a CS firm managing many
client entities. The MSME in-house case is the same product with a different opening screen.

Three client shapes, one codebase:

| Shape | Who owns the data | When |
|---|---|---|
| **Own** | The tenant itself | An MSME keeping its own books and registers in OptiReach. Entity is linked to the tenant Company; ledger facts feed applicability. |
| **Managed** | The CS firm's tenant | A client who is *not* an OptiReach customer. The firm holds the entity. Financial facts entered manually. The common case for a practice. |
| **Delegated** | The client's own tenant | A client who *is* an OptiReach customer and has engaged this firm. The firm reaches in through a grant; the client keeps ownership. |

Managed clients can be **promoted** to delegated when they sign up (§2.2). All three run
the same tables and the same code path.

**What it is not:** it does not file anything with the MCA. Nothing in this module
depends on scraping the MCA portal. See §11.

---

## 2. Load-bearing design decisions

These thirteen calls determine whether the module fits OptiReach or fights it. Each one
is expensive to reverse after Phase 1, so they are settled here first.

### 2.1 The aggregate root is `secretarial_entities`, and it names its owner

CoSecOffice's hierarchy is `Organization → Company (CIN)`. OptiReach's `Company` is
already the **tenant** — RLS isolates every table on `company_id` from the JWT
(`app/core/database.py`). Hanging secretarial data straight off `companies` would work
for the in-house case and make the practice case impossible.

So: a new company-scoped aggregate root, where `company_id` means *the tenant that owns
this data*, not *the tenant currently looking at it*.

```sql
secretarial_entities(
  id, company_id,                          -- OWNING tenant → RLS applies unchanged
  kind company|llp, cin, llpin, entity_name,
  entity_class private|public|opc|section8|nidhi|producer|llp,
  is_listed bool, incorporated_on date,
  fy_end_mmdd char(4) default '0331',
  registered_office jsonb, status active|dormant|struck_off|amalgamated|closed,
  linked_company_id uuid null → companies.id,   -- books-in-OptiReach switch
  unique(company_id, cin), unique(company_id, llpin))
```

`linked_company_id` decides where financial facts come from. Set → derived from the GL.
Null → read from `secretarial_financial_facts` (§2.8). Nothing else branches on it.

`company_id` decides *ownership*, and ownership is the thing that must be right from day
one: re-homing an entity later means rewriting `company_id` across ~46 tables. A CS firm's
own client (not an OptiReach customer) is owned by the firm's tenant. An OptiReach
customer's entity is owned by the customer's tenant, always — even while a CS firm does
all the work on it. If the client fires the firm, the records do not move.

Every secretarial table carries **both** `company_id` (owning tenant, for RLS) and `entity_id`
(the subject). Services filter on both — defence in depth, matching the existing rule.

### 2.2 Delegation is a governed grant, not a new auth path

An MSME on OptiReach engages a CS firm also on OptiReach. The firm must work on the
client's entity without the client's data leaving the client's tenant.

The machinery for this already exists and is already guarded. `user_roles.company_id` lets
one user hold roles in several companies, and `POST /auth/switch-company`
(`api/v1/auth.py:126`) re-issues the JWT for another company **only if the user holds a
role there**. Tenant context, RLS and permissions then work unchanged.

So delegation needs no new auth path — only a governed record of *why* those roles exist:

```sql
secretarial_engagements(
  id, client_company_id,        -- the owning tenant (grantor)
  firm_company_id,              -- the CS practice (grantee)
  entity_id,                    -- scoped to ONE entity, never the whole tenant
  secretarial_access read|write,
  financial_access none|derived_only|reports_read|ledger_read,   -- §2.2.1
  role_granted, status pending|active|suspended|ended,
  starts_on, ends_on, accepted_at, ended_reason,
  unique(entity_id, firm_company_id) where status = 'active')
```

The engagement is the source of truth; `user_roles` rows in the client's tenant are its
**projection**, created on activation and deleted on revoke. Consequences to enforce:

- Delegated users get **company-scoped roles only** — never a global (`company_id IS NULL`)
  role, which `get_user_roles` would otherwise apply to every tenant.
- Revocation is instant and one-sided — the client ends it, roles vanish, data stays.
- The audit log records **who acted, from which firm, on whose data** — three facts, not
  two. `audit_logs` gains `acting_company_id` for this.
- **Promotion path:** when a managed client signs up, `transfer_entity_ownership()` re-homes
  the entity to the new tenant and converts the firm's access into an engagement. One
  scripted, audited operation — designed in Phase 0, shipped by Phase 6. Without it, every
  client who signs up later becomes a support incident.

#### 2.2.1 Financial access is a ladder, and it stops before mutation

**Decided 2026-08-17: `ledger_read` is the default; write access is never granted by
delegation at any level.**

A CS doing ROC work needs to *verify* things — paid-up capital, borrowings, related-party
transactions, director remuneration, prior-year figures — not just be told a total. Giving
them two summary numbers means a phone call to the accounts team every week, which defeats
the point. So the ladder goes further than derived facts, and stops hard before mutation:

| Level | Grants read on | For |
|---|---|---|
| `none` | — | Practice does secretarial work only |
| `derived_only` | `secretarial_financial_facts` | Applicability thresholds, nothing more |
| `reports_read` | + P&L, balance sheet, trial balance, ageing | Verifying figures against statements |
| `ledger_read` | + GL entries, journal entries, sales/purchase invoices and their attachments | **Default** — tracing a number to its source |

Excluded from every level, granted only by a separate explicit toggle: bank transactions
and statements, payment-instrument details, and payroll/salary lines. Those carry personal
and banking data a CS engagement does not imply consent for.

**No level grants create, write, delete, submit or cancel on any financial doctype**, and
none grants access to Settings. The existing permission engine expresses this directly —
each level is a seeded role with `can_read` on a named doctype bundle and every other flag
false — so this is configuration, not new authorization code.

Two obligations that come with `ledger_read` being the default:

- The client sees, in plain words on the engagement screen, that this firm can read their
  books — before they accept, not buried in a scope list.
- Financial-report and ledger access by a delegated user is written to `audit_logs` with
  `acting_company_id`. Coarse (report opened, ledger queried), not row-level — enough to
  answer "what did they look at" without drowning the log.

### 2.3 Two shells, one product

Not two dashboards — one `/secretarial` module with two opening screens, chosen by a tenant
profile (`secretarial_settings.profile = practice | business`):

| | Practice shell | Business shell |
|---|---|---|
| Lands on | Client roster (own + managed + delegated, one list) | The single entity's overview |
| Entity switcher | Primary navigation, always visible | Hidden — there is one entity |
| Extra surfaces | Cross-client calendar, DIN batch runs, team, fee tracking | "Who can access my records", revoke |
| Sits beside | Nothing — this is the whole product for them | Accounting, Stock, Taxation, GST |

Same routes, same components, same API. Building two literal dashboards would double the
maintenance cost of one conditional.

The **cross-client roster** is the only genuinely hard part: it spans tenants, and RLS
permits one tenant per query. Solution: `practice_client_index` — a projection owned by the
*firm's* tenant, refreshed by the nightly compliance job and on write. Drill-through
switches tenant context and fetches the real rows. Summary counts cross the boundary;
documents never do.

**Decided 2026-08-17: the index is the practice's operational spine, not just a cache.**
It is where client count, workload and (eventually) metering are read from, so it carries
lifecycle from the start:

```sql
practice_client_index(
  id, company_id,               -- the FIRM's tenant (owner of this row)
  entity_id, owner_company_id,  -- owner_company_id = company_id for managed clients
  relationship own|managed|delegated,
  entity_name, entity_kind, onboarding_state prospect|onboarding|active|dormant|exited,
  billable bool, active_from, active_to,
  assigned_to_user_id,          -- which associate owns this client
  next_due_on, overdue_count, open_item_count, last_activity_at,
  refreshed_at)
```

That one table then serves the roster, the cross-client calendar, workload and assignment
views, client health, churn analysis **and** per-entity metering — without any of them
needing a cross-tenant query. It is cheap now and awkward to retrofit, because
`onboarding_state` and `active_from/to` are history that cannot be reconstructed later.

**Billing note:** the repo has no SaaS self-billing today — `services/subscription.py` is a
*tenant* feature (a tenant invoicing its own customers), not OptiReach invoicing tenants.
So Phase 0 builds the **meter**, not the billing. Pleasant side effect: a CS firm can bill
its own clients for retainers using that existing Subscription + Sales Invoice engine,
which covers the teardown's "work & fee tracking" for roughly free.

### 2.4 Persons are tenant-scoped and keyed by DIN

The DIN graph is the reason batch MBP-1/DIR-8 and conflict checks work. A person must be
reusable across every entity in the tenant, so `secretarial_persons` is scoped to `company_id`
(tenant) with `unique(company_id, din)`, and directorships are the m:n join, effective-dated.

Do **not** reuse `contacts` — a Contact is a customer/supplier contact, company-scoped
in the ERP sense and without DIN/KYC/effective-dating. Link optionally
(`secretarial_persons.contact_id`) so email/phone can be shared, but the person record is its own thing.

### 2.5 Documents are re-renderable, not blobs

The repo has no object storage and no file-upload surface at all today (Tally takes
base64 in a JSON body with a size cap — `api/v1/migration/imports.py`). Building a document
library on blob storage would be a large detour.

Instead: **a generated document is stored as its inputs plus a pinned template version,
and re-rendered on download.** `generation_inputs jsonb` + `template_code` +
`template_version` + `resolved_snapshot jsonb` make every re-download byte-identical,
and "regenerate when facts change" becomes an explicit *new version row* rather than a
silent overwrite. That is strictly better than the competitor's regenerate-in-place model
and it costs nothing to store.

Real bytes are only needed for things we did not generate: signed minutes scans, challan
PDFs, MCA Director Data Excel. Those get a small `secretarial_files` table (`bytea`, size-capped,
base64 in / stream out) behind a `storage_backend` seam so S3 can be swapped in later
without touching callers.

### 2.6 Content packs render to a block tree; PDF ships in v1, DOCX later

**Decided 2026-08-17: PDF-only in v1, with generation behind a renderer interface.**

The discipline this demands is worth stating plainly, because the cheap shortcut looks
identical on day one and forecloses the option: a content pack's fragments must be a
**structured block tree** (heading / paragraph / numbered-list / table / signature-grid /
page-break), *not* Jinja HTML strings. If v1 templates are authored as HTML, the DOCX
renderer later is a rewrite of every pack, because HTML→DOCX conversion produces
documents no CS can edit cleanly.

```
services/secretarial/blocks.py        the block tree schema — the interface
services/secretarial/render_html.py   blocks → Jinja themes + letterhead → WeasyPrint PDF   [v1]
services/secretarial/render_docx.py   blocks → python-docx → .docx on letterhead            [later]
```

Cost of the discipline in v1: authoring packs as JSON block trees rather than HTML —
call it two extra days in Phase 2. Cost of skipping it: Phase 2 again, later. The DOCX
renderer itself is then ~3 days plus one pure-Python dependency (`python-docx>=1.1`, no
native libs, unlike WeasyPrint).

One source of truth, and the notice/minutes/attendance trio provably share the same
agenda blocks.

### 2.7 Extend the existing cap table; do not fork it

`models/accounts/share.py` already has `ShareType`, `Shareholder`, `ShareTransfer`
(Issue/Transfer/Buyback, docstatus draft→submit→cancel) with **holdings derived from
submitted transfers so there is no balance child to drift**. That is the right design and
the statutory register of members must be a *view over it*, not a second copy.

Phase 5 therefore adds the legal wrapper, not a parallel ledger:

- `secretarial_share_transfer_details` — SH-4 particulars (instrument date, stamp duty, consideration, FEMA flag, board approval ref) 1:1 on the existing `share_transfers.id`
- `secretarial_share_certificates` — SH-1 certificate numbers, distinctive ranges, `deferred|issued|cancelled|renewed`
- Register of Members / Transfers = queries over `share_transfers` + certificates, exported through the register table pattern

A reversal is `ShareTransfer` cancel **plus** a mandatory reason on the secretarial detail row
(DB CHECK: `reverted_reason NOT NULL WHEN status='reverted'`). Evidence records are never
hard-deleted.

### 2.8 Financial facts are a single interface with two implementations

The competitor's compliance calendar cannot compute applicability because it has no
financial data. OptiReach does. This is the module's real moat, and it must survive
practice mode too.

```
secretarial_financial_facts(id, company_id, entity_id, fy,
  turnover, net_profit, net_worth, paid_up_capital, free_reserves,
  borrowings, securities_premium, source auto|manual, computed_at)
```

`services/secretarial/financial_facts.py` exposes one function: `get_facts(entity, fy)`.
Linked entity → derive from the GL (reusing `services/financial_reports/`) and cache the
row with `source='auto'`. Unlinked → read the manually entered row. The rules engine, the
s.186 ceiling and the dividend distributable-profit check all call this one function and
never know which mode they are in.

### 2.9 The director portal is a new auth path, and it needs care

Every session today gets `company_id` from a JWT (`get_tenant_db`). Directors have no
login — they click a tokenised link. That needs a second dependency:

```python
# app/core/portal.py
async def get_portal_db(token: str) -> tuple[AsyncSession, PortalToken]
```

It resolves the token on a plain `get_db()` session (`portal_tokens` is exempt from RLS
because it must be readable *before* tenant context exists), then calls
`set_company_context()` with the token's tenant and returns the scoped session.

Non-negotiables, all enforced in the token service:

- Store `sha256(token)`, never the raw value; compare in constant time
- Single-purpose (`circulation_view`, `consent_respond`, `client_view`) — a view token cannot cast a consent
- Expiry mandatory; auto-revoke on directorship cessation
- Rate-limited by token and IP; every hit appended to `portal_token_events`
- Portal routers mounted under `/api/v1/portal/*` and excluded from the auth middleware, never from logging

### 2.10 Simple masters go through the metadata engine; stateful documents do not

The registry (`app/registry/descriptors.py`) already serves list/form/permissions/naming
for a master with zero per-doctype code. That is the standing direction for new modules.

- **Registry descriptors** (no bespoke code): Person/Director, Committee, Auditor, Charge, DSC, Group Link, Beneficial Owner, Register Type, Content Pack template, Compliance Rule override
- **Hand-coded services** (state machines, sequences, side effects): Meeting, Circulation, Circular Resolution, CTC, Share Transfer detail, Compliance Item, Filing

Roughly 60% of the module's tables need no router code at all.

### 2.11 The minutes-book counter is a serialized sequence, not `MAX()+1`

Consecutive, gap-free entry and page numbers per (entity, scope) are the integrity
mechanism against back-dated insertion — an auditor checks exactly this. `app/core/naming.py`
already does atomic counters against `naming_series`; mirror it with
`SELECT … FOR UPDATE` inside the minutes-finalisation transaction. Numbers are consumed at
`minutes_signed`, not at meeting creation, so an abandoned draft never burns a number.

### 2.12 Legal text is a versioned, sourced, human-reviewed content layer

**Decided 2026-08-17: engineering and LLMs do not invent statutory text.**

The taxation module already models "government says so" data as owner-written,
company-less tables in the `statutory` schema (`models/statutory.py`, loaded by
`load_statutory`). Companies Act rules and resolution wording are the same kind of data —
they belong there, and rule updates ship as data, not code. Tenant *instances* stay in
the public schema.

On top of that, both content packs and compliance rules carry provenance and a review
gate. No artifact renders in production until a named human has signed it off:

```
source_ref      "Companies Act 2013 s.152(2)" | "SS-1 ¶1.2.1" | "MCA Form DIR-12 instr. 4"
source_excerpt  the reviewed source material, verbatim
version         integer; a new version supersedes, never overwrites
effective_from  / superseded_by, superseded_on
review_status   draft → reviewed → approved → published
reviewed_by     / reviewed_on           -- approved requires both, DB CHECK
published_at    -- only `published` rows are visible to generation, DB-enforced
```

Two consequences that are already half-built into this design:

- Documents pin the pack version they used (§2.5), so a resolution generated today still
  re-renders under today's wording after the Act is amended tomorrow. That is what makes
  a five-year-old minute book defensible.
- An amendment is a *new version* with `effective_from`; the rule engine picks the version
  in force on the relevant date, not the latest one.

**Decided 2026-08-17: this is a parallel workstream, not an engineering dependency.**
Nothing waits on a Company Secretary reading 30 rules before code starts. The split:

| Engineering builds (Phase 0 →) | A qualified CS / legal professional supplies |
|---|---|
| Rule engine, versioning, effective dates, applicability evaluator | Statutory interpretation and applicability thresholds |
| Review workflow and the publish gate | Required wording, form and document language |
| Content-pack loader, block-tree schema, PDF generation | Legal references and source excerpts |
| Slots for all ~30 rules and ~17 packs, seeded as `draft` | The approval itself — reviewer name on every row |

Every slot exists from Phase 0, populated with `draft` placeholders, so the whole pipeline
is testable end to end long before a word of it is legally signed off. The gate is at
**publish**: `services/secretarial/content.py` refuses to render or evaluate anything not in
`published` state, so a half-reviewed rule can never reach a client's compliance calendar
or a generated document. Draft content is fully usable in dev and demo tenants, and
visibly watermarked.

The consequence to hold to: **a phase is not "done" when the code works — it is done when
its content is published.** Phase 2 and Phase 4 each carry a content checklist alongside
the engineering one.

### 2.13 One appointments table for directors, partners and KMP

**Decided 2026-08-17: LLP parity is in v1.**

The temptation is separate `directorships` and `partnerships` tables. Resist it: the
meeting, circulation, consent, CTC and register machinery is byte-identical for a director
of a company and a designated partner of an LLP — only the labels and the forms differ.

```sql
secretarial_appointments(id, company_id, entity_id, person_id,
  role_type director|designated_partner|partner|kmp|auditor|secretary,
  designation, appointed_on, ceased_on, is_signing bool, ...)
```

`entity.kind` then drives the differences that are real: DIN vs DPIN, board meeting vs
partners' meeting, share capital vs contribution, MGT-7/AOC-4 vs Form 11/Form 8, articles
vs LLP agreement. Those live in the content packs and the rule catalogue — data, not
branches in the service layer. Doing this in one pass costs ~1 week across Phases 1–4;
retrofitting it costs a schema migration plus every governance query.

---

## 3. Domain model by phase

~50 new tables. `company_id` (owning tenant) + timestamps on all; `entity_id` on all
entity-scoped ones; append-only where marked.

**Phase 0 — spine + tenancy (`0094`)**
`secretarial_entities` · `secretarial_persons` · `secretarial_appointments` (§2.13) ·
`secretarial_settings` (carries `profile`) · `secretarial_engagements` ·
`practice_client_index` · `acting_company_id` column on `audit_logs`

**Phase 1 — masters, registers, thin calendar (`0095`)**
`secretarial_members` (register of members header, holdings via §2.7) · `committees` ·
`committee_members` · `group_links` · `related_parties` · `beneficial_owners` ·
`auditors` · `charges` · `dsc_registrations` · `person_kyc` · `secretarial_files` ·
`statutory.secretarial_compliance_rule` · `secretarial_compliance_items` · `secretarial_reminders`

**Phase 2 — document engine (`0096`)**
`secretarial_document_templates` · `secretarial_documents` (versioned, `supersedes_id`) ·
`secretarial_document_events`

**Phase 3 — governance (`0097`)**
`meetings` · `agenda_items` · `attendance` · `minutes_book_seq` · `circulations` ·
`circulation_recipients` *(append-only events)* · `circulars` · `consent_responses`
*(append-only)* · `ctcs` *(append-only)* · `ctc_signatories` · `portal_tokens` ·
`portal_token_events` *(append-only)*

**Phase 4 — compliance engine & filings (`0098`)**
`secretarial_financial_facts` · `secretarial_filings` (form, SRN, challan, filed_on) ·
`secretarial_compliance_status_history` *(append-only)* — plus applicability columns on the rule catalogue

**Phase 5 — capital & s.186 (`0100`) — BUILT**
`secretarial_share_transfer_details` · `secretarial_share_certificates` ·
`secretarial_distinctive_seq` · `secretarial_capital_events` ·
`secretarial_s186_limits` · `secretarial_s186_entries`

**Phase 6 — practice & differentiation (`0100`)**
`secretarial_tasks` · `secretarial_task_checklist_items` · `secretarial_approvals` (maker–checker) ·
`client_portal_users` · `mca_snapshots` *(append-only)*

Constraints worth naming now: `unique(company_id, cin)`; `unique(meeting_id, seq)` on
agenda items; GiST index on `daterange(valid_from, valid_to)` for BO/group FY-overlap
filters; partial unique index on active portal tokens per (purpose, subject); DB CHECKs
mirroring every state machine in §5.

---

## 4. File map

```
backend/app/
  models/secretarial/                 entity.py persons.py masters.py governance.py
                                documents.py compliance.py capital.py portal.py
  schemas/secretarial/                same split
  services/secretarial/               entity.py persons.py registers.py
                                content_pack.py render_html.py render_docx.py documents.py
                                meeting.py ss_dates.py minutes_seq.py circulation.py
                                circular.py eligibility.py ctc.py
                                compliance_rules.py compliance_items.py financial_facts.py filings.py
                                capital.py s186.py portal_tokens.py workspace.py
  api/v1/secretarial/                 entities.py persons.py masters.py meetings.py circulars.py
                                ctcs.py documents.py compliance.py filings.py capital.py
                                registers.py workspace.py
  api/v1/portal/                circulation.py consent.py client.py     # unauthenticated, token-scoped
  core/portal.py                get_portal_db + token verification
  jobs/secretarial_reminders.py       compliance reminders (cron hour=8)
  jobs/secretarial_circular_expiry.py circular expiry sweeper (cron every 15 min)
  data/seeds/secretarial_rules.json   statutory rule catalogue
  data/seeds/content_packs/     one JSON block tree per event type
  migrations/versions/0094..0100_secretarial_*.py

frontend/src/
  views/secretarial/                  ~28 views (see §10 per phase)
  views/portal/                 ConsentView.vue CirculationView.vue    # public layout, mobile-first
  components/secretarial/             EntitySwitcher.vue AgendaBuilder.vue StatusChipTimeline.vue
                                EligibilityGate.vue SignatoryGrid.vue PrefillPicker.vue
                                RegisterTable.vue EvidenceDrawer.vue PackPreviewTabs.vue
  config/workspaces.ts          + SECRETARIAL workspace config
  router/index.ts               /secretarial/** + public /p/**
```

---

## 5. State machines (server-enforced, DB-checked)

| Object | States | Gates |
|---|---|---|
| Meeting | `draft → scheduled → circulated → held → minutes_draft → minutes_signed → closed` | notice-period check on `→scheduled`; minutes number consumed on `→minutes_signed` |
| Circulation recipient | `pending → viewed → acknowledged` | append-only event rows; status is derived, never written directly |
| Circular | `draft → circulating → passed \| failed \| expired → ratified` | **Rule 5 eligibility gate on `draft→circulating`** — 422 with statutory refs; outcome computed server-side from responses, never client-set |
| Consent response | `pending → viewed → consented \| declined \| abstained` | append-only; one active token per (circular, person) |
| CTC | `issued` only | append-only; a correction is a *new* issuance referencing the superseded one |
| Share transfer | `draft → board_approved → issued_posted → [reverted(reason)]` | reason mandatory; approval refused against a meeting not yet held; certificates cancelled/reissued in the same transaction |
| Share certificate | `issued → cancelled \| surrendered` | never edited; a replacement supersedes and carries the **same distinctive range**, and a GiST exclusion constraint stops two live certificates of a class overlapping |
| Capital event | `draft → approved → allotted`, plus `cancelled` before allotment | approval needs the authorising resolution; a dividend is refused above distributable profit and the s.123 verdict is frozen onto the row; allotment cuts a certificate per allottee in the same transaction |
| Compliance item | `not_applicable → upcoming → due → in_progress → pending_review → filed → completed`, plus `overdue`, `waived(reason)` | waive needs reason + permission; every transition appended to history |
| Task | `open → in_progress → blocked → done \| cancelled` | — |

---

## 6. Document engine

**Content pack schema** (seeded JSON, versioned, `is_system` packs not editable by tenants):

```jsonc
{
  "code": "director-appointment", "event_type": "DIR_APPOINT", "version": 1,
  "variables": [ {"name": "appointee_name", "source": "person.name", "required": true} ],
  "fragments": {
    "notice":            [ /* block tree */ ],
    "notes_to_agenda":   [ ... ],
    "minutes_narration": [ ... ],
    "resolution":        [ ... ],
    "ctc":               [ ... ]
  },
  "compliance_meta": {
    "act_sections": ["152", "161"], "ss_refs": ["SS-1 ¶1.2.1"],
    "triggered_filings": ["DIR-12"], "register_updates": ["directors"]
  }
}
```

`compliance_meta` is the connective tissue: generating the pack tells the calendar which
form is now due and tells the registers what to update. That is the "one thread from
resolution → form → SRN → challan" the teardown identifies as the extension of the
competitor's strongest feature.

**Generation** = resolve variables from entity master + guided form → block tree →
persist `secretarial_documents` row (inputs + pinned template version + resolved snapshot) →
render on demand to PDF and DOCX. Regeneration writes a **new version row** with
`supersedes_id` and a diff summary, so "what changed since the board saw it" is answerable.

**Phase-2 pack catalogue (15):** director appointment · director resignation · auditor
appointment · KMP appointment · registered-office change · board meeting notice · board
minutes · attendance sheet · AGM notice + explanatory statement · AGM minutes · directors'
report · MBP-1 · DIR-8 · dividend declaration · shorter-notice consent.

**AI is not in this engine until Phase 6**, and then only at declared fragment slots
(`notes_to_agenda`, `explanatory_statement`, `resolution` draft) with review-before-issue.
Free-form "generate any document" is explicitly out (§11) — it would void the
`compliance_meta` guarantees that make packs trustworthy.

---

## 7. Compliance engine

```
statutory.secretarial_compliance_rule(
  code, title, act, section, basis fy|event, form_code,
  due_formula jsonb,      -- e.g. {"anchor":"fy_end","offset_days":60}
                          --      {"anchor":"event","event":"agm_held","offset_days":30}
  applicability jsonb,    -- predicate AST over entity attrs + financial facts
  reminder_offsets int[]  -- [-30,-14,-7,-1]
)
```

Applicability is a small predicate AST evaluated by `services/secretarial/compliance_rules.py`
against `{entity attributes} ∪ {financial facts from §2.8}`:

```jsonc
{"all": [ {"eq": ["entity.kind", "company"]},
          {"any": [ {"gte": ["facts.net_worth", 5000000000]},
                    {"gte": ["facts.turnover", 10000000000]},
                    {"gte": ["facts.net_profit", 50000000]} ]} ]}   // CSR s.135
```

This is what the competitor structurally cannot do, because the numbers live in a
different product. Here they are one join away.

**Pipeline:** nightly evaluation + on-change (FY close, event capture, facts update) →
compliance instances per (entity, FY/event) → optional auto-task with checklist →
reminders at the rule's offsets (`jobs/secretarial_reminders.py`, same shape as
`jobs/tax_reminders.py`) → evidence attach (document / filing / challan) → completion,
optionally maker–checker → immutable status history.

**Seed catalogue v1 (~30 rules):** AGM · MGT-7/7A · AOC-4 (+XBRL) · DIR-3 KYC · DPT-3 ·
MSME-1 · BEN-2 · MBP-1 · DIR-8 · ADT-1 · CSR-2 · PAS-6 · Form 11 · Form 8 · DIR-12 ·
INC-22 · INC-28 · SH-7 · PAS-3 · CHG-1/4 · MGT-14 · board-meeting-per-quarter (SS-1) ·
first-BM-within-30-days · AGM notice ≥21 days · statutory register upkeep.

---

## 8. RBAC

New doctypes registered with the existing permission engine (`core/permissions.py` —
role × doctype × action, company-scoped roles):

`Secretarial Entity` · `Secretarial Person` · `Secretarial Register` · `Meeting` · `Circular
Resolution` · `Certified True Copy` · `Secretarial Document` · `Secretarial Compliance Item` ·
`Secretarial Filing` · `Secretarial Share Certificate` · `Secretarial Task`

New roles seeded: **Company Secretary** (full), **CS Associate** (create/write, no issue
or waive), **CS Trainee** (read + draft), **Compliance Reviewer** (read + approve).

Four more roles exist solely as engagement projections (§2.2.1) — never assigned by hand,
created and destroyed by the grant lifecycle:

| Role | Read on | Write on |
|---|---|---|
| `Delegated CS — Secretarial` | all secretarial doctypes | all secretarial doctypes |
| `Delegated CS — Facts` | `secretarial_financial_facts` | — |
| `Delegated CS — Reports` | + P&L, Balance Sheet, Trial Balance, ageing | — |
| `Delegated CS — Ledger` | + GL Entry, Journal Entry, Sales Invoice, Purchase Invoice, attachments | — |

The three financial roles are cumulative and read-only by construction: seeded with
`can_read` true and every other flag false. Bank transactions, payment-instrument detail
and payroll are in **no** bundle. This is why the ladder needs no new authorization code —
it is four rows in `role_permissions`, not a second permission model.

Two additions the existing engine does not have, both needed:

1. **Per-entity access lists** — `user_entity_access(user_id, entity_id)`, checked in the
   entity resolver. Without it a practice firm cannot separate associates by client.
2. **Capability tokens for non-user principals** (§2.9) — directors are not users and
   must never become users.

Sensitive actions gated on their own permission atoms rather than plain write:
CTC issue, compliance waive, share-transfer revert, filing record.

---

## 9. Module surface

`/secretarial` workspace (module flag `secretarial` in `services/module_flags.py`, default **off** —
this is an opt-in module, unlike manufacturing).

The **entity switcher** is the safety pattern of this category and the one UI element that
must be persistent and unmissable: every generative action is scoped to the selected
entity, and the selection is visible in the sidebar at all times. It reuses the existing
sticky-module logic in `AppShell.vue`.

Public routes `/p/c/:token` (circulation) and `/p/r/:token` (consent) render in a
minimal mobile-first layout with no app chrome and no auth.

Phase 5 adds a **Capital** sidebar group: `/secretarial/capital` (cap table · certificates ·
transfers · capital events, with the s.123 dividend check on the cap-table tab) and
`/secretarial/s186` (the MBP-2 register against its s.186(2) ceiling). Both are served by
`/api/v1/secretarial/capital/*`.

---

## 10. Phases

Sizes are working estimates for one focused developer; they assume the existing
substrates (RLS, RBAC, audit, print, email, scheduler, registry) are reused as described.

### Phase 0 — Spine + tenancy · `0094` · ✅ **BUILT 2026-08-17**
Entities with ownership, persons, unified appointments (§2.13), settings with the
`practice | business` profile. Entity switcher. Module flag, both workspace shells, RBAC
doctypes + roles. Auto-create the business-shell entity from the tenant Company.
**Engagements**: grant → role projection (including the four financial ladder roles,
§2.2.1) → switch-company → revoke, with `acting_company_id` threaded through the audit
log; `practice_client_index` with full lifecycle columns and the client roster.
`transfer_entity_ownership()` designed (shipped Phase 6). Registry descriptors for Person
and Committee. Legal-content tables, review workflow and the publish gate (§2.12), with
all ~30 rule and ~17 pack slots seeded as `draft` so the content workstream can start
immediately and in parallel.
**Done when:** a CS-firm tenant sees a roster of its own managed clients *and* one
delegated MSME entity; opening the delegated one switches tenant context and every list
is correctly scoped; the delegated CS can open the client's trial balance but gets 403 on
posting a journal entry, on bank transactions and on Settings; the MSME revokes the
engagement and the firm loses access immediately while keeping nothing.

### Phase 1 — Masters, registers, thin calendar · `0095` · ✅ **BUILT 2026-08-17**
Members, committees, group links, related-party sync, BO/SBO/UBO with FY-overlap filters,
auditors, charges, DSC, KYC — for companies **and** LLPs. `RegisterTable` pattern
(filter → search → export) applied across all of them. `secretarial_files` upload/download.
Rule catalogue seeded, instances generated per FY by entity class only (no applicability
yet), reminder job live.
**Done when:** every register renders, filters by FY, and exports; the calendar shows the
right ~15 rows for a private company and ~6 for an LLP, and emails a reminder at T-7.

### Phase 2 — Document engine · `0096` · ✅ **BUILT 2026-08-18**
Block-tree schema (§2.6) + content-pack loader, **PDF renderer only**, letterhead layer,
document library with versioning, guided event forms, New version (regenerate). Packs
seed as `draft`; `seed_secretarial_scenario` publishes them with a placeholder reviewer
so Generate actually produces a PDF.
**Still open:** `render_docx.py` (Phase 6). Byte-identical re-download is the HTML/PDF
path, not a stored blob.

### Phase 3 — Governance · `0096`+`0097` · ✅ **BUILT 2026-08-18** *(the moat)*
Meetings (BM/AGM/EGM/committee) with agenda · SS-1/SS-2 date math and minutes-book
numbering · notice/minutes/attendance from one agenda · tracked circulation with
tokenised links · **director portal** (`0097` token lookup) · circular resolutions with
the Rule 5 gate · CTC with issuance log.
**Still open:** the full “circulate → phone consent → auto-pass → auto ratification
agenda → CTC” thread is wired but needs live emails on directors to exercise end-to-end
without the API; demo seed creates the draft meeting and both circulars (one eligible,
one Rule-5 blocked).

### Phase 4 — Compliance engine & filings · `0096` (not a separate `0098`) · ✅ **BUILT 2026-08-18**
Financial-facts interface (ledger-derived and manual), applicability AST, filings with
form/SRN/challan and an evidence-chain walk. UI: **Financial figures**
(`/secretarial/facts`) plus the same panel on Company details.
**Still open:** nightly on-change re-evaluation of the calendar when facts cross a
threshold is generate-on-demand, not a watcher.

### Phase 5 — Capital & s.186 · `0100` · ✅ **BUILT 2026-08-21**
SH-4 wrapper on the accounts `ShareTransfer`, SH-1 certificates with serialised
distinctive ranges and deferred issue, register of transfers and renewed certificates,
revert-with-reason, right issue / private placement / ESOP / s.186 packs, s.186 register
with ledger-derived enabling limits, s.123 dividend check. UI: **Share capital**
(`/secretarial/capital`) and **Loans & investments** (`/secretarial/s186`).

**The load-bearing decision:** there is still exactly one cap table, and it is the
accounts one. `secretarial_share_transfer_details` *wraps* a `share_transfers` row rather
than duplicating it; where the client's books are elsewhere the wrapper stands alone and
the cap-table endpoint reports `source: register` instead of `ledger`. See
[the plan](plans/secretarial_phase5_capital.plan.md) §1.

**Two integrity mechanisms worth knowing before editing:** distinctive numbers come from
a locked counter row (the minutes-book pattern) and are additionally protected by a GiST
exclusion constraint, so two live certificates of a class can never claim the same
shares; and a cancelled certificate's range is never returned to the pool — the
replacement carries it, because the range identifies the shares and not the paper.

**Still open:** PAS-3/SH-7 are recorded as filings by hand rather than pre-filled from
the capital event; ESOP exercise (options → shares) is not modelled, only the grant; a
transfer surrenders a whole certificate, so splitting one is a manual cancel-and-reissue.

Two defects in earlier phases surfaced while driving this end to end and were fixed:
meetings were numbered per entity instead of per book (`0101`), so an entity's first AGM
collided with its first board meeting; and `transition(…, "held", on_date=…)` ignored the
date, stamping a meeting recorded after the fact with today and starting the SS-1 minutes
clocks from the wrong day.

### Phase 6 — Practice & differentiation · `0102` · ~3.5 weeks · ❌ not started
Task manager with checklist templates, maker–checker approvals, read-only client portal,
`transfer_entity_ownership()` (managed → delegated promotion), **DOCX renderer**, AI
drafting at declared slots, MCA import adapters (manual + Excel + provider seam), e-sign
seam, WhatsApp channel.

**Total: ~19 weeks** of focused work for the full scope. Phases 0–3 (~11 weeks) are the
competitive core; 4–6 are where OptiReach passes the competitor rather than matching it.

Phases 4 and 5 are order-swappable — take Capital first if early users are transaction-heavy.

Not on the critical path but blocking Phases 2 and 4: **legal content authoring**
(~30 rules, ~17 packs) runs in parallel from the end of Phase 0. See §2.12.

---

## 11. Deliberate cuts

| Cut | Why |
|---|---|
| MCA captcha-relay scraping | Fragile and ToS-sensitive. Manual entry + Excel import ship first; a provider adapter sits behind an interface. **No core flow may depend on MCA availability.** |
| Direct e-filing to MCA | Not possible; the competitor says so too. We prepare the pack and record the SRN/challan. |
| In-product marketing email campaigns | Deliverability and consent liability, off-mission for a FinOS. |
| User-supplied Telegram bot tokens | Support notifications through one unified layer later; do not architect around a side channel. |
| Free-form "generate any document" AI | Undermines the `compliance_meta` guarantees. AI stays inside declared fragment slots. |
| Directors as logged-in users | Deliberate: capability tokens only. A director account is a support and security burden with no upside. |

---

## 12. Decisions taken (2026-08-17)

| # | Question | Decision | Lands in |
|---|---|---|---|
| 1 | In-house vs CS practice | **Practice first**, but the client's `Company` stays the data-owning tenant. Delegation by governed grant. | §2.1, §2.2, §2.3, Phase 0 |
| 2 | DOCX vs PDF | **PDF-only in v1**, generation behind a block-tree renderer interface so DOCX drops in later without touching packs. | §2.6, Phase 2 → 6 |
| 3 | LLP parity | **In v1** — one appointments table, `entity.kind` drives the differences through data. | §2.13, Phases 1–4 |
| 4 | Statutory text | **Versioned legal-content layer.** Sourced, human-reviewed, version-pinned; engineering and LLMs never invent it. | §2.12, Phase 0 tables |
| 5 | Legal review as a blocker | **Parallel workstream, never a dependency.** All slots seeded as `draft` in Phase 0; the gate is at *publish*, not at code. | §2.12 |
| 6 | Delegated financial access | **`ledger_read` is the default**, on a four-rung ladder. Read-only at every rung; mutations, banking and payroll never granted by delegation. | §2.2.1, §8 |
| 7 | Practice metering | **Per-client-entity primary, seats secondary.** `practice_client_index` carries lifecycle from Phase 0 and serves roster, workload and metering alike. | §2.3, Phase 0 |

### Still open

1. **The named reviewer.** Decision 5 removes the schedule risk, not the need — a
   qualified CS still has to approve every rule and pack before it can be published.
   Engagement can start any time from Phase 0; it only becomes urgent at the end of Phase 2.
2. **Whether banking gets its own delegated toggle in v1.** Bank transactions sit outside
   the ladder by design. Some CS work (DPT-3 deposits, s.185/186 verification) touches
   them. Ship the toggle in Phase 0 or defer to Phase 6?
