"""Persisted Form 26AS / AIS reconciliation against tax_credit_entries."""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser
from app.models.tax_credits import Tax26asReconRun, TaxCreditEntry
from app.schemas.taxation import Tax26asReconOut, Tax26asReconRowOut
from app.services.taxation import credits as credit_service
from app.services.taxation.kernel.money import ZERO, money, q


def _portal_rows(payload: dict) -> list[dict]:
    rows = (
        payload.get("tds")
        or payload.get("credits")
        or payload.get("deductors")
        or payload.get("data")
        or []
    )
    if isinstance(payload.get("form26as"), dict):
        nested = payload["form26as"]
        rows = nested.get("tds") or nested.get("credits") or rows
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "deductor_name": r.get("deductor_name") or r.get("name") or r.get("deductor"),
                "deductor_tan": (r.get("deductor_tan") or r.get("tan") or "").strip().upper() or None,
                "section": r.get("section") or r.get("nature") or r.get("section_code"),
                "amount": q(money(r.get("tds") or r.get("amount") or r.get("credit") or 0)),
            }
        )
    return out


def _payload_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _match_key(tan: str | None, section: str | None, amount: Decimal) -> str:
    return f"{(tan or '').upper()}|{(section or '').upper()}|{format(q(amount), 'f')}"


async def reconcile_26as(
    db: AsyncSession,
    *,
    user: CurrentUser,
    ay_code: str,
    form26as: dict,
    computation_id: uuid.UUID | None = None,
) -> Tax26asReconRun:
    """Match portal rows to book credits; persist a recon run and stamp credit statuses."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    if not isinstance(form26as, dict):
        raise ValidationError("form26as must be an object", field="form26as")

    book_rows = await credit_service.list_credits(
        db, user.company_id, ay_code=ay_code, computation_id=computation_id
    )
    # Prefer TDS-like kinds for 26AS
    tds_kinds = {"TDS", "SalaryTDS", "TCS"}
    books = [r for r in book_rows if r.credit_kind in tds_kinds] or list(book_rows)
    portal = _portal_rows(form26as)

    books_by_key: dict[str, list[TaxCreditEntry]] = {}
    for b in books:
        key = _match_key(b.deductor_tan, b.section_code, q(money(b.amount_credited)))
        books_by_key.setdefault(key, []).append(b)

    portal_used: set[int] = set()
    matched = 0
    mismatch = 0
    only_books = 0
    only_portal = 0
    detail_rows: list[dict] = []

    # Reset stamps for this AY scope (status only — amounts untouched)
    for b in books:
        b.reconciliation_status = "OnlyInBooks"
        b.portal_amount = None

    for i, p in enumerate(portal):
        key = _match_key(p["deductor_tan"], p["section"], p["amount"])
        candidates = books_by_key.get(key) or []
        if candidates:
            b = candidates.pop(0)
            portal_used.add(i)
            b.reconciliation_status = "Matched"
            b.portal_amount = p["amount"]
            matched += 1
            detail_rows.append(
                {
                    "bucket": "Matched",
                    "deductor_tan": b.deductor_tan,
                    "section_code": b.section_code,
                    "books_amount": format(b.amount_credited, "f"),
                    "portal_amount": format(p["amount"], "f"),
                    "credit_id": str(b.id),
                }
            )
            continue
        # Same TAN+section, different amount → mismatch if any book with that pair
        soft = [
            b
            for b in books
            if (b.deductor_tan or "").upper() == (p["deductor_tan"] or "")
            and (b.section_code or "").upper() == (p["section"] or "").upper()
            and b.reconciliation_status == "OnlyInBooks"
        ]
        if soft:
            b = soft[0]
            portal_used.add(i)
            b.reconciliation_status = "Mismatch"
            b.portal_amount = p["amount"]
            mismatch += 1
            detail_rows.append(
                {
                    "bucket": "Mismatch",
                    "deductor_tan": b.deductor_tan,
                    "section_code": b.section_code,
                    "books_amount": format(b.amount_credited, "f"),
                    "portal_amount": format(p["amount"], "f"),
                    "credit_id": str(b.id),
                }
            )
        else:
            only_portal += 1
            detail_rows.append(
                {
                    "bucket": "OnlyIn26AS",
                    "deductor_tan": p["deductor_tan"],
                    "section_code": p["section"],
                    "books_amount": "0",
                    "portal_amount": format(p["amount"], "f"),
                    "credit_id": None,
                }
            )

    for b in books:
        if b.reconciliation_status == "OnlyInBooks":
            only_books += 1
            detail_rows.append(
                {
                    "bucket": "OnlyInBooks",
                    "deductor_tan": b.deductor_tan,
                    "section_code": b.section_code,
                    "books_amount": format(b.amount_credited, "f"),
                    "portal_amount": "0",
                    "credit_id": str(b.id),
                }
            )

    books_total = q(sum((money(b.amount_credited) for b in books), ZERO))
    portal_total = q(sum((p["amount"] for p in portal), ZERO))
    difference = q(portal_total - books_total)

    if matched and not mismatch and not only_books and not only_portal:
        status = "Matched"
    elif books_total == ZERO and portal_total == ZERO:
        status = "Matched"
    else:
        status = "Mismatch"

    run = Tax26asReconRun(
        company_id=user.company_id,
        computation_id=computation_id,
        ay_code=ay_code,
        status=status,
        books_total=books_total,
        portal_total=portal_total,
        difference=difference,
        payload_hash=_payload_hash(form26as),
        summary={
            "matched": matched,
            "mismatch": mismatch,
            "only_in_books": only_books,
            "only_in_26as": only_portal,
            "rows": detail_rows,
        },
        payload=form26as,
        user_id=user.id,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


def recon_out(run: Tax26asReconRun) -> Tax26asReconOut:
    rows_raw = (run.summary or {}).get("rows") or []
    rows = [Tax26asReconRowOut.model_validate(r) for r in rows_raw]
    return Tax26asReconOut(
        id=run.id,
        ay_code=run.ay_code,
        computation_id=run.computation_id,
        status=run.status,
        books_total=run.books_total,
        portal_total=run.portal_total,
        difference=run.difference,
        payload_hash=run.payload_hash,
        matched=int((run.summary or {}).get("matched") or 0),
        mismatch=int((run.summary or {}).get("mismatch") or 0),
        only_in_books=int((run.summary or {}).get("only_in_books") or 0),
        only_in_26as=int((run.summary or {}).get("only_in_26as") or 0),
        rows=rows,
        creation=run.creation,
    )
