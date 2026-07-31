"""ScheduleCap — Chapter VI-A style capped deductions (80G, 80C, …)."""

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
    rate = q(params.get("deduction_rate", "100"))
    cap_key = str(params.get("cap_base_key") or "schedule_amount")
    schedule_amt = q(facts.schedule_inputs.get(rule.section_code, ZERO))
    if schedule_amt == ZERO:
        schedule_amt = q(facts.schedule_inputs.get("schedule_amount", ZERO))
    if schedule_amt == ZERO and cap_key == "salary_income":
        schedule_amt = q(facts.extras.get("salary_income", ZERO))
    if schedule_amt == ZERO and params.get("needs_input_message"):
        return build_result(
            rule,
            status="NeedsInput",
            direction="Deduct",
            explanation={
                "method": "ScheduleCap",
                "deduction_rate": str(rate),
                "message": params["needs_input_message"],
            },
            inputs={"schedule_amount": "0"},
        )

    gross = q(schedule_amt * rate / Decimal("100"))
    # Qualifying limit (e.g. 80G 10% of AGI)
    ql_pct = params.get("qualifying_limit_pct_of")
    if ql_pct is not None:
        agi = q(facts.extras.get("adjusted_gross_total_income", facts.book_profit))
        ql = q(agi * q(ql_pct) / Decimal("100"))
        gross = min(gross, ql) if ql > ZERO else gross

    abs_cap = params.get("absolute_cap")
    if abs_cap is not None:
        gross = min(gross, q(abs_cap))

    return build_result(
        rule,
        base_amount=schedule_amt,
        computed_amount=gross,
        status="Computed" if schedule_amt != ZERO else "NeedsInput",
        direction="Deduct",
        explanation={
            "method": "ScheduleCap",
            "deduction_rate": str(rate),
            "qualifying_limit_pct_of": str(ql_pct) if ql_pct is not None else None,
            "absolute_cap": str(abs_cap) if abs_cap is not None else None,
            "cap_base_key": cap_key,
        },
        inputs={"schedule_amount": str(schedule_amt)},
    )
