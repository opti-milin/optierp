"""Unit tests for order fulfillment helpers (no DB)."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.exceptions import ValidationError
from app.services.order_fulfillment import (
    FulfillmentLineResult,
    OrderFulfillmentResult,
    _dashboard_path,
    _margin_pct,
    raise_if_blocked,
)


def test_margin_pct():
    assert _margin_pct(Decimal("20"), Decimal("100")) == Decimal("20.00")
    assert _margin_pct(Decimal("0"), Decimal("0")) is None


def test_dashboard_path():
    oid = uuid4()
    assert _dashboard_path(context="sales-order", document_id=oid) == (
        f"/manufacturing-planning?context=sales-order&id={oid}"
    )
    assert _dashboard_path(context="item", document_id=None) == "/manufacturing-planning"


def test_raise_if_blocked_warn_mode_noop():
    result = OrderFulfillmentResult(
        mode="warn",
        can_fulfill_on_time=False,
        earliest_promise_date=date(2026, 8, 1),
        estimated_cost=Decimal("100"),
        estimated_selling_amount=Decimal("150"),
        estimated_margin=Decimal("50"),
        estimated_margin_pct=Decimal("33.33"),
        lines=[],
        warnings=["late"],
        hard_block_reasons=[],
        planning_dashboard_path="/manufacturing-planning",
    )
    raise_if_blocked(result)  # must not raise


def test_raise_if_blocked_block_mode():
    result = OrderFulfillmentResult(
        mode="block",
        can_fulfill_on_time=False,
        earliest_promise_date=date(2026, 8, 1),
        estimated_cost=Decimal("100"),
        estimated_selling_amount=Decimal("150"),
        estimated_margin=Decimal("50"),
        estimated_margin_pct=Decimal("33.33"),
        lines=[
            FulfillmentLineResult(
                item_id=uuid4(),
                item_code="FG",
                item_name="FG",
                qty=Decimal("10"),
                delivery_date=date(2026, 7, 25),
                warehouse_id=None,
                bom_id=None,
                bom_name=None,
                on_time=False,
                earliest_promise_date=date(2026, 8, 1),
                bom_cost_per_unit=Decimal("10"),
                estimated_cost=Decimal("100"),
                selling_amount=Decimal("150"),
                estimated_margin=Decimal("50"),
                estimated_margin_pct=Decimal("33.33"),
            )
        ],
        warnings=["FG: cannot meet delivery"],
        hard_block_reasons=["FG: cannot meet delivery"],
        planning_dashboard_path="/manufacturing-planning",
    )
    with pytest.raises(ValidationError) as exc:
        raise_if_blocked(result)
    assert exc.value.code == "FULFILLMENT_BLOCKED"
