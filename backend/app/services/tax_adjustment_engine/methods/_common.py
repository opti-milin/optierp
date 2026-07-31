"""Shared helpers for building AdjustmentLineResult rows."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.tax_adjustment_engine.context import (
    AdjustmentLineResult,
    ResolvedAdjustmentRule,
    ZERO,
    q,
)


def effect_to_direction(effect: str) -> str:
    if effect == "Deduct":
        return "Deduct"
    return "Add"


def build_result(
    rule: ResolvedAdjustmentRule,
    *,
    base_amount: Decimal = ZERO,
    computed_amount: Decimal = ZERO,
    status: str = "Manual",
    explanation: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    source_refs: dict[str, Any] | None = None,
    override_amount: Decimal | None = None,
    direction: str | None = None,
) -> AdjustmentLineResult:
    direction = direction or effect_to_direction(rule.default_effect)
    if rule.default_effect == "Informational":
        status = "Skipped" if status == "Manual" and computed_amount == ZERO else status
    final = q(override_amount) if override_amount is not None else q(computed_amount)
    return AdjustmentLineResult(
        provision_id=rule.provision_id,
        rule_id=rule.rule_id,
        section_code=rule.section_code,
        stage=rule.stage,
        description=rule.title,
        direction=direction,
        base_amount=q(base_amount),
        computed_amount=q(computed_amount),
        override_amount=q(override_amount) if override_amount is not None else None,
        final_amount=final,
        status=status if rule.default_effect != "Informational" else (
            "Skipped" if final == ZERO else status
        ),
        explanation=explanation or {},
        inputs=inputs or {},
        source_refs=source_refs or {},
    )
