"""Unit tests — Contribution Margin waterfall + template matching."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.services.cm_classification import list_cm_templates, match_cm_class
from app.services.financial_reports.contribution_margin import build_cm_waterfall


class _FakeAccount:
    def __init__(
        self,
        *,
        account_name: str,
        account_type: str | None = None,
        account_category: str | None = None,
        cm_class: str | None = None,
    ) -> None:
        self.account_name = account_name
        self.account_type = account_type
        self.account_category = account_category
        self.cm_class = cm_class


def test_list_cm_templates_includes_manufacturing() -> None:
    templates = list_cm_templates()
    ids = {t.id for t in templates}
    assert "manufacturing" in ids
    assert "retail" in ids
    assert "saas" in ids


def test_match_cm_class_first_rule_wins() -> None:
    rules = [
        {"match": {"account_category": "Revenue from Operations"}, "cm_class": "revenue"},
        {"match": {"account_type": "Cost of Goods Sold"}, "cm_class": "variable_cost"},
        {"match": {"name_contains": ["Rent", "Admin"]}, "cm_class": "corporate_overhead"},
    ]
    assert (
        match_cm_class(
            _FakeAccount(  # type: ignore[arg-type]
                account_name="Sales",
                account_category="Revenue from Operations",
            ),
            rules,
        )
        == "revenue"
    )
    assert (
        match_cm_class(
            _FakeAccount(  # type: ignore[arg-type]
                account_name="COGS",
                account_type="Cost of Goods Sold",
            ),
            rules,
        )
        == "variable_cost"
    )
    assert (
        match_cm_class(
            _FakeAccount(account_name="Office Rent"),  # type: ignore[arg-type]
            rules,
        )
        == "corporate_overhead"
    )
    assert (
        match_cm_class(
            _FakeAccount(account_name="Miscellaneous"),  # type: ignore[arg-type]
            rules,
        )
        is None
    )


def test_build_cm_waterfall_levels() -> None:
    rev_id = uuid.uuid4()
    var_id = uuid.uuid4()
    prod_id = uuid.uuid4()
    seg_id = uuid.uuid4()
    corp_id = uuid.uuid4()
    uncl_id = uuid.uuid4()

    meta = {
        rev_id: {
            "account_name": "Sales",
            "root_type": "Income",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "income.sales",
            "cm_class": "revenue",
        },
        var_id: {
            "account_name": "COGS",
            "root_type": "Expense",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "expense.cogs",
            "cm_class": "variable_cost",
        },
        prod_id: {
            "account_name": "Plant Depreciation",
            "root_type": "Expense",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "expense.depreciation",
            "cm_class": "product_channel_fixed",
        },
        seg_id: {
            "account_name": "Regional Marketing",
            "root_type": "Expense",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "expense.marketing",
            "cm_class": "segment_bu_fixed",
        },
        corp_id: {
            "account_name": "HQ Rent",
            "root_type": "Expense",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "expense.rent",
            "cm_class": "corporate_overhead",
        },
        uncl_id: {
            "account_name": "Misc Expense",
            "root_type": "Expense",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "expense.misc",
            "cm_class": None,
        },
    }
    # Revenue: credit 1000 → (debit 0, credit 1000)
    # Costs: debit amounts
    leaf_dc = {
        rev_id: (Decimal("0"), Decimal("1000")),
        var_id: (Decimal("400"), Decimal("0")),
        prod_id: (Decimal("100"), Decimal("0")),
        seg_id: (Decimal("50"), Decimal("0")),
        corp_id: (Decimal("75"), Decimal("0")),
        uncl_id: (Decimal("25"), Decimal("0")),
    }

    report = build_cm_waterfall(
        from_date=date(2026, 4, 1),
        to_date=date(2026, 6, 30),
        cost_center_id=None,
        account_meta=meta,
        leaf_dc=leaf_dc,
        unclassified_policy="bucket",
    )

    assert report.revenue == Decimal("1000")
    assert report.variable_cost == Decimal("400")
    assert report.cm1 == Decimal("600")
    assert report.product_channel_fixed == Decimal("100")
    assert report.cm2 == Decimal("500")
    assert report.segment_bu_fixed == Decimal("50")
    assert report.cm3 == Decimal("450")
    assert report.corporate_overhead == Decimal("75")
    assert report.operating_profit == Decimal("375")
    assert report.unclassified_total == Decimal("25")
    assert report.cm1_pct == Decimal("60.00")
    assert any(s.cm_class is None for s in report.sections)
    assert report.warnings


def test_build_cm_waterfall_error_on_unclassified() -> None:
    aid = uuid.uuid4()
    meta = {
        aid: {
            "account_name": "Mystery",
            "root_type": "Expense",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "expense.mystery",
            "cm_class": None,
        }
    }
    with pytest.raises(ValidationError) as exc:
        build_cm_waterfall(
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 31),
            cost_center_id=None,
            account_meta=meta,
            leaf_dc={aid: (Decimal("10"), Decimal("0"))},
            unclassified_policy="error",
        )
    assert exc.value.code == "CM_UNCLASSIFIED" or "Unclassified" in str(exc.value)


def test_build_cm_waterfall_ignores_balance_sheet() -> None:
    cash_id = uuid.uuid4()
    sales_id = uuid.uuid4()
    meta = {
        cash_id: {
            "account_name": "Cash",
            "root_type": "Asset",
            "report_type": "Balance Sheet",
            "is_group": False,
            "path": "asset.cash",
            "cm_class": None,
        },
        sales_id: {
            "account_name": "Sales",
            "root_type": "Income",
            "report_type": "Profit and Loss",
            "is_group": False,
            "path": "income.sales",
            "cm_class": "revenue",
        },
    }
    report = build_cm_waterfall(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 31),
        cost_center_id=None,
        account_meta=meta,
        leaf_dc={
            cash_id: (Decimal("500"), Decimal("0")),
            sales_id: (Decimal("0"), Decimal("200")),
        },
    )
    assert report.revenue == Decimal("200")
    assert report.unclassified_total == Decimal("0")
