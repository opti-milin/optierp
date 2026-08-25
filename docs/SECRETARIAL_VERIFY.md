# Verifying the Secretarial module by hand (Phases 0–5)

What was built, and how to satisfy yourself it works. Seeded click-through is ~15 minutes;
the publish-gate walk in §2 is extra if you want to see draft rules behave.

Plan: [SECRETARIAL_GAP_AND_PLAN.md](SECRETARIAL_GAP_AND_PLAN.md) · Migrations `0094`–`0101`.

---

## 0. Start from a running stack

```bash
docker compose up -d
docker compose exec backend alembic upgrade head     # applies 0094–0101
docker compose exec backend python -m scripts.seed_secretarial_demo
docker compose exec backend python -m scripts.seed_secretarial_scenario
docker compose exec backend python -m scripts.seed_secretarial_deep
```

`seed_secretarial_demo` creates the two logins and turns the module flag **on**.
`seed_secretarial_scenario` publishes the draft rules/packs with a placeholder reviewer
and fills directors, registers, calendar FY 2025-26, a board meeting, two circulars
(one Rule-5 blocked), a director-appointment PDF, DIR-12, and an active engagement
Mango → OptiReach Demo.

`seed_secretarial_deep` then **works** that data: it drives meetings to signed minutes,
carries a circular on real consents, issues and supersedes a CTC, completes one filing's
evidence chain and leaves another's gappy, and builds the whole Phase-5 capital and s.186
picture. Without it every record sits in its opening state and most of what follows has
nothing to look at. It is idempotent and resumes from wherever a previous run stopped.

The module is **opt-in**. If you skipped the demo seed, turn it on at
**Setup → Settings → Modules → Enable Secretarial & Compliance → Save**, or:

```bash
curl -X PUT http://localhost:8000/api/v1/settings/module-flags \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"secretarial":true}'
```

`GET /settings/module-flags` is readable by any signed-in user (the Home launcher needs
it). Changing flags still needs System Settings write.

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

## 8. Share capital (Phase 5)

**Sidebar → Capital → Share capital**, as `owner@mangoappliances.com`.

### 8.1 The cap table says where its numbers come from

- [ ] The **Cap table** tab totals **120,000** shares: Priya Sharma 66,000 (55%) and
      Rahul Mehta 54,000 (45%).
- [ ] The blue banner reads *"Backed by the share ledger in this company's books"* —
      `source: ledger`, because Mango keeps its books here and the members are linked to
      accounts `Shareholder` rows.
- [ ] Switch to the practice login and open **Sunrise Textiles**: the same screen reads
      *"From the certificate register — this client's books are kept elsewhere"*. Same
      code, different honesty. That difference is the point of §2.8 of the plan.
- [ ] The distinctive-number ranges tile without gaps: 1–55000, 55001–60000,
      60001–100000, 100001–111000, 111001–120000. **Next unissued: 120001.**

### 8.2 A transfer moves the distinctive numbers with the shares

Open the **Transfers** tab.

- [ ] `SH-4/1` is `issued_posted`, Priya → Rahul, 5,000 shares, distinctive 55001–60000.
- [ ] On **Certificates**, filter *Cancelled*: certificate **2** (Priya, 55001–60000) is
      cancelled with the reason *"Transferred to Rahul Mehta under instrument SH-4/1"*.
- [ ] Filter *Live*: certificate **4** is Rahul's, `renewed`, over **the same** range
      55001–60000. The range followed the shares, not the paper.

### 8.3 The guards actually bite

