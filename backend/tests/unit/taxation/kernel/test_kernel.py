"""Golden vectors and property tests for the pure taxation kernel."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.taxation.kernel import (
    ZERO,
    IncomeComponent,
    KernelInput,
    RateBand,
    RebateSpec,
    SurchargeBand,
    SurchargeSpec,
    compute,
    flat_bands,
    money,
    q,
    round_to_nearest_ten,
    tax_on_bands,
)

D = Decimal


def _bands(*rows: tuple[str, str | None, str]) -> tuple[RateBand, ...]:
    out: list[RateBand] = []
    for lo, hi, rate in rows:
        out.append(
            RateBand(
                lower=D(lo),
                upper=None if hi is None else D(hi),
                rate_percent=D(rate),
            )
        )
    return tuple(out)


# AY 2024-25 new regime (FA2023)
_BAC_2425 = _bands(
    ("0", "300000", "0"),
    ("300000", "600000", "5"),
    ("600000", "900000", "10"),
    ("900000", "1200000", "15"),
    ("1200000", "1500000", "20"),
    ("1500000", None, "30"),
)

# AY 2025-26 new regime (FA2024)
_BAC_2526 = _bands(
    ("0", "300000", "0"),
    ("300000", "700000", "5"),
    ("700000", "1000000", "10"),
    ("1000000", "1200000", "15"),
    ("1200000", "1500000", "20"),
    ("1500000", None, "30"),
)

_OLD_GENERAL = _bands(
    ("0", "250000", "0"),
    ("250000", "500000", "5"),
    ("500000", "1000000", "20"),
    ("1000000", None, "30"),
)

_IND_SUR = (
    SurchargeBand(D("0"), D("5000000"), D("0")),
    SurchargeBand(D("5000000"), D("10000000"), D("10")),
    SurchargeBand(D("10000000"), D("20000000"), D("15")),
    SurchargeBand(D("20000000"), D("50000000"), D("25")),
    SurchargeBand(D("50000000"), None, D("25")),
)


def test_reject_float_money() -> None:
    with pytest.raises(TypeError, match="float"):
        money(1.5)  # type: ignore[arg-type]


def test_round_half_up_not_bankers() -> None:
    # 0.005 → 0.01 under HALF_UP; banker's (HALF_EVEN) would give 0.00
    assert q("0.005") == D("0.01")
    assert q("1.225") == D("1.23")


def test_288a_288b_nearest_ten() -> None:
    assert round_to_nearest_ten("1234") == D("1230")
    assert round_to_nearest_ten("1235") == D("1240")
    assert round_to_nearest_ten("1236") == D("1240")


def test_corporate_flat_25_cess() -> None:
    result = compute(
        KernelInput(
            components=(
                IncomeComponent("ORDINARY", D("1050000"), flat_bands("25")),
            ),
            cess_rate_percent=D("4"),
            apply_288a=True,
            apply_288b=True,
        )
    )
    assert result.tax_before_rebate == D("262500.00")
    assert result.cess_amount == D("10500.00")
    assert result.total_tax == D("273000.00")
    assert result.tax_payable == D("273000")


def test_old_regime_slab_12l() -> None:
    assert tax_on_bands(D("1200000"), _OLD_GENERAL) == D("172500.00")


def test_87a_wipes_tax_ay2425() -> None:
    # 6.5L under FA2023 new regime → tax 20_000, fully rebated
    result = compute(
        KernelInput(
            components=(IncomeComponent("ORDINARY", D("650000"), _BAC_2425),),
            rebate=RebateSpec(
                max_taxable_income=D("700000"),
                max_rebate_amount=D("25000"),
                marginal_relief_enabled=True,
            ),
            cess_rate_percent=D("4"),
        )
    )
    assert result.tax_before_rebate == D("20000.00")
    assert result.rebate_amount == D("20000.00")
    assert result.tax_after_rebate == ZERO
    assert result.total_tax == ZERO


def test_87a_wipes_tax_ay2526() -> None:
    result = compute(
        KernelInput(
            components=(IncomeComponent("ORDINARY", D("650000"), _BAC_2526),),
            rebate=RebateSpec(
                max_taxable_income=D("700000"),
                max_rebate_amount=D("25000"),
                marginal_relief_enabled=True,
            ),
            cess_rate_percent=D("4"),
        )
    )
    assert result.tax_before_rebate == D("17500.00")
    assert result.rebate_amount == D("17500.00")
    assert result.tax_payable == ZERO


def test_87a_marginal_relief_above_ceiling() -> None:
    # Income just above 7L: tax must not exceed (income - 7L)
    income = D("710000")
    result = compute(
        KernelInput(
            components=(IncomeComponent("ORDINARY", income, _BAC_2526),),
            rebate=RebateSpec(
                max_taxable_income=D("700000"),
                max_rebate_amount=D("25000"),
                marginal_relief_enabled=True,
            ),
            cess_rate_percent=D("0"),  # isolate rebate effect
            apply_288b=False,
        )
    )
    # Tax after rebate ≤ excess over ceiling
    assert result.tax_after_rebate <= (income - D("700000"))
    assert result.rebate_amount > ZERO


def test_rebate_excludes_ltcg_112a() -> None:
    result = compute(
        KernelInput(
            components=(
                IncomeComponent("ORDINARY", D("500000"), _BAC_2526, rebate_eligible=True),
                IncomeComponent(
                    "LTCG_112A",
                    D("200000"),
                    flat_bands("12.5"),
                    surcharge_cap_percent=D("15"),
                    rebate_eligible=False,
                ),
            ),
            rebate=RebateSpec(
                max_taxable_income=D("700000"),
                max_rebate_amount=D("25000"),
                excluded_characters=frozenset({"LTCG_112A"}),
            ),
            cess_rate_percent=D("0"),
            apply_288b=False,
        )
    )
    # Ordinary at 5L under 2526: 0-3 nil, 3-5 @5% = 10_000 → fully rebated
    # LTCG 2L @ 12.5% = 25_000 remains
    assert result.rebate_amount == D("10000.00")
    assert result.tax_after_rebate == D("25000.00")


def test_surcharge_cap_15_on_ltcg() -> None:
    # Total income > 5Cr → schedule rate 25%, but LTCG capped at 15%
    ordinary = IncomeComponent("ORDINARY", D("1000000"), flat_bands("30"))
    ltcg = IncomeComponent(
        "LTCG_112A",
        D("60000000"),
        flat_bands("12.5"),
        surcharge_cap_percent=D("15"),
        rebate_eligible=False,
    )
    result = compute(
        KernelInput(
            components=(ordinary, ltcg),
            surcharge=SurchargeSpec(
                bands=_IND_SUR,
                marginal_relief=False,
                capped_characters=frozenset({"LTCG_112A"}),
            ),
            cess_rate_percent=D("0"),
            apply_288a=False,
            apply_288b=False,
        )
    )
    ordinary_tax = D("300000.00")  # 1L * 30%
    ltcg_tax = D("7500000.00")  # 6Cr * 12.5%
    # Schedule rate at 6.1Cr is 25%; LTCG capped at 15%
    expected_sur = q(ordinary_tax * D("0.25") + ltcg_tax * D("0.15"))
    assert result.surcharge_before_relief == expected_sur
    assert result.surcharge_amount == expected_sur


def test_marginal_relief_rerun_not_proportional() -> None:
    # Flat 30%, surcharge 10% above 50L, income just over threshold
    income = D("5000100")
    result = compute(
        KernelInput(
            components=(IncomeComponent("ORDINARY", income, flat_bands("30")),),
            surcharge=SurchargeSpec(bands=_IND_SUR, marginal_relief=True),
            cess_rate_percent=D("0"),
            apply_288a=False,
            apply_288b=False,
        )
    )
    assert result.surcharge_before_relief > ZERO
    assert result.marginal_relief_amount > ZERO
    assert result.surcharge_amount < result.surcharge_before_relief
    # Liability ≤ tax_at_50L + excess
    tax_at_50l = tax_on_bands(D("5000000"), flat_bands("30"))
    cap = tax_at_50l + (income - D("5000000"))
    assert result.tax_after_rebate + result.surcharge_amount <= cap


def test_post_tax_income_monotonic_across_surcharge_threshold() -> None:
    """Property: income − tax_payable must not fall as pre-tax income rises."""
    prev_net: Decimal | None = None
    # Sweep across the 50L surcharge threshold where the old proportional formula failed
    for rupees in range(4_990_000, 5_050_000, 1_000):
        income = D(rupees)
        result = compute(
            KernelInput(
                components=(IncomeComponent("ORDINARY", income, flat_bands("30")),),
                surcharge=SurchargeSpec(bands=_IND_SUR, marginal_relief=True),
                cess_rate_percent=D("4"),
                apply_288a=True,
                apply_288b=True,
            )
        )
        net = income - result.tax_payable
        if prev_net is not None:
            assert net >= prev_net, f"non-monotonic at {income}: {net} < {prev_net}"
        prev_net = net


def test_company_surcharge_7_and_12_brackets() -> None:
    co_sur = (
        SurchargeBand(D("0"), D("10000000"), D("0")),
        SurchargeBand(D("10000000"), D("100000000"), D("7")),
        SurchargeBand(D("100000000"), None, D("12")),
    )
    mid = compute(
        KernelInput(
            components=(IncomeComponent("ORDINARY", D("15000000"), flat_bands("25")),),
            surcharge=SurchargeSpec(bands=co_sur, marginal_relief=True),
            cess_rate_percent=D("4"),
            apply_288a=False,
            apply_288b=False,
        )
    )
    assert mid.surcharge_before_relief == q(D("3750000") * D("0.07"))  # tax 25% of 1.5Cr

    top = compute(
        KernelInput(
            components=(IncomeComponent("ORDINARY", D("150000000"), flat_bands("25")),),
            surcharge=SurchargeSpec(bands=co_sur, marginal_relief=False),
            cess_rate_percent=D("4"),
            apply_288a=False,
            apply_288b=False,
        )
    )
    assert top.surcharge_before_relief == q(D("37500000") * D("0.12"))
