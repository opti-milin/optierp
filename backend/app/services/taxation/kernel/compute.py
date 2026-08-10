"""Pure tax kernel orchestrator — no DB, no I/O."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.services.taxation.kernel.cess import apply_cess
from app.services.taxation.kernel.money import ZERO, money, q, round_to_nearest_ten
from app.services.taxation.kernel.rates import tax_on_bands
from app.services.taxation.kernel.rebate import apply_rebate, eligible_tax_from_components
from app.services.taxation.kernel.surcharge import TaxedSlice, compute_surcharge
from app.services.taxation.kernel.types import (
    ComponentTax,
    IncomeComponent,
    KernelInput,
    KernelResult,
    RateBand,
)


def _scale_components(
    components: tuple[IncomeComponent, ...],
    *,
    from_total: Decimal,
    to_total: Decimal,
) -> tuple[IncomeComponent, ...]:
    """Proportionally scale component amounts when s.288A changes total income."""
    if from_total == to_total or from_total <= ZERO:
        return components
    factor = to_total / from_total
    scaled: list[IncomeComponent] = []
    for c in components:
        scaled.append(
            IncomeComponent(
                character_code=c.character_code,
                amount=q(money(c.amount) * factor),
                bands=c.bands,
                surcharge_cap_percent=c.surcharge_cap_percent,
                rebate_eligible=c.rebate_eligible,
            )
        )
    return tuple(scaled)


def _tax_components(components: tuple[IncomeComponent, ...]) -> tuple[ComponentTax, ...]:
    out: list[ComponentTax] = []
    for c in components:
        amt = q(c.amount)
        tax = tax_on_bands(amt, c.bands) if amt > ZERO else ZERO
        out.append(ComponentTax(character_code=c.character_code, amount=amt, tax=tax))
    return tuple(out)


def _ordinary_tax_fn(
    components: tuple[IncomeComponent, ...],
) -> Callable[[Decimal], Decimal]:
    """Build a function that recomputes total tax (pre-rebate) at a given total income.

    Used for surcharge marginal relief: re-run schedules at the threshold by scaling
    component amounts to sum to the threshold (character mix held constant).
    """

    raw_total = q(sum((money(c.amount) for c in components), ZERO))

    def tax_at(income: Decimal) -> Decimal:
        income = money(income)
        if income <= ZERO:
            return ZERO
        scaled = _scale_components(components, from_total=raw_total, to_total=income)
        return q(sum((ct.tax for ct in _tax_components(scaled)), ZERO))

    return tax_at


def compute(inp: KernelInput) -> KernelResult:
    """Run the pure kernel: 288A → character tax → rebate → surcharge → cess → 288B."""
    raw_total = q(sum((money(c.amount) for c in inp.components), ZERO))
    total_income = round_to_nearest_ten(raw_total) if inp.apply_288a else raw_total
    components = _scale_components(inp.components, from_total=raw_total, to_total=total_income)

    component_taxes = _tax_components(components)
    tax_before_rebate = q(sum((ct.tax for ct in component_taxes), ZERO))

    excluded = inp.rebate.excluded_characters if inp.rebate else frozenset()
    rebate_flags = {c.character_code: c.rebate_eligible for c in components}
    eligible = eligible_tax_from_components(
        [(ct.character_code, ct.tax) for ct in component_taxes],
        excluded=excluded,
        rebate_flags=rebate_flags,
    )
    rebate_amount, tax_after_rebate = apply_rebate(
        total_income=total_income,
        tax_before_rebate=tax_before_rebate,
        rebate_eligible_tax=eligible,
        spec=inp.rebate,
    )

    # After rebate, scale slice taxes for surcharge allocation (eligible reduced first).
    slices = _slices_after_rebate(component_taxes, components, rebate_amount, excluded, rebate_flags)

    sur_after = sur_before = relief = ZERO
    if inp.surcharge is not None:
        sur_after, sur_before, relief = compute_surcharge(
            total_income=total_income,
            tax_after_rebate=tax_after_rebate,
            slices=slices,
            spec=inp.surcharge,
            tax_at_income=_ordinary_tax_fn(components),
            cess_rate_percent=inp.cess_rate_percent,
        )

    cess = apply_cess(
        tax_after_rebate=tax_after_rebate,
        surcharge=sur_after,
        rate_percent=inp.cess_rate_percent,
    )
    total_tax = q(tax_after_rebate + sur_after + cess)
    tax_payable = round_to_nearest_ten(total_tax) if inp.apply_288b else total_tax

    return KernelResult(
        total_income_raw=raw_total,
        total_income=total_income,
        component_taxes=component_taxes,
        tax_before_rebate=tax_before_rebate,
        rebate_amount=rebate_amount,
        tax_after_rebate=tax_after_rebate,
        surcharge_before_relief=sur_before,
        marginal_relief_amount=relief,
        surcharge_amount=sur_after,
        cess_amount=cess,
        total_tax=total_tax,
        tax_payable=tax_payable,
        breakdown={
            "total_income_raw": str(raw_total),
            "total_income_288a": str(total_income),
            "tax_before_rebate": str(tax_before_rebate),
            "rebate": str(rebate_amount),
            "tax_after_rebate": str(tax_after_rebate),
            "surcharge_before": str(sur_before),
            "marginal_relief": str(relief),
            "surcharge": str(sur_after),
            "cess": str(cess),
            "total_tax": str(total_tax),
            "tax_payable_288b": str(tax_payable),
        },
    )


def _slices_after_rebate(
    component_taxes: tuple[ComponentTax, ...],
    components: tuple[IncomeComponent, ...],
    rebate: Decimal,
    excluded: frozenset[str],
    rebate_flags: dict[str, bool],
) -> tuple[TaxedSlice, ...]:
    caps = {c.character_code: c.surcharge_cap_percent for c in components}
    remaining = q(rebate)
    slices: list[TaxedSlice] = []
    for ct in component_taxes:
        tax = ct.tax
        eligible = ct.character_code not in excluded and rebate_flags.get(ct.character_code, True)
        if eligible and remaining > ZERO and tax > ZERO:
            take = min(tax, remaining)
            tax = q(tax - take)
            remaining = q(remaining - take)
        slices.append(
            TaxedSlice(
                character_code=ct.character_code,
                tax=tax,
                surcharge_cap_percent=caps.get(ct.character_code),
            )
        )
    return tuple(slices)


def flat_bands(rate_percent: Decimal | int | str) -> tuple[RateBand, ...]:
    return (RateBand(lower=ZERO, upper=None, rate_percent=money(rate_percent)),)
