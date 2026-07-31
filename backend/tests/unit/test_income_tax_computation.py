"""Unit: income-tax computation math + ITR mapping helpers (pure, no DB)."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.schemas.compliance import IncomeTaxSettings
from app.services.income_tax_computation import (
    compute_tax_pack,
    individual_heads_base,
    net_adjustments_of,
    tax_from_slabs,
)
from app.services.income_tax_engine.context import (
    ResolvedTaxRules,
    SpecialIncomeInput,
    SlabBand,
    SurchargeBand,
    TaxEngineInput,
    ZERO,
)
from app.services.income_tax_engine import run_pipeline
from app.services.itr_export import advance_tax_instalments, entity_form_for, form_for_computation

D = Decimal


def test_net_adjustments_add_and_deduct():
    assert net_adjustments_of([("Add", D("100")), ("Deduct", D("40"))]) == D("60.00")


def test_net_adjustments_rejects_bad_direction():
    with pytest.raises(ValidationError):
        net_adjustments_of([("Maybe", D("10"))])


def test_compute_tax_pack_basic_corporate():
    pack = compute_tax_pack(
        book_profit=D("1000000"),
        adjustments=[("Add", D("50000"))],
        tax_rate=D("25"),
        surcharge_rate=D("0"),
        cess_rate=D("4"),
        tds_credit=D("10000"),
        advance_tax_paid=D("50000"),
    )
    assert pack.net_adjustments == D("50000.00")
    assert pack.taxable_income == D("1050000.00")
    assert pack.tax_amount == D("262500.00")
    assert pack.surcharge_amount == D("0.00")
    assert pack.cess_amount == D("10500.00")
    assert pack.total_tax == D("273000.00")
    assert pack.tax_payable == D("213000.00")


def test_compute_tax_pack_loss_year_zero_tax():
    pack = compute_tax_pack(
        book_profit=D("-100000"),
        adjustments=[("Deduct", D("20000"))],
        tax_rate=D("25"),
        surcharge_rate=D("7"),
        cess_rate=D("4"),
        tds_credit=D("0"),
        advance_tax_paid=D("0"),
    )
    assert pack.taxable_income == D("-120000.00")
    assert pack.tax_amount == D("0.00")
    assert pack.total_tax == D("0.00")
    assert pack.tax_payable == D("0.00")


def test_compute_tax_pack_refundable_when_credits_exceed():
    pack = compute_tax_pack(
        book_profit=D("100000"),
        adjustments=[],
        tax_rate=D("25"),
        surcharge_rate=D("0"),
        cess_rate=D("0"),
        tds_credit=D("30000"),
        advance_tax_paid=D("0"),
    )
    assert pack.tax_amount == D("25000.00")
    assert pack.tax_payable == D("-5000.00")


def test_tax_from_slabs_progressive():
    tax = tax_from_slabs(
        D("1200000"),
        [
            (D("0"), D("250000"), D("0")),
            (D("250000"), D("500000"), D("5")),
            (D("500000"), D("1000000"), D("20")),
            (D("1000000"), None, D("30")),
        ],
    )
    assert tax == D("172500.00")


def test_compute_tax_pack_with_slabs_and_87a():
    pack = compute_tax_pack(
        book_profit=D("650000"),
        adjustments=[],
        tax_rate=D("0"),
        surcharge_rate=D("0"),
        cess_rate=D("4"),
        tds_credit=D("0"),
        advance_tax_paid=D("0"),
        slabs=[
            (D("0"), D("300000"), D("0")),
            (D("300000"), D("700000"), D("5")),
            (D("700000"), D("1000000"), D("10")),
        ],
        filing_regime="New",
        apply_rebate_87a=True,
    )
    assert pack.tax_amount == D("0.00")
    assert pack.rebate_87a == D("17500.00")
    assert pack.total_tax == D("0.00")


def test_engine_threshold_surcharge_and_marginal_relief():
    rules = ResolvedTaxRules(
        policy_id=None,
        computation_method="FlatRate",
        ordinary_method="FlatRate",
        filing_regime="Normal",
        entity_type="Individual",
        flat_tax_rate=D("30"),
        surcharge_brackets=[
            SurchargeBand(income_from=D("0"), income_to=D("5000000"), rate_percent=D("0")),
            SurchargeBand(income_from=D("5000000"), income_to=None, rate_percent=D("10")),
        ],
        marginal_relief_enabled=True,
        cess_rate=D("0"),
    )
    # Just above 50L: surcharge with marginal relief should be capped
    result = run_pipeline(
        TaxEngineInput(book_profit=D("5000100"), adjustments=[]),
        rules,
    )
    assert result.surcharge_before_relief > ZERO
    assert result.marginal_relief_amount > ZERO
    assert result.surcharge_amount < result.surcharge_before_relief


def test_engine_special_rate_rule_based():
    rules = ResolvedTaxRules(
        policy_id=None,
        computation_method="RuleBased",
        ordinary_method="FlatRate",
        filing_regime="Normal",
        entity_type="Company",
        flat_tax_rate=D("25"),
        cess_rate=D("4"),
    )
    result = run_pipeline(
        TaxEngineInput(
            book_profit=D("1000000"),
            adjustments=[],
            special_income=[
                SpecialIncomeInput(
                    income_category_code="LOTTERY",
                    amount=D("100000"),
                    rate_percent=D("30"),
                )
            ],
        ),
        rules,
    )
    # Ordinary 900k @ 25% = 225000; special 100k @ 30% = 30000;
    # cess 4% on (225000 + 30000) = 10200; total = 265200
    assert result.special_tax == D("30000.00")
    assert result.ordinary_income == D("900000.00")
    assert result.tax_amount == D("225000.00")
    assert result.cess_amount == D("10200.00")
    assert result.total_tax == D("265200.00")


def test_engine_special_rate_additive_heads():
    """IndividualHeads: special lines are additive, not carved from salary/heads."""
    rules = ResolvedTaxRules(
        policy_id=None,
        computation_method="RuleBased",
        ordinary_method="SlabBased",
        filing_regime="New",
        entity_type="Individual",
        slabs=[
            SlabBand(from_amount=D("0"), to_amount=D("300000"), rate_percent=D("0")),
            SlabBand(from_amount=D("300000"), to_amount=D("700000"), rate_percent=D("5")),
        ],
        cess_rate=D("4"),
        rebate_section="87A",
        rebate_max_taxable=D("700000"),
        rebate_max_amount=D("1000"),
    )
    result = run_pipeline(
        TaxEngineInput(
            book_profit=D("600000"),
            adjustments=[],
            special_income=[
                SpecialIncomeInput(
                    income_category_code="LOTTERY",
                    amount=D("100000"),
                    rate_percent=D("30"),
                )
            ],
            special_income_in_book=False,
        ),
        rules,
    )
    # Ordinary on full 600k: 5% of 300k = 15000 − rebate 1000 = 14000
    # Special 100k @ 30% = 30000; cess 4% of 44000 = 1760; total = 45760
    assert result.ordinary_income == D("600000.00")
    assert result.taxable_income == D("700000.00")
    assert result.tax_before_rebate == D("15000.00")
    assert result.rebate_amount == D("1000.00")
    assert result.tax_amount == D("14000.00")
    assert result.special_tax == D("30000.00")
    assert result.cess_amount == D("1760.00")
    assert result.total_tax == D("45760.00")


def test_engine_rebate_from_fixture_rules():
    rules = ResolvedTaxRules(
        policy_id=None,
        computation_method="SlabBased",
        ordinary_method="SlabBased",
        filing_regime="New",
        entity_type="Individual",
        slabs=[
            SlabBand(from_amount=D("0"), to_amount=D("300000"), rate_percent=D("0")),
            SlabBand(from_amount=D("300000"), to_amount=D("700000"), rate_percent=D("5")),
        ],
        cess_rate=D("0"),
        rebate_section="87A",
        rebate_max_taxable=D("700000"),
        rebate_max_amount=D("25000"),
    )
    result = run_pipeline(
        TaxEngineInput(book_profit=D("650000"), adjustments=[]),
        rules,
    )
    assert result.rebate_amount == D("17500.00")
    assert result.tax_amount == D("0.00")


def test_individual_heads_base():
    base = individual_heads_base(
        salary_income=D("900000"),
        house_property_income=D("-50000"),
        other_sources_income=D("20000"),
        capital_gains_income=D("10000"),
        chapter_via_deduction=D("50000"),
        standard_deduction=D("50000"),
    )
    assert base == D("780000.00")


def test_advance_tax_instalments_ay_2025_26():
    rows = advance_tax_instalments("2025-26")
    assert [r["due_date"] for r in rows] == [
        date(2024, 6, 15),
        date(2024, 9, 15),
        date(2024, 12, 15),
        date(2025, 3, 15),
    ]
    assert [r["cumulative_percent"] for r in rows] == [15, 45, 75, 100]


def test_entity_form_mapping():
    assert entity_form_for(IncomeTaxSettings(entity_type="Company")) == "ITR-6"
    assert entity_form_for(IncomeTaxSettings(entity_type="Proprietor")) == "ITR-3"
    assert entity_form_for(IncomeTaxSettings(entity_type="Individual")) == "ITR-1"
    assert entity_form_for(IncomeTaxSettings(entity_type="LLP")) == "ITR-5"


def test_form_for_computation_mapping():
    settings = IncomeTaxSettings(entity_type="Individual")
    assert form_for_computation(settings, "IndividualHeads") == "ITR-1"
    assert form_for_computation(settings, "EntityBooks") == "ITR-3"


@pytest.mark.asyncio
async def test_itr_efile_null_and_sandbox():
    from app.services import itr_efile

    null = itr_efile.get_provider(IncomeTaxSettings(itr_efile_provider=None))
    assert null.name == "none"
    assert null.configured is False

    sandbox = itr_efile.get_provider(IncomeTaxSettings(itr_efile_provider="sandbox"))
    assert sandbox.name == "sandbox"
    assert sandbox.configured is True
    result = await sandbox.submit_return({"form": "ITR-6", "computation_ref": "ITR-COMP-1"})
    assert result["status"] == "Accepted"
    assert str(result["ack_no"]).startswith("SANDBOX-ACK-")