- [ ] Lodge a new SH-4 (any two members, 100 shares, today's date), then press **Move the
      shares** without approving it. Refused: *"A transfer that is draft cannot become
      issued_posted…"*, naming `board_approved` as the next step.
- [ ] Press **Board approval**. If the only meetings are drafts you get *"A transfer
      cannot be approved by a meeting that has not happened"* — a back-dated approval is
      exactly what the register exists to make visible.
- [ ] Press **Revert** on `SH-4/1` and cancel the prompt: nothing happens. Confirm with a
      reason and the register realigns, Rahul's certificate is cancelled, Priya's is
      reissued over 55001–60000, and the reason shows in red under the status. Nothing is
      deleted. (Re-run `seed_secretarial_deep` after a reset if you want the original
      state back.)

### 8.4 Capital events

- [ ] **Events** tab: the rights issue is `allotted` (20,000 shares at ₹25, allotted
      2026-06-24); the ESOP grant is `approved` and **not** allotted — options are not
      shares until exercised; the dividend is `approved` and shows its frozen s.123 check.
- [ ] On the **Cap table** tab press **Run the check**: verdict `ok`, distributable
      2,47,00,000. The reasons list ends by naming what the check does *not* cover
      (transfer to reserves, the s.123(3) interim ceiling, the unpaid-dividend account).

---

## 9. Loans & investments — s.186

**Sidebar → Capital → Loans & investments.**

- [ ] The banner reads **"Ceiling lifted by special resolution under s.186(3)"** and links
      the July 2026 EGM. Paid-up 10,00,000 · free reserves 1,85,00,000 · securities
      premium 15,00,000 → 60% test 1,26,00,000, 100% test 2,00,00,000, ceiling
      **2,00,00,000** (s.186(2) says *whichever is more*).
- [ ] Four entries. The Mango Retail loan is tagged **exempt** — s.186(11) exempts a
      wholly-owned subsidiary from the ceiling, but the register entry is still required,
      so it is listed and left out of the exposure.
- [ ] **Edit figures → clear the special resolution → Save.** The banner flips to
      **"Above the s.186(2) ceiling"** with a red bar: exposure 2,20,00,000 against
      2,00,00,000. Add the resolution back to restore it.
- [ ] With the resolution cleared, try adding a ₹50,00,000 loan. Refused with the numbers
      in the message, not a bare 422:
      *"s.186(2): this would take the company's loans, guarantees and investments to
      27,000,000.00 against a ceiling of 20,000,000.00. Pass a special resolution under
      s.186(3)…"*
- [ ] **Edit figures → clear Free reserves → Save.** The verdict becomes **"Cannot be
      judged"**, amber, listing what is missing — *not* "within limits". A company is
      never told it is clear because a number is absent.
- [ ] Open **Sunrise Textiles** on the practice login: same screen, `source: manual`,
      ceiling 42,00,000, one loan inside it, verdict **within** with a green bar.

---

## What is *not* built yet

Phase **6** in the plan. Concretely, the module does **not** yet do:

- task manager, maker–checker, `transfer_entity_ownership()`, DOCX renderer, AI drafting,
  MCA import, e-sign, WhatsApp (Phase 6)
- registry descriptors for Person/Committee (engine masters — still bespoke UI)

Within Phase 5, three things are deliberately short: PAS-3 and SH-7 are recorded as
filings by hand rather than pre-filled from the capital event; ESOP **exercise** (options
becoming shares) is not modelled, only the grant; and a transfer surrenders a whole
certificate, so moving part of a holding needs the certificate split first by cancelling
and reissuing.

Phases 2–5 **are** built: meetings, circulars + Rule 5, CTC, document PDF generate /
new version, filings + evidence chain, financial figures + applicability, share
transfers + certificates + capital events, s.186 register and the s.123 dividend check.

---

## Known rough edges

0. **The integration suite is green, but it does not run by default.**
   Every test in `tests/integration/` self-skips unless `TEST_DATABASE_URL` is set, so a
   plain `pytest` reports *224 skipped* and looks like a pass. The database it wants is
   **the local PostgreSQL on port 5432, not the container** — a local server shadows the
   compose port mapping, so `docker compose exec postgres psql` and the test suite are
   talking to two different servers. Create `erp_test` on the local one once, then:

   ```bash
   cd backend
   TEST_DATABASE_URL="postgresql+asyncpg://postgres:<pw>@localhost:5432/erp_test"    SECRET_KEY="local-dev-secret-key-change-in-production-0001"    python -m pytest tests/integration -q -p no:randomly
   ```

   Last full run on this branch: **224 passed, 0 failed** in ~34 min. `-p no:randomly`
   matters — the tests share one database and are not order-independent.

   *(Superseded note: `test_tally_import.py::test_workspace_stats_track_the_run` used to
   fail on a `stats["total_imports"]` key removed by `33f9eca`. It passes now — the Tally
   work on this branch restored the key.)*

1. **Company switching resets on page reload.** `POST /auth/refresh` re-issues the token
   against `users.default_company_id`, so a hard refresh sends you back to your default
   company. Pre-existing behaviour, not introduced here, but you will notice it while
   testing delegation.
2. **Rule content is placeholder-reviewed.** The seeded rules carry real citations, but
   until a qualified CS publishes them under their own name they are drafts. The scenario
   seed uses `Demo Reviewer (placeholder — NOT a real sign-off)` / `ACS 00000` so the
   calendar and PDFs run in demo. That is not a legal sign-off.
3. **Hard-refresh Home after enabling the flag** so Pinia reloads module flags.
