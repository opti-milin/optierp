"""Tax challans, credit entries, and persisted 26AS recon — Tier 3."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin


class TaxChallan(Base, DocumentMixin, CompanyScopedMixin):
    """Advance / self-assessment / regular assessment challan — posts GL on submit."""

    __tablename__ = "tax_challans"

    name: Mapped[str] = mapped_column(String(40), nullable=False)
    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    challan_type: Mapped[str] = mapped_column(String(40), nullable=False)
    bsr_code: Mapped[str] = mapped_column(String(20), nullable=False)
    challan_serial: Mapped[str] = mapped_column(String(40), nullable=False)
    cin: Mapped[str | None] = mapped_column(String(40))
    deposit_date: Mapped[date] = mapped_column(Date, nullable=False)
    major_head: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'0021'"))
    minor_head: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'100'"))
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    tax_payable_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    computation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computations.id")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))


class TaxCreditEntry(Base, DocumentMixin, CompanyScopedMixin):
    """Append-only TDS/TCS/advance/self-assessment credit line."""

    __tablename__ = "tax_credit_entries"

    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    computation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computations.id")
    )
    credit_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    deductor_tan: Mapped[str | None] = mapped_column(String(20))
    deductor_name: Mapped[str | None] = mapped_column(String(200))
    section_code: Mapped[str | None] = mapped_column(String(40))
    amount_credited: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    amount_claimed: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    challan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_challans.id")
    )
    reconciliation_status: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'Unmatched'")
    )
    portal_amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    source_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))


class Tax26asReconRun(Base, DocumentMixin, CompanyScopedMixin):
    """Persisted Form 26AS / AIS reconciliation run."""

    __tablename__ = "tax_26as_recon_runs"

    computation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computations.id")
    )
    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    books_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    portal_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    difference: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    remarks: Mapped[str | None] = mapped_column(String(255))
