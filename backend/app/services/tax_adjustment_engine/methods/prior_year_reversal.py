"""PriorYearReversal — deduct prior-year Add when cured (43B, 40(a)(ia))."""

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
    match_section = str(params.get("match_section") or "43B")
    amount = q(facts.prior_year_43b_reversals)
    if match_section != "43B":
        amount = ZERO
        for ln in facts.prior_year_lines:
            if ln.get("section_code") == match_section and ln.get("direction") == "Add":
                amount += q(ln.get("final_amount") or ln.get("amount") or 0)

    if amount == ZERO:
        return build_result(
            rule,
            status="Skipped",
            direction="Deduct",
            explanation={
                "method": "PriorYearReversal",
                "match_section": match_section,
                "message": "No prior-year disallowance to reverse",
            },
        )
    return build_result(
        rule,
        base_amount=amount,
        computed_amount=amount,
        status="Computed",
        direction="Deduct",
        explanation={
            "method": "PriorYearReversal",
            "match_section": match_section,
            "match_keys": params.get("match_keys", []),
        },
        inputs={"reversal_amount": str(amount)},
        source_refs={"prior_year_lines": len(facts.prior_year_lines)},
    )
