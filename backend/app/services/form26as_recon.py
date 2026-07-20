"""Form 26AS / AIS credit reconciliation — Phase 5 (file-upload, lean).

Match TDS credits claimed on an Income Tax Computation against an uploaded
portal 26AS-style JSON. Buckets: Matched / Mismatch / Only in Books / Only in 26AS.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.base import DOCSTATUS_SUBMITTED
from app.schemas.compliance import (
    Form26asReconReport,
    Form26asReconRow,
    Form26asReconSummary,
)
from app.services.income_tax_computation import get_computation

ZERO = Decimal("0")
Q2 = Decimal("0.01")


def _q(x: Decimal | int | float | str | None) -> Decimal:
    try:
        return Decimal(str(x or 0)).quantize(Q2)
    except Exception:
        return ZERO


def _portal_credits(payload: dict) -> list[dict]:
    """Accept a few common shapes of 26AS / AIS TDS credit extracts."""
    rows = (
        payload.get("tds")
        or payload.get("credits")
        or payload.get("deductors")
        or payload.get("data")
        or []
    )
    if isinstance(payload.get("form26as"), dict):
        rows = payload["form26as"].get("tds") or payload["form26as"].get("credits") or rows
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "deductor_name": r.get("deductor_name") or r.get("name") or r.get("deductor"),
                "deductor_tan": r.get("deductor_tan") or r.get("tan"),
                "section": r.get("section") or r.get("nature"),
                "amount": _q(r.get("tds") or r.get("amount") or r.get("credit")),
            }
        )
    return out


async def reconcile_26as(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    computation_id: uuid.UUID,
    form26as: dict,
) -> Form26asReconReport:
    doc = await get_computation(db, computation_id, company_id)
    if doc.docstatus not in (DOCSTATUS_SUBMITTED, 0):
        raise ValidationError("Computation not found or cancelled", field="computation_id")

    portal = _portal_credits(form26as)
    portal_total = _q(sum((r["amount"] for r in portal), ZERO))
    books_credit = _q(doc.tds_credit)
    diff = _q(portal_total - books_credit)

    if portal_total == ZERO and books_credit == ZERO:
        status = "Matched"
    elif abs(diff) <= Decimal("1.00"):
        status = "Matched"
    elif portal_total == ZERO:
        status = "Only in Books"
    elif books_credit == ZERO:
        status = "Only in 26AS"
    else:
        status = "Mismatch"

    rows = [
        Form26asReconRow(
            source="Books",
            description=f"TDS credit on {doc.name}",
            amount=books_credit,
            status="Only in Books" if status == "Only in Books" else status,
        ),
        Form26asReconRow(
            source="26AS",
            description=f"{len(portal)} portal credit line(s)",
            amount=portal_total,
            status="Only in 26AS" if status == "Only in 26AS" else status,
        ),
    ]
    for p in portal[:50]:
        rows.append(
            Form26asReconRow(
                source="26AS line",
                description=f"{p.get('deductor_name') or '—'} / {p.get('section') or '—'}",
                amount=p["amount"],
                status="portal",
            )
        )

    return Form26asReconReport(
        computation_id=doc.id,
        assessment_year=doc.assessment_year,
        summary=Form26asReconSummary(
            books_tds_credit=books_credit,
            portal_tds_credit=portal_total,
            difference=diff,
            status=status,
            portal_line_count=len(portal),
        ),
        rows=rows,
    )
