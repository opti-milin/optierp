"""Unit tests for tax adjustment engine methods (no DB)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.services.tax_adjustment_engine.context import (
    AdjustmentFactBag,
    ResolvedAdjustmentRule,
    ZERO,
    q,
)
from app.services.tax_adjustment_engine.methods import (
    diff_two_sources,
    percent_of_base,
    prior_year_reversal,
    schedule_cap,
    threshold_disallow,
)
from app.services.tax_adjustment_engine.pipeline import net_from_results


def _rule(**kwargs) -> ResolvedAdjustmentRule:
    defaults = dict(
        rule_id=uuid.uuid4(),
        rule_code="t",
        provision_id=uuid.uuid4(),
        section_code="40(a)(ia)",
        title="Test",
        stage="PGBP",
        default_effect="Add",
        evaluation_method="PercentOfBase",
        parameters={},
        source_type="Manual",
        source_config={},
        depends_on=[],
        allow_manual_override=True,
        include_in_seed_lines=True,
        sequence=1,
    )
    defaults.update(kwargs)
    return ResolvedAdjustmentRule(**defaults)


def test_percent_of_base_30_percent_40a_ia() -> None:
    rule = _rule(
        parameters={"rate_percent": "30", "base_key": "tds_gap_resident_expense"},
    )
    facts = AdjustmentFactBag(tds_gap_resident_expense=Decimal("100000"))
    result = percent_of_base.evaluate(rule, facts, {})
    assert result.computed_amount == q("30000")
    assert result.status == "Computed"
    assert result.direction == "Add"


def test_percent_of_base_needs_input_when_zero() -> None:
    rule = _rule(
        parameters={
            "rate_percent": "30",
            "base_key": "tds_gap_resident_expense",
            "needs_input_message": "enter base",
        },
    )
    result = percent_of_base.evaluate(rule, AdjustmentFactBag(), {})
    assert result.status == "NeedsInput"
    assert result.final_amount == ZERO


def test_schedule_cap_80g_with_qualifying_limit() -> None:
    rule = _rule(
        section_code="80G",
        title="Donations",
        default_effect="Deduct",
        evaluation_method="ScheduleCap",
        parameters={
            "deduction_rate": "50",
            "qualifying_limit_pct_of": "10",
            "cap_base_key": "adjusted_gross_total_income",
        },
    )
    facts = AdjustmentFactBag(
        book_profit=Decimal("1000000"),
        schedule_inputs={"80G": Decimal("200000")},
        extras={"adjusted_gross_total_income": Decimal("1000000")},
    )
    # 50% of 200000 = 100000; QL = 10% of 1e6 = 100000 → min = 100000
    result = schedule_cap.evaluate(rule, facts, {})
    assert result.direction == "Deduct"
    assert result.computed_amount == q("100000")


def test_schedule_cap_absolute_cap_80c() -> None:
    rule = _rule(
        section_code="80C",
        default_effect="Deduct",
        evaluation_method="ScheduleCap",
        parameters={
            "deduction_rate": "100",
            "absolute_cap": "150000",
            "cap_base_key": "schedule_amount",
        },
    )
    facts = AdjustmentFactBag(schedule_inputs={"80C": Decimal("200000")})
    result = schedule_cap.evaluate(rule, facts, {})
    assert result.computed_amount == q("150000")


def test_threshold_disallow_40a3() -> None:
    rule = _rule(
        section_code="40A(3)",
        parameters={"threshold_amount": "10000"},
    )
    facts = AdjustmentFactBag(cash_over_threshold=Decimal("55000"))
    result = threshold_disallow.evaluate(rule, facts, {})
    assert result.computed_amount == q("55000")
    assert result.status == "Computed"


def test_diff_two_sources_dep_add() -> None:
    rule = _rule(
        section_code="Dep-add",
        parameters={
            "source_a": "books_depreciation",
            "source_b": "tax_depreciation",
            "sign_rule": "positive_a_minus_b_as_add",
        },
    )
    facts = AdjustmentFactBag(
        books_depreciation=Decimal("120000"),
        tax_depreciation=Decimal("100000"),
    )
    result = diff_two_sources.evaluate(rule, facts, {})
    assert result.computed_amount == q("20000")
    assert result.direction == "Add"


def test_diff_two_sources_dep_ded() -> None:
    rule = _rule(
        section_code="Dep-ded",
        default_effect="Deduct",
        parameters={
            "source_a": "tax_depreciation",
            "source_b": "books_depreciation",
            "sign_rule": "positive_a_minus_b_as_deduct",
        },
    )
    facts = AdjustmentFactBag(
        books_depreciation=Decimal("80000"),
        tax_depreciation=Decimal("100000"),
    )
    result = diff_two_sources.evaluate(rule, facts, {})
    assert result.computed_amount == q("20000")
    assert result.direction == "Deduct"


def test_prior_year_reversal_43b() -> None:
    rule = _rule(
        section_code="43B-rev",
        default_effect="Deduct",
        evaluation_method="PriorYearReversal",
        parameters={"match_section": "43B"},
    )
    facts = AdjustmentFactBag(prior_year_43b_reversals=Decimal("25000"))
    result = prior_year_reversal.evaluate(rule, facts, {})
    assert result.direction == "Deduct"
    assert result.computed_amount == q("25000")


def test_net_from_results() -> None:
    from app.services.tax_adjustment_engine.context import AdjustmentLineResult

    lines = [
        AdjustmentLineResult(
            provision_id=None,
            rule_id=None,
            section_code="a",
            stage="PGBP",
            description="",
            direction="Add",
            base_amount=ZERO,
            computed_amount=q("100"),
            override_amount=None,
            final_amount=q("100"),
            status="Manual",
            explanation={},
            inputs={},
            source_refs={},
        ),
        AdjustmentLineResult(
            provision_id=None,
            rule_id=None,
            section_code="b",
            stage="ChapterVIA",
            description="",
            direction="Deduct",
            base_amount=ZERO,
            computed_amount=q("30"),
            override_amount=None,
            final_amount=q("30"),
            status="Manual",
            explanation={},
            inputs={},
            source_refs={},
        ),
    ]
    assert net_from_results(lines) == q("70")
