"""Rebate rules (e.g. Section 87A) — fully data-driven."""

from __future__ import annotations

from decimal import Decimal

from app.services.income_tax_engine.context import ResolvedTaxRules, ZERO, q


def apply_rebate(
    *,
    taxable_income: Decimal,
    tax_before_rebate: Decimal,
    rules: ResolvedTaxRules,
) -> tuple[Decimal, Decimal]:
    """Return (rebate_amount, tax_after_rebate)."""
    if not rules.rebate_section:
        return ZERO, q(tax_before_rebate)
    if q(taxable_income) > q(rules.rebate_max_taxable):
        return ZERO, q(tax_before_rebate)
    rebate = min(q(tax_before_rebate), q(rules.rebate_max_amount))
    return rebate, q(tax_before_rebate - rebate)
