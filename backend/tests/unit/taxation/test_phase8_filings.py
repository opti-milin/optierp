"""Phase 8 — ITR-6 field-map generator, hash stability, filing-type chaining rules."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import ValidationError
from app.services.taxation.forms.generator import (
    FieldMapEntry,
    apply_transform,
    build_cbdt_payload,
    payload_sha256,
)
from app.services.taxation.forms.itr6 import (
    FILING_TYPE_CODES,
    NON_ORIGINAL_TYPES,
    build_canonical_values,
    generate_itr6_payload,
)


def test_apply_transform_rupees() -> None:
    assert apply_transform(Decimal("12345.67"), "rupees") == 12346
    assert apply_transform(Decimal("10.40"), "rupees") == 10
    assert apply_transform("ABCDE1234F", "string") == "ABCDE1234F"


def test_build_cbdt_payload_nests_paths() -> None:
    maps = [
        FieldMapEntry("pan", "Form_ITR6.PartA_GEN.PAN"),
        FieldMapEntry("total_income", "Form_ITR6.PartB_TI.TotalIncome", "rupees"),
        FieldMapEntry(
            "interest_234a",
            "Form_ITR6.PartB_TTI.InterestPay.IntrstPayUs234A",
            "rupees",
        ),
    ]
    values = {
        "pan": "AABCT1234C",
        "total_income": Decimal("1000000.00"),
        "interest_234a": Decimal("1500.00"),
    }
    payload = build_cbdt_payload(maps, values)
    assert payload["Form_ITR6"]["PartA_GEN"]["PAN"] == "AABCT1234C"
    assert payload["Form_ITR6"]["PartB_TI"]["TotalIncome"] == 1000000
    assert payload["Form_ITR6"]["PartB_TTI"]["InterestPay"]["IntrstPayUs234A"] == 1500


def test_payload_sha256_is_stable() -> None:
    a = {"Form_ITR6": {"PartA_GEN": {"PAN": "X"}, "PartB_TI": {"TotalIncome": 1}}}
    b = {"Form_ITR6": {"PartB_TI": {"TotalIncome": 1}, "PartA_GEN": {"PAN": "X"}}}
    assert payload_sha256(a) == payload_sha256(b)
    assert len(payload_sha256(a)) == 64


def test_canonical_values_from_computation() -> None:
    computation = SimpleNamespace(
        filing_type="Revised",
        ay_code="2025-26",
        regime_code="115BAA",
        assessee_class_code="Company",
        name="TC-2025-0001",
    )
    result = SimpleNamespace(
        taxable_income=Decimal("500000"),
        tax_normal=Decimal("125000"),
        tax_mat=Decimal("75000"),
        total_tax=Decimal("130000"),
        surcharge_amount=Decimal("0"),
        cess_amount=Decimal("5000"),
        rebate_amount=Decimal("0"),
        credits_total=Decimal("20000"),
        interest_234a=Decimal("0"),
        interest_234b=Decimal("1000"),
        interest_234c=Decimal("500"),
        net_payable=Decimal("111500"),
    )
    reg = SimpleNamespace(pan="AABCT1234C", cin="U12345MH2020PTC000001")
    values = build_canonical_values(
        computation=computation,  # type: ignore[arg-type]
        result=result,  # type: ignore[arg-type]
        registration=reg,  # type: ignore[arg-type]
    )
    assert values["pan"] == "AABCT1234C"
    assert values["filing_type"] == FILING_TYPE_CODES["Revised"]
    assert values["total_income"] == Decimal("500000.00")
    assert values["net_tax_payable"] == Decimal("111500.00")

    maps = [
        FieldMapEntry("pan", "Form_ITR6.PartA_GEN.PAN"),
        FieldMapEntry("net_tax_payable", "Form_ITR6.PartB_TTI.NetTaxLiability", "rupees"),
    ]
    payload = generate_itr6_payload(field_maps=maps, values=values)
    assert payload["Form_ITR6"]["PartB_TTI"]["NetTaxLiability"] == 111500


def test_non_original_filing_types() -> None:
    assert "Revised" in NON_ORIGINAL_TYPES
    assert "Belated" in NON_ORIGINAL_TYPES
    assert "Updated" in NON_ORIGINAL_TYPES
    assert "Original" not in NON_ORIGINAL_TYPES


@pytest.mark.asyncio
async def test_validate_chain_requires_prior() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.taxation.filings import validate_computation_chain

    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(ValidationError) as exc:
        await validate_computation_chain(
            db,
            company_id=uuid4(),
            filing_type="Revised",
            revises_computation_id=None,
        )
    assert exc.value.code == "revises_required"

    with pytest.raises(ValidationError) as exc2:
        await validate_computation_chain(
            db,
            company_id=uuid4(),
            filing_type="Original",
            revises_computation_id=uuid4(),
        )
    assert exc2.value.code == "revises_not_allowed"
