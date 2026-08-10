"""Statutory marginal relief — re-run rate schedule at the threshold (not proportional)."""

from __future__ import annotations

from decimal import Decimal

from app.services.taxation.kernel.money import HUNDRED, ONE, ZERO, money, q


def surcharge_marginal_relief(
    *,
    income: Decimal,
    tax_after_rebate: Decimal,
    surcharge_before: Decimal,
    threshold: Decimal,
    tax_at_threshold: Decimal,
    cess_rate_percent: Decimal | int | str = ZERO,
) -> tuple[Decimal, Decimal]:
    """Return ``(surcharge_after_relief, relief_amount)``.

    Cap (cess-inclusive): ``tax + surcharge + cess`` must not exceed
    ``tax_at_threshold + cess_at_threshold + (income - threshold)``, where
    ``tax_at_threshold`` is obtained by re-running the rate schedule at the
    threshold (surcharge at the threshold is typically nil).
    """
    from app.services.taxation.kernel.cess import apply_cess

    income = money(income)
    tax = q(tax_after_rebate)
    surcharge = q(surcharge_before)
    threshold = money(threshold)
    tax_at = q(tax_at_threshold)
    cess_rate = money(cess_rate_percent)

    if income <= threshold or surcharge <= ZERO:
        return surcharge, ZERO

    excess = q(income - threshold)
    cess_at = apply_cess(tax_after_rebate=tax_at, surcharge=ZERO, rate_percent=cess_rate)
    cap = q(tax_at + cess_at + excess)

    cess_before = apply_cess(tax_after_rebate=tax, surcharge=surcharge, rate_percent=cess_rate)
    liability = q(tax + surcharge + cess_before)
    if liability <= cap:
        return surcharge, ZERO

    # Reduce surcharge until tax + surcharge + cess(tax, surcharge) <= cap.
    # cess = (tax + surcharge) * rate/100, so
    # (tax + sur) * (1 + rate/100) <= cap
    # sur <= cap / (1 + rate/100) - tax
    if cess_rate > ZERO:
        factor = ONE + (cess_rate / HUNDRED)
        max_tax_plus_sur = q(cap / factor)
        max_sur = max(q(max_tax_plus_sur - tax), ZERO)
    else:
        max_sur = max(q(cap - tax), ZERO)

    after = min(surcharge, max_sur)
    return after, q(surcharge - after)


def rebate_marginal_relief(
    *,
    income: Decimal,
    tax_before_rebate: Decimal,
    max_taxable_income: Decimal,
    max_rebate_amount: Decimal,
) -> Decimal:
    """New-regime s.87A style: when income exceeds the rebate ceiling, tax payable
    must not exceed ``(income - ceiling)``.
    """
    income = money(income)
    tax = q(tax_before_rebate)
    ceiling = money(max_taxable_income)
    max_rebate = q(max_rebate_amount)

    if tax <= ZERO:
        return ZERO
    if income <= ceiling:
        return min(tax, max_rebate)

    excess = q(income - ceiling)
    if tax <= excess:
        return ZERO
    # Need tax - rebate <= excess  ⇒  rebate >= tax - excess
    needed = q(tax - excess)
    return min(needed, max_rebate, tax)
