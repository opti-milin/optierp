"""Unit tests for CM planning waterfall, min price, and scenario diffs."""

from decimal import Decimal

from app.services.cm_planning.waterfall import (
    EstimateInput,
    LineEstimateIn,
    build_waterfall,
    diff_waterfalls,
    estimate_from_lines,
    min_selling_from_variable,
)

D = Decimal


def test_build_waterfall_basic() -> None:
    w = build_waterfall(
        revenue=D("1000"),
        material=D("400"),
        labor=D("100"),
        freight=D("40"),
        commission=D("50"),
        packaging=D("10"),
        product_channel_fixed=D("30"),
        segment_bu_fixed=D("20"),
        corporate_overhead=D("50"),
        target_cm1_pct=D("35"),
    )
    assert w.variable_cost == D("600.00")
    assert w.cm1 == D("400.00")
    assert w.cm2 == D("370.00")
    assert w.cm3 == D("350.00")
    assert w.operating_profit == D("300.00")
    assert w.cm1_pct == D("40.00")
    # min sell for V=600 at 35% CM1 → 600/0.65
    assert w.min_selling_total == D("923.08")


def test_price_1000_vs_950_scenario() -> None:
    base = estimate_from_lines(
        EstimateInput(
            lines=[
                LineEstimateIn(
                    line_key="1",
                    item_id=None,
                    qty=D("1"),
                    selling_rate=D("1000"),
                    material_per_unit=D("400"),
                )
            ],
            freight_total=D("40"),
            target_cm1_pct=D("35"),
        )
    )[0]
    alt = estimate_from_lines(
        EstimateInput(
            lines=[
                LineEstimateIn(
                    line_key="1",
                    item_id=None,
                    qty=D("1"),
                    selling_rate=D("950"),
                    material_per_unit=D("400"),
                )
            ],
            freight_total=D("40"),
            target_cm1_pct=D("35"),
        )
    )[0]
    assert base.cm1 == D("560.00")
    assert alt.cm1 == D("510.00")
    diff = diff_waterfalls(base, alt)
    cm1_row = next(r for r in diff["rows"] if r["field"] == "cm1")
    assert cm1_row["delta"] == D("-50.00")


def test_freight_40_vs_70() -> None:
    base = build_waterfall(revenue=D("1000"), material=D("400"), freight=D("40"))
    alt = build_waterfall(revenue=D("1000"), material=D("400"), freight=D("70"))
    assert alt.cm1 == base.cm1 - D("30.00")


def test_material_plus_10_percent() -> None:
    base_in = EstimateInput(
        lines=[
            LineEstimateIn(
                line_key="1", item_id=None, qty=D("100"), selling_rate=D("10"), material_per_unit=D("4")
            )
        ],
        material_cost_factor=D("1"),
    )
    alt_in = EstimateInput(
        lines=base_in.lines,
        material_cost_factor=D("1.10"),
    )
    base, _details, _eng = estimate_from_lines(base_in)
    alt, _details2, _eng2 = estimate_from_lines(alt_in)
    assert base.material == D("400.00")
    assert alt.material == D("440.00")
    assert alt.cm1 == base.cm1 - D("40.00")


def test_qty_100_vs_500() -> None:
    def mk(q: Decimal) -> EstimateInput:
        return EstimateInput(
            lines=[
                LineEstimateIn(
                    line_key="1", item_id=None, qty=q, selling_rate=D("10"), material_per_unit=D("4")
                )
            ],
            freight_total=D("40"),
        )

    b, _d, _e = estimate_from_lines(mk(D("100")))
    a, _d2, _e2 = estimate_from_lines(mk(D("500")))
    assert b.revenue == D("1000.00")
    assert a.revenue == D("5000.00")
    assert a.material == D("2000.00")
    # freight is fixed absolute in this fixture
    assert b.freight == a.freight == D("40.00")


def test_min_selling_from_variable() -> None:
    assert min_selling_from_variable(D("600"), D("35")) == D("923.08")
    assert min_selling_from_variable(D("0"), D("35")) == D("0")
    assert min_selling_from_variable(D("100"), D("100")) is None


def test_scenario_overrides_are_json_serializable() -> None:
    import json

    from app.schemas.cm_planning import CmScenarioOverrides
    from app.services.cm_planning.plan import _merge_overrides

    merged = _merge_overrides(
        {"material_cost_factor": D("1.05")},
        CmScenarioOverrides(material_cost_factor=D("1.10"), freight_amount=D("70")),
    )
    json.dumps(merged)
    assert merged["material_cost_factor"] == "1.10"
    assert merged["freight_amount"] == "70"


def test_header_selling_rate_in_overrides_schema() -> None:
    from app.schemas.cm_planning import CmScenarioOverrides
    from app.services.cm_planning.plan import _merge_overrides

    merged = _merge_overrides({}, CmScenarioOverrides(selling_rate=D("1500"), qty=D("20")))
    assert merged["selling_rate"] == "1500"
    assert merged["qty"] == "20"
    # empty items must not be stored
    assert "items" not in merged


