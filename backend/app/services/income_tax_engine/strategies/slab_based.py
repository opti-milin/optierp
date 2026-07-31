"""Progressive slab tax."""

from __future__ import annotations

from decimal import Decimal

from app.services.income_tax_engine.context import SlabBand, ZERO, q


def tax_from_slabs(taxable: Decimal, slabs: list[SlabBand]) -> tuple[Decimal, list[dict]]:
    """Progressive tax: each band is [from_amount, to_amount) at rate %; to=None = open."""
    taxable = q(taxable)
    steps: list[dict] = []
    if taxable <= ZERO or not slabs:
        return ZERO, steps
    tax = ZERO
    for band in slabs:
        lo = q(band.from_amount)
        if taxable <= lo:
            continue
        hi = q(band.to_amount) if band.to_amount is not None else taxable
        band_top = min(taxable, hi)
        width = band_top - lo
        if width > ZERO:
            part = q(width * (q(band.rate_percent) / Decimal("100")))
            tax += part
            steps.append(
                {
                    "from": str(lo),
                    "to": str(band.to_amount) if band.to_amount is not None else None,
                    "rate_percent": str(q(band.rate_percent)),
                    "taxable_in_band": str(q(width)),
                    "tax": str(part),
                }
            )
    return q(tax), steps
