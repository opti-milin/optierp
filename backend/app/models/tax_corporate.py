"""Tax depreciation, loss carry-forward, and MAT credit ledgers — Phase 6."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin


class TaxDepreciationRegister(Base, DocumentMixin, CompanyScopedMixin):
    """Per-company per-AY IT Act block WDV register."""

    __tablename__ = "tax_depreciation_registers"

    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    block_code: Mapped[str] = mapped_column(String(40), nullable=False)
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    opening_wdv: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    additions_full: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    additions_half: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    deletions: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    depreciation_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    additional_depreciation_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    closing_wdv: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))

    movements: Mapped[list[TaxDepreciationMovement]] = relationship(
        "TaxDepreciationMovement",
        back_populates="register",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TaxDepreciationMovement(Base, DocumentMixin, CompanyScopedMixin):
    """Asset-level addition / deletion feeding a block register."""

    __tablename__ = "tax_depreciation_movements"

    register_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_depreciation_registers.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id")
    )
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, server_default=text("0"))
    put_to_use_date: Mapped[date | None] = mapped_column(Date)
    half_rate: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    remarks: Mapped[str | None] = mapped_column(String(255))

    register: Mapped[TaxDepreciationRegister] = relationship(
        "TaxDepreciationRegister", back_populates="movements"
    )


class TaxLossCarryForward(Base, DocumentMixin, CompanyScopedMixin):
    """Append-friendly loss CF balance row (amount_remaining updated on set-off)."""

    __tablename__ = "tax_loss_carry_forward_ledger"

    origin_ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_after_ay: Mapped[str | None] = mapped_column(String(20))
    setoff_group: Mapped[str] = mapped_column(String(40), nullable=False)
    loss_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'Created'")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    amount_remaining: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    computation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computations.id")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))


class TaxLossSetoffEntry(Base, DocumentMixin, CompanyScopedMixin):
    """Append-only set-off of a CF loss against current-year income."""

    __tablename__ = "tax_loss_setoff_entries"

    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    computation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computations.id")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computation_runs.id")
    )
    ledger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_loss_carry_forward_ledger.id"), nullable=False
    )
    against_character: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_set_off: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class MatCreditLedger(Base, DocumentMixin, CompanyScopedMixin):
    """Append-only 115JAA MAT credit created / utilised / expired."""

    __tablename__ = "mat_credit_ledger"

    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    tax_mat: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    tax_normal: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    expires_after_ay: Mapped[str | None] = mapped_column(String(20))
    computation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computations.id")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computation_runs.id")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))
