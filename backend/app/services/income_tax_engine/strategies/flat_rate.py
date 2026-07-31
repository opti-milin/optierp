"""Flat-rate tax strategy."""

from __future__ import annotations

from decimal import Decimal

from app.services.income_tax_engine.context import ZERO, q


def flat_tax(taxable: Decimal, rate_percent: Decimal) -> Decimal:
    taxable = max(q(taxable), ZERO)
    return q(taxable * (q(rate_percent) / Decimal("100")))
