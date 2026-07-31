"""ThresholdDisallow — e.g. 40A(3) cash over limit."""

from __future__ import annotations

from app.services.tax_adjustment_engine.context import (
    AdjustmentFactBag,
    AdjustmentLineResult,
    ResolvedAdjustmentRule,
    ZERO,
    q,
)
from app.services.tax_adjustment_engine.methods._common import build_result


def evaluate(
    rule: ResolvedAdjustmentRule,
    facts: AdjustmentFactBag,
    prior: dict[str, AdjustmentLineResult],
) -> AdjustmentLineResult:
    _ = prior
    params = rule.parameters or {}
    threshold = q(params.get("threshold_amount", "10000"))
    amount = q(facts.cash_over_threshold)
    if amount == ZERO and params.get("needs_input_message"):
        return build_result(
            rule,
            status="NeedsInput",
            explanation={
                "method": "ThresholdDisallow",
                "threshold_amount": str(threshold),
                "transporter_threshold": str(params.get("transporter_threshold_amount", "35000")),
                "exceptions": params.get("exception_codes", []),
                "message": params["needs_input_message"],
            },
            inputs={"threshold_amount": str(threshold)},
        )
    return build_result(
        rule,
        base_amount=amount,
        computed_amount=amount,
        status="Computed" if amount != ZERO else "NeedsInput",
        explanation={
            "method": "ThresholdDisallow",
            "threshold_amount": str(threshold),
            "aggregate_grain": params.get("aggregate_grain"),
            "exceptions": params.get("exception_codes", []),
        },
        inputs={"cash_over_threshold": str(amount)},
    )
