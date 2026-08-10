"""Tax computation documents — Tier 3 (RLS, company-scoped, append-only runs)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin


class TaxComputation(Base, DocumentMixin, CompanyScopedMixin):
    """Slim annual tax worksheet header — law pinned via finance_act_version_id."""

    __tablename__ = "tax_computations"

    name: Mapped[str] = mapped_column(String(40), nullable=False)
    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    assessee_class_code: Mapped[str] = mapped_column(String(40), nullable=False)
    regime_election_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_regime_elections.id")
    )
    regime_code: Mapped[str] = mapped_column(String(40), nullable=False)
    filing_type: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'Original'")
    )
    revises_computation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computations.id")
    )
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Draft'"))
    remarks: Mapped[str | None] = mapped_column(String(255))
    current_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computation_runs.id", use_alter=True)
    )
    provision_expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    provision_liability_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    provision_gl_posted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # 115JB book profit input (companies); None skips MAT compare.
    book_profit_115jb: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    # Phase 7 — ITR filing / interest inputs
    return_filed_date: Mapped[date | None] = mapped_column(Date)
    audit_applicable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    itr_due_date_override: Mapped[date | None] = mapped_column(Date)

    income_lines: Mapped[list[TaxComputationIncomeLine]] = relationship(
        "TaxComputationIncomeLine",
        back_populates="computation",
        cascade="all, delete-orphan",
        order_by="TaxComputationIncomeLine.seq",
        lazy="selectin",
        foreign_keys="TaxComputationIncomeLine.computation_id",
    )
    adjustment_lines: Mapped[list[TaxComputationAdjustmentLine]] = relationship(
        "TaxComputationAdjustmentLine",
        back_populates="computation",
        cascade="all, delete-orphan",
        order_by="TaxComputationAdjustmentLine.creation",
        lazy="selectin",
        foreign_keys="TaxComputationAdjustmentLine.computation_id",
    )
    runs: Mapped[list[TaxComputationRun]] = relationship(
        "TaxComputationRun",
        back_populates="computation",
        cascade="all, delete-orphan",
        order_by="TaxComputationRun.run_no",
        lazy="selectin",
        foreign_keys="TaxComputationRun.computation_id",
    )


class TaxComputationIncomeLine(Base, DocumentMixin, CompanyScopedMixin):
    """Head-wise income line (Salary / HP / PGBP / CG / OS)."""

    __tablename__ = "tax_computation_income_lines"

    computation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_computations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    head: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'PGBP'"))
    income_character_code: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'ORDINARY'")
    )
    sub_ref: Mapped[str | None] = mapped_column(String(80))
    gross: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    deductions: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    net: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, server_default=text("0"))
    source_doc_type: Mapped[str | None] = mapped_column(String(80))
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    computation: Mapped[TaxComputation] = relationship(
        back_populates="income_lines", foreign_keys=[computation_id]
    )


class TaxComputationAdjustmentLine(Base, DocumentMixin, CompanyScopedMixin):
    """Manual or evaluated add-back / deduction — keyed to a run when evaluated."""

    __tablename__ = "tax_computation_adjustment_lines"

    computation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_computations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computation_runs.id")
    )
    provision_section_code: Mapped[str | None] = mapped_column(String(40))
    rule_code: Mapped[str | None] = mapped_column(String(40))
    section_code: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("''"))
    stage: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'PGBP'"))
    description: Mapped[str | None] = mapped_column(String(255))
    direction: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Add'"))
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    override_amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    final_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Manual'"))
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    prior_year_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_computation_adjustment_lines.id")
    )

    computation: Mapped[TaxComputation] = relationship(
        back_populates="adjustment_lines", foreign_keys=[computation_id]
    )


class TaxComputationRun(Base, DocumentMixin, CompanyScopedMixin):
    """Append-only evaluation record — never UPDATE amounts or DELETE."""

    __tablename__ = "tax_computation_runs"

    computation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_computations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'manual'"))
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ruleset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    superseded_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    computation: Mapped[TaxComputation] = relationship(
        back_populates="runs", foreign_keys=[computation_id]
    )
    result: Mapped[TaxComputationResult | None] = relationship(
        "TaxComputationResult",
        back_populates="run",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="joined",
    )


class TaxComputationResult(Base, DocumentMixin, CompanyScopedMixin):
    """One result row per run — tax under each basis plus net payable."""

    __tablename__ = "tax_computation_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_computation_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    taxable_income: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    tax_normal: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    tax_mat: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    tax_applied_basis: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'Normal'")
    )
    tax_before_rebate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    rebate_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    surcharge_before_relief: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    marginal_relief_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    surcharge_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    cess_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    interest_234a: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    interest_234b: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    interest_234c: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    credits_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    total_tax: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    net_payable: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )
    breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    run: Mapped[TaxComputationRun] = relationship(back_populates="result", foreign_keys=[run_id])
