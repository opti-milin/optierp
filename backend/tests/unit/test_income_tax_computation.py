"""Unit: entity income-tax computation math + advance-tax calendar (pure, no DB)."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.schemas.compliance import IncomeTaxSettings
from app.services.income_tax_computation import compute_tax_pack, net_adjustments_of
from app.services.itr_export import advance_tax_instalments, entity_form_for

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
    assert entity_form_for(IncomeTaxSettings(entity_type="LLP")) == "ITR-5"


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
