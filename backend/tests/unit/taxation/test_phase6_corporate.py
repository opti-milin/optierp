"""Phase 6 — MAT compare, loss set-off, depreciation half-rate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.taxation.depreciation import compute_block_depreciation, is_half_rate
from app.services.taxation.kernel.mat import MatInput, compare_mat
from app.services.taxation.kernel.money import ZERO, q
from app.services.taxation.kernel.setoff import BroughtForwardLoss, apply_loss_setoff
from app.services.taxation.pipeline import run_pipeline
from app.services.taxation.facts import IncomeFact
from app.services.taxation.kernel.types import RateBand, SurchargeBand, SurchargeSpec
from app.services.taxation.resolve.types import ResolvedRuleSet, ResolvedSchedule
import uuid

D = Decimal


def test_half_rate_under_180_days() -> None:
    fy_end = date(2025, 3, 31)
    assert is_half_rate(date(2024, 10, 5), fy_end) is True  # < 180 days
    assert is_half_rate(date(2024, 4, 1), fy_end) is False  # full year


def test_block_depreciation_half_addition() -> None:
    dep, addl, closing = compute_block_depreciation(
        opening_wdv=D("100000"),
        additions_full=D("0"),
        additions_half=D("100000"),
        deletions=D("0"),
        rate_percent=D("15"),
        additional_eligible=False,
    )
    # full: 15% of 100k = 15k; half add: 7.5% of 100k = 7.5k → 22.5k
    assert dep == D("22500.00")
    assert addl == ZERO
    assert closing == D("177500.00")


def test_mat_higher_creates_credit() -> None:
    res = compare_mat(
        MatInput(
            book_profit=D("10000000"),
            normal_tax_payable=D("100000"),
            mat_rate_percent=D("15"),
            available_mat_credit=ZERO,
        )
    )
    # 15% of 1cr = 15L + 4% cess = 15.6L
    assert res.applied_basis == "MAT"
    assert res.mat_tax == D("1560000.00")
    assert res.mat_credit_created == q(res.mat_tax - D("100000"))
    assert res.tax_after_mat == res.mat_tax


def test_mat_normal_utilises_credit() -> None:
    res = compare_mat(
        MatInput(
            book_profit=D("100000"),
            normal_tax_payable=D("500000"),
            mat_rate_percent=D("15"),
            available_mat_credit=D("200000"),
        )
    )
    assert res.applied_basis == "Normal"
    # MAT ≈ 15600; excess normal over MAT ≈ 484400; utilise min(200k, excess)=200k
    assert res.mat_credit_utilised == D("200000.00")
    assert res.tax_after_mat == D("300000.00")


def test_loss_setoff_business_against_ordinary() -> None:
    nets = {"ORDINARY": D("1000000")}
    losses = (
        BroughtForwardLoss(
            ledger_id="L1",
            setoff_group="ORDINARY",
            loss_kind="Business",
            amount_remaining=D("400000"),
            origin_ay_code="2023-24",
            expires_after_ay="2031-32",
        ),
    )
    result = apply_loss_setoff(nets, losses, current_ay="2025-26")
    assert result.character_nets["ORDINARY"] == D("600000.00")
    assert result.total_set_off == D("400000.00")
    assert len(result.applications) == 1


def test_pipeline_mat_and_setoff() -> None:
    bands = (RateBand(lower=D("0"), upper=None, rate_percent=D("25")),)
    ruleset = ResolvedRuleSet(
        finance_act_version_id=uuid.uuid4(),
        ay_code="2025-26",
        assessee_class_code="Company",
        regime_code="Normal",
        schedules=(
            ResolvedSchedule("COMPANY_NORMAL", "ORDINARY", "Flat", bands),
        ),
        surcharge=SurchargeSpec(
            bands=(SurchargeBand(D("0"), None, D("0")),),
            marginal_relief=False,
        ),
        rebate=None,
        cess_rate_percent=D("4"),
    )
    income = (
        IncomeFact("PGBP", "ORDINARY", D("2000000"), D("0"), D("2000000")),
    )
    bf = (
        BroughtForwardLoss(
            ledger_id="L1",
            setoff_group="ORDINARY",
            loss_kind="Business",
            amount_remaining=D("500000"),
            origin_ay_code="2024-25",
        ),
    )
    state = run_pipeline(
        income_facts=income,
        adjustment_facts=(),
        ruleset=ruleset,
        bf_losses=bf,
        book_profit_115jb=D("5000000"),
        available_mat_credit=ZERO,
        tax_depreciation_total=D("0"),
    )
    assert state.setoff_result is not None
    assert state.setoff_result.total_set_off == D("500000.00")
    assert state.mat_result is not None
    assert state.kernel_result is not None
    # taxable after setoff 15L → 25% = 3.75L + cess
    assert state.mat_result.normal_tax == state.kernel_result.tax_payable
