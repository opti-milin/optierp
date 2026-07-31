"""India Income Tax — ORM models.

Rule masters (rate table, slab sets, surcharge, cess, rebate, special rates,
tax policy) are engine-served. Income Tax Computation is a bespoke submittable
worksheet (no GL posting) supporting EntityBooks and IndividualHeads modes.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

ADJUSTMENT_DIRECTIONS = ("Add", "Deduct")
ADJUSTMENT_EFFECTS = ("Add", "Deduct", "Informational")
ADJUSTMENT_STAGES = ("PGBP", "ICDS", "ChapterVIA", "SetOff", "MAT", "Other")
ADJUSTMENT_LINE_STATUSES = ("Computed", "Manual", "Overridden", "NeedsInput", "Skipped")
ADJUSTMENT_EVAL_METHODS = (
    "Manual",
    "PercentOfBase",
    "ThresholdDisallow",
    "PaymentTiming",
    "DiffTwoSources",
    "ScheduleCap",
    "FormulaSafe",
    "PriorYearReversal",
    "Composite",
)
COMPUTATION_STATUSES = ("Draft", "Submitted", "Cancelled")
ASSESSEE_MODES = ("EntityBooks", "IndividualHeads")
SEED_SOURCES = ("Manual", "Payroll", "Books")
COMPUTATION_METHODS = ("FlatRate", "SlabBased", "RuleBased")
CESS_BASES = ("TaxPlusSurcharge",)


# ---------------------------------------------------------------------------
# Flat-rate master (Company / Firm / LLP)
# ---------------------------------------------------------------------------


class IncomeTaxRateTable(Base, DocumentMixin, CompanyScopedMixin):
    """Flat tax % for one assessment year × entity × regime — engine master."""

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

    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Company", server_default=text("'Company'")
    )
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


# ---------------------------------------------------------------------------
# Progressive slab masters
# ---------------------------------------------------------------------------


class IncomeTaxSlabSet(Base, DocumentMixin, CompanyScopedMixin):
    """Named progressive slab pack for one AY × entity × regime."""

    __tablename__ = "income_tax_slab_sets"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "set_name",
            name="uq_income_tax_slab_set_name",
        ),
    )

    set_name: Mapped[str] = mapped_column(String(140), nullable=False)
    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    lines: Mapped[list[IncomeTaxSlabLine]] = relationship(
        "IncomeTaxSlabLine",
        back_populates="slab_set",
        cascade="all, delete-orphan",
        order_by="IncomeTaxSlabLine.idx",
        lazy="selectin",
    )


class IncomeTaxSlabLine(Base, DocumentMixin):
    """One progressive tax band on an Income Tax Slab Set."""

    __tablename__ = "income_tax_slab_lines"

    slab_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("income_tax_slab_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    from_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    to_amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 6), nullable=True)
    rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )

    slab_set = relationship("IncomeTaxSlabSet", back_populates="lines")


# ---------------------------------------------------------------------------
# Surcharge masters
# ---------------------------------------------------------------------------


class SurchargeRuleSet(Base, DocumentMixin, CompanyScopedMixin):
    """Threshold surcharge pack with optional marginal relief."""

    __tablename__ = "surcharge_rule_sets"
    __table_args__ = (
        UniqueConstraint("company_id", "set_name", name="uq_surcharge_rule_set_name"),
    )

    set_name: Mapped[str] = mapped_column(String(140), nullable=False)
    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, default="Company")
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    marginal_relief_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    brackets: Mapped[list[SurchargeBracket]] = relationship(
        "SurchargeBracket",
        back_populates="rule_set",
        cascade="all, delete-orphan",
        order_by="SurchargeBracket.idx",
        lazy="selectin",
    )


class SurchargeBracket(Base, DocumentMixin):
    """One income-threshold surcharge bracket (rate applied to tax)."""

    __tablename__ = "surcharge_brackets"

    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("surcharge_rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    income_from: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    income_to: Mapped[Decimal | None] = mapped_column(Numeric(21, 6), nullable=True)
    rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )

    rule_set = relationship("SurchargeRuleSet", back_populates="brackets")


# ---------------------------------------------------------------------------
# Cess / rebate / special rates
# ---------------------------------------------------------------------------


class HealthEducationCessRule(Base, DocumentMixin, CompanyScopedMixin):
    """Health & Education Cess rate for an AY (optionally entity/regime scoped)."""

    __tablename__ = "health_education_cess_rules"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "rule_name",
            name="uq_hec_cess_rule_name",
        ),
    )

    rule_name: Mapped[str] = mapped_column(String(140), nullable=False)
    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    filing_regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cess_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=4, server_default=text("4")
    )
    cess_base: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="TaxPlusSurcharge",
        server_default=text("'TaxPlusSurcharge'"),
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class RebateRule(Base, DocumentMixin, CompanyScopedMixin):
    """Rebate configuration (e.g. Section 87A) — fully data-driven."""

    __tablename__ = "rebate_rules"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "assessment_year",
            "section_code",
            "filing_regime",
            name="uq_rebate_rule_ay_section_regime",
        ),
    )

    section_code: Mapped[str] = mapped_column(String(40), nullable=False)
    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    max_taxable_income: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    max_rebate_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class SpecialIncomeTaxRate(Base, DocumentMixin, CompanyScopedMixin):
    """Special rates for capital gains, lottery, crypto, etc."""

    __tablename__ = "special_income_tax_rates"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "assessment_year",
            "income_category_code",
            "filing_regime",
            name="uq_special_rate_ay_cat_regime",
        ),
    )

    income_category_code: Mapped[str] = mapped_column(String(40), nullable=False)
    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    description: Mapped[str | None] = mapped_column(String(255))
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


# ---------------------------------------------------------------------------
# Tax Policy — selects computation method + links rule packs
# ---------------------------------------------------------------------------


class TaxPolicy(Base, DocumentMixin, CompanyScopedMixin):
    """Computation router: method + linked masters for AY × entity × regime."""

    __tablename__ = "tax_policies"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "assessment_year",
            "entity_type",
            "filing_regime",
            name="uq_tax_policy_ay_entity_regime",
        ),
    )

    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    computation_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="FlatRate", server_default=text("'FlatRate'")
    )
    # When computation_method=RuleBased, ordinary residual uses this method.
    ordinary_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="FlatRate", server_default=text("'FlatRate'")
    )
    rate_table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("income_tax_rate_tables.id")
    )
    slab_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("income_tax_slab_sets.id")
    )
    surcharge_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surcharge_rule_sets.id")
    )
    cess_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("health_education_cess_rules.id")
    )
    rebate_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rebate_rules.id")
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    rate_table = relationship("IncomeTaxRateTable", lazy="joined", viewonly=True)
    slab_set = relationship("IncomeTaxSlabSet", lazy="joined", viewonly=True)
    surcharge_set = relationship("SurchargeRuleSet", lazy="joined", viewonly=True)
    cess_rule = relationship("HealthEducationCessRule", lazy="joined", viewonly=True)
    rebate_rule = relationship("RebateRule", lazy="joined", viewonly=True)


# ---------------------------------------------------------------------------
# Tax adjustment engine masters + computation worksheet lines
# ---------------------------------------------------------------------------


class TaxAdjustmentProvision(Base, DocumentMixin, CompanyScopedMixin):
    """Statutory provision identity (section taxonomy) — engine master."""

    __tablename__ = "tax_adjustment_provisions"
    __table_args__ = (
        UniqueConstraint("company_id", "section_code", name="uq_tax_adj_provision_section"),
    )

    section_code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    act_reference: Mapped[str | None] = mapped_column(String(80))
    stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PGBP", server_default=text("'PGBP'")
    )
    default_effect: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Add", server_default=text("'Add'")
    )
    applies_to_modes: Mapped[str] = mapped_column(
        String(40), nullable=False, default="EntityBooks", server_default=text("'EntityBooks'")
    )
    regime_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Both", server_default=text("'Both'")
    )
    itr_schedule_hint: Mapped[str | None] = mapped_column(String(40))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class TaxAdjustmentRulePack(Base, DocumentMixin, CompanyScopedMixin):
    """AY × entity × regime router for adjustment rules (mirrors Tax Policy)."""

    __tablename__ = "tax_adjustment_rule_packs"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "assessment_year",
            "entity_type",
            "filing_regime",
            name="uq_tax_adj_pack_ay_entity_regime",
        ),
    )

    pack_name: Mapped[str] = mapped_column(String(140), nullable=False)
    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_regime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Normal", server_default=text("'Normal'")
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    rules: Mapped[list[TaxAdjustmentRule]] = relationship(
        "TaxAdjustmentRule",
        back_populates="pack",
        cascade="all, delete-orphan",
        order_by="TaxAdjustmentRule.sequence",
        lazy="selectin",
    )


class TaxAdjustmentRule(Base, DocumentMixin, CompanyScopedMixin):
    """One configurable evaluation rule inside a rule pack."""

    __tablename__ = "tax_adjustment_rules"
    __table_args__ = (
        UniqueConstraint("pack_id", "rule_code", name="uq_tax_adj_rule_code_per_pack"),
    )

    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_adjustment_rule_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_adjustment_provisions.id"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluation_method: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Manual", server_default=text("'Manual'")
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Manual", server_default=text("'Manual'")
    )
    source_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    depends_on: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    allow_manual_override: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    include_in_seed_lines: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    pack = relationship("TaxAdjustmentRulePack", back_populates="rules")
    provision = relationship("TaxAdjustmentProvision", lazy="joined", viewonly=True)


class TaxAdjustmentCategory(Base, DocumentMixin, CompanyScopedMixin):
    """Legacy Add/Deduct label — kept for compatibility; prefer TaxAdjustmentProvision."""

    __tablename__ = "tax_adjustment_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "category_code", name="uq_tax_adj_category_code"),
    )

    category_code: Mapped[str] = mapped_column(String(40), nullable=False)
    category_name: Mapped[str] = mapped_column(String(140), nullable=False)
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default="Add", server_default=text("'Add'")
    )
    provision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_adjustment_provisions.id")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    provision = relationship("TaxAdjustmentProvision", lazy="joined", viewonly=True)


class TaxDepreciationBlock(Base, DocumentMixin, CompanyScopedMixin):
    """Lean IT Act block-of-assets WDV register (Phase B dep bridge)."""

    __tablename__ = "tax_depreciation_blocks"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "assessment_year",
            "block_code",
            name="uq_tax_dep_block_ay_code",
        ),
    )

    assessment_year: Mapped[str] = mapped_column(String(20), nullable=False)
    block_code: Mapped[str] = mapped_column(String(40), nullable=False)
    block_name: Mapped[str] = mapped_column(String(140), nullable=False)
    rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    opening_wdv: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    additions: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    deletions: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    depreciation_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    closing_wdv: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class IncomeTaxAdjustmentLine(Base, DocumentMixin, CompanyScopedMixin):
    """One evaluated / manual add-back or deduction on an Income Tax Computation."""

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
    provision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_adjustment_provisions.id")
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_adjustment_rules.id")
    )
    section_code: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", server_default=text("''")
    )
    stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PGBP", server_default=text("'PGBP'")
    )
    description: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=text("''")
    )
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default="Add", server_default=text("'Add'")
    )
    # Legacy mirror of final_amount for API / pipeline parity.
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    base_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    computed_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    override_amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 6), nullable=True)
    final_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Manual", server_default=text("'Manual'")
    )
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    prior_year_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    computation = relationship("IncomeTaxComputation", back_populates="adjustments")
    category = relationship("TaxAdjustmentCategory", lazy="joined", viewonly=True)
    provision = relationship("TaxAdjustmentProvision", lazy="joined", viewonly=True)
    rule = relationship("TaxAdjustmentRule", lazy="joined", viewonly=True)


class IncomeTaxSpecialIncomeLine(Base, DocumentMixin, CompanyScopedMixin):
    """Income taxed at a special rate (CG / lottery / crypto, etc.)."""

    __tablename__ = "income_tax_special_income_lines"

    computation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("income_tax_computations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    special_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("special_income_tax_rates.id")
    )
    income_category_code: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", server_default=text("''")
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    description: Mapped[str | None] = mapped_column(String(255))

    computation = relationship("IncomeTaxComputation", back_populates="special_income_lines")
    special_rate = relationship("SpecialIncomeTaxRate", lazy="joined", viewonly=True)


class IncomeTaxComputation(Base, DocumentMixin, CompanyScopedMixin):
    """Annual income-tax worksheet — EntityBooks or IndividualHeads (no GL)."""

    __tablename__ = "income_tax_computations"
    __table_args__ = (
        Index(
            "uq_itr_entity_books_ay_active",
            "company_id",
            "assessment_year",
            unique=True,
            postgresql_where=text("assessee_mode = 'EntityBooks' AND docstatus <> 2"),
        ),
        Index(
            "uq_itr_individual_employee_ay_active",
            "company_id",
            "assessment_year",
            "employee_id",
            unique=True,
            postgresql_where=text(
                "assessee_mode = 'IndividualHeads' AND employee_id IS NOT NULL AND docstatus <> 2"
            ),
        ),
        Index(
            "uq_itr_individual_unassigned_ay_active",
            "company_id",
            "assessment_year",
            unique=True,
            postgresql_where=text(
                "assessee_mode = 'IndividualHeads' AND employee_id IS NULL AND docstatus <> 2"
            ),
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
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_policies.id")
    )
    computation_method: Mapped[str | None] = mapped_column(String(20), nullable=True)

    assessee_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="EntityBooks", server_default=text("'EntityBooks'")
    )

    book_profit: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # IndividualHeads income
    salary_income: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    house_property_income: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    other_sources_income: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    capital_gains_income: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    chapter_via_deduction: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    standard_deduction: Mapped[Decimal] = mapped_column(
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
    rebate_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # Legacy alias kept in sync with rebate_amount for API stability.
    rebate_87a: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    marginal_relief_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    total_tax: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tds_credit: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tcs_credit: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    salary_tds: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    advance_tax_paid: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tax_payable: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tax_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Form 16 lean (manual or Payroll-seeded)
    employer_name: Mapped[str | None] = mapped_column(String(255))
    employer_tan: Mapped[str | None] = mapped_column(String(20))
    employer_address: Mapped[str | None] = mapped_column(String(500))
    employee_name: Mapped[str | None] = mapped_column(String(255))
    employee_pan: Mapped[str | None] = mapped_column(String(20))
    gross_salary: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    exemptions_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    taxable_salary: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    tax_deducted: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    seed_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Manual", server_default=text("'Manual'")
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payroll_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    salary_slip_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
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
    special_income_lines: Mapped[list[IncomeTaxSpecialIncomeLine]] = relationship(
        "IncomeTaxSpecialIncomeLine",
        back_populates="computation",
        cascade="all, delete-orphan",
        order_by="IncomeTaxSpecialIncomeLine.idx",
        lazy="selectin",
    )
    rate_table = relationship("IncomeTaxRateTable", lazy="joined", viewonly=True)
    policy = relationship("TaxPolicy", lazy="joined", viewonly=True)
