"""Surcharge brackets + optional marginal relief."""

from __future__ import annotations

from decimal import Decimal

from app.services.income_tax_engine.context import SurchargeBand, ZERO, q


def surcharge_rate_for_income(
    taxable_income: Decimal,
    brackets: list[SurchargeBand],
) -> Decimal:
    """Pick the surcharge % whose income band contains taxable_income."""
    income = q(taxable_income)
    if not brackets:
        return ZERO
    # Prefer most specific match: highest income_from that is <= income
    matched = ZERO
    for b in sorted(brackets, key=lambda x: q(x.income_from)):
        lo = q(b.income_from)
        hi = q(b.income_to) if b.income_to is not None else None
        if income < lo:
            continue
        if hi is not None and income >= hi:
            continue
        matched = q(b.rate_percent)
    return matched


def apply_surcharge(
    *,
    tax_after_rebate: Decimal,
    taxable_income: Decimal,
    brackets: list[SurchargeBand],
    marginal_relief_enabled: bool,
    special_tax: Decimal = ZERO,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (surcharge_after_relief, surcharge_before_relief, marginal_relief).

    Surcharge is computed on ordinary tax (after rebate) plus special-rate tax.
    Marginal relief (lean): if crossing a bracket threshold would make
    (tax + surcharge) exceed (tax_at_threshold + income_excess), reduce surcharge
    so total liability equals tax_at_threshold + excess income.
    """
    tax = q(tax_after_rebate) + q(special_tax)
    income = q(taxable_income)
    rate = surcharge_rate_for_income(income, brackets)
    before = q(tax * (rate / Decimal("100")))
    if before <= ZERO or not marginal_relief_enabled or not brackets:
        return before, before, ZERO

    # Find the lower edge of the active bracket (threshold just crossed).
    threshold = ZERO
    for b in sorted(brackets, key=lambda x: q(x.income_from)):
        lo = q(b.income_from)
        if income >= lo and lo > ZERO:
            threshold = lo

    if threshold <= ZERO or income <= threshold:
        return before, before, ZERO

    # Tax as if income were exactly at threshold (same tax rate path approximated
    # by scaling: use tax * threshold/income when progressive detail unavailable).
    # Lean formula used in practice: liability without surcharge at threshold
    # + (income - threshold) should cap (tax + surcharge).
    excess = income - threshold
    # Approximate tax at threshold by proportion of current tax (conservative lean).
    tax_at_threshold = q(tax * (threshold / income)) if income > ZERO else ZERO
    cap = q(tax_at_threshold + excess)
    with_surcharge = q(tax + before)
    if with_surcharge <= cap:
        return before, before, ZERO
    relief = q(with_surcharge - cap)
    after = max(q(before - relief), ZERO)
    relief = q(before - after)
    return after, before, relief
