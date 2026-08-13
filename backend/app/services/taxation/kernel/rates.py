"""Flat and slab rate engines — one schedule structure (flat = single open band)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.services.taxation.kernel.money import ZERO, money, percent_of, q
from app.services.taxation.kernel.types import RateBand


def tax_on_bands(income: Decimal | int | str, bands: Sequence[RateBand]) -> Decimal:
    """Progressive tax on ``[lower, upper)`` bands; open top when ``upper is None``.

    A flat rate is expressed as one band ``(0, None, rate)``.
    """
    taxable = money(income)
    if taxable <= ZERO or not bands:
        return ZERO
    ordered = sorted(bands, key=lambda b: b.lower)
    total = ZERO
    for band in ordered:
        lo = money(band.lower)
        hi = money(band.upper) if band.upper is not None else None
        if taxable <= lo:
            break
        width = (min(taxable, hi) - lo) if hi is not None else (taxable - lo)
        if width > ZERO:
            total += percent_of(width, band.rate_percent)
            total += q(band.fixed_amount)
    return q(total)


def flat_tax(income: Decimal | int | str, rate_percent: Decimal | int | str) -> Decimal:
    """Convenience: single-band flat schedule."""
    return tax_on_bands(
        income,
        (RateBand(lower=ZERO, upper=None, rate_percent=money(rate_percent)),),
    )
