"""Unit tests for Phase 7.0/7.1 lead-time helpers (no DB)."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.services.lead_time import (
    LeadTimeComponentRow,
    LeadTimeEstimate,
    ProcurementSuggestion,
    ReverseSchedule,
    _days_from_operation_mins,
)


def test_days_from_operation_mins_ceil():
    assert _days_from_operation_mins(Decimal("0")) == 0
    assert _days_from_operation_mins(Decimal("1")) == 1  # any positive mins → at least 1 day
    assert _days_from_operation_mins(Decimal("480")) == 1  # exactly 8h
    assert _days_from_operation_mins(Decimal("481")) == 2
    assert _days_from_operation_mins(Decimal("960")) == 2  # 16h
    assert _days_from_operation_mins(Decimal("1440")) == 3  # 24h


def test_reverse_schedule_math_on_time():
    """Delivery far enough out → on_time with positive slack; order dates = start − lead."""
    as_of = date(2026, 7, 1)
    delivery = date(2026, 7, 20)
    # forward: procurement 5 + mfg 1 = 6 → earliest 7/7; slack = 13
    manufacturing_days = 1
    earliest = as_of + timedelta(days=6)
    mfg_start = delivery - timedelta(days=manufacturing_days)
    assert mfg_start == date(2026, 7, 19)
    assert (delivery - earliest).days == 13
    latest_order = mfg_start - timedelta(days=5)
    assert latest_order == date(2026, 7, 14)
    assert (latest_order - as_of).days == 13


def test_reverse_schedule_dataclass_shape():
    item_id = uuid4()
    comp_id = uuid4()
    as_of = date(2026, 7, 1)
    delivery = date(2026, 7, 10)
    row = LeadTimeComponentRow(
        item_id=comp_id,
        item_code="RAW",
        item_name="Raw",
        required_qty=Decimal("10"),
        available_qty=Decimal("0"),
        shortfall_qty=Decimal("10"),
        lead_time_days=5,
        drives_wait=True,
    )
    plan = ReverseSchedule(
        item_id=item_id,
        item_code="FG",
        item_name="FG",
        bom_id=None,
        bom_name=None,
        qty=Decimal("5"),
        as_of=as_of,
        delivery_date=delivery,
        warehouse_id=None,
        procurement_days=5,
        manufacturing_days=1,
        outbound_days=0,
        total_days=6,
        earliest_promise_date=date(2026, 7, 7),
        ready_to_dispatch_date=date(2026, 7, 7),
        manufacturing_start_date=date(2026, 7, 9),
        materials_ready_by=date(2026, 7, 9),
        on_time=True,
        slack_days=3,
        operation_mins=Decimal("48"),
        procurement=[
            ProcurementSuggestion(
                item_id=comp_id,
                item_code="RAW",
                item_name="Raw",
                shortfall_qty=Decimal("10"),
                lead_time_days=5,
                latest_order_date=date(2026, 7, 4),
                days_until_order=3,
            )
        ],
        components=[row],
        notes=[],
    )
    assert plan.on_time is True
    assert plan.procurement[0].latest_order_date == date(2026, 7, 4)
    assert LeadTimeEstimate.__name__ == "LeadTimeEstimate"
