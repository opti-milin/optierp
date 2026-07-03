"""GST returns — GSTR-1 (outward supplies) and GSTR-3B (summary) — India Compliance Phase 2.

Read-only monthly returns computed from **submitted** Sales/Purchase Invoices, reusing the
existing GST fields (place_of_supply, per-line HSN, the Output/Input GST tax rows). All figures
are base currency (INR) and **net of credit notes** (returns carry negative amounts), the way
the GST portal expects.

Design notes / deliberate simplifications (v1):
- Tax amounts come from the invoices' **actual tax rows** (join to the Account name to bucket
  CGST / SGST / IGST / Cess), so the totals tie to the Output GST ledger.
- An invoice's overall **rate** is derived from its tax ÷ taxable value. GSTR-1 groups B2B/B2CL/
  CDN rows one-per-invoice at that rate — exact for the common single-rate invoice; a mixed-rate
  invoice is reported at its blended rate.
- The **HSN summary** and the **3.1 taxable/exempt split** are computed line-by-line: each line's
  rate is looked up from the HSN master (``_hsn_rates``) and the invoice's real tax is allocated to
  its lines by ``taxable × rate`` weight, so nil/exempt lines get zero and the allocation still
  sums to the ledger tax.
- **ITC** (GSTR-3B §4) reads Purchase-Invoice tax rows; an unlabelled "Input GST" head is split to
  IGST vs CGST+SGST by the purchase's inter/intra state, matching the auto-GST posting.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.gst_states import gst_state_label_of, state_code_of
from app.models.accounts import Account, PurchaseInvoice, SalesInvoice
from app.models.core import Company
from app.models.stock import Item
from app.schemas.compliance import (
    Cmp08Report,
    CompositionTaxRow,
    Gstr1B2B,
    Gstr1B2CS,
    Gstr1DocSummary,
    Gstr1Hsn,
    Gstr1Invoice,
    Gstr1Report,
    Gstr1Totals,
    Gstr3bInterStateRow,
    Gstr3bItcRow,
    Gstr3bReport,
    Gstr3bTaxRow,
    Gstr4Report,
    IffReport,
    composition_rate_for,
)
from app.services.accounts_common import _hsn_rates
from app.services.gst_settings import get_gst_settings

ZERO = Decimal("0")
Q2 = Decimal("0.01")

# Inter-state B2C invoices above this value are reported invoice-wise (B2CL); the
# rest are consolidated (B2CS). Reduced from ₹2.5L to ₹1L (Notf. 12/2024).
B2CL_THRESHOLD = Decimal("100000")


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


def _component(name: str | None) -> str | None:
    """Bucket a tax account/description into a GST component (or None if not GST)."""
    u = (name or "").upper()
    if "IGST" in u:
        return "igst"
    if "CGST" in u:
        return "cgst"
    if "UTGST" in u or "SGST" in u:
        return "sgst"
    if "CESS" in u:
        return "cess"
    return None


def _filing_period(to_date: date) -> str:
    """Portal filing period 'MMYYYY'. Derived from the window's LAST month so a monthly
    return reads as that month and a **quarterly** (QRMP) return reads as the quarter's
    last month — the GST portal's convention for a quarterly GSTR-1/GSTR-3B."""
    return f"{to_date.month:02d}{to_date.year}"


def _fy_quarter(d: date) -> int:
    """Indian financial-year quarter (Q1 Apr–Jun … Q4 Jan–Mar) of a date."""
    return {1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3}[d.month]


def _fy_start_year(d: date) -> int:
    """Starting calendar year of the Indian FY (Apr–Mar) a date falls in."""
    return d.year if d.month >= 4 else d.year - 1


def _quarter_label(d: date) -> str:
    """CMP-08 quarter label, e.g. 'Q1-2026' (FY starting-year based)."""
    return f"Q{_fy_quarter(d)}-{_fy_start_year(d)}"


def _fy_label(d: date) -> str:
    """Financial-year label, e.g. '2026-27'."""
    start = _fy_start_year(d)
    return f"{start}-{(start + 1) % 100:02d}"


@dataclass
class _Split:
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    igst: Decimal = ZERO
    cess: Decimal = ZERO

    @property
    def tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst

    def add(self, other: "_Split") -> None:
        self.cgst += other.cgst
        self.sgst += other.sgst
        self.igst += other.igst
        self.cess += other.cess


