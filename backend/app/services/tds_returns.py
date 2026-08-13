"""TDS returns — Form 26Q (quarterly TDS on non-salary payments) + Form 16A (the TDS
certificate for a deductee) — India Compliance Phase 6.2.

Read from submitted **Purchase Invoices** that carry a TDS ``tax_withholding_category``: the base
is ``base_net_total`` (amount paid/credited), the tax is ``tax_withholding_amount`` (deducted), and
the nature-of-payment **section** (194C, 194J, …) is parsed from the category name. Deductions are
grouped by **(deductee, section)** — the 26Q unit. The deductee **PAN** is derived from the supplier
GSTIN (chars 3-12). The deductor **TAN** is not captured yet (left blank for the filer).
"""

import re
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.accounts import PurchaseInvoice, TaxWithholdingCategory
from app.models.buying import Supplier
from app.models.core import Company
from app.schemas.compliance import (
    Form16A,
    Tds26qDoc,
    Tds26qReport,
    Tds26qRow,
    Tds26qSummary,
)

ZERO = Decimal("0")
Q2 = Decimal("0.01")
_SECTION = re.compile(r"19[0-9][A-Z]{0,2}")


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


def _section_of(category_name: str | None) -> str | None:
    """Extract the nature-of-payment section (e.g. '194C') from the category name."""
    if not category_name:
        return None
    m = _SECTION.search(category_name.upper())
    return m.group(0) if m else None


def pan_from_gstin(gstin: str | None) -> str | None:
    """A 15-char GSTIN embeds the holder's 10-char PAN at positions 3-12 (0-indexed 2:12)."""
    g = (gstin or "").strip()
    return g[2:12] if len(g) == 15 else None


async def _deductions(
    db: AsyncSession, company: Company, from_date: date, to_date: date,
    supplier_id: uuid.UUID | None = None,
) -> list[dict]:
    """Submitted purchase invoices in the window that withheld TDS → flat deduction records."""
    stmt = (
        select(PurchaseInvoice, Supplier, TaxWithholdingCategory)
        .join(Supplier, Supplier.id == PurchaseInvoice.supplier_id)
        .join(
            TaxWithholdingCategory,
            TaxWithholdingCategory.id == PurchaseInvoice.tax_withholding_category_id,
        )
        .where(
            PurchaseInvoice.company_id == company.id,
            PurchaseInvoice.docstatus == 1,
            PurchaseInvoice.tax_withholding_category_id.isnot(None),
            PurchaseInvoice.tax_withholding_amount != ZERO,
            PurchaseInvoice.posting_date >= from_date,
            PurchaseInvoice.posting_date <= to_date,
        )
        .order_by(PurchaseInvoice.posting_date, PurchaseInvoice.name)
    )
    if supplier_id is not None:
        stmt = stmt.where(PurchaseInvoice.supplier_id == supplier_id)

    out = []
    for inv, sup, twc in (await db.execute(stmt)).all():
        if twc.kind != "TDS":
            continue
        out.append(
            {
                "supplier_id": sup.id,
                "deductee_name": sup.supplier_name,
                "gstin": sup.tax_id,
                "pan": pan_from_gstin(sup.tax_id),
                "section": _section_of(twc.category_name),
                "category": twc.category_name,
                "rate": Decimal(twc.rate or 0),
                "base": Decimal(inv.base_net_total or 0),
                "tds": Decimal(inv.tax_withholding_amount or 0),
                "voucher": inv.name,
                "date": inv.posting_date,
            }
        )
    return out


def _group(recs: list[dict]) -> list[Tds26qRow]:
    """Group deduction records by (deductee, section, category) into 26Q rows."""
    buckets: dict[tuple, dict] = {}
    for r in recs:
        key = (r["supplier_id"], r["section"], r["category"])
        b = buckets.setdefault(
            key,
            {
                "supplier_id": r["supplier_id"],
                "deductee_name": r["deductee_name"], "pan": r["pan"], "gstin": r["gstin"],
                "section": r["section"], "category": r["category"], "rate": r["rate"],
                "base": ZERO, "tds": ZERO, "docs": [],
            },
        )
        b["base"] += r["base"]
        b["tds"] += r["tds"]
        b["docs"].append(
            Tds26qDoc(voucher=r["voucher"], date=r["date"], base_amount=_q(r["base"]),
                      tds=_q(r["tds"]), rate=_q(r["rate"]))
        )
    rows = [
        Tds26qRow(
            supplier_id=b["supplier_id"],
            deductee_name=b["deductee_name"], pan=b["pan"], gstin=b["gstin"],
            section=b["section"], category=b["category"], rate=_q(b["rate"]),
            total_base=_q(b["base"]), total_tds=_q(b["tds"]),
            doc_count=len(b["docs"]), documents=b["docs"],
        )
        for b in buckets.values()
    ]
    rows.sort(key=lambda r: (r.section or "zzz", r.deductee_name or ""))
    return rows


async def tds_26q(db: AsyncSession, company: Company, *, from_date: date, to_date: date) -> Tds26qReport:
    """Form 26Q — deductee × section TDS summary for the period, from the purchase register."""
    recs = await _deductions(db, company, from_date, to_date)
    rows = _group(recs)
    return Tds26qReport(
        deductor_name=company.company_name,
        deductor_gstin=company.tax_id,
        deductor_tan=company.tan,
        from_date=from_date,
        to_date=to_date,
        rows=rows,
        summary=Tds26qSummary(
            deductee_count=len({r["supplier_id"] for r in recs}),
            document_count=len(recs),
            total_base=_q(sum((r["base"] for r in recs), ZERO)),
            total_tds=_q(sum((r["tds"] for r in recs), ZERO)),
        ),
    )


async def form_16a(
    db: AsyncSession, company: Company, *, supplier_id: uuid.UUID, from_date: date, to_date: date
) -> Form16A:
    """Form 16A — the TDS certificate for one deductee (supplier) for the period."""
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None or supplier.company_id != company.id:
        raise NotFoundError("Supplier not found")
    recs = await _deductions(db, company, from_date, to_date, supplier_id=supplier_id)
    sections = _group(recs)
    return Form16A(
        deductor_name=company.company_name,
        deductor_gstin=company.tax_id,
        deductor_tan=company.tan,
        deductee_name=supplier.supplier_name,
        deductee_pan=pan_from_gstin(supplier.tax_id),
        deductee_gstin=supplier.tax_id,
        from_date=from_date,
        to_date=to_date,
        sections=sections,
        total_base=_q(sum((r["base"] for r in recs), ZERO)),
        total_tds=_q(sum((r["tds"] for r in recs), ZERO)),
    )
