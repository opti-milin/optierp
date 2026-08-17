# How the Tally import works — in plain language

This explains the machinery without jargon. The reference tables (which Tally
thing becomes which OptiERP thing) live in [TALLY_IMPORT.md](TALLY_IMPORT.md);
this is the *why* and *how*.

---

## The problem in one sentence

Tally knows things by **name** ("ABC Traders"). We know things by **ID** (a long
unique code). So importing is really one question, asked a few thousand times:
*which of our records does this Tally name mean?* — plus the bookkeeping to make
sure a wrong answer is cheap to fix.

---

## The shipping-container analogy

Think of moving a warehouse to a new building.

You don't back the truck up to the new building and start throwing boxes
through the door. You **unload onto the loading bay first**, label every box,
check the labels, and only then carry them inside. If a box turns out to be
mislabelled, it's still sitting on the bay — you fix the label and carry that
one box in. You never have to unload the whole truck again.

That loading bay is the **staging table**. It is the single most important idea
in this feature.

| Warehouse move | Tally import |
|---|---|
| The truck | Your Tally XML file |
| The loading bay | Staging rows (`tally_staging_records`) |
| Labelling each box | Mapping each Tally name to one of our records |
| Walking the bay before carrying anything in | The dry run |
| Carrying boxes inside | The actual import |
| Carrying a bad box back out | Rollback |

---

## The five steps

### 1. Upload — "unload onto the bay"

You give us a Tally export file. We read it and write **one row per thing we
found**: one for each ledger, each stock item, each invoice, each payment.

Nothing touches your accounts yet. Not one rupee moves.

Three things about Tally files make this harder than it sounds, and each has a
specific defence in the code:

- **Tally can't decide on an encoding.** Depending on version and export
  option, the same file might be UTF-8 or UTF-16. So the browser sends us the
  raw bytes (base64-encoded) rather than text, and we work out the encoding
  ourselves. Guessing wrong would turn every Indian name into question marks.
- **Tally writes broken XML.** A company called "A & B Traders" comes out with
  a bare `&`, which strictly speaking is illegal XML and makes normal parsers
  refuse the file. We repair those before parsing.
- **Tally's debits look like credits.** In a Tally voucher, a **negative**
  amount means a *debit*. That is backwards from what most people expect, and
  getting it wrong would flip the sign on every single voucher. We convert once,
  at the door, into plain "debit" and "credit" columns — and there's a test that
  fails loudly if anyone ever changes it.

### 2. Auto-map — "label the boxes"

Now the real question: for every Tally name, which of our records is it?

We try four things, in order of how much we trust them:

1. **Have we seen this exact Tally ID before?** Tally gives everything a
   permanent internal ID (a GUID) that survives renaming. If you imported this
   company before and we recorded "this GUID = that customer", we reuse it.
   Confidence: 100%.
2. **Does the name match exactly?** "ABC Traders" = "ABC Traders". 95%.
3. **Does it match once you ignore the noise?** "ABC Traders Pvt. Ltd." and
   "ABC Traders" are the same company. We strip punctuation and legal suffixes
   before comparing. 85%.
4. **Is it close?** "Bharath Steel" vs "Bharat Steel" — probably the same, but
   we're not sure. We propose it and show the confidence score.

If nothing clears the bar, we say so honestly: **"this will be created new"**.

The screen sorts **least confident first**, on purpose. If you import 400
ledgers and 390 matched exactly, you shouldn't have to look at 400 rows — you
should look at the 10 the computer wasn't sure about.

**When you correct one, we lock it.** Re-running auto-map won't touch it, and
neither will the next file from the same Tally company. Your judgement beats
our guess, permanently.

#### One important detail: groups and ledgers are different shapes

In a chart of accounts, some entries are *folders* ("Sundry Debtors") and some
are *pages* ("ABC Traders"). Early on this had a real bug: the name matcher
happily matched the Tally folder "North Debtors" to one of our *pages*. Every
ledger that should have gone inside then had nowhere to live and got dumped at
the top of the tree.

The fix is structural, not a tweak to the matching: a Tally folder is only ever
compared against our folders, and a Tally page only against our pages.

### 3. Dry run — "walk the bay before carrying anything in"

We work out exactly what *would* happen and tell you:

- how many invoices, payments, customers, items you'd get;
- which Tally names are still unmatched — and therefore which vouchers will
  fail, and why;
- anything that would block the whole run (no chart of accounts, say).

Still nothing written. This is where a tester compares the numbers to Tally
*before* committing.

### 4. Run — "carry the boxes in"

