"""Frozen kernel types — pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.services.taxation.kernel.money import ZERO


@dataclass(frozen=True, slots=True)
class RateBand:
    lower: Decimal
    upper: Decimal | None
    rate_percent: Decimal
    fixed_amount: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class SurchargeBand:
    lower: Decimal
    upper: Decimal | None
    rate_percent: Decimal


@dataclass(frozen=True, slots=True)
class IncomeComponent:
    """One slice of income of a given statutory character."""

    character_code: str
    amount: Decimal
    bands: tuple[RateBand, ...]
    surcharge_cap_percent: Decimal | None = None
    rebate_eligible: bool = True


@dataclass(frozen=True, slots=True)
class RebateSpec:
    max_taxable_income: Decimal
    max_rebate_amount: Decimal
    marginal_relief_enabled: bool = False
    excluded_characters: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class SurchargeSpec:
    bands: tuple[SurchargeBand, ...]
    marginal_relief: bool = True
    capped_characters: frozenset[str] = field(default_factory=frozenset)
    default_cap_percent: Decimal = Decimal("15")


@dataclass(frozen=True, slots=True)
class KernelInput:
    """Inputs for a pure tax computation (post Chapter VI-A total income)."""

    components: tuple[IncomeComponent, ...]
    surcharge: SurchargeSpec | None = None
    rebate: RebateSpec | None = None
    cess_rate_percent: Decimal = Decimal("4")
    apply_288a: bool = True
    apply_288b: bool = True


@dataclass(frozen=True, slots=True)
class ComponentTax:
    character_code: str
    amount: Decimal
    tax: Decimal


@dataclass(frozen=True, slots=True)
class KernelResult:
    total_income_raw: Decimal
    total_income: Decimal  # after s.288A if applied
    component_taxes: tuple[ComponentTax, ...]
    tax_before_rebate: Decimal
    rebate_amount: Decimal
    tax_after_rebate: Decimal
    surcharge_before_relief: Decimal
    marginal_relief_amount: Decimal
    surcharge_amount: Decimal
    cess_amount: Decimal
    total_tax: Decimal
    tax_payable: Decimal  # after s.288B if applied
    breakdown: dict[str, str] = field(default_factory=dict)