@dataclass
class _Doc:
    """A per-invoice computed record used across the GSTR-1 sections."""

    inv: SalesInvoice
    gstin: str | None
    party_name: str | None
    pos: str  # "NN-State" label
    pos_code: str | None
    inter: bool
    taxable: Decimal
    invoice_value: Decimal
    split: _Split
    rate: Decimal
    is_return: bool
    line_tax: list = field(default_factory=list)  # [(line, treatment, rate, _Split), ...]


def _split_invoice(inv, acct_names: dict, *, inter: bool) -> _Split:
    """Bucket an invoice's actual tax rows into CGST/SGST/IGST/Cess. An unlabelled
    'Input GST' head (purchases) is allocated by the supply's inter/intra split."""
    s = _Split()
    unclassified = ZERO
    for t in inv.taxes:
        amt = Decimal(t.base_tax_amount or 0)
        comp = _component(acct_names.get(t.account_head_id)) or _component(t.description)
        if comp:
            setattr(s, comp, getattr(s, comp) + amt)
        else:
            unclassified += amt
    if unclassified:
        if inter:
            s.igst += unclassified
        else:
            s.cgst += unclassified / 2
            s.sgst += unclassified / 2
    return s


def _allocate_lines(inv, split: _Split, hsn_rate: dict, treat: dict) -> list:
    """Attribute the invoice's real tax to its lines by ``taxable × rate`` weight so
    nil/exempt lines get zero and the allocation sums back to the invoice tax.
    Returns [(line, treatment, rate, _Split), ...]."""
    infos = []
    weights = []
    total_w = ZERO
    for li in inv.items:
        treatment = treat.get(li.item_id, "Taxable") if li.item_id else "Taxable"
        rate = hsn_rate.get(li.hsn_sac_code or "", ZERO) if treatment == "Taxable" else ZERO
        base = Decimal(li.base_net_amount or 0)
        w = abs(base) * (rate if rate else ZERO)
        infos.append([li, treatment, rate, base])
        weights.append(w)
        total_w += w
    # Fallback: a taxable invoice whose lines carry no HSN rate — distribute by value
    # so the ledger tax is never lost.
    if total_w == ZERO and split.tax:
        weights = [abs(Decimal(li.base_net_amount or 0)) for li in inv.items]
        total_w = sum(weights, ZERO)

    out = []
    for (li, treatment, rate, _base), w in zip(infos, weights):
        frac = (w / total_w) if total_w else ZERO
        out.append(
            (
                li,
                treatment,
                rate,
                _Split(
                    cgst=split.cgst * frac,
                    sgst=split.sgst * frac,
                    igst=split.igst * frac,
                    cess=split.cess * frac,
                ),
            )
        )
    return out


async def _load_docs(
    db: AsyncSession, company: Company, from_date: date, to_date: date
) -> tuple[list[_Doc], dict, dict]:
    """Load submitted sales invoices in the window as computed `_Doc`s, plus the
    account-name map and per-item gst_treatment map."""
    acct_names = dict(
        (
            await db.execute(select(Account.id, Account.account_name).where(Account.company_id == company.id))
        ).all()
    )
    rows = (
        (
            await db.execute(
                select(SalesInvoice)
                .options(selectinload(SalesInvoice.items), selectinload(SalesInvoice.taxes))
                .where(
                    SalesInvoice.company_id == company.id,
                    SalesInvoice.docstatus == 1,
                    SalesInvoice.is_opening.is_(False),
                    SalesInvoice.posting_date >= from_date,
                    SalesInvoice.posting_date <= to_date,
                )
                .order_by(SalesInvoice.posting_date, SalesInvoice.name)
            )
        )
        .scalars()
        .all()
    )
    item_ids = {li.item_id for inv in rows for li in inv.items if li.item_id}
    treat = (
        dict((await db.execute(select(Item.id, Item.gst_treatment).where(Item.id.in_(item_ids)))).all())
        if item_ids
        else {}
    )
    hsn_codes = {li.hsn_sac_code for inv in rows for li in inv.items if li.hsn_sac_code}
    hsn_rate = await _hsn_rates(db, hsn_codes)

    company_code = state_code_of(company.tax_id)
    docs: list[_Doc] = []
    for inv in rows:
        gstin = inv.customer.tax_id if inv.customer else None
        gstin = gstin.strip() if gstin else None
        registered = bool(gstin and len(gstin) == 15)
        pos = (
            inv.place_of_supply
            if inv.place_of_supply and inv.place_of_supply[:2].isdigit()
            else gst_state_label_of(gstin) or gst_state_label_of(company.tax_id) or ""
        )
        pos_code = pos[:2] if pos[:2].isdigit() else None
        inter = bool(company_code and pos_code and company_code != pos_code)
        split = _split_invoice(inv, acct_names, inter=inter)
        taxable = Decimal(inv.base_net_total or 0)
        rate = (split.tax / taxable * 100) if taxable else ZERO
        docs.append(
            _Doc(
                inv=inv,
                gstin=gstin if registered else None,
                party_name=inv.customer.customer_name if inv.customer else None,
                pos=pos,
                pos_code=pos_code,
                inter=inter,
                taxable=taxable,
                invoice_value=Decimal(inv.base_grand_total or 0),
                split=split,
                rate=rate,
                is_return=bool(inv.is_return),
                line_tax=_allocate_lines(inv, split, hsn_rate, treat),
            )
        )
    return docs, hsn_rate, treat