Now we create the real records. Two rules govern this.

**Rule one: order matters.** You cannot create an invoice for a customer who
doesn't exist, or for an item that doesn't exist. So there's a fixed order:
folders before pages, masters before transactions, and — the one people miss —
**invoices before payments**, because a Tally receipt says "this settles bill
INV-001", and we can only honour that if INV-001 already exists.

**Rule two: each record is its own transaction.** If voucher 2,847 of 4,000 is
malformed, it fails *on its own*. It gets marked with a readable reason, and the
other 3,999 still import. The alternative — one giant transaction — means one
bad voucher throws away an hour of work.

And here's the part that matters most for trusting the result:

> **We don't write to your ledgers directly.** An imported invoice is created by
> exactly the same code that runs when you type an invoice into the screen. Same
> validation, same accounting entries, same GST, same stock movement.

There's no "import mode" that skips the rules. That's why an imported book
balances: it went through the same door as everything else.

### 5. Rollback — "carry them back out"

Numbers don't match Tally? One button cancels everything the import created.

Two deliberate choices here:

- **Newest first.** Payments are cancelled before the invoices they paid,
  because you can't un-bill something that still has a payment attached to it.
- **We cancel, we don't delete.** Cancelling writes the *reversing* accounting
  entries, so your books show what the import did and what it undid. Deleting
  would leave a hole where an auditor expects a trail. Master records
  (customers, items) are left alone — they're harmless, and deleting them would
  break anything you've created against them since.

Then: fix the mapping, import again. That loop — import, compare, roll back,
fix, repeat — is exactly what a tester needs, and it's why the staging bay
exists.

---

## Two problems we hit, and what they taught us

These are worth recording because both were invisible until the tests ran.

### "The books know things the file doesn't say"

Tally has 28 built-in account folders — "Sundry Debtors", "Bank Accounts",
"Duties & Taxes". Because they're built in, many exports **never mention them**.
The file just says a ledger's parent is "Sundry Debtors" and assumes you know
what that is.

Result: every customer landed at the root of the chart of accounts, and the
imported tree looked nothing like the Tally one — which defeats the entire point
when someone is comparing the two side by side.

The fix: when a ledger names a built-in folder that isn't in the file, we
**create that folder** in the right place. We only do this for the 28 we
recognise. Inventing folders for names we don't recognise would be guessing, so
we don't.

### "A failure poisons what comes after it"

When one record failed, we undid its changes — correct. But undoing also
invalidates every other record the program is holding in memory. The *next*
record then crashed on a plain data read, and so did the one after that. One bad
voucher took the run down with it, which is the exact failure mode the whole
per-record design was meant to prevent.

Two fixes:

1. The fast lookup table the import uses ("this Tally name = that record ID")
   now holds **plain copies** of the data, not live database handles. A failed
   record can't invalidate it any more.
2. Anything still holding a live handle is refreshed before it's read.

The lesson generalises: *a rollback isn't only about the database — it's about
everything you were holding when it happened.*

---

## Where this shows up in the app

- **Its own module**, `Data Migration`, on the home screen — because a migration
  is a project, not a screen: upload, map, dry run, compare, roll back, repeat.
- **An "Import from Tally" button** on the module screens where it makes sense
  (Customers, Items, Warehouses, Chart of Accounts, and every master list served
  by the generic engine), plus a nav entry in every module.

That button doesn't run a separate mini-import. It can't: a sales voucher needs
its customer, its item *and* its tax ledger, so a per-screen import would fail
on its own. What it does is open the wizard with that module's entities
pre-selected, and bring the masters those documents depend on. That's the
honest version of "import Customers from here".

- **A coverage page** listing every Tally entity and what it becomes, including
  the things we *don't* support yet. That page is generated from the same table
  the importer reads, so it can't drift from reality.

---

## What it can't do yet

Stated plainly, because a migration tool that overstates itself is worse than
one that doesn't exist:

- **Payroll** — there's no HR module here. Those vouchers are counted and
  reported, not imported.
- **Cost Categories** — Tally lets you slice costs two ways at once; we have one
  cost-centre tree.
- **Quantity-slab pricing** — collapses to the lowest slab, and says so.
- **Credit notes with no traceable original** — Tally doesn't always record
  which invoice a note reverses. When we can't find it, the note is imported as
  a journal with the same accounting effect, and flagged.
- **CSV files** — work, but carry no bill references or warehouse detail. Every
  CSV import says so in its log. Use XML if you need payments matched to
  invoices.

Everything in that list is visible in the app, on the coverage page and in the
import log. Nothing is quietly skipped.
