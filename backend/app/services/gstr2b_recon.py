"""GSTR-2B reconciliation — India Compliance Phase 6.1.

Match the **purchase register** (booked Purchase Invoices) against the portal's **GSTR-2B**
(what suppliers actually filed) to protect Input Tax Credit — you can only claim ITC that
appears in your 2B. Each supplier document is bucketed:

* **Matched** — in both, taxable value + tax agree (within ₹1).
* **Mismatch** — in both, but the values differ (fix the bill or chase the supplier).
* **Only in Books** — you booked it, the supplier hasn't filed → ITC **at risk** (claim later).
* **Only in 2B** — the supplier filed it, you haven't booked the purchase → a missing bill / ITC.

Matching key = (supplier GSTIN, normalised supplier-invoice-number). The supplier invoice number
is our Purchase Invoice ``bill_no``; the 2B side is the ``inum``. Only registered-supplier
purchases (with a GSTIN) are reconciled — an unregistered supplier can't appear in 2B.

The 2B JSON is the file downloaded from the GST portal (or, later, pulled via a GSP). This is the
data layer; a live GSTR-2B pull is Phase 5 (GSP).
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounts import Account, PurchaseInvoice
from app.models.core import Company
from app.schemas.compliance import (
    Gstr2bReconReport,
    Gstr2bReconRow,
    Gstr2bReconSummary,
)

ZERO = Decimal("0")
Q2 = Decimal("0.01")
TOL = Decimal("1")  # ₹1 tolerance for a "match" (paise/rounding noise)


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


def _dec(x) -> Decimal:
    if x is None or x == "":
        return ZERO
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return ZERO


def _norm(s) -> str:
    """Normalise an invoice number for matching (drop spaces, upper-case)."""
    return "".join(str(s or "").split()).upper()


def _parse_date(s) -> date | None:
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _extract(ctin: str, obj: dict, inum, dt, *, sign: int) -> dict:
    """One portal invoice/note → a comparable record (item taxes summed)."""
    taxable = tax = ZERO
    for it in obj.get("itms", []) or []:
        d = it.get("itm_det", it)
        taxable += _dec(d.get("txval"))
        # GSTR-2B uses igst/cgst/sgst/cess; accept iamt/camt/samt/csamt too (GSTR-1 style)
        tax += (
            _dec(d.get("igst")) + _dec(d.get("cgst")) + _dec(d.get("sgst")) + _dec(d.get("cess"))
            + _dec(d.get("iamt")) + _dec(d.get("camt")) + _dec(d.get("samt")) + _dec(d.get("csamt"))
        )
    return {
        "ctin": (ctin or "").strip().upper(),
        "inum": str(inum or "").strip(),
        "dt": _parse_date(dt),
        "taxable": taxable * sign,
        "tax": tax * sign,
    }


def _parse_2b(doc: dict) -> list[dict]:
    """Pull comparable records from a portal GSTR-2B JSON (b2b + credit/debit notes),
    tolerating the common wrapper shapes (``data.docdata.b2b`` / ``docdata.b2b`` / ``b2b``)."""
    node = doc.get("data", doc) if isinstance(doc, dict) else {}
    if isinstance(node, dict):
        node = node.get("docdata", node)
    if not isinstance(node, dict):
        return []
    out: list[dict] = []
    for blk in node.get("b2b", []) or []:
        ctin = blk.get("ctin", "")
        for inv in blk.get("inv", []) or []:
            out.append(_extract(ctin, inv, inv.get("inum"), inv.get("dt"), sign=1))
    for blk in node.get("cdnr", []) or []:
        ctin = blk.get("ctin", "")
        for nt in blk.get("nt", []) or []:
            typ = (nt.get("ntty") or nt.get("typ") or "").upper()
            sign = -1 if typ == "C" else 1  # credit note reduces ITC
            out.append(
                _extract(ctin, nt, nt.get("nt_num") or nt.get("ntnum"),
                         nt.get("nt_dt") or nt.get("dt"), sign=sign)
            )
    return out


def _books_itc(inv: PurchaseInvoice, acct_names: dict) -> Decimal:
    """The Input GST (ITC) we booked on a purchase invoice. For a reverse-charge bill only
    the Input head is ITC (the Output heads are the self-assessed liability)."""
    total = ZERO
    for t in inv.taxes:
        amt = Decimal(t.base_tax_amount or 0)
        name = (acct_names.get(t.account_head_id) or "").upper()
        if inv.is_reverse_charge:
            if "INPUT" in name:
                total += amt
        else:
            total += amt
    return total


async def _books(db: AsyncSession, company: Company, from_date: date, to_date: date) -> list[dict]:
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
    out: list[dict] = []
    for inv in rows:
        gstin = (inv.supplier.tax_id or "").strip().upper() if inv.supplier else ""
        if len(gstin) != 15:
            continue  # unregistered supplier — cannot appear in 2B, skip from recon
        out.append(
            {
                "ctin": gstin,
                "inum": inv.bill_no or "",
                "dt": inv.bill_date,
                "taxable": Decimal(inv.base_net_total or 0),
                "tax": _books_itc(inv, acct_names),
                "ref": inv.name,
                "supplier_name": inv.supplier.supplier_name if inv.supplier else None,
            }
        )
    return out


async def reconcile_gstr2b(
    db: AsyncSession, company: Company, *, from_date: date, to_date: date, gstr2b: dict
) -> Gstr2bReconReport:
    portal = _parse_2b(gstr2b)
    books = await _books(db, company, from_date, to_date)

    portal_by_key: dict[tuple, dict] = {(p["ctin"], _norm(p["inum"])): p for p in portal}
    books_by_key: dict[tuple, dict] = {(b["ctin"], _norm(b["inum"])): b for b in books}

    rows: list[Gstr2bReconRow] = []
    matched = mismatch = only_books = only_2b = 0
    matched_itc = at_risk_itc = ZERO

    for key in sorted(set(portal_by_key) | set(books_by_key)):
        p = portal_by_key.get(key)
        b = books_by_key.get(key)
        if p and b:
            t_diff = p["taxable"] - b["taxable"]
            tax_diff = p["tax"] - b["tax"]
            ok = abs(t_diff) <= TOL and abs(tax_diff) <= TOL
            status = "Matched" if ok else "Mismatch"
            if ok:
                matched += 1
                matched_itc += b["tax"]
            else:
                mismatch += 1
            rows.append(
                Gstr2bReconRow(
                    supplier_gstin=b["ctin"], supplier_name=b["supplier_name"],
                    invoice_no=b["inum"] or p["inum"], invoice_date=b["dt"] or p["dt"],
                    books_ref=b["ref"], books_taxable=_q(b["taxable"]), books_tax=_q(b["tax"]),
                    portal_taxable=_q(p["taxable"]), portal_tax=_q(p["tax"]),
                    taxable_diff=_q(t_diff), tax_diff=_q(tax_diff), status=status,
                )
            )
        elif b:
            only_books += 1
            at_risk_itc += b["tax"]
            rows.append(
                Gstr2bReconRow(
                    supplier_gstin=b["ctin"], supplier_name=b["supplier_name"],
                    invoice_no=b["inum"], invoice_date=b["dt"],
                    books_ref=b["ref"], books_taxable=_q(b["taxable"]), books_tax=_q(b["tax"]),
                    status="Only in Books",
                )
            )
        else:  # p only
            only_2b += 1
            rows.append(
                Gstr2bReconRow(
                    supplier_gstin=p["ctin"], invoice_no=p["inum"], invoice_date=p["dt"],
                    portal_taxable=_q(p["taxable"]), portal_tax=_q(p["tax"]),
                    status="Only in 2B",
                )
            )

    summary = Gstr2bReconSummary(
        books_count=len(books),
        portal_count=len(portal),
        matched=matched,
        mismatch=mismatch,
        only_in_books=only_books,
        only_in_2b=only_2b,
        books_itc=_q(sum((b["tax"] for b in books), ZERO)),
        portal_itc=_q(sum((p["tax"] for p in portal), ZERO)),
        matched_itc=_q(matched_itc),
        at_risk_itc=_q(at_risk_itc),
    )
    # order: problems first (mismatch, only-in-2B, only-in-books), then matched
    order = {"Mismatch": 0, "Only in 2B": 1, "Only in Books": 2, "Matched": 3}
    rows.sort(key=lambda r: (order.get(r.status, 9), r.supplier_gstin or "", r.invoice_no or ""))
    return Gstr2bReconReport(from_date=from_date, to_date=to_date, summary=summary, rows=rows)
