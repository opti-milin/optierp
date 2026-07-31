"""PercentOfBase — e.g. 40(a)(ia) 30%, 80JJAA 30%."""

from __future__ import annotations

from decimal import Decimal

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
    rate = q(params.get("rate_percent", "0"))
    base_key = str(params.get("base_key") or "")
    base = _base_from_facts(facts, base_key)
    if base == ZERO and params.get("needs_input_message"):
        return build_result(
            rule,
            status="NeedsInput",
            base_amount=ZERO,
            explanation={
                "method": "PercentOfBase",
                "rate_percent": str(rate),
                "base_key": base_key,
                "message": params["needs_input_message"],
            },
            inputs={"base_key": base_key, "base_amount": "0"},
        )
    computed = q(base * rate / Decimal("100"))
    return build_result(
        rule,
        base_amount=base,
        computed_amount=computed,
        status="Computed" if base != ZERO else "NeedsInput",
        explanation={
            "method": "PercentOfBase",
            "rate_percent": str(rate),
            "base_key": base_key,
            "formula": f"{base_key} × {rate}%",
        },
        inputs={"base_key": base_key, "base_amount": str(base)},
    )


def _base_from_facts(facts: AdjustmentFactBag, key: str) -> Decimal:
    if key == "tds_gap_resident_expense":
        return q(facts.tds_gap_resident_expense)
    if key == "tds_gap_non_resident_expense":
        return q(facts.tds_gap_non_resident_expense)
    if key == "additional_employee_cost":
        return q(facts.schedule_inputs.get("additional_employee_cost", ZERO))
    if key in facts.schedule_inputs:
        return q(facts.schedule_inputs[key])
    if key in facts.extras:
        return q(facts.extras[key])
    return ZERO
