"""FormulaSafe — sandboxed arithmetic against fact env."""

from __future__ import annotations

from app.services.cm_planning.formula_safe import eval_formula
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
    params = rule.parameters or {}
    expr = str(params.get("expression") or "").strip()
    allowed = set(params.get("allowed_vars") or [])
    env: dict[str, float] = {
        "book_profit": float(facts.book_profit),
        "books_depreciation": float(facts.books_depreciation),
        "tax_depreciation": float(facts.tax_depreciation),
    }
    for k, v in facts.schedule_inputs.items():
        if not allowed or k in allowed:
            env[k] = float(v)
    for k, v in facts.extras.items():
        if isinstance(v, (int, float, str)) and (not allowed or k in allowed):
            try:
                env[k] = float(v)
            except (TypeError, ValueError):
                continue
    for code, ln in prior.items():
        env[f"line_{code.replace('(', '_').replace(')', '_').replace('.', '_')}"] = float(
            ln.final_amount
        )
    if not expr:
        return build_result(
            rule,
            status="NeedsInput",
            explanation={"method": "FormulaSafe", "message": "No expression configured"},
        )
    try:
        value = q(eval_formula(expr, env))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        return build_result(
            rule,
            status="NeedsInput",
            explanation={"method": "FormulaSafe", "error": str(exc), "expression": expr},
        )
    return build_result(
        rule,
        base_amount=value,
        computed_amount=value if value > ZERO else ZERO,
        status="Computed",
        explanation={"method": "FormulaSafe", "expression": expr, "env_keys": list(env.keys())},
        inputs={k: str(env[k]) for k in list(env)[:20]},
    )
