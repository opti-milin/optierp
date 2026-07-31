"""DTOs for the tax adjustment engine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


ZERO = Decimal("0")


def q(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(Decimal("0.000001"))


@dataclass
class ResolvedAdjustmentRule:
    rule_id: uuid.UUID
    rule_code: str
    provision_id: uuid.UUID
    section_code: str
    title: str
    stage: str
    default_effect: str
    evaluation_method: str
    parameters: dict[str, Any]
    source_type: str
    source_config: dict[str, Any]
    depends_on: list[str]
    allow_manual_override: bool
    include_in_seed_lines: bool
    sequence: int
    itr_schedule_hint: str | None = None


@dataclass
class ResolvedAdjustmentPack:
    pack_id: uuid.UUID | None
    assessment_year: str
    entity_type: str
    filing_regime: str
    rules: list[ResolvedAdjustmentRule] = field(default_factory=list)


@dataclass
class AdjustmentFactBag:
    """ERP-sourced facts available to evaluation methods."""

    book_profit: Decimal = ZERO
    books_depreciation: Decimal = ZERO
    tax_depreciation: Decimal = ZERO
    tds_gap_resident_expense: Decimal = ZERO
    tds_gap_non_resident_expense: Decimal = ZERO
    cash_over_threshold: Decimal = ZERO
    unpaid_43b: Decimal = ZERO
    prior_year_43b_reversals: Decimal = ZERO
    prior_year_lines: list[dict[str, Any]] = field(default_factory=list)
    schedule_inputs: dict[str, Decimal] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdjustmentEngineInput:
    company_id: uuid.UUID
    assessment_year: str
    from_date: date
    to_date: date
    assessee_mode: str
    entity_type: str
    filing_regime: str
    book_profit: Decimal = ZERO
    salary_income: Decimal = ZERO
    chapter_via_deduction: Decimal = ZERO
    standard_deduction: Decimal = ZERO
    existing_lines: list[dict[str, Any]] = field(default_factory=list)
    """Prior draft lines (for override preservation), keyed by section_code/rule_code."""
    force_seed: bool = False
    """When True, materialise include_in_seed_lines even if amount is zero."""


@dataclass
class AdjustmentLineResult:
    provision_id: uuid.UUID | None
    rule_id: uuid.UUID | None
    section_code: str
    stage: str
    description: str
    direction: str
    base_amount: Decimal
    computed_amount: Decimal
    override_amount: Decimal | None
    final_amount: Decimal
    status: str
    explanation: dict[str, Any]
    inputs: dict[str, Any]
    source_refs: dict[str, Any]
    prior_year_line_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None

    @property
    def amount(self) -> Decimal:
        return self.final_amount


@dataclass
class AdjustmentEngineResult:
    lines: list[AdjustmentLineResult]
    net_adjustments: Decimal
    pack_id: uuid.UUID | None
    stage_nets: dict[str, Decimal] = field(default_factory=dict)
