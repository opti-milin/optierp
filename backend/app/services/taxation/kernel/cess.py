"""Health and Education Cess — applied on tax + surcharge."""

from __future__ import annotations

from decimal import Decimal

from app.services.taxation.kernel.money import ZERO, money, percent_of, q


def apply_cess(
    *,
    tax_after_rebate: Decimal,
    surcharge: Decimal,
    rate_percent: Decimal | int | str = "4",
) -> Decimal:
    base = q(money(tax_after_rebate) + money(surcharge))
    if base <= ZERO:
        return ZERO
    return percent_of(base, rate_percent)
