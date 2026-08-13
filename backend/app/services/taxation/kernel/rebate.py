"""s.87A rebate with character exclusions and optional new-regime marginal relief."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.services.taxation.kernel.marginal_relief import rebate_marginal_relief
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.kernel.types import RebateSpec


def apply_rebate(
    *,
    total_income: Decimal,
    tax_before_rebate: Decimal,
    rebate_eligible_tax: Decimal,
    spec: RebateSpec | None,
) -> tuple[Decimal, Decimal]:
    """Return ``(rebate_amount, tax_after_rebate)``.

    Rebate applies only to ``rebate_eligible_tax`` (characters not in
    ``excluded_characters``). Special-rate tax is never reduced by 87A.
    """
    if spec is None:
        return ZERO, q(tax_before_rebate)

    income = money(total_income)
    eligible_tax = q(rebate_eligible_tax)
    total_tax = q(tax_before_rebate)

    if eligible_tax <= ZERO:
        return ZERO, total_tax

    if income <= money(spec.max_taxable_income):
        rebate = min(eligible_tax, q(spec.max_rebate_amount))
    elif spec.marginal_relief_enabled:
        rebate = rebate_marginal_relief(
            income=income,
            tax_before_rebate=eligible_tax,
            max_taxable_income=spec.max_taxable_income,
            max_rebate_amount=spec.max_rebate_amount,
        )
    else:
        rebate = ZERO

    rebate = min(q(rebate), eligible_tax)
    # Only reduce the eligible portion; special tax rides through.
    after = q(total_tax - rebate)
    return rebate, after


def eligible_tax_from_components(
    component_taxes: Sequence[tuple[str, Decimal]],
    *,
    excluded: frozenset[str],
    rebate_flags: dict[str, bool] | None = None,
) -> Decimal:
    total = ZERO
    for code, tax in component_taxes:
        if code in excluded:
            continue
        if rebate_flags is not None and not rebate_flags.get(code, True):
            continue
        total += q(tax)
    return q(total)
