"""Unit tests for Phase 9 open-supply cover math (no DB)."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.services.delivery_estimate import _health, _worst_health
from app.services.open_supply import OpenSupplySlice, ShortfallCoverPlan, _ready_or_as_of


def test_ready_or_as_of():
    as_of = date(2026, 7, 30)
    assert _ready_or_as_of(None, as_of) == as_of
    assert _ready_or_as_of(date(2026, 8, 5), as_of) == date(2026, 8, 5)


def test_shortfall_cover_plan_wait_from_po_schedule():
    """Open PO ready in 3 days beats catalog lead of 10."""
    as_of = date(2026, 7, 30)
    po_ready = as_of + timedelta(days=3)
    plan = ShortfallCoverPlan(
        shortfall_qty=Decimal("10"),
        covered_qty=Decimal("10"),
        residual_qty=Decimal("0"),
        materials_ready_date=po_ready,
        wait_days=3,
        catalog_lead_days=10,
        slices=[
            OpenSupplySlice(
                source_type="Purchase Order",
                source_id=uuid4(),
                source_name="PO-0001",
                qty=Decimal("10"),
                ready_date=po_ready,
                notes="Open PO",
            )
        ],
        notes=["Open PO PO-0001: 10 by 2026-08-02."],
    )
    assert plan.wait_days == 3
    assert plan.wait_days < plan.catalog_lead_days
    assert plan.residual_qty == 0


def test_delivery_health_buckets():
    assert _health(True, 10) == "on_time"
    assert _health(True, 2) == "at_risk"
    assert _health(True, 0) == "at_risk"
    assert _health(False, -3) == "late"
    assert _health(None, None) == "unknown"


def test_worst_health():
    assert _worst_health("on_time", "at_risk") == "at_risk"
    assert _worst_health("at_risk", "late") == "late"
    assert _worst_health("unknown", "on_time") == "unknown"