def _to_invoice(d: _Doc) -> Gstr1Invoice:
    return Gstr1Invoice(
        invoice_id=d.inv.id,
        name=d.inv.name,
        posting_date=d.inv.posting_date,
        counterparty_gstin=d.gstin,
        invoice_value=_q(d.invoice_value),
        place_of_supply=d.pos,
        reverse_charge=bool(d.inv.is_reverse_charge),
        rate=_q(d.rate),
        taxable_value=_q(d.taxable),
        cgst=_q(d.split.cgst),
        sgst=_q(d.split.sgst),
        igst=_q(d.split.igst),
        cess=_q(d.split.cess),
        is_return=d.is_return,
    )


async def gstr1(db: AsyncSession, company: Company, *, from_date: date, to_date: date) -> Gstr1Report:
    """GSTR-1 outward-supply return: B2B, B2C-Large, B2C-Small, credit/debit notes,
    HSN summary and the document-issued summary."""
    docs, _hsn_rate, _treat = await _load_docs(db, company, from_date, to_date)

    b2b_map: dict[str, list[_Doc]] = defaultdict(list)
    b2cl: list[Gstr1Invoice] = []
    b2cs_map: dict[tuple, _Split] = defaultdict(_Split)
    b2cs_taxable: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    cdnr: list[Gstr1Invoice] = []
    cdnur: list[Gstr1Invoice] = []

    for d in docs:
        if d.is_return:
            (cdnr if d.gstin else cdnur).append(_to_invoice(d))
            continue
        if d.gstin:
            b2b_map[d.gstin].append(d)
        elif d.inter and d.invoice_value > B2CL_THRESHOLD:
            b2cl.append(_to_invoice(d))
        else:
            key = (d.pos, "INTER" if d.inter else "INTRA", _q(d.rate))
            b2cs_map[key].add(d.split)
            b2cs_taxable[key] += d.taxable

    b2b = []
    for gstin, ds in sorted(b2b_map.items()):
        invoices = [_to_invoice(d) for d in ds]
        b2b.append(
            Gstr1B2B(
                gstin=gstin,
                party_name=ds[0].party_name,
                invoices=invoices,
                taxable_value=_q(sum((d.taxable for d in ds), ZERO)),
                total_tax=_q(sum((d.split.tax + d.split.cess for d in ds), ZERO)),
            )
        )

    b2cs = [
        Gstr1B2CS(
            place_of_supply=pos,
            supply_type=sply,
            rate=rate,
            taxable_value=_q(b2cs_taxable[(pos, sply, rate)]),
            cgst=_q(s.cgst),
            sgst=_q(s.sgst),
            igst=_q(s.igst),
            cess=_q(s.cess),
        )
        for (pos, sply, rate), s in sorted(b2cs_map.items(), key=lambda kv: (kv[0][0], str(kv[0][2])))
    ]

    hsn = _hsn_summary(docs)
    report_docs = _doc_summary(docs)

    tx = _Split()
    taxable_total = ZERO
    count = 0
    for d in docs:
        tx.add(d.split)
        taxable_total += d.taxable
        count += 1

    return Gstr1Report(
        gstin=company.tax_id or None,
        filing_period=_filing_period(to_date),
        from_date=from_date,
        to_date=to_date,
        b2b=b2b,
        b2cl=sorted(b2cl, key=lambda i: i.name),
        b2cs=b2cs,
        cdnr=cdnr,
        cdnur=cdnur,
        hsn=hsn,
        docs=report_docs,
        totals=Gstr1Totals(
            taxable_value=_q(taxable_total),
            cgst=_q(tx.cgst),
            sgst=_q(tx.sgst),
            igst=_q(tx.igst),
            cess=_q(tx.cess),
            invoice_count=count,
        ),
    )


