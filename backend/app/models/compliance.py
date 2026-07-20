"""India Income Tax (entity ITR) — ORM models.

Phase 0–1 of docs/ITR_GAP_AND_PLAN.md. Rate table + adjustment category are
engine masters; Income Tax Computation is a bespoke submittable worksheet
(no GL posting — reads books and stores the tax pack).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

ADJUSTMENT_DIRECTIONS = ("Add", "Deduct")
COMPUTATION_STATUSES = ("Draft", "Submitted", "Cancelled")


class IncomeTaxRateTable(Base, DocumentMixin, CompanyScopedMixin):
    """Entity tax rates for one assessment year × entity × regime — engine master."""

    __tablename__ = "income_tax_rate_tables"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "assessment_year",
            "entity_type",
            "filing_regime",
            name="uq_income_tax_rate_ay_entity_regime",
        ),
    )

    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "2025-26"
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Company", server_default=text("'Company'")
    )
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    surcharge_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    cess_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class TaxAdjustmentCategory(Base, DocumentMixin, CompanyScopedMixin):
    """Add-back / deduction category for books → taxable income — engine master."""

    __tablename__ = "tax_adjustment_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "category_code", name="uq_tax_adj_category_code"),
    )

    category_code: Mapped[str] = mapped_column(String(40), nullable=False)
    category_name: Mapped[str] = mapped_column(String(140), nullable=False)
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default="Add", server_default=text("'Add'")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class IncomeTaxAdjustmentLine(Base, DocumentMixin, CompanyScopedMixin):
    """One add-back or deduction on an Income Tax Computation."""

    __tablename__ = "income_tax_adjustment_lines"

    computation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("income_tax_computations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_adjustment_categories.id")
    )
    description: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=text("''")
    )
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default="Add", server_default=text("'Add'")
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    computation = relationship("IncomeTaxComputation", back_populates="adjustments")
    category = relationship("TaxAdjustmentCategory", lazy="joined", viewonly=True)


class IncomeTaxComputation(Base, DocumentMixin, CompanyScopedMixin):
    """Annual entity income-tax worksheet — bespoke submittable (no GL)."""

    __tablename__ = "income_tax_computations"
    __table_args__ = (
        Index(
            "uq_income_tax_computation_ay_active",
            "company_id",
            "assessment_year",
            unique=True,
            postgresql_where=text("docstatus <> 2"),
        ),
        UniqueConstraint("company_id", "name", name="uq_income_tax_computation_name"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate_table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("income_tax_rate_tables.id")
    )

    book_profit: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    net_adjustments: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    taxable_income: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    surcharge_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    cess_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    total_tax: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tds_credit: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    advance_tax_paid: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tax_payable: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    adjustments: Mapped[list[IncomeTaxAdjustmentLine]] = relationship(
        "IncomeTaxAdjustmentLine",
        back_populates="computation",
        cascade="all, delete-orphan",
        order_by="IncomeTaxAdjustmentLine.idx",
        lazy="selectin",
    )
    rate_table = relationship("IncomeTaxRateTable", lazy="joined", viewonly=True)
