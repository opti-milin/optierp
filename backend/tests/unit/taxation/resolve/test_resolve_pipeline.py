"""Unit tests for Phase 4 resolver, pipeline, and append-only run hashing."""

from __future__ import annotations

from decimal import Decimal

from app.services.taxation.facts import AdjustmentFact, IncomeFact, apply_adjustments
from app.services.taxation.kernel.types import RateBand, RebateSpec, SurchargeBand, SurchargeSpec
from app.services.taxation.pipeline import input_hash, run_pipeline
from app.services.taxation.resolve.types import ResolvedRuleSet, ResolvedSchedule
import uuid

D = Decimal


def _company_flat_ruleset() -> ResolvedRuleSet:
    bands = (
        RateBand(lower=D("0"), upper=None, rate_percent=D("25")),
    )
    surcharge = SurchargeSpec(
        bands=(
            SurchargeBand(D("0"), D("10000000"), D("0")),
            SurchargeBand(D("10000000"), D("100000000"), D("7")),
            SurchargeBand(D("100000000"), None, D("12")),
        ),
        marginal_relief=True,
    )
    return ResolvedRuleSet(
        finance_act_version_id=uuid.uuid4(),
        ay_code="2025-26",
        assessee_class_code="Company",
        regime_code="Normal",
        schedules=(
            ResolvedSchedule(
                code="COMPANY_NORMAL",
                income_character_code="ORDINARY",
                schedule_kind="Flat",
                bands=bands,
            ),
        ),
        surcharge=surcharge,
        rebate=None,
        cess_rate_percent=D("4"),
    )


def test_ruleset_hash_stable() -> None:
    r1 = _company_flat_ruleset()
    # Rebuild with same content but new object identity for FA id — change id intentionally
    r2 = ResolvedRuleSet(
        finance_act_version_id=r1.finance_act_version_id,
        ay_code=r1.ay_code,
        assessee_class_code=r1.assessee_class_code,
        regime_code=r1.regime_code,
        schedules=r1.schedules,
        surcharge=r1.surcharge,
        rebate=r1.rebate,
        cess_rate_percent=r1.cess_rate_percent,
    )
    assert r1.hash() == r2.hash()
    assert len(r1.hash()) == 64


def test_pipeline_company_flat_tax() -> None:
    ruleset = _company_flat_ruleset()
    income = (
        IncomeFact(
            head="PGBP",
            income_character_code="ORDINARY",
            gross=D("10000000"),
            deductions=D("0"),
            net=D("10000000"),
        ),
    )
    state = run_pipeline(income_facts=income, adjustment_facts=(), ruleset=ruleset)
    assert state.kernel_result is not None
    # 25% of 1cr = 25L; surcharge 0 below 1cr threshold (exceeds means >); cess 4%
    # income == 1cr is NOT > 1cr, so surcharge 0
    assert state.kernel_result.tax_before_rebate == D("2500000.00")
    assert state.kernel_result.surcharge_amount == D("0.00")
    assert state.kernel_result.cess_amount == D("100000.00")


def test_adjustments_add_and_override_key() -> None:
    base = D("1000")
    adjs = (
        AdjustmentFact(
            section_code="37",
            rule_code="ADD_DISC",
            stage="PGBP",
            description="disallow",
            direction="Add",
            amount=D("100"),
            override_amount=D("50"),
            status="Override",
        ),
    )
    assert apply_adjustments(base, adjs) == D("1050.00")
    assert adjs[0].override_key == "ADD_DISC|37|disallow"


def test_input_hash_changes_with_income() -> None:
    ruleset = _company_flat_ruleset()
    h = ruleset.hash()
    a = (
        IncomeFact("PGBP", "ORDINARY", D("100"), D("0"), D("100")),
    )
    b = (
        IncomeFact("PGBP", "ORDINARY", D("200"), D("0"), D("200")),
    )
    assert input_hash(income_facts=a, adjustment_facts=(), ruleset_hash=h) != input_hash(
        income_facts=b, adjustment_facts=(), ruleset_hash=h
    )


def test_rebate_excluded_from_hash_order() -> None:
    """Excluded characters frozenset order must not affect hash."""
    fav = uuid.uuid4()
    bands = (RateBand(D("0"), None, D("30")),)
    sched = (
        ResolvedSchedule("X", "ORDINARY", "Flat", bands),
    )
    r1 = ResolvedRuleSet(
        finance_act_version_id=fav,
        ay_code="2025-26",
        assessee_class_code="Individual",
        regime_code="New",
        schedules=sched,
        surcharge=None,
        rebate=RebateSpec(
            max_taxable_income=D("700000"),
            max_rebate_amount=D("25000"),
            excluded_characters=frozenset({"LTCG_112A", "STCG_111A"}),
        ),
        cess_rate_percent=D("4"),
    )
    r2 = ResolvedRuleSet(
        finance_act_version_id=fav,
        ay_code="2025-26",
        assessee_class_code="Individual",
        regime_code="New",
        schedules=sched,
        surcharge=None,
        rebate=RebateSpec(
            max_taxable_income=D("700000"),
            max_rebate_amount=D("25000"),
            excluded_characters=frozenset({"STCG_111A", "LTCG_112A"}),
        ),
        cess_rate_percent=D("4"),
    )
    assert r1.hash() == r2.hash()
