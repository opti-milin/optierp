# OptiReach — Income Tax Module Guide (Plain Language)

### *From “how much tax do we owe?” to an ITR-6 pack — explained without jargon overload.*

**Who this is for:** business owners, office managers, junior accountants, and anyone who is
comfortable with OptiReach but **not** with Income-tax Act jargon.

**What this module does:** it helps a **company** (or similar business entity) work out its
**annual income-tax liability**, keep track of what was already paid / deducted, and prepare an
**ITR-6 style JSON pack** you can hand to a CA or upload via your e-filing flow.

> This is **not** GST. GST is tax on selling/buying goods and services through the year.
> **Income tax** is tax on the **profit / taxable income** of the business for a whole year.

Architecture for developers: [TAXATION_ARCHITECTURE.md](TAXATION_ARCHITECTURE.md).

---

## Table of contents

1. [The big picture in one minute](#1-the-big-picture-in-one-minute)
2. [Glossary — words you will see on screen](#2-glossary--words-you-will-see-on-screen)
3. [Map of every screen](#3-map-of-every-screen)
4. [First-time setup (do this once)](#4-first-time-setup-do-this-once)
5. [The main yearly workflow](#5-the-main-yearly-workflow)
6. [Use cases (step by step)](#6-use-cases-step-by-step)
7. [What “Run” actually calculates](#7-what-run-actually-calculates)
8. [Draft vs Submitted — and why history matters](#8-draft-vs-submitted--and-why-history-matters)
9. [Common mistakes & how to avoid them](#9-common-mistakes--how-to-avoid-them)
10. [What is built today vs later](#10-what-is-built-today-vs-later)
11. [Quick FAQ](#11-quick-faq)

---

## 1. The big picture in one minute

Think of income tax like a **year-end school report card for money**:

1. You collect the year’s income under different **heads** (business profit, capital gains, etc.).
2. You add back / deduct certain items (**adjustments**) so the profit matches tax rules.
3. You apply **losses from earlier years**, **depreciation**, and maybe **MAT** (a special company rule).
4. The system applies **tax rates**, **surcharge**, **cess**, **rebates**, and **interest** if you paid late.
5. It subtracts tax already paid (**TDS, TCS, advance tax challans**).
6. What’s left is **net tax payable** (or refundable).
7. You generate an **ITR-6 JSON** snapshot with a fingerprint (hash) for filing / audit.

```
  Settings (PAN, class, regime)
           │
           ▼
  Tax Workspace  ──▶  Heads → Adjustments → Ledgers → Run → Result
           │
           ├─ Challans & Credits (money already paid / deducted)
           ├─ Calendar (when to pay advance tax; interest preview)
           └─ Filings (ITR-6 JSON + acknowledgement)
```

**Primary screen:** Taxation → **Tax Workspace** (`/tax/workspace`).

---

## 2. Glossary — words you will see on screen

Read this section like a dictionary. Skip what you don’t need yet; come back when a label appears.

### People & identity

| Term | Simple meaning |
|------|----------------|
| **Assessee** | The person or company whose tax is being calculated. In OptiReach this is usually your **Company**. |
| **Assessee class** | Legal shape of the assessee — e.g. **Company**, Firm, LLP, Individual. Picks which form and rates apply. |
| **PAN** | Permanent Account Number — 10-character tax ID (e.g. `AABCT1234C`). The **4th letter** must match the class (companies use `C`). |
| **TAN** | Tax Deduction Account Number — used when *you* deduct TDS on payments to others. |
| **CIN** | Company Identification Number from the MCA (Registrar of Companies). |
| **Residential status** | Roughly: whether the company is treated as **Resident** in India for tax (most domestic companies are). |

### Time periods

| Term | Simple meaning |
|------|----------------|
| **Financial Year (FY)** | Business year in India: **1 April → 31 March**. Example: FY 2024-25 = 1 Apr 2024 to 31 Mar 2025. |
| **Assessment Year (AY)** | The year **after** the FY, when the return for that FY is assessed. Income of FY 2024-25 is reported in **AY 2025-26**. |
| **Due date** | Last day to pay an instalment or file a return without late interest / late fees. |

> Tip: On screens you mostly pick an **AY** (e.g. `2025-26`). That means “tax for the FY that just ended before that AY.”

### Income & profit

| Term | Simple meaning |
|------|----------------|
| **Income head** | A bucket of income under the Act: **Salary**, **House Property (HP)**, **Business/Profession (PGBP)**, **Capital Gains (CG)**, **Other Sources (OS)**. Companies mostly use **PGBP** (+ CG/OS when needed). |
| **PGBP** | “Profits and Gains of Business or Profession” — normal trading / manufacturing profit. |
| **Gross** | Amount before deductions on that line. |
| **Deductions (on a line)** | Amounts allowed to reduce that line’s income. |
| **Net** | Gross − deductions for that line. |
| **Taxable income / Total income** | The final yearly income figure after adjustments, set-off, etc., on which tax is computed (rounded under special rules). |
| **Book profit** | Profit as per books (often used for **MAT**). Not always the same as taxable income. |
| **Income character** | *How* an amount is taxed — e.g. ordinary business income vs special capital-gains rates (`ORDINARY`, `LTCG_112A`, …). |

### Adjustments & corporate extras

| Term | Simple meaning |
|------|----------------|
| **Adjustment / add-back** | Something disallowed for tax (e.g. certain cash expenses) that you **add back** to profit. |
| **Adjustment / deduction** | Something allowed only for tax that you **subtract**. |
| **Stage** | Where the adjustment sits in the worksheet (PGBP, ICDS, Chapter VI-A, SetOff, MAT, Other). You don’t need the legal detail to start — your CA will care. |
| **Tax depreciation** | Wear-and-tear of assets **for tax**, which can differ from accounting depreciation. OptiReach keeps a **WDV register** per asset block. |
| **WDV** | Written Down Value — asset cost left after depreciation. |
| **&lt;180-day rule** | If you buy an asset and use it for less than 180 days in the year, tax often allows only **half** the year’s depreciation rate. |
| **Loss carry-forward** | A tax loss you couldn’t use this year, saved to reduce future taxable income (with expiry rules). |
| **Set-off** | Using a loss against income (same year or brought forward), in a legal order. |
| **MAT (115JB)** | Minimum Alternate Tax — companies may pay tax on **book profit** if that is *higher* than normal tax. |
| **MAT credit (115JAA)** | If you paid MAT earlier, you may get credit in later years when normal tax is higher. |

### Tax calculation words

| Term | Simple meaning |
|------|----------------|
| **Tax regime** | Which rate structure you chose for the year (e.g. **Normal**, **115BAA** for companies). Some elections are **irrevocable**. |
| **Slab / rate schedule** | Table of “income from–to → tax %”. Companies often use a **flat %**; individuals use slabs. |
| **Surcharge** | Extra % on tax when income is very high. |
| **Marginal relief** | Softens a sudden surcharge jump so you don’t lose more than the income that crossed the limit. |
| **Cess (Health & Education Cess)** | Small extra % on (tax + surcharge), currently commonly **4%**. |
| **Rebate** | Reduction in tax (e.g. 87A for individuals — less relevant for companies). |
| **Interest 234A / 234B / 234C** | Penalties for **late return**, **late/short advance tax**, and **missed advance-tax instalments**. Computed from **real challan dates**. |
| **Net payable** | Tax after surcharge, cess, interest, **minus** credits already paid. |

### Payments & credits

| Term | Simple meaning |
|------|----------------|
| **Challan** | Proof of tax paid to the government (BSR code, serial, deposit date, amount). Types: **Advance Tax**, **Self-Assessment**, etc. |
| **Advance tax** | Tax paid **during** the year in instalments (usually June / Sep / Dec / Mar), before the final return. |
| **Self-assessment tax** | Tax paid when filing if something is still due. |
| **TDS** | Tax Deducted at Source — someone else cut tax from a payment *to you* and remitted it. You claim it as a **credit**. |
| **TCS** | Tax Collected at Source — similar idea on certain receipts. |
| **26AS / AIS** | Government statement of tax already credited to your PAN. OptiReach can **reconcile** an upload against your books. |
| **Credit entry** | A row saying “this TDS/TCS/advance amount is claimable against our tax”. |

### Filing

| Term | Simple meaning |
|------|----------------|
| **ITR** | Income Tax Return — the form you file. Companies (non-exempt) typically use **ITR-6**. |
| **Filing type** | **Original**, **Belated** (late), **Revised** (correcting an earlier return), **Updated** (special later update). |
| **Ack / acknowledgement** | Number the portal gives after accepting the return. |
| **Payload / JSON** | Machine-readable form content. OptiReach builds CBDT-shaped JSON from a **field map**. |
| **SHA-256 / hash** | Fingerprint of the JSON. If even one rupee changes, the hash changes — useful for audit. |
| **Sandbox e-file** | Practice submit that invents a fake ack — **not** a real filing with the Income-tax portal. |
| **Statutory catalogue** | Built-in, **read-only** copy of tax law rates for each AY. Tenants cannot edit the law. |

### Document status

| Term | Simple meaning |
|------|----------------|
| **Draft** | Editable worksheet. |
| **Submitted** | Locked for editing; can post a tax provision to the books (GL). |
| **Run** | One calculation attempt. Every recompute **adds** a new run — old runs are kept (append-only). |
| **Superseded** | An older run that was replaced by a newer one (still visible for audit). |

---

## 3. Map of every screen

Open the **Taxation** module from the launcher.

| Screen | Path | Use it for |
|--------|------|------------|
| **Tax Workspace** | `/tax/workspace` | Day-to-day worksheet: heads, adjustments, run, result, form preview |
| **Income Tax Settings** | `/income-tax-settings` | PAN / TAN / CIN, assessee class, per-AY **regime election** |
| **Tax Computations** | `/tax/computations` | Simple list + quick create / run |
| **Tax Challans** | `/tax/challans` | Record tax payments (posts to GL when submitted) |
| **Tax Credits** | `/tax/credits` | TDS/TCS/advance credits + 26AS reconcile |
| **Tax Depreciation** | `/tax/depreciation` | Tax WDV registers; sync from assets |
| **Loss Carry-Forward** | `/tax/losses` | Loss ledger + set-off entries |
| **MAT Credits** | `/tax/mat-credits` | 115JAA credit ledger / balance |
| **Tax Calendar & Interest** | `/tax/calendar` | Advance-tax shortfall + 234A/B/C preview + reminders |
| **Tax Filings** | `/tax/filings` | Generate ITR-6, sandbox e-file, chain Revised/Belated/Updated |
| **Statutory Catalogue** | `/tax/catalogue` | Browse official rates / provisions (read-only) |
| **Taxation Settings** | `/taxation-settings` | Turn the Taxation **module** on/off for the company |

---

## 4. First-time setup (do this once)

### Step A — Turn the module on

1. Go to **Taxation → Taxation Settings** (`/taxation-settings`).
2. Ensure the Taxation module is enabled for your company.

### Step B — Register the company for tax

1. Open **Income Tax Settings** (`/income-tax-settings`).
2. Fill in:
   - **PAN** (must match assessee class — companies: 4th character `C`)
   - **TAN** / **CIN** if you have them
   - **Assessee class** (usually `Company`)
   - **Residential status** (usually `Resident`)
   - Optional: default assessment year, jurisdiction, e-file provider (`sandbox` for practice)
3. Save.

### Step C — Choose the tax regime for the year

Still on Income Tax Settings:

1. Pick **Assessment Year** (e.g. `2025-26`).
2. Pick **Regime** (e.g. `Normal` or `115BAA`).
3. Save the **election**.

> Some regimes (like 115BAA) are **irrevocable** once chosen. Confirm with your CA before locking them in.

### Step D — Optional: browse the law

Open **Statutory Catalogue** and confirm your AY has rates. You cannot edit these — that is intentional.

---

## 5. The main yearly workflow

This is the happy path most companies will follow.

```
1. Settings + regime election
2. Create computation for the AY
3. Enter income heads (PGBP, etc.)
4. Add manual adjustments if needed
5. Sync depreciation / review losses / MAT inputs
6. Record challans & credits during the year
7. Click Run → check Result
8. Submit computation (optional GL provision)
9. Generate ITR-6 → record acknowledgement
```

**Where to do most of this:** **Tax Workspace**.

1. Taxation → **Tax Workspace**.
2. Enter AY, optional PGBP net, optional book profit (for MAT) → **New**.
3. Work through the tabs (next section).
4. Click **Run** after each meaningful change.
5. When numbers look right → **Submit**.
6. Tab **Form preview** → **Generate ITR-6**.

---

## 6. Use cases (step by step)

### Use case 1 — “I just want a rough tax estimate”

**Goal:** See approx tax on business profit for an AY.

1. `/tax/workspace` → set AY → enter PGBP net (e.g. `10000000`) → **New**.
2. Open **Heads** — confirm one PGBP line; Save if you edit.
3. Click **Run**.
4. Open **Result** — read **Taxable income**, **Tax (normal)**, **Net payable**.

No challans needed for a first estimate. Credits will show as zero.

---

### Use case 2 — Edit income under multiple heads

**Goal:** Split income (e.g. business + capital gains).

1. Open the draft computation in Tax Workspace → **Heads**.
2. **Add line** for each head (`PGBP`, `CG`, `OS`, …).
3. Set **Character** when special rates apply (ask your CA; default `ORDINARY` is fine for normal business income).
4. Enter Gross / Deductions / Net → **Save heads** → **Run**.

---

### Use case 3 — Manual tax adjustments (add-backs)

**Goal:** Increase taxable profit for disallowed expenses (example: personal expense wrongly booked).

1. Tab **Adjustments** → **Add**.
2. Enter **Section** code (e.g. a note like `37-personal`), **Stage** `PGBP`, **Direction** `Add`, **Amount**.
3. **Save adjustments** → **Run**.
4. Check **Result** — taxable income should rise.

Evaluated engine lines (if any) appear after Run; manual lines stay editable while the doc is Draft.

---

### Use case 4 — Pay advance tax and see the shortfall

**Goal:** Know how much more advance tax to pay before the next instalment date.

1. During the year, record payments under **Tax Challans** (`/tax/challans`):
   - Type **AdvanceTax**
   - BSR code, challan serial, **deposit date**, amount
   - Submit (posts to the general ledger when configured)
2. Open **Tax Calendar & Interest** (`/tax/calendar`).
3. Enter AY + estimated tax → **Refresh**.
4. Read each instalment: **required cumulative**, **paid to date**, **shortfall**, **suggested payment**.

Optional: **Dispatch reminders** to email people about upcoming shortfalls.

---

### Use case 5 — Claim TDS / reconcile 26AS

**Goal:** Don’t pay tax twice — claim tax already deducted by customers/banks.

1. Open **Tax Credits** (`/tax/credits`).
2. Add credit rows (deductor TAN, section, amounts) or import/reconcile via **26AS**.
3. Run **reconcile-26as** with your portal JSON (from the Credits screen).
4. Re-open the computation → **Run** so **credits_total** reduces **net payable**.

You can also peek at the **Credits** tab inside Tax Workspace for an AY summary.

---

### Use case 6 — Tax depreciation from assets

**Goal:** Claim Income-tax Act depreciation (not just accounting depreciation).

1. Keep assets maintained in the Assets module.
2. Open **Tax Depreciation** (`/tax/depreciation`).
3. **Sync** for the AY (creates/updates block WDV movements; half-rate if held &lt; 180 days).
4. In Tax Workspace → **Depreciation** tab, confirm records exist for the AY.
5. Recompute the computation so depreciation feeds the pipeline where wired.

---

### Use case 7 — Bring forward old losses

**Goal:** Reduce this year’s tax using last year’s unabsorbed loss.

1. Open **Loss Carry-Forward** (`/tax/losses`).
2. Ensure prior-year loss ledger rows exist (from earlier filed years / seed).
3. Set-offs are recorded when computations run against eligible income.
4. Workspace tab **Set-off** shows AY-scoped summary; full detail on the Losses page.

---

### Use case 8 — MAT vs normal tax (companies)

**Goal:** Ensure the company pays at least MAT when book profit tax is higher.

1. When creating/editing the computation, set **Book profit 115JB**.
2. **Run**.
3. On **Result**, compare **Tax (normal)** vs **MAT** and note **Basis applied**.
4. Open **MAT Credits** to see 115JAA credit created or utilised.

---

### Use case 9 — Preview late-payment interest (234A/B/C)

**Goal:** Estimate interest before you file.

1. Ensure challans have correct **deposit dates**.
2. On the computation (or Calendar interest preview), set **return filed date** / audit flag if known.
3. `/tax/calendar` → **Interest preview** (or recompute so Result shows 234A/B/C lines).
4. Read the three interest figures separately — they mean different late events (see glossary).

---

### Use case 10 — Generate ITR-6 and record acknowledgement

**Goal:** Produce a filing pack with a hash, then store the portal ack.

**From Tax Workspace**

1. Prefer a **Submitted** computation (Draft allowed only as preview).
2. Tab **Form preview** → **Generate ITR-6**.
3. Copy/inspect JSON; note **SHA-256**.
4. **Sandbox e-file** for a practice ack, **or**
5. Go to **Tax Filings** → open the filing → **Acknowledge** with the real portal `ack_no` + filed date.

**From Tax Filings page**

1. `/tax/filings` → pick computation → **Generate**.
2. Open row → Sandbox or enter ack manually via API/UI flow.

---

### Use case 11 — File a Revised / Belated / Updated return

**Goal:** Correct or late-file after an original return.

1. Original computation must be **Submitted** (and ideally already filed/acked).
2. `/tax/filings` → **Chain** section → choose prior computation → type **Revised** / **Belated** / **Updated** → **Create**.
3. A **new** draft computation is created, linked via `revises_computation_id`, with lines copied as a starting point.
4. Edit → Run → Submit → Generate a new filing.
5. The new filing links to the prior filing when an ack exists (`revises_filing_id`).

| Type | Everyday meaning |
|------|------------------|
| **Original** | First return for the year |
| **Belated** | Filed after the due date |
| **Revised** | Corrects an earlier return |
| **Updated** | Special later update window (talk to your CA) |

---

### Use case 12 — Audit “why did the number change?”

**Goal:** Prove what rule-set and inputs produced a result.

1. Tax Workspace → **Runs / Audit**.
2. Each Run shows `ruleset_hash` and `input_hash`.
3. Click **Explain** for breakdown JSON (pipeline notes, hashes, finance-act pin).
4. Older runs stay visible even after recompute (**append-only** — nothing is deleted).

---

## 7. What “Run” actually calculates

When you click **Run**, OptiReach roughly does this (you don’t type these steps):

1. Gather facts (income lines, adjustments, credits, challans, MAT book profit, …).
2. Build income by head / character.
3. Apply adjustments and loss set-off.
4. Arrive at total / taxable income (with statutory rounding).
5. Compute tax by character / schedule.
6. Apply rebate → surcharge (+ marginal relief) → cess.
7. Compare **normal tax vs MAT**; pick the higher when MAT applies.
8. Subtract credits; add 234A/B/C interest from challan dates.
9. Produce **net payable** and save a new **Run + Result** row.

Law rates come from the **Statutory Catalogue** for that AY — not from editable tenant masters.

---

## 8. Draft vs Submitted — and why history matters

| Status | You can… | You should… |
|--------|----------|-------------|
| **Draft** | Edit heads/adjustments, Run many times | Experiment freely |
| **Submitted** | Generate filings; books may get a tax **provision** JE | Treat as “ready for CA / filing” |
| **Cancelled** | Stop using the doc | Start a new computation if needed |

**Runs are never deleted.** That protects you in audits: “Show me the calculation from March” stays possible even after an April recompute.

---

## 9. Common mistakes & how to avoid them

| Mistake | What happens | Fix |
|---------|--------------|-----|
| Wrong **AY** | Rates / due dates don’t match the year you mean | Confirm FY→AY mapping with your CA |
| PAN 4th letter ≠ class | Settings validation error | Company PAN must have `C` in position 4 |
| No **Run** after edits | Result looks stale | Always **Run** before trusting Result / Form |
| Missing **challan dates** | 234B/C interest wrong | Enter real deposit dates on challans |
| Expecting GST screens here | Confusion | GST lives under Compliance → GST; this module is **income tax** |
| Editing “law” masters | Those screens were removed | Law is read-only in **Statutory Catalogue** |
| Sandbox ack = real filing | False sense of completion | Sandbox is practice only |
| Chaining without a submitted original | Validation error | Submit (and preferably file) the original first |

---

## 10. What is built today vs later

### Built (companies / ITR-6 first)

- Registration + regime elections  
- Computation worksheet + append-only runs  
- Challans (GL), credits, 26AS reconcile  
- Tax depreciation, loss CF/set-off, MAT + MAT credit  
- Advance-tax calendar + 234 interest + reminders  
- ITR-6 JSON from statutory field maps + hash + ack + revised/belated/updated chaining  
- Tax Workspace UI  

### Later / limited today

- Full individual Salary / HP / CG schedules and Form 16 depth  
- Per-section Chapter VI-A wizard UX  
- Presumptive schemes (44AD / 44ADA / 44AE)  
- Live Income-tax portal HTTPS + DSC (sandbox only for now)  
- 143(1) intimation tracking  

If you need something in the “later” list, keep worksheets exportable for your CA in the meantime.

---

## 11. Quick FAQ

**Is this the same as GST?**  
No. GST is on turnover of goods/services. Income tax is on yearly taxable income / profit.

**Do I need a CA?**  
OptiReach computes and organises. A CA should still review elections (especially irrevocable regimes), adjustments, and the final return before portal filing.

**Where do I start every year?**  
Settings → regime election for the new AY → new computation in Tax Workspace → enter heads → Run.

**Why can’t I edit slab rates?**  
Because tax law is not tenant data. It lives in the global **statutory** catalogue so one wrong edit cannot silently change every return.

**What is the “hash” on a filing?**  
A fingerprint of the JSON. Same inputs → same hash. Change a number → new hash. Useful for “this is exactly what we generated.”

**Can I delete a wrong Run?**  
No — by design. Create a new Run instead. History is the audit trail.

**Who is this module aimed at?**  
Primarily **companies** filing **ITR-6**. Other entity types are modelled in settings/catalogue; depth beyond companies is sequenced later.

---

## Cheat sheet — button → meaning

| Button | Meaning |
|--------|---------|
| **New** | Create a tax computation for an AY |
| **Save heads / adjustments** | Write worksheet lines (Draft only) |
| **Run** | Append a fresh calculation |
| **Submit** | Lock worksheet; may post tax provision to GL |
| **Generate ITR-6** | Build CBDT-shaped JSON + sha256 |
| **Sandbox e-file** | Fake portal ack for testing |
| **Chain Revised/Belated/Updated** | Start a follow-on return from a submitted original |
| **Explain** | Show hashes + breakdown for one Run |

---

*Last updated with the enterprise Income Tax stack (Phases 1–10). For engineers, see [TAXATION_ARCHITECTURE.md](TAXATION_ARCHITECTURE.md).*
