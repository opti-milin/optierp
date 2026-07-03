"""HSN/SAC lookup — "type a product name (or code) → get HSN + GST rate".

Backs the auto-fetch on the Item form. Ranking strategy, cheapest-first:

1. **Alias boost** — a curated trade-name → HSN map (``fridge`` → 8418,
   ``mobile`` → 8517) surfaces the right code even when the official description
   uses different words (*"refrigerating equipment"*, *"cellular networks"*).
2. **Code prefix** — if the query is only digits (e.g. ``8418``), match
   ``hsn_code LIKE '8418%'``.
3. **Full-text (AND)** — ranked ``to_tsvector(description) @@
   websearch_to_tsquery(q)`` (uses the GIN index from migration 0060; ``english``
   config stems, so *refrigerators* matches *refrigerator*). High precision.
4. **Full-text (OR-prefix)** — for multi-word queries the strict AND above often
   returns nothing (*"cotton shirt"*, *"air conditioner"*); a ``word:* | word:*``
   query ranked by ``ts_rank`` recovers them, best-overlap first.
5. **Substring fallback** — ``ILIKE %q%`` on the description, last resort.

Results are de-duplicated on (code, description) preserving the above priority.
"""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hsn_aliases import lookup_alias_codes
from app.models.core import HsnCode

MIN_QUERY_LEN = 2
MAX_LIMIT = 50

# Query is a pure code fragment: only digits, dots or spaces (e.g. "8418", "8418.90").
_CODE_ONLY = re.compile(r"^[\d.\s]+$")
_NON_DIGIT = re.compile(r"\D")
_WORD = re.compile(r"[A-Za-z0-9]+")


async def search_hsn(db: AsyncSession, query: str, limit: int = 15) -> list[HsnCode]:
    """Return HSN rows matching ``query``, best match first (empty if too short)."""
    q = (query or "").strip()
    if len(q) < MIN_QUERY_LEN:
        return []
    limit = max(1, min(limit, MAX_LIMIT))

    results: list[HsnCode] = []
    seen: set[tuple[str, str]] = set()

    def add(rows: list[HsnCode]) -> None:
        for r in rows:
            key = (r.hsn_code, r.description)
            if key not in seen:
                seen.add(key)
                results.append(r)

    # 1) Code-prefix lookup for purely numeric queries — short-circuits.
    if _CODE_ONLY.match(q):
        digits = _NON_DIGIT.sub("", q)
        if len(digits) >= 2:
            add(await _by_code_prefix(db, digits, limit))
        return results[:limit]

    tsv = func.to_tsvector("english", HsnCode.description)

    # 2) Curated trade-name aliases (fridge → 8418) — surfaced first.
    alias_codes = lookup_alias_codes(q)
    if alias_codes:
        rows = (
            await db.execute(
                select(HsnCode)
                .where(HsnCode.hsn_code.in_(alias_codes))
                .order_by(HsnCode.hsn_code, HsnCode.description)
            )
        ).scalars().all()
        add(rows)

    # 3) Full-text AND (precise).
    if len(results) < limit:
        tsq = func.websearch_to_tsquery("english", q)
        add(await _by_fts(db, tsv, tsq, limit))

    # 4) Full-text OR-prefix (recall) — recovers multi-word queries.
    if len(results) < limit:
        words = [w for w in _WORD.findall(q.lower()) if len(w) >= 2]
        if words:
            or_expr = " | ".join(f"{w}:*" for w in words)
            add(await _by_fts(db, tsv, func.to_tsquery("english", or_expr), limit))

    # 5) Substring fallback for partial words (e.g. "refriger").
    if len(results) < limit:
        rows = (
            await db.execute(
                select(HsnCode)
                .where(HsnCode.description.ilike(f"%{q}%"))
                .order_by(HsnCode.hsn_code)
                .limit(limit)
            )
        ).scalars().all()
        add(rows)

    return results[:limit]


async def _by_code_prefix(db: AsyncSession, digits: str, limit: int) -> list[HsnCode]:
    return list(
        (
            await db.execute(
                select(HsnCode)
                .where(HsnCode.hsn_code.like(f"{digits}%"))
                .order_by(HsnCode.hsn_code)
                .limit(limit)
            )
        ).scalars().all()
    )


async def _by_fts(db: AsyncSession, tsv, tsq, limit: int) -> list[HsnCode]:
    rank = func.ts_rank(tsv, tsq)
    return list(
        (
            await db.execute(
                select(HsnCode)
                .where(tsv.op("@@")(tsq))
                .order_by(rank.desc(), HsnCode.hsn_code)
                .limit(limit)
            )
        ).scalars().all()
    )
