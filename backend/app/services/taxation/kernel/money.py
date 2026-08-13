"""Money helpers — Decimal only, ROUND_HALF_UP, statutory s.288A / s.288B rounding.

Pure: no DB, no I/O. Rejects ``float`` to avoid binary contamination.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import SupportsInt

ZERO = Decimal("0")
ONE = Decimal("1")
TEN = Decimal("10")
PAISE = Decimal("0.01")
HUNDRED = Decimal("100")

MoneyLike = Decimal | int | str | SupportsInt | None


def money(value: MoneyLike) -> Decimal:
    """Coerce to Decimal. Floats are rejected."""
    if value is None:
        return ZERO
    if isinstance(value, float):
        raise TypeError("float is not allowed — pass str or Decimal")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q(value: MoneyLike) -> Decimal:
    """Quantize to 2 decimal places with ROUND_HALF_UP (paisa)."""
    return money(value).quantize(PAISE, rounding=ROUND_HALF_UP)


def round_to_nearest_ten(value: MoneyLike) -> Decimal:
    """s.288A (total income) / s.288B (tax payable) — round to nearest ₹10."""
    amount = money(value)
    # Half-up toward nearest multiple of 10: divide by 10, half-up to int, ×10.
    return (amount / TEN).quantize(ONE, rounding=ROUND_HALF_UP) * TEN


def percent_of(base: MoneyLike, rate_percent: MoneyLike) -> Decimal:
    return q(money(base) * money(rate_percent) / HUNDRED)


def rupees(value: MoneyLike) -> str:
    """Money as a Chartered Accountant reads it: ``12135000.00`` as ``₹1,21,35,000``.

    For summaries and messages that are read rather than calculated with, so paise are
    dropped unless they are non-zero. Grouping is the Indian lakh/crore convention.
    """
    amount = q(value)
    sign = "-" if amount < ZERO else ""
    whole, _, paise = f"{abs(amount):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    if head:
        pairs = [head[max(i - 2, 0) : i] for i in range(len(head), 0, -2)][::-1]
        tail = ",".join([*pairs, tail])
    return f"{sign}₹{tail}" + (f".{paise}" if paise != "00" else "")
