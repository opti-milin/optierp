# OptiReach — USPs & Future Scope ("Why we beat ERPNext")

> **Status:** VISION / SCOPE. This is a reference document, not a build plan.
> It captures the six things we believe make OptiReach stand out from ERPNext,
> written in plain language so anyone — sales, founders, or new engineers — can
> read it. Each section ends with a short "how we'd build it" note that ties
> back to [the machine](metadata_engine_plan.md) (our metadata engine).
>
> **Companion docs:**
> - [`metadata_engine_plan.md`](metadata_engine_plan.md) — the engine these features ride on.
> - [`PRICING_AND_CHILD_TABLES.md`](PRICING_AND_CHILD_TABLES.md) — the pricing/child-table layer several of these reuse.

---

## 0. TL;DR (plain language)

ERPNext is a giant, capable, *generic* ERP. It does almost everything — but it
feels like a toolbox, not a finished product. Setting it up, making it
India-ready, and making it pleasant for everyday staff and customers is left to
you.

OptiReach's bet is **not** "more features than ERPNext." It's **"the few things
people actually touch every day, done so well they feel finished."** Six of
those are below. Think of ERPNext as the raw engine and OptiReach as the car
that's actually nice to drive.

| # | USP | One-line pitch | ERPNext today |
|---|-----|----------------|---------------|
| 1 | **Marketing & Promotions** | Leads → campaigns → promotions, in one flow | Split across CRM + bolt-on apps |
| 2 | **Customer Tracking Dashboard** | Customers watch their own order move, stage by stage | No real customer-facing tracker |
| 3 | **One-Page Reconciliation** | Sales, purchase **and** bank matched on a single screen | Three separate tools |
| 4 | **MCA Compliance** | India company-law filings, built in | Manual / third-party |
| 4b | **Income Tax (entity ITR)** | Compute + export ITR pack from books (ITR-6 first) | Manual / CA re-key — ERPNext has none |
| 5 | **Company Onboarding Wizard** | A new business is live in minutes, not weeks | Long manual setup |
| 6 | **POS + Loyalty** | Till that remembers and rewards customers | POS yes, loyalty is thin |

Module naming reminder (so the rest of the doc reads cleanly): **Selling = sales**,
**Buying = purchase**, **Stock = inventory**, **Marketing = promotions + leads**.

---

## 1. Marketing & Promotions

### What it is
One place to run the *top of the funnel*: capture a **lead**, group leads into a
**campaign**, and attach **promotions** (discounts, coupons, schemes) that the
sales side already understands.

### The ERPNext gap
ERPNext *has* the pieces — Lead, Opportunity, Campaign — but they live in a
separate CRM area, and "promotions" (Pricing Rules, Coupons, Promotional
Schemes) live in Selling. The two halves don't talk. A marketer running a
"Diwali 10% off for new leads" promo has to stitch it together by hand.

### Our edge
Marketing and Selling share **one pricing engine** (already built — see
[`PRICING_AND_CHILD_TABLES.md`](PRICING_AND_CHILD_TABLES.md)). So a promotion a
marketer creates is the *same object* the order screen applies at checkout. Lead
→ campaign → promotion → order is a single connected chain, not four apps taped
together.

### How we'd build it
- **Lead, Campaign, Lead Source** = simple masters → recipe cards on the machine.
- **Promotions** = reuse the existing Pricing Rule / Coupon / Promotional Scheme
  engine; just expose them under the Marketing workspace too.
- The only genuinely new work is the **lead → opportunity → quotation** hand-off
  (a small state machine), which plugs in as a hook.

---

## 2. Customer Order-Tracking Dashboard

### What it is
A simple, customer-facing page where *the customer* (not our staff) can log in
and watch their order move through its stages — like the pizza tracker, but for
"Quotation → Order Confirmed → Packed → Dispatched → Delivered → Invoiced."

### The ERPNext gap
ERPNext's customer portal is bare: a customer can see a list of their documents,
but there's no friendly, stage-by-stage **"where is my order right now"** view.
Most ERPNext shops end up emailing status updates manually or building a portal
themselves.

### Our edge
We already track document **status** and the links between them (Quotation →
Sales Order → Delivery Note → Invoice). Turning that into a customer-readable
timeline is mostly presentation, and it removes a huge chunk of "where's my
order?" phone calls — which is exactly the kind of everyday pain that sells an ERP.

### How we'd build it
- A read-only **customer role** + portal route (auth scoped to *their* party only).
- A **timeline widget** that reads the existing document-status chain and renders
  it as stages.
- No new core data — it's a new *view* over data the machine already produces.

---

## 3. One-Page Reconciliation (Sales · Purchase · Bank)

### What it is
A single screen where you match three things side by side:
- **Sales** — money customers owe vs. what they've actually paid,
- **Purchase** — money we owe suppliers vs. what we've paid,
- **Bank** — the real bank statement, matched against both.

### The ERPNext gap
ERPNext makes you reconcile in **three separate tools** (Payment Reconciliation,
Bank Reconciliation, and supplier/customer ledgers). Accountants hop between
screens and lose the thread. It works, but it's tedious.

