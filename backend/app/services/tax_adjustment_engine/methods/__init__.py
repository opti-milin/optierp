"""Evaluation method registry."""

from __future__ import annotations

from typing import Callable

from app.services.tax_adjustment_engine.context import (
    AdjustmentFactBag,
    AdjustmentLineResult,
    ResolvedAdjustmentRule,
)
from app.services.tax_adjustment_engine.methods import (
    diff_two_sources,
    formula_safe,
    manual,
    payment_timing,
    percent_of_base,
    prior_year_reversal,
    schedule_cap,
    threshold_disallow,
)

MethodFn = Callable[
    [ResolvedAdjustmentRule, AdjustmentFactBag, dict[str, AdjustmentLineResult]],
    AdjustmentLineResult,
]

METHODS: dict[str, MethodFn] = {
    "Manual": manual.evaluate,
    "PercentOfBase": percent_of_base.evaluate,
    "ThresholdDisallow": threshold_disallow.evaluate,
    "PaymentTiming": payment_timing.evaluate,
    "DiffTwoSources": diff_two_sources.evaluate,
    "ScheduleCap": schedule_cap.evaluate,
    "FormulaSafe": formula_safe.evaluate,
    "PriorYearReversal": prior_year_reversal.evaluate,
    "Composite": manual.evaluate,  # composite expands via depends_on / child rules in pipeline
}


def get_method(name: str) -> MethodFn:
    return METHODS.get(name, manual.evaluate)
