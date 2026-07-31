"""Shared dataclasses for the income-tax engine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


ZERO = Decimal("0")
Q2 = Decimal("0.01")


def q(x: Decimal | int | float | str | None) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


@dataclass(frozen=True)
class SlabBand:
    from_amount: Decimal
    to_amount: Decimal | None
    rate_percent: Decimal


@dataclass(frozen=True)
class SurchargeBand:
    income_from: Decimal
    income_to: Decimal | None
    rate_percent: Decimal


@dataclass(frozen=True)
class SpecialIncomeInput:
    income_category_code: str
    amount: Decimal
    rate_percent: Decimal
    description: str | None = None
    special_rate_id: uuid.UUID | None = None


@dataclass
class ResolvedTaxRules:
    """Masters resolved for one computation (policy + linked packs)."""

    policy_id: uuid.UUID | None
    computation_method: str  # FlatRate | SlabBased | RuleBased
    ordinary_method: str  # FlatRate | SlabBased
    filing_regime: str
    entity_type: str
    flat_tax_rate: Decimal = ZERO
    slabs: list[SlabBand] = field(default_factory=list)
    surcharge_brackets: list[SurchargeBand] = field(default_factory=list)
    marginal_relief_enabled: bool = False
    cess_rate: Decimal = ZERO
    cess_base: str = "TaxPlusSurcharge"
    rebate_section: str | None = None
    rebate_max_taxable: Decimal = ZERO
    rebate_max_amount: Decimal = ZERO
    rate_table_id: uuid.UUID | None = None


@dataclass
class TaxEngineInput:
    book_profit: Decimal
    adjustments: list[tuple[str, Decimal]]  # (direction, amount)
    tds_credit: Decimal = ZERO
    tcs_credit: Decimal = ZERO
    advance_tax_paid: Decimal = ZERO
    special_income: list[SpecialIncomeInput] = field(default_factory=list)
    # True (EntityBooks): special amounts are already inside book_profit → carve out.
    # False (IndividualHeads): special lines are additive to heads → do not carve out.
    special_income_in_book: bool = True
    as_of: date | None = None


@dataclass
class TaxEngineResult:
    net_adjustments: Decimal
    taxable_income: Decimal
    ordinary_income: Decimal
    special_tax: Decimal
    tax_amount: Decimal  # ordinary base tax after rebate
    tax_before_rebate: Decimal
    surcharge_before_relief: Decimal
    marginal_relief_amount: Decimal
    surcharge_amount: Decimal
    rebate_amount: Decimal
    cess_amount: Decimal
    total_tax: Decimal
    tax_payable: Decimal
    computation_method: str
    policy_id: uuid.UUID | None
    rate_table_id: uuid.UUID | None
    special_line_taxes: list[dict[str, Any]]
    breakdown: dict[str, Any]

    @property
    def rebate_87a(self) -> Decimal:
        """Alias for legacy unit tests / API field name."""
        return self.rebate_amount