def _hsn_summary(docs: list[_Doc]) -> list[Gstr1Hsn]:
    agg: dict[tuple, dict] = {}
    for d in docs:
        for li, treatment, rate, s in d.line_tax:
            key = (li.hsn_sac_code or "", _q(rate))
            e = agg.setdefault(
                key,
                {"desc": li.item_name, "uqc": li.uom, "qty": ZERO, "taxable": ZERO, "s": _Split()},
            )
            e["qty"] += Decimal(li.qty or 0)
            e["taxable"] += Decimal(li.base_net_amount or 0)
            e["s"].add(s)
    rows = []
    for (hsn, rate), e in sorted(agg.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        rows.append(
            Gstr1Hsn(
                hsn_code=hsn or None,
                description=e["desc"],
                uqc=e["uqc"],
                qty=_q(e["qty"]),
                rate=rate,
                taxable_value=_q(e["taxable"]),
                cgst=_q(e["s"].cgst),
                sgst=_q(e["s"].sgst),
                igst=_q(e["s"].igst),
                cess=_q(e["s"].cess),
            )
        )
    return rows


def _doc_summary(docs: list[_Doc]) -> list[Gstr1DocSummary]:
    """Document-issued summary, split by nature (invoice vs credit note). Cancelled
    docs are not loaded (docstatus filter), so cancelled = 0 here."""
    groups = {
        "Invoices for outward supply": [d for d in docs if not d.is_return],
        "Credit Note": [d for d in docs if d.is_return],
    }
    out = []
    for nature, ds in groups.items():
        if not ds:
            continue
        names = sorted(d.inv.name for d in ds)
        out.append(
            Gstr1DocSummary(
                nature=nature,
                from_no=names[0],
                to_no=names[-1],
                total_count=len(names),
                cancelled=0,
                net_issued=len(names),
            )
        )
    return out


async def gstr3b(db: AsyncSession, company: Company, *, from_date: date, to_date: date) -> Gstr3bReport:
    """GSTR-3B summary: §3.1 outward tax liability, §3.2 inter-state to unregistered,
    §4 eligible ITC (from Purchase Invoices)."""
    docs, _hsn_rate, _treat = await _load_docs(db, company, from_date, to_date)

    taxable = _Split()  # 3.1(a) tax
    taxable_value = ZERO
    nil_exempt_value = ZERO
    non_gst_value = ZERO
    inter_unreg: dict[str, dict] = {}

    for d in docs:
        for li, treatment, _rate, s in d.line_tax:
            val = Decimal(li.base_net_amount or 0)
            if treatment in ("Nil-Rated", "Exempt"):
                nil_exempt_value += val
            elif treatment == "Non-GST":
                non_gst_value += val
            else:  # Taxable
                taxable_value += val
                taxable.add(s)
                # 3.2: inter-state, unregistered recipient
                if d.inter and not d.gstin:
                    e = inter_unreg.setdefault(d.pos, {"val": ZERO, "igst": ZERO})
                    e["val"] += val
                    e["igst"] += s.igst

    # 3.1(d) inward reverse charge + §4 ITC — from purchase invoices.
    rcm, itc_rcm, itc_other = await _purchase_itc(db, company, from_date, to_date)

    outward = [
        Gstr3bTaxRow(
            label="(a) Outward taxable supplies (other than zero-rated, nil, exempt)",
            taxable_value=_q(taxable_value),
            igst=_q(taxable.igst),
            cgst=_q(taxable.cgst),
            sgst=_q(taxable.sgst),
            cess=_q(taxable.cess),
        ),
        Gstr3bTaxRow(
            label="(b) Outward taxable supplies (zero-rated)",
            taxable_value=ZERO, igst=ZERO, cgst=ZERO, sgst=ZERO, cess=ZERO,
        ),
        Gstr3bTaxRow(
            label="(c) Other outward supplies (nil-rated, exempt)",
            taxable_value=_q(nil_exempt_value), igst=ZERO, cgst=ZERO, sgst=ZERO, cess=ZERO,
        ),
        Gstr3bTaxRow(
            label="(d) Inward supplies (liable to reverse charge)",
            taxable_value=_q(rcm["val"]),
            igst=_q(rcm["s"].igst), cgst=_q(rcm["s"].cgst), sgst=_q(rcm["s"].sgst), cess=_q(rcm["s"].cess),
        ),
        Gstr3bTaxRow(
            label="(e) Non-GST outward supplies",
            taxable_value=_q(non_gst_value), igst=ZERO, cgst=ZERO, sgst=ZERO, cess=ZERO,
        ),
    ]

    itc = [
        Gstr3bItcRow(
            label="(A)(3) Inward supplies liable to reverse charge",
            igst=_q(itc_rcm.igst), cgst=_q(itc_rcm.cgst), sgst=_q(itc_rcm.sgst), cess=_q(itc_rcm.cess),
        ),
        Gstr3bItcRow(
            label="(A)(5) All other ITC",
            igst=_q(itc_other.igst), cgst=_q(itc_other.cgst), sgst=_q(itc_other.sgst), cess=_q(itc_other.cess),
        ),
    ]

    # net payable = output tax (3.1a + 3.1d reverse-charge liability) − eligible ITC
    net = Gstr3bTaxRow(
        label="Net tax payable",
        taxable_value=ZERO,
        igst=_q(taxable.igst + rcm["s"].igst - itc_rcm.igst - itc_other.igst),
        cgst=_q(taxable.cgst + rcm["s"].cgst - itc_rcm.cgst - itc_other.cgst),
        sgst=_q(taxable.sgst + rcm["s"].sgst - itc_rcm.sgst - itc_other.sgst),
        cess=_q(taxable.cess + rcm["s"].cess - itc_rcm.cess - itc_other.cess),
    )

    return Gstr3bReport(
        gstin=company.tax_id or None,
        filing_period=_filing_period(to_date),
        from_date=from_date,
        to_date=to_date,
        outward=outward,
        inter_state_unreg=[
            Gstr3bInterStateRow(place_of_supply=pos, taxable_value=_q(e["val"]), igst=_q(e["igst"]))
            for pos, e in sorted(inter_unreg.items())
        ],
        itc=itc,
        net_tax_payable=net,
    )


async def _purchase_itc(
    db: AsyncSession, company: Company, from_date: date, to_date: date
) -> tuple[dict, _Split, _Split]:
    """Read Purchase-Invoice tax rows → (reverse-charge liability+base, RCM ITC, other ITC)."""
    acct_names = dict(
        (
            await db.execute(select(Account.id, Account.account_name).where(Account.company_id == company.id))
        ).all()
    )
    rows = (
        (
            await db.execute(
                select(PurchaseInvoice)
                .options(selectinload(PurchaseInvoice.taxes))
                .where(
                    PurchaseInvoice.company_id == company.id,
                    PurchaseInvoice.docstatus == 1,
                    PurchaseInvoice.is_opening.is_(False),
                    PurchaseInvoice.posting_date >= from_date,
                    PurchaseInvoice.posting_date <= to_date,
                )
            )
        )
        .scalars()
        .all()
    )
    company_code = state_code_of(company.tax_id)
    rcm = {"val": ZERO, "s": _Split()}
    itc_rcm = _Split()
    itc_other = _Split()
    for inv in rows:
        sup_gstin = inv.supplier.tax_id if inv.supplier else None
        sup_code = state_code_of(sup_gstin)
        inter = bool(company_code and sup_code and company_code != sup_code)
        inp, outp = _purchase_split(inv, acct_names, inter=inter)
        if inv.is_reverse_charge:
            # RCM: the Input-GST head is the ITC (4A3); the Output-GST heads are the
            # self-assessed liability (3.1d). A legacy RCM bill with only an Input head
            # falls back to using it for both sides (they net to zero, which is correct).
            rcm["val"] += Decimal(inv.base_net_total or 0)
            liability = outp if (outp.tax or outp.cess) else inp
            rcm["s"].add(liability)
            itc_rcm.add(inp)
        else:
            itc_other.add(inp)
    return rcm, itc_rcm, itc_other


def _purchase_split(inv, acct_names: dict, *, inter: bool) -> tuple[_Split, _Split]:
    """Split a purchase invoice's tax rows into (input-tax-credit heads, output/RCM-liability
    heads). An unlabelled 'Input GST' head is treated as ITC and allocated by inter/intra."""
    inp = _Split()
    outp = _Split()
    inp_unclassified = ZERO
    for t in inv.taxes:
        amt = Decimal(t.base_tax_amount or 0)
        name = acct_names.get(t.account_head_id) or ""
        comp = _component(name) or _component(t.description)
        if "INPUT" in name.upper() or "INPUT" in (t.description or "").upper():
            if comp:
                setattr(inp, comp, getattr(inp, comp) + amt)
            else:
                inp_unclassified += amt
        elif comp:  # an Output GST head → liability side
            setattr(outp, comp, getattr(outp, comp) + amt)
        else:
            inp_unclassified += amt
    if inp_unclassified:
        if inter:
            inp.igst += inp_unclassified
        else:
            inp.cgst += inp_unclassified / 2
            inp.sgst += inp_unclassified / 2
    return inp, outp


# --------------------------------------------------------------------------------------
# IFF — Invoice Furnishing Facility (QRMP months 1 & 2 of a quarter).
#
# A QRMP filer furnishes B2B (+ B2CL + credit/debit notes) monthly via IFF, then the full
# quarterly GSTR-1 (with B2CS/HSN/docs) at quarter end. IFF is therefore GSTR-1 restricted
# to the invoice-wise sections, so it is derived from `gstr1()` — no duplicated bucketing.
# --------------------------------------------------------------------------------------


async def iff(db: AsyncSession, company: Company, *, from_date: date, to_date: date) -> IffReport:
    """IFF for a month: the B2B / B2C-Large / credit-debit-note sections of GSTR-1, with
    totals over only those furnished documents."""
    g1 = await gstr1(db, company, from_date=from_date, to_date=to_date)

    tx = _Split()
    taxable = ZERO
    count = 0

    def _acc(inv: Gstr1Invoice) -> None:
        nonlocal taxable, count
        tx.cgst += inv.cgst
        tx.sgst += inv.sgst
        tx.igst += inv.igst
        tx.cess += inv.cess
        taxable += inv.taxable_value
        count += 1

    for blk in g1.b2b:
        for inv in blk.invoices:
            _acc(inv)
    for inv in g1.b2cl:
        _acc(inv)
    for inv in g1.cdnr:
        _acc(inv)
    for inv in g1.cdnur:
        _acc(inv)

    return IffReport(
        gstin=g1.gstin,
        filing_period=g1.filing_period,
        from_date=from_date,
        to_date=to_date,
        b2b=g1.b2b,
        b2cl=g1.b2cl,
        cdnr=g1.cdnr,
        cdnur=g1.cdnur,
        totals=Gstr1Totals(
            taxable_value=_q(taxable),
            cgst=_q(tx.cgst),
            sgst=_q(tx.sgst),
            igst=_q(tx.igst),
            cess=_q(tx.cess),
            invoice_count=count,
        ),
    )


def iff_json(report: IffReport) -> dict:
    """Serialise IFF to the portal JSON — the GSTR-1 schema with only the furnished
    (b2b / b2cl / cdnr / cdnur) sections."""
    shell = Gstr1Report(
        gstin=report.gstin,
        filing_period=report.filing_period,
        from_date=report.from_date,
        to_date=report.to_date,
        b2b=report.b2b,
        b2cl=report.b2cl,
        b2cs=[],
        cdnr=report.cdnr,
        cdnur=report.cdnur,
        hsn=[],
        docs=[],
        totals=report.totals,
    )
    return gstr1_json(shell)


# --------------------------------------------------------------------------------------
# Composition scheme — CMP-08 (quarterly) + GSTR-4 (annual).
#
# A composition dealer's outward supplies carry NO output GST (Bill of Supply — the tax
# engine suppresses it). Their liability is a flat composite tax on turnover plus the
# reverse-charge tax self-assessed on inward supplies. Both reuse `_load_docs` (outward
# turnover, net of returns) and `_purchase_itc` (inward RCM liability).
# --------------------------------------------------------------------------------------


def _composition_tax(turnover: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Composite tax on turnover, split equally CGST/SGST (composition = intra-state only)."""
    half = turnover * rate / 100 / 2
    return half, half


async def cmp08(db: AsyncSession, company: Company, *, from_date: date, to_date: date) -> Cmp08Report:
    """CMP-08 — quarterly self-assessed composition tax: 3(1) composite tax on outward
    turnover, 3(2) tax on inward reverse-charge supplies, 3(3) total payable."""
    settings = await get_gst_settings(db, company.id)
    rate = composition_rate_for(settings.composition_category)

    docs, _hsn_rate, _treat = await _load_docs(db, company, from_date, to_date)
    turnover = sum((d.taxable for d in docs), ZERO)  # net of returns
    out_cgst, out_sgst = _composition_tax(turnover, rate)

    rcm, _itc_rcm, _itc_other = await _purchase_itc(db, company, from_date, to_date)
    rs = rcm["s"]

    rows = [
        CompositionTaxRow(
            label="3(1) Outward supplies (composition levy)",
            taxable_value=_q(turnover), igst=ZERO, cgst=_q(out_cgst), sgst=_q(out_sgst), cess=ZERO,
        ),
        CompositionTaxRow(
            label="3(2) Inward supplies attracting reverse charge",
            taxable_value=_q(rcm["val"]), igst=_q(rs.igst), cgst=_q(rs.cgst), sgst=_q(rs.sgst), cess=_q(rs.cess),
        ),
    ]
    payable = CompositionTaxRow(
        label="3(3) Tax payable",
        taxable_value=ZERO,
        igst=_q(rs.igst),
        cgst=_q(out_cgst + rs.cgst),
        sgst=_q(out_sgst + rs.sgst),
        cess=_q(rs.cess),
    )
    rows.append(payable)
    total_tax = payable.igst + payable.cgst + payable.sgst + payable.cess

    return Cmp08Report(
        gstin=company.tax_id or None,
        filing_period=_quarter_label(from_date),
        from_date=from_date,
        to_date=to_date,
        composition_category=settings.composition_category,
        composition_rate=rate,
        rows=rows,
        total_tax=_q(total_tax),
    )


async def gstr4(db: AsyncSession, company: Company, *, from_date: date, to_date: date) -> Gstr4Report:
    """GSTR-4 — the composition dealer's annual return: outward turnover + composite tax
    (Table 6), inward reverse-charge (Table 4B), a per-quarter breakdown (the CMP-08s),
    and the total annual tax."""
    settings = await get_gst_settings(db, company.id)
    rate = composition_rate_for(settings.composition_category)

    docs, _hsn_rate, _treat = await _load_docs(db, company, from_date, to_date)
    turnover = sum((d.taxable for d in docs), ZERO)
    out_cgst, out_sgst = _composition_tax(turnover, rate)

    rcm, _itc_rcm, _itc_other = await _purchase_itc(db, company, from_date, to_date)
    rs = rcm["s"]

    outward = CompositionTaxRow(
        label="Table 6 — Outward supplies (composition levy)",
        taxable_value=_q(turnover), igst=ZERO, cgst=_q(out_cgst), sgst=_q(out_sgst), cess=ZERO,
    )
    inward = CompositionTaxRow(
        label="Table 4B — Inward supplies (reverse charge)",
        taxable_value=_q(rcm["val"]), igst=_q(rs.igst), cgst=_q(rs.cgst), sgst=_q(rs.sgst), cess=_q(rs.cess),
    )
    total = CompositionTaxRow(
        label="Total tax payable",
        taxable_value=ZERO,
        igst=_q(rs.igst), cgst=_q(out_cgst + rs.cgst), sgst=_q(out_sgst + rs.sgst), cess=_q(rs.cess),
    )

    # per-quarter turnover → composite tax (what each CMP-08 declared)
    q_turnover: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for d in docs:
        q_turnover[_fy_quarter(d.inv.posting_date)] += d.taxable
    quarters = []
    for q in sorted(q_turnover):
        c, s = _composition_tax(q_turnover[q], rate)
        quarters.append(
            CompositionTaxRow(
                label=f"Q{q}", taxable_value=_q(q_turnover[q]), igst=ZERO, cgst=_q(c), sgst=_q(s), cess=ZERO,
            )
        )

    total_tax = total.igst + total.cgst + total.sgst + total.cess
    return Gstr4Report(
        gstin=company.tax_id or None,
        filing_period=_fy_label(from_date),
        from_date=from_date,
        to_date=to_date,
        composition_category=settings.composition_category,
        composition_rate=rate,
        rows=[outward, inward, total],
        quarters=quarters,
        total_tax=_q(total_tax),
    )


# --------------------------------------------------------------------------------------
# GSTR-1 portal JSON export (GST return offline-tool schema, subset).
# --------------------------------------------------------------------------------------


def _idt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def gstr1_json(report: Gstr1Report) -> dict:
    """Serialise a GSTR-1 report to the GST portal's JSON schema (subset: b2b, b2cl,
    b2cs, cdnr, cdnur, hsn, doc_issue) — the offline-tool upload format."""

    def _itms(inv: Gstr1Invoice) -> list[dict]:
        det = {"txval": float(inv.taxable_value), "rt": float(inv.rate)}
        if inv.igst:
            det["iamt"] = float(inv.igst)
        if inv.cgst:
            det["camt"] = float(inv.cgst)
        if inv.sgst:
            det["samt"] = float(inv.sgst)
        if inv.cess:
            det["csamt"] = float(inv.cess)
        return [{"num": 1, "itm_det": det}]

    b2b = [
        {
            "ctin": blk.gstin,
            "inv": [
                {
                    "inum": inv.name,
                    "idt": _idt(inv.posting_date),
                    "val": float(inv.invoice_value),
                    "pos": inv.place_of_supply[:2],
                    "rchrg": "Y" if inv.reverse_charge else "N",
                    "inv_typ": "R",
                    "itms": _itms(inv),
                }
                for inv in blk.invoices
            ],
        }
        for blk in report.b2b
    ]

    b2cl_map: dict[str, list[Gstr1Invoice]] = defaultdict(list)
    for inv in report.b2cl:
        b2cl_map[inv.place_of_supply[:2]].append(inv)
    b2cl = [
        {
            "pos": pos,
            "inv": [
                {
                    "inum": inv.name,
                    "idt": _idt(inv.posting_date),
                    "val": float(inv.invoice_value),
                    "itms": [{"num": 1, "itm_det": {"txval": float(inv.taxable_value), "rt": float(inv.rate),
                                                    "iamt": float(inv.igst), "csamt": float(inv.cess)}}],
                }
                for inv in invs
            ],
        }
        for pos, invs in sorted(b2cl_map.items())
    ]

    b2cs = [
        {
            "sply_ty": row.supply_type,
            "pos": row.place_of_supply[:2],
            "typ": "OE",
            "txval": float(row.taxable_value),
            "rt": float(row.rate),
            "iamt": float(row.igst),
            "camt": float(row.cgst),
            "samt": float(row.sgst),
            "csamt": float(row.cess),
        }
        for row in report.b2cs
    ]

    def _cdn(rows: list[Gstr1Invoice]) -> list[dict]:
        return [
            {
                "nt_num": inv.name,
                "nt_dt": _idt(inv.posting_date),
                "ntty": "C",
                "val": float(abs(inv.invoice_value)),
                "pos": inv.place_of_supply[:2],
                "itms": [
                    {
                        "num": 1,
                        "itm_det": {
                            "txval": float(abs(inv.taxable_value)),
                            "rt": float(inv.rate),
                            "iamt": float(abs(inv.igst)),
                            "camt": float(abs(inv.cgst)),
                            "samt": float(abs(inv.sgst)),
                            "csamt": float(abs(inv.cess)),
                        },
                    }
                ],
            }
            for inv in rows
        ]

    hsn = {
        "data": [
            {
                "num": i + 1,
                "hsn_sc": row.hsn_code,
                "desc": row.description,
                "uqc": (row.uqc or "OTH")[:3].upper(),
                "qty": float(row.qty),
                "rt": float(row.rate),
                "txval": float(row.taxable_value),
                "iamt": float(row.igst),
                "camt": float(row.cgst),
                "samt": float(row.sgst),
                "csamt": float(row.cess),
            }
            for i, row in enumerate(report.hsn)
        ]
    }

    doc_issue = {
        "doc_det": [
            {
                "doc_num": i + 1,
                "docs": [
                    {
                        "num": 1,
                        "from": doc.from_no,
                        "to": doc.to_no,
                        "totnum": doc.total_count,
                        "cancel": doc.cancelled,
                        "net_issue": doc.net_issued,
                    }
                ],
            }
            for i, doc in enumerate(report.docs)
        ]
    }

    out: dict = {
        "gstin": report.gstin,
        "fp": report.filing_period,
        "version": "GST3.0.4",
        "hash": "hash",
    }
    if b2b:
        out["b2b"] = b2b
    if b2cl:
        out["b2cl"] = b2cl
    if b2cs:
        out["b2cs"] = b2cs
    if report.cdnr:
        cdnr_map: dict[str, list[Gstr1Invoice]] = defaultdict(list)
        for inv in report.cdnr:
            cdnr_map[inv.counterparty_gstin or ""].append(inv)
        out["cdnr"] = [{"ctin": ctin, "nt": _cdn(invs)} for ctin, invs in sorted(cdnr_map.items())]
    if report.cdnur:
        out["cdnur"] = _cdn(report.cdnur)
    if hsn["data"]:
        out["hsn"] = hsn
    if doc_issue["doc_det"]:
        out["doc_issue"] = doc_issue
    return out
