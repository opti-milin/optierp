"""Rule-based path: special-rate income + ordinary residual."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.income_tax_engine.context import SpecialIncomeInput, ZERO, q
from app.services.income_tax_engine.strategies.flat_rate import flat_tax
from app.services.income_tax_engine.strategies.slab_based import tax_from_slabs
from app.services.income_tax_engine.context import ResolvedTaxRules


def special_income_tax(
    lines: list[SpecialIncomeInput],
) -> tuple[Decimal, list[dict[str, Any]], Decimal]:
    """Return (special_tax_total, line_details, special_income_total)."""
    total_tax = ZERO
    total_income = ZERO
    details: list[dict[str, Any]] = []
    for ln in lines:
        amt = q(ln.amount)
        if amt <= ZERO:
            continue
        tax = q(amt * (q(ln.rate_percent) / Decimal("100")))
        total_tax += tax
        total_income += amt
        details.append(
            {
                "income_category_code": ln.income_category_code,
                "amount": str(amt),
                "rate_percent": str(q(ln.rate_percent)),
                "tax_amount": str(tax),
                "description": ln.description,
                "special_rate_id": str(ln.special_rate_id) if ln.special_rate_id else None,
            }
        )
    return q(total_tax), details, q(total_income)


def ordinary_tax(
    ordinary_income: Decimal,
    rules: ResolvedTaxRules,
    *,
    method: str | None = None,
) -> tuple[Decimal, list[dict]]:
    """Tax ordinary residual via FlatRate or SlabBased."""
    method = method or rules.ordinary_method
    base = max(q(ordinary_income), ZERO)
    if method == "SlabBased":
        return tax_from_slabs(base, rules.slabs)
    return flat_tax(base, rules.flat_tax_rate), []
