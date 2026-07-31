"""AI / rules-based pricing recommendation port (Phase 4 stub)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.services.cm_planning.waterfall import WaterfallResult


@dataclass(frozen=True)
class PricingRecommendation:
    suggested_rate: Decimal | None
    target_cm1_pct: Decimal | None
    rationale: str
    provider: str


class RecommendationPort(Protocol):
    def recommend(
        self,
        *,
        waterfall: WaterfallResult,
        current_rate: Decimal,
        target_cm1_pct: Decimal | None,
    ) -> PricingRecommendation: ...


class NoopRecommendationPort:
    """Default — no AI / vendor dependency."""

    def recommend(
        self,
        *,
        waterfall: WaterfallResult,
        current_rate: Decimal,
        target_cm1_pct: Decimal | None,
    ) -> PricingRecommendation:
        return PricingRecommendation(
            suggested_rate=None,
            target_cm1_pct=target_cm1_pct,
            rationale="No recommendation provider configured",
            provider="noop",
        )


class RulesBasedRecommendationPort:
    """Suggest min selling rate from target CM1% when below target."""

    def recommend(
        self,
        *,
        waterfall: WaterfallResult,
        current_rate: Decimal,
        target_cm1_pct: Decimal | None,
    ) -> PricingRecommendation:
        if target_cm1_pct is None or waterfall.min_selling_total is None:
            return PricingRecommendation(
                suggested_rate=None,
                target_cm1_pct=target_cm1_pct,
                rationale="No target CM1% or min selling total",
                provider="rules",
            )
        if waterfall.cm1_pct is not None and waterfall.cm1_pct >= target_cm1_pct:
            return PricingRecommendation(
                suggested_rate=current_rate,
                target_cm1_pct=target_cm1_pct,
                rationale=f"CM1 {waterfall.cm1_pct}% already meets target {target_cm1_pct}%",
                provider="rules",
            )
        return PricingRecommendation(
            suggested_rate=waterfall.min_selling_total,
            target_cm1_pct=target_cm1_pct,
            rationale=(
                f"Raise total selling to {waterfall.min_selling_total} "
                f"to reach CM1 target {target_cm1_pct}%"
            ),
            provider="rules",
        )


_ACTIVE: RecommendationPort = NoopRecommendationPort()


def get_recommendation_port() -> RecommendationPort:
    return _ACTIVE


def set_recommendation_port(port: RecommendationPort) -> None:
    global _ACTIVE
    _ACTIVE = port


def recommend_pricing(
    *,
    waterfall: WaterfallResult,
    current_rate: Decimal,
    target_cm1_pct: Decimal | None,
    use_rules: bool = False,
) -> PricingRecommendation:
    port: RecommendationPort = RulesBasedRecommendationPort() if use_rules else get_recommendation_port()
    return port.recommend(
        waterfall=waterfall, current_rate=current_rate, target_cm1_pct=target_cm1_pct
    )
