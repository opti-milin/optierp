"""Tenant tax configuration ORM — Tier 2 (RLS, company-scoped)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin


class TaxRegistration(Base, DocumentMixin, CompanyScopedMixin):
    """One row per company — PAN/TAN/CIN, assessee class, jurisdiction."""

    __tablename__ = "tax_registrations"

    pan: Mapped[str | None] = mapped_column(String(10))
    tan: Mapped[str | None] = mapped_column(String(10))
    cin: Mapped[str | None] = mapped_column(String(30))
    assessee_class_code: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'Company'")
    )
    residential_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'Resident'")
    )
    incorporation_date: Mapped[date | None] = mapped_column(Date)
    nature_of_business_codes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    jurisdiction: Mapped[str | None] = mapped_column(String(120))
    default_assessment_year: Mapped[str | None] = mapped_column(String(20))
    itr_efile_provider: Mapped[str | None] = mapped_column(String(40))
    remarks: Mapped[str | None] = mapped_column(String(255))


class TaxRegimeElection(Base, DocumentMixin, CompanyScopedMixin):
    """Per-company per-AY regime election (auditable; irrevocable for 115BAA)."""

    __tablename__ = "tax_regime_elections"

    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    regime_code: Mapped[str] = mapped_column(String(40), nullable=False)
    assessee_class_code: Mapped[str] = mapped_column(String(40), nullable=False)
    elected_on: Mapped[date | None] = mapped_column(Date)
    form_ack_no: Mapped[str | None] = mapped_column(String(80))
    irrevocable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    remarks: Mapped[str | None] = mapped_column(String(255))


class TaxPolicyOverride(Base, DocumentMixin, CompanyScopedMixin):
    """Narrow audited tenant override of a statutory schedule or rule."""

    __tablename__ = "tax_policy_overrides"

    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    override_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_code: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
