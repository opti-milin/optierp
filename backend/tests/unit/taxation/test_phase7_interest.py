"""Phase 7 — 234A/B/C interest and advance-tax projection."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.taxation.kernel.interest_234 import (
    AdvanceInstalment,
    ChallanPayment,
    Interest234Input,
    compute_interest_234,
    months_or_part,
    project_advance_tax,
)

D = Decimal


def test_months_or_part_full_and_partial() -> None:
    assert months_or_part(date(2025, 10, 31), date(2025, 10, 31)) == 0
    assert months_or_part(date(2025, 10, 31), date(2025, 11, 1)) == 1
    assert months_or_part(date(2025, 10, 31), date(2025, 12, 15)) == 2


def test_234c_shortfall_on_q1() -> None:
    instalments = (
        AdvanceInstalment(1, date(2024, 6, 15), D("15"), "ADV_Q1", "15 June"),
        AdvanceInstalment(2, date(2024, 9, 15), D("45"), "ADV_Q2", "15 Sep"),
        AdvanceInstalment(3, date(2024, 12, 15), D("75"), "ADV_Q3", "15 Dec"),
        AdvanceInstalment(4, date(2025, 3, 15), D("100"), "ADV_Q4", "15 Mar"),
    )
    # Assessed 10L, no advance paid → 234C on each instalment
    result = compute_interest_234(
        Interest234Input(
            assessed_tax=D("1000000"),
            instalments=instalments,
            ay_start=date(2025, 4, 1),
            as_of_date=date(2025, 4, 30),
            return_filed_date=date(2025, 10, 31),
            itr_due_date=date(2025, 10, 31),
        )
    )
    assert result.interest_234c > 0
    # Q1 shortfall 1.5L × 1% × 3 months = 4500
    assert result.breakdown["234c"][0]["interest"] == "4500.00"


def test_234b_when_advance_under_90_percent() -> None:
    payments = (
        ChallanPayment(date(2025, 3, 15), D("50000"), "AdvanceTax"),
    )
    result = compute_interest_234(
        Interest234Input(
            assessed_tax=D("1000000"),
            advance_payments=payments,
            ay_start=date(2025, 4, 1),
            return_filed_date=date(2025, 7, 31),
            as_of_date=date(2025, 7, 31),
        )
    )
    assert result.interest_234b > 0
    assert result.advance_tax_paid == D("50000.00")


def test_no_interest_below_threshold() -> None:
    result = compute_interest_234(
        Interest234Input(
            assessed_tax=D("5000"),
            instalments=(
                AdvanceInstalment(1, date(2024, 6, 15), D("15")),
            ),
            ay_start=date(2025, 4, 1),
        )
    )
    assert result.interest_234b == 0
    assert result.interest_234c == 0


def test_advance_tax_projection_shortfall() -> None:
    instalments = (
        AdvanceInstalment(1, date(2024, 6, 15), D("15"), "ADV_Q1", "Q1"),
        AdvanceInstalment(2, date(2024, 9, 15), D("45"), "ADV_Q2", "Q2"),
    )
    payments = (ChallanPayment(date(2024, 6, 10), D("10000"), "AdvanceTax"),)
    rows = project_advance_tax(
        estimated_tax=D("1000000"),
        tds_tcs_credit=D("0"),
        payments=payments,
        instalments=instalments,
        as_of=date(2024, 8, 1),
    )
    assert rows[0].required_cumulative == D("150000.00")
    assert rows[0].shortfall == D("140000.00")
    assert rows[0].status == "Overdue"
