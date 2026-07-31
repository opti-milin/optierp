"""PaymentTiming — 43B / 40(a) payment-date skeleton (NeedsInput when facts missing)."""

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
    disallow_pct = q(params.get("disallowance_percent", "100"))
    if rule.section_code.startswith("40(a)(i)") and not rule.section_code.endswith("ia"):
        base = q(facts.tds_gap_non_resident_expense)
    elif rule.section_code == "43B" or rule.section_code.startswith("43B"):
        base = q(facts.unpaid_43b)
    elif rule.section_code == "36(1)(va)":
        base = q(facts.extras.get("unpaid_employee_contrib", ZERO))
    else:
        base = q(facts.tds_gap_resident_expense)

    if base == ZERO:
        return build_result(
            rule,
            status="NeedsInput",
            explanation={
                "method": "PaymentTiming",
                "due_date_rule": params.get("due_date_rule"),
                "reversal_mode": params.get("reversal_mode"),
                "message": params.get(
                    "needs_input_message",
                    "Payment-date facts unavailable — enter unpaid / non-compliant amount",
                ),
            },
            inputs={"base_amount": "0"},
        )

    from decimal import Decimal

    computed = q(base * disallow_pct / Decimal("100"))
    return build_result(
        rule,
        base_amount=base,
        computed_amount=computed,
        status="Computed",
        explanation={
            "method": "PaymentTiming",
            "due_date_rule": params.get("due_date_rule"),
            "disallowance_percent": str(disallow_pct),
            "reversal_mode": params.get("reversal_mode"),
        },
        inputs={"base_amount": str(base)},
        source_refs=dict(facts.extras.get("payment_refs") or {}),
    )
