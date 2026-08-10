"""Character-aware surcharge with per-character rate caps (e.g. 15% on 112A/111A)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.services.taxation.kernel.marginal_relief import surcharge_marginal_relief
from app.services.taxation.kernel.money import ZERO, money, percent_of, q
from app.services.taxation.kernel.types import SurchargeBand, SurchargeSpec


@dataclass(frozen=True, slots=True)
class TaxedSlice:
    character_code: str
    tax: Decimal
    surcharge_cap_percent: Decimal | None = None


def pick_surcharge_rate(income: Decimal, bands: Sequence[SurchargeBand]) -> Decimal:
    """Rate for the bracket where income *exceeds* ``lower`` and is ≤ ``upper``.

    Matches the Act's wording ("where total income exceeds …"): at exactly the
    threshold the prior (usually nil) rate still applies.
    """
    income = money(income)
    if income < ZERO or not bands:
        return ZERO
    matched = ZERO
    for band in sorted(bands, key=lambda b: b.lower):
        lo = money(band.lower)
        hi = money(band.upper) if band.upper is not None else None
        exceeds_lower = income > lo or (lo == ZERO and income >= ZERO)
        within_upper = hi is None or income <= hi
        if exceeds_lower and within_upper:
            matched = money(band.rate_percent)
    return matched


def _effective_rate(
    character_code: str,
    schedule_rate: Decimal,
    slice_cap: Decimal | None,
    spec: SurchargeSpec,
) -> Decimal:
    if character_code in spec.capped_characters or slice_cap is not None:
        cap = money(slice_cap if slice_cap is not None else spec.default_cap_percent)
        return min(schedule_rate, cap)
    return schedule_rate


def compute_surcharge(
    *,
    total_income: Decimal,
    tax_after_rebate: Decimal,
    slices: Sequence[TaxedSlice],
    spec: SurchargeSpec,
    tax_at_income: Callable[[Decimal], Decimal],
    cess_rate_percent: Decimal | int | str = ZERO,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return ``(surcharge_after_relief, surcharge_before_relief, marginal_relief)``."""
    income = money(total_income)
    schedule_rate = pick_surcharge_rate(income, spec.bands)
    if schedule_rate <= ZERO or tax_after_rebate <= ZERO:
        return ZERO, ZERO, ZERO

    if slices:
        before = ZERO
        for sl in slices:
            rate = _effective_rate(sl.character_code, schedule_rate, sl.surcharge_cap_percent, spec)
            before += percent_of(sl.tax, rate)
        before = q(before)
    else:
        before = percent_of(tax_after_rebate, schedule_rate)

    if not spec.marginal_relief or before <= ZERO:
        return before, before, ZERO

    threshold = _threshold_for_rate(schedule_rate, spec.bands)
    if threshold is None or income <= threshold:
        return before, before, ZERO

    tax_at_threshold = q(tax_at_income(threshold))
    after, relief = surcharge_marginal_relief(
        income=income,
        tax_after_rebate=tax_after_rebate,
        surcharge_before=before,
        threshold=threshold,
        tax_at_threshold=tax_at_threshold,
        cess_rate_percent=cess_rate_percent,
    )
    return after, before, relief


def _threshold_for_rate(rate: Decimal, bands: Sequence[SurchargeBand]) -> Decimal | None:
    for band in sorted(bands, key=lambda b: b.lower):
        if money(band.rate_percent) == money(rate) and money(rate) > ZERO:
            return money(band.lower)
    return None
