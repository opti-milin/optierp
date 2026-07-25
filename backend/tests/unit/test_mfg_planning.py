"""Unit tests for Phase 7.3 planning helpers (no DB)."""

from datetime import date
from decimal import Decimal

from app.services.mfg_planning import _add_months, _month_start, _period_key


def test_month_helpers():
    assert _month_start(date(2026, 7, 22)) == date(2026, 7, 1)
    assert _add_months(date(2026, 7, 1), 1) == date(2026, 8, 1)
    assert _add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert _add_months(date(2025, 12, 1), 2) == date(2026, 2, 1)
    assert _period_key(date(2026, 7, 22)) == "2026-07"


def test_forecast_row_avg_math():
    """Non-zero months average — mirrors demand_forecast aggregation."""
    hist = [Decimal("10"), Decimal("0"), Decimal("20")]
    nonzero = [v for v in hist if v > 0]
    avg = (sum(nonzero, Decimal("0")) / Decimal(len(nonzero))).quantize(Decimal("0.01"))
    assert avg == Decimal("15.00")
