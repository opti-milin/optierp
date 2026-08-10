"""ITR-6 canonical value assembly from a submitted tax computation + latest run."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.models.tax_computation import TaxComputation, TaxComputationResult
from app.models.tax_config import TaxRegistration
from app.services.taxation.forms.generator import FieldMapEntry, build_cbdt_payload
from app.services.taxation.kernel.money import ZERO, money, q

# Portal ReturnFileSec codes (simplified; mapped from our filing_type).
FILING_TYPE_CODES: dict[str, str] = {
    "Original": "11",  # 139(1)
    "Belated": "12",  # 139(4)
    "Revised": "17",  # 139(5)
    "Updated": "18",  # 139(8A)
}

NON_ORIGINAL_TYPES = frozenset({"Revised", "Belated", "Updated"})


def _dec(v: Decimal | None) -> Decimal:
    return q(money(v)) if v is not None else ZERO


def build_canonical_values(
    *,
    computation: TaxComputation,
    result: TaxComputationResult | None,
    registration: TaxRegistration | None,
) -> dict[str, Any]:
    """Flatten registration + computation result into field-map canonical keys."""
    filing = computation.filing_type or "Original"
    values: dict[str, Any] = {
        "pan": (registration.pan if registration else None) or "",
        "cin": (registration.cin if registration else None) or "",
        "assessment_year": computation.ay_code,
        "filing_type": FILING_TYPE_CODES.get(filing, filing),
        "regime_code": computation.regime_code,
        "assessee_class_code": computation.assessee_class_code,
        "computation_name": computation.name,
    }
    if result is not None:
        values.update(
            {
                "total_income": _dec(result.taxable_income),
                "tax_normal": _dec(result.tax_normal),
                "tax_mat": _dec(result.tax_mat) if result.tax_mat is not None else None,
                "tax_payable": _dec(result.total_tax),
                "surcharge": _dec(result.surcharge_amount),
                "cess": _dec(result.cess_amount),
                "rebate": _dec(result.rebate_amount),
                "credits_total": _dec(result.credits_total),
                "interest_234a": _dec(result.interest_234a),
                "interest_234b": _dec(result.interest_234b),
                "interest_234c": _dec(result.interest_234c),
                "net_tax_payable": _dec(result.net_payable),
            }
        )
    return values


def generate_itr6_payload(
    *,
    field_maps: list[FieldMapEntry] | tuple[FieldMapEntry, ...],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Produce the CBDT-shaped ITR-6 JSON from statutory field maps + values."""
    return build_cbdt_payload(field_maps, values)
