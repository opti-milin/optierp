"""Health & Education Cess from master data."""

from __future__ import annotations

from decimal import Decimal

from app.services.income_tax_engine.context import ZERO, q


def apply_cess(
    *,
    tax_after_rebate: Decimal,
    surcharge_after_relief: Decimal,
    cess_rate: Decimal,
    cess_base: str = "TaxPlusSurcharge",
    special_tax: Decimal = ZERO,
) -> Decimal:
    _ = cess_base  # only TaxPlusSurcharge supported; reserved for future bases
    # Cess applies on income-tax (ordinary + special-rate) + surcharge.
    base = q(tax_after_rebate) + q(special_tax) + q(surcharge_after_relief)
    return q(base * (q(cess_rate) / Decimal("100"))) if base > ZERO else ZERO