def test_compare_to_csv() -> None:
    import uuid

    from app.schemas.cm_planning import CmCompareResponse, CmCompareScenarioColumn
    from app.services.cm_planning.plan import compare_to_csv

    a = uuid.uuid4()
    b = uuid.uuid4()
    csv_text = compare_to_csv(
        CmCompareResponse(
            plan_id=uuid.uuid4(),
            columns=[
                CmCompareScenarioColumn(
                    scenario_id=a,
                    name="Baseline",
                    is_baseline=True,
                    revenue=D("18000"),
                    variable_cost=D("20960"),
                    cm1=D("-2960"),
                    cm2=D("-3500"),
                    cm3=D("-3860"),
                    operating_profit=D("-4760"),
                    cm1_pct=D("-16.44"),
                    min_selling_total=D("32246.15"),
                ),
                CmCompareScenarioColumn(
                    scenario_id=b,
                    name="SP 1500",
                    is_baseline=False,
                    revenue=D("30000"),
                    variable_cost=D("20960"),
                    cm1=D("9040"),
                    cm2=D("8140"),
                    cm3=D("7540"),
                    operating_profit=D("6040"),
                    cm1_pct=D("30.13"),
                    min_selling_total=D("32246.15"),
                ),
            ],
        )
    )
    assert "metric,Baseline,SP 1500" in csv_text
    assert "revenue,18000,30000" in csv_text
    assert "cm1,-2960,9040" in csv_text


def test_evaluate_margin_policy_block() -> None:
    import pytest

    from app.core.exceptions import ValidationError
    from app.services.cm_planning.plan import evaluate_margin_policy

    warn = evaluate_margin_policy(
        policy="warn", cm1_pct=D("10"), min_cm1_pct=D("15"), raise_on_block=True
    )
    assert warn.ok is False
    assert warn.message is not None

    with pytest.raises(ValidationError) as exc:
        evaluate_margin_policy(
            policy="block", cm1_pct=D("10"), min_cm1_pct=D("15"), raise_on_block=True
        )
    assert exc.value.code == "CM_MARGIN_BLOCK"

    ok = evaluate_margin_policy(
        policy="block", cm1_pct=D("20"), min_cm1_pct=D("15"), raise_on_block=True
    )
    assert ok.ok is True


def test_build_actual_comparison_rows() -> None:
    from app.services.cm_planning.plan import build_actual_comparison_rows

    rows = build_actual_comparison_rows(
        {
            "revenue": D("1000"),
            "variable_cost": D("600"),
            "product_channel_fixed": D("30"),
            "segment_bu_fixed": D("20"),
            "corporate_overhead": D("50"),
            "cm1": D("400"),
            "cm2": D("370"),
            "cm3": D("350"),
            "operating_profit": D("300"),
        },
        {
            "revenue": D("1200"),
            "variable_cost": D("700"),
            "product_channel_fixed": D("40"),
            "segment_bu_fixed": D("25"),
            "corporate_overhead": D("60"),
            "cm1": D("500"),
            "cm2": D("460"),
            "cm3": D("435"),
            "operating_profit": D("375"),
        },
    )
    by_field = {r.field: r for r in rows}
    assert by_field["revenue"].delta == D("200.00")
    assert by_field["revenue"].delta_pct == D("20.00")
    assert by_field["cm1"].estimated == D("400.00")
    assert by_field["cm1"].actual == D("500.00")
    assert by_field["operating_profit"].delta == D("75.00")


def test_item_rows_to_lines_preserves_rates() -> None:
    from types import SimpleNamespace

    from app.services.cm_planning.plan import _item_rows_to_lines

    lines = _item_rows_to_lines(
        [
            SimpleNamespace(
                line_key="1",
                item_id=None,
                item_code="FG",
                item_name="Gearbox",
                qty=D("20"),
                selling_rate=D("1800"),
                quotation_item_id=None,
                sales_order_item_id=None,
            )
        ]
    )
    assert lines[0]["selling_rate"] == D("1800")
    assert lines[0]["qty"] == D("20")


def test_recalculate_quotation_totals_updates_header() -> None:
    """Apply-to-quotation must refresh header net/grand, not only line rate."""
    from types import SimpleNamespace

    from app.services.cm_planning.plan import _recalculate_quotation_totals

    item = SimpleNamespace(
        qty=D("20"),
        rate=D("2000"),
        price_list_rate=D("2000"),
        discount_percentage=D("0"),
        discount_amount=D("0"),
        amount=None,
        base_rate=None,
        base_amount=None,
        net_amount=None,
        base_net_amount=None,
        base_price_list_rate=None,
    )
    tax = SimpleNamespace(
        charge_type="On Net Total",
        rate=D("18"),
        tax_amount=D("0"),
        row_id=None,
        account_head_id=None,
        included_in_print_rate=False,
        total=None,
        base_tax_amount=None,
        base_total=None,
    )
    qtn = SimpleNamespace(
        items=[item],
        taxes=[tax],
        conversion_rate=D("1"),
        apply_discount_on="Grand Total",
        additional_discount_percentage=D("0"),
        discount_amount=D("0"),
        total_qty=None,
        total=None,
        base_total=None,
        net_total=None,
        base_net_total=None,
        total_taxes_and_charges=None,
        base_total_taxes_and_charges=None,
        grand_total=None,
        base_grand_total=None,
        rounded_total=None,
        rounding_adjustment=None,
    )
    _recalculate_quotation_totals(qtn)  # type: ignore[arg-type]
    assert qtn.net_total == D("40000.00")
    assert qtn.total_taxes_and_charges == D("7200.00")
    assert qtn.grand_total == D("47200.00")
    assert item.amount == D("40000.00")
    assert tax.tax_amount == D("7200.00")
