"""DiffTwoSources — books dep vs IT Act dep."""

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
    a_key = str(params.get("source_a") or "books_depreciation")
    b_key = str(params.get("source_b") or "tax_depreciation")
    a = _lookup(facts, a_key)
    b = _lookup(facts, b_key)
    if a == ZERO and b == ZERO and params.get("needs_input_message"):
        return build_result(
            rule,
            status="NeedsInput",
            explanation={
                "method": "DiffTwoSources",
                "source_a": a_key,
                "source_b": b_key,
                "message": params["needs_input_message"],
            },
            inputs={a_key: "0", b_key: "0"},
        )
    sign_rule = str(params.get("sign_rule") or "positive_a_minus_b_as_add")
    diff = q(a - b)
    if sign_rule.endswith("_as_deduct"):
        computed = diff if diff > ZERO else ZERO
        direction = "Deduct"
    else:
        computed = diff if diff > ZERO else ZERO
        direction = "Add"
    return build_result(
        rule,
        base_amount=q(a),
        computed_amount=computed,
        status="Computed" if (a != ZERO or b != ZERO) else "NeedsInput",
        direction=direction,
        explanation={
            "method": "DiffTwoSources",
            "source_a": a_key,
            "source_b": b_key,
            "a": str(a),
            "b": str(b),
            "sign_rule": sign_rule,
        },
        inputs={a_key: str(a), b_key: str(b)},
    )


def _lookup(facts: AdjustmentFactBag, key: str) -> Decimal:
    if key == "books_depreciation":
        return q(facts.books_depreciation)
    if key == "tax_depreciation":
        return q(facts.tax_depreciation)
    return q(facts.extras.get(key, ZERO))
