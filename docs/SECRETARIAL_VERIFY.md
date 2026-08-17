# Verifying the Secretarial module by hand (Phases 0–1)

What was built, and how to satisfy yourself it works. Roughly 15 minutes end to end.

Plan: [SECRETARIAL_GAP_AND_PLAN.md](SECRETARIAL_GAP_AND_PLAN.md) · Branch `feat/secretarial` ·
Migrations `0094`, `0095`.

---

## 0. Start from a running stack

```bash
docker compose up -d
docker compose exec backend alembic upgrade head     # applies 0094 + 0095
docker compose exec backend python -m scripts.seed   # roles + 33 draft rules
```

Expected on the seed run:

```
Seeded 33 secretarial compliance rules (draft — awaiting legal review)
```

The module is **opt-in**, so turn it on for your company first:
**Setup → Settings → Module flags → Secretarial**, or:

```bash
curl -X PUT http://localhost:8000/api/v1/settings/module-flags \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"secretarial":true}'
```

### You need two logins, and neither should be the admin

The module has two audiences, and the difference only becomes real with two separate
accounts. More importantly: **`admin@example.com` is a `System Manager`, and that role is
allowed everything everywhere** — `has_permission()` returns `True` for it before any
check runs ([permissions.py:55](../backend/app/core/permissions.py#L55)). Testing the
delegated access ladder as admin would prove nothing.

```bash
docker compose exec backend python -m scripts.seed_secretarial_demo
```

| Login | Password | Who they are |
|---|---|---|
| `owner@mangoappliances.com` | `Demo!Pass123` | The appliance business. Does its own secretarial work. Profile: **business**. |
| `cs@optireachsecretarial.com` | `Demo!Pass123` | The CS practice. Works on clients. Profile: **practice**. |

Neither holds `System Manager`, so what they can and cannot do is the real answer.
Use `admin@example.com` / `ChangeMe!123` only for setup (module flags, publishing rules).

Log in at http://localhost:8080. **Secretarial** appears in the launcher and sidebar
once the flag is on for that company.

---

## 1. The module sets itself up (business mode)

Open **Secretarial**.

- [ ] The sidebar shows **ENTITY → <your company name>** — the module created an entity
      from your own company automatically. An MSME never has to learn what a "secretarial
      entity" is.
- [ ] Cards read: Entities 1 · Directors & partners 0 · Open obligations 0.
- [ ] There is **no** entity dropdown — you have one entity, so a picker would be noise.

> Why it matters: `linked_company_id` is set on this entity, which is the switch that
> later lets compliance thresholds be computed from your actual ledger (plan §2.1).

---

## 2. The publish gate — the thing to check most carefully

Go to **Compliance calendar**, set the financial year to `2025-26`, press **Generate**.

- [ ] It reports **0 created** and tells you *N rules skipped — not yet reviewed and
      published by a qualified professional*.

This is correct and is the point of the design: the engine is complete, but statutory
wording is authored and approved by a Company Secretary, and nothing unreviewed can reach
a real calendar (plan §2.12).

Now go to **Statutory content review**:

- [ ] 33 rules, all `draft`, each with its source citation (e.g. *Companies Act, 2013 s.96(1)*).
- [ ] Click **Mark reviewed → Mark approved → Mark published** on one rule. Publishing
      prompts for the reviewer's name and refuses without it.
- [ ] Try to publish straight from `draft` via the API — it is rejected:

```bash
curl -X POST .../secretarial/compliance/rules/$ID/review \
  -d '{"review_status":"published","reviewer_name":"X","reviewed_on":"2026-08-17"}'
# -> "Cannot move ... from 'draft' to 'published'. The path is draft → reviewed → approved → published."
```

To publish the whole catalogue for testing (note the deliberately loud placeholder name):

```bash
docker compose exec backend python -m scripts.publish_secretarial_content \
  --reviewer-name "Demo Reviewer (placeholder — NOT a real sign-off)" \
  --reviewer-credential "ACS 00000"
```

There is **no way to publish anonymously** — not through the API, not through the script,
and a database CHECK constraint enforces it as well.

---

## 3. The calendar computes real statutory dates

Press **Generate** again for `2025-26`.

- [ ] Now reports **14 created, 9 not applicable**.
- [ ] Spot-check the dates against the Act — all are derived from *your entity's own*
      31-March year end, not hardcoded:

| Obligation | Expected | Why |
|---|---|---|
| AGM | 30 Sep 2026 | FY end + 6 months (s.96) |
| AOC-4 | 30 Oct 2026 | AGM + 30 days (s.137) |
| MGT-7 | 29 Nov 2026 | AGM + 60 days (s.92) |
| ADT-1 | 15 Oct 2026 | AGM + 15 days |
| DPT-3 | 30 Jun 2026 | fixed 30 June |
| MSME-1 (H1) | 31 Oct 2025 | anchored to FY **start**, not end |
| MSME-1 (H2) | 30 Apr 2026 | anchored to FY end |

- [ ] Press **Generate** a third time → **0 created, 0 refreshed**. Re-running is safe;
      it never duplicates and never overwrites work in progress.
- [ ] Change one row to *In progress*, regenerate → your change survives.
- [ ] Set a row to **Waive…** → it demands a reason and refuses without one.

To prove the year-end maths generalises, create an entity with `fy_end_mmdd = 1231` and
generate — its AGM lands 6 months after 31 December, not after 31 March.

---

## 4. Registers

**Directors & KMP → Appoint.** Use *Person not on the list? Add them* to create a director
with DIN `01234567`.

- [ ] Appointing works; the row shows the *signs* / *chair* chips you ticked.
- [ ] **Add a second person with the same DIN → rejected**: "DIN 01234567 is already held
      by …". One person = one record is what makes cross-entity batches possible later.
- [ ] Appointing a **designated partner** to a *company* → rejected with a message naming
      the roles a company can actually have.
- [ ] **Cease** an appointment → asks for a reason and a date, then greys the row out.
      It is never deleted: past minutes refer to it.

**Members** → add a member with a folio and some shares.
**Group structure** → add a holding company at 60%.
**Related parties → Sync from master data**:

- [ ] Reports rows added, derived from the live director, the group link, and any member
      holding 20%+.
- [ ] Add a related party by hand, sync again → your row is **kept** (`kept_manual`), and
      derived rows refresh around it.

**Every register**: `Export CSV` downloads it, `Financial year` filters to rows whose
validity window overlaps that year, and `Current only` hides closed rows.

---

## 5. Practice mode and the client roster

Switch to a second company (or create one), enable the module there, then:
**Secretarial → (settings)** set profile to `practice`, or:

```bash
curl -X PATCH .../secretarial/settings -d '{"profile":"practice","practice_name":"Your CS LLP"}'
```

- [ ] The module now opens on a **client roster** instead of a single entity, and the
      sidebar shows a **WORKING ENTITY dropdown**. Same routes, same components — only
      the front door changed.
- [ ] Add two clients under **Entities**: one company (CIN), one LLP (LLPIN).
- [ ] Generate the calendar for all entities. The **LLP gets Form 11, Form 8 and
      designated-partner KYC only** — no AGM, no MGT-7, no AOC-4, no DPT-3.

---

## 6. Delegation — the security-critical part

From the **client** company (the one that owns the records):

```bash
curl -X POST .../secretarial/engagements -d '{
  "entity_id": "<client entity>",
  "firm_company_id": "<CS firm company>",
  "financial_access": "ledger_read",
  "grant_to_user_ids": ["<a user who works at the firm>"]
}'
```

Then open **Access & engagements**:

- [ ] The grant appears as `pending` with a **plain-English summary** — "Read-only access
      to your books… They can look at anything, but change nothing" — and a warning
      banner. Not a screen of enum names.
- [ ] Before activation, the firm's user has **no** roles in your company:

```sql
SELECT count(*) FROM user_roles
WHERE user_id = '<firm user>' AND company_id = '<client company>' AND role LIKE 'Delegated%';
-- 0
```

- [ ] Press **Activate**. The same query now returns **4** rows, and critically
      `company_id IS NOT NULL` on every one — a global role row would leak the firm into
      every tenant that user touches.
- [ ] The firm can now `POST /auth/switch-company` into the client and see the client's
      entity on its roster, tagged `delegated`.
- [ ] **The firm tries to revoke its own engagement → refused**: "Only the client who
      granted this engagement can change or end it."
- [ ] The client presses **Revoke**, gives a reason. Then:
  - [ ] delegated roles → **0**
  - [ ] the client's compliance rows → **unchanged** (data stays with its owner)
  - [ ] the engagement row → still there, `status=ended` with the reason, for the audit trail

### What a delegated CS can and cannot do

This is the part worth checking properly, as `cs@optireachsecretarial.com` — **not** as
admin. Verified 2026-08-17 with exactly these calls:

| Action | Result | Why |
|---|---|---|
| Switch into the client *before* any grant | **403** "You do not have access to this company" | No role there yet |
| `GET /secretarial/entities` | **200** | Secretarial access |
| `GET /secretarial/compliance/items` | **200** | Secretarial access |
| `GET /reports/trial-balance?fiscal_year_id=…` | **200** | `reports_read` rung |
| `GET /reports/general-ledger?from_date=…&to_date=…` | **200** | `ledger_read` rung |
| `GET /bank-transactions` | **403** | Outside the ladder — separate opt-in |
| `POST /journal-entries` | **403** | No rung grants write |
| `POST /sales-invoices` | **403** | No rung grants write |
| `PUT /settings/module-flags` | **403** | Settings never granted |

If you copy these into a shell, note that `/reports/trial-balance` needs
`fiscal_year_id` and `/reports/general-ledger` needs `from_date`/`to_date` — a 404 or
422 there is a malformed URL, not an access decision, and proves nothing either way.

---

## 7. Reminders

```bash
docker compose exec backend python -c "
import asyncio
from app.jobs.secretarial_reminders import run_nightly
print(asyncio.run(run_nightly()))"
```

- [ ] Prints `{'reminders_sent': N, 'rosters_refreshed': M}`.
- [ ] Mail appears in Mailhog at http://localhost:8025 for any obligation falling exactly
      30/14/7/1 days out (set an entity's email first, or the run records
      *"No email address on the entity"* against the item rather than silently skipping).
- [ ] Run it **twice** — the second run sends nothing. The `(item, offset)` unique
      constraint is what makes that true.
- [ ] Delegated clients' counts on the practice roster fill in after this run; before it
      they show zero, which is deliberate (a made-up number on a compliance dashboard is
      worse than a blank one).

---

## What is *not* built yet

Phases 2–6 in the plan. Concretely, the module does **not** yet do:

- board meetings, minutes, agendas, SS-1/SS-2 date maths
- circular resolutions or the Rule 5 eligibility gate
- certified true copies
- document generation of any kind (no content-pack renderer yet — the tables and the
  review workflow exist, the renderer does not)
- share transfers, certificates, s.186
- filings with SRN/challan evidence chains
- applicability computed from ledger facts (Phase 1 filters on entity class only; the
  `applicability` column already accepts the richer predicate)

The workspace deliberately shows *"Rules awaiting legal review"* as a card so a working
engine is never mistaken for a finished module.

---

## Known rough edges

0. **One test in the suite is red, and it is not this module's.**
   `tests/integration/test_tally_import.py::test_workspace_stats_track_the_run` asserts
   `stats["total_imports"]`, a key commit `33f9eca` removed from the Tally workspace
   schema before this branch started; the test was never updated. Run the suite with the
   postgres superuser and `-p no:randomly` and you get **209 passed, 1 failed, 0 errors** —
   the errors in a default run are test-ordering interference over the shared `erp_test`
   database, not real failures.

1. **Company switching resets on page reload.** `POST /auth/refresh` re-issues the token
   against `users.default_company_id`, so a hard refresh sends you back to your default
   company. Pre-existing behaviour, not introduced here, but you will notice it while
   testing delegation.
2. **Register rows are read-only in the UI.** Creation and editing work through the API
   (and Directors & KMP has a full form); the generic register table renders and exports
   but has no inline add/edit form yet.
3. **Rule content is placeholder-reviewed.** The 33 seeded rules carry real citations, but
   until a qualified CS publishes them under their own name they are drafts. The dates in
   §3 above were checked against the Act by hand and are correct; the remaining rules
   (CSR-2 thresholds, MR-3 applicability, cost audit) explicitly need professional review.
