"""Manual / NeedsInput evaluation."""

from __future__ import annotations

from app.services.tax_adjustment_engine.context import (
    AdjustmentFactBag,
    AdjustmentLineResult,
    ResolvedAdjustmentRule,
    ZERO,
)
from app.services.tax_adjustment_engine.methods._common import build_result


def evaluate(
    rule: ResolvedAdjustmentRule,
    facts: AdjustmentFactBag,
    prior: dict[str, AdjustmentLineResult],
) -> AdjustmentLineResult:
    _ = facts, prior
    params = rule.parameters or {}
    if params.get("informational"):
        return build_result(
            rule,
            status="Skipped",
            explanation={
                "method": "Manual",
                "note": "Informational provision — no PGBP impact until dedicated engine slice",
            },
        )
    msg = params.get("needs_input_message") or (
        "Manual entry required" if params.get("required_evidence") else "Enter amount if applicable"
    )
    return build_result(
        rule,
        status="NeedsInput",
        computed_amount=ZERO,
        explanation={"method": "Manual", "message": msg},
    )