### Our edge
**Everything on one page.** Pull the bank statement on one side, the open sales
and purchase items on the other, and let the screen **suggest matches** (same
amount, near date, same party). The accountant confirms with a click. The pitch
is simple: *"close your books on one screen instead of three."*

### How we'd build it
- This is a **hand-built, calculation-heavy screen**, not a recipe card — the
  machine renders the shell, the *matching logic* is a service (an "auto-suggest
  match" algorithm: amount + date window + party).
- Reuses the existing accounting/ledger models; adds a reconciliation
  service + a three-pane UI.

---

## 4. MCA Compliance (India company law)

### What it is
Built-in help for **Ministry of Corporate Affairs** obligations — the statutory
filings every Indian Pvt Ltd / LLP must make (annual returns, board/financial
filings, director details, registered-office records, etc.).

### The ERPNext gap
ERPNext is global-first. Indian GST exists, but **MCA / Companies-Act
compliance is not built in** — businesses use a CA or a separate compliance tool
and re-key data. For an India-focused ERP this is a real, concrete differentiator.

### Our edge
Because OptiReach already holds the company's master data (directors,
shareholding, registered address, financials), it can **pre-fill compliance
forms and warn before deadlines** instead of making the owner gather everything
again. "Your ERP also keeps you compliant" is a strong, India-specific story
ERPNext can't tell out of the box.

### How we'd build it
- **Company / Director / Shareholding** masters → recipe cards on the machine.
- A **compliance calendar** (due dates by company type) + reminders → a small
  rules service.
- Form **pre-fill / export** per filing → templated documents fed from existing
  master data. (Actual e-filing integration is a later, optional phase.)

---

## 5. New-Company Onboarding (Organization Setup)

### What it is
A guided wizard that takes a brand-new business from *nothing* to *ready to
trade* — company details, chart of accounts, tax setup, first warehouse, first
users — in one smooth flow.

### The ERPNext gap
ERPNext's setup is famously heavy: many disconnected settings pages, and it's
easy to miss a step and get stuck. New users often need a consultant just to get
going.

### Our edge
**Minutes, not weeks.** A single wizard with sensible India-ready defaults (a
starter chart of accounts, GST tax templates, common units) means a customer can
self-onboard. Lower setup friction is one of the biggest reasons people *choose*
one ERP over another — it's the first thing they experience.

### How we'd build it
- The machine already knows how to create masters from recipe cards — onboarding
  is essentially a **scripted sequence of those creations** wrapped in a friendly
  multi-step UI.
- Ship **default data packs** (chart of accounts, tax templates, UoMs) that the
  wizard loads in one click.
- Strongly reuses existing screens; mostly new *flow*, little new *data*.

---

## 6. POS + Loyalty Program

### What it is
A point-of-sale till for over-the-counter sales that also **remembers the
customer and rewards them** — points earned per purchase, redeemable on future
visits, with tiers if we want them.

### The ERPNext gap
ERPNext has a POS, but loyalty is thin and bolted on. Running a real
points-and-rewards program (earn rules, redemption, expiry, tiers) usually means
a third-party add-on.

### Our edge
Loyalty is **part of the same pricing engine** that already handles discounts and
schemes — so "earn 1 point per ₹100" and "redeem 500 points for ₹50 off" are just
more pricing rules, applied live at the till. One engine, consistent everywhere
(online order *or* POS).

### How we'd build it
- **POS till** = a focused, hand-built fast-entry screen (speed matters) over the
  existing order/invoice models.
- **Loyalty Program / Points Ledger** = a master (recipe card) + an
  earn/redeem **hook** into the pricing engine — the same hook pattern Pricing
  Rules already use.

---

## 7. The thread running through all six

Notice the pattern — every USP is either:

1. **A new view over data the machine already produces** (tracking dashboard,
   onboarding), or
2. **A new screen whose *calculations* plug into the existing pricing/ledger
   engines** (reconciliation, loyalty, promotions), or
3. **A few new masters** (leads, company/director records) that are just recipe
   cards.

That's deliberate. We are **not** out-featuring ERPNext line by line — we'd lose
that race. We're taking the engine we've already built and pointing it at the
handful of everyday experiences ERPNext leaves rough: the marketer, the waiting
customer, the accountant closing books, the Indian company owner facing a
filing, the brand-new business on day one, and the cashier at the till.

| USP | Build type | Reuses |
|-----|-----------|--------|
| Marketing & Promotions | masters + existing engine | pricing engine, child tables |
| Customer Tracking | new view | document-status chain |
| One-Page Reconciliation | hand-built screen + service | ledger/accounting models |
| MCA Compliance | masters + rules service | company master data |
| Company Onboarding | scripted flow + data packs | recipe-card creation |
| POS + Loyalty | hand-built till + hook | pricing engine |

**The one-sentence pitch:** *ERPNext gives you the engine; OptiReach gives you
the finished car — India-ready, customer-friendly, and live in minutes.*
