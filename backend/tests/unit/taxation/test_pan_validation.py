"""Unit tests for PAN ↔ assessee-class validation."""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.services.taxation.registration import validate_pan_vs_class


def test_company_pan_accepts_c() -> None:
    # 4th character must be C for Company
    validate_pan_vs_class("ABCCE1234F", "Company", "C")


def test_company_pan_rejects_individual_letter() -> None:
    with pytest.raises(ValidationError, match="4th character"):
        validate_pan_vs_class("ABCPG1234H", "Company", "C")


def test_individual_pan_p() -> None:
    validate_pan_vs_class("ABCPG1234H", "Individual", "P")


def test_invalid_format() -> None:
    with pytest.raises(ValidationError, match="Invalid PAN"):
        validate_pan_vs_class("BAD", "Company", "C")


def test_empty_pan_ok() -> None:
    validate_pan_vs_class(None, "Company", "C")
    validate_pan_vs_class("", "Company", "C")
