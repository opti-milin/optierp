"""Statutory catalogue ORM — schema ``statutory``, no company_id, read-only to app.

These tables are written only by ``erp_owner`` (migrations + load_statutory).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, SmallInteger, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

SCHEMA = "statutory"


class StatutoryTimestampMixin:
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    creation: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    modified: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AssessmentYear(StatutoryTimestampMixin, Base):
    __tablename__ = "assessment_year"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    ay_start: Mapped[date] = mapped_column(Date, nullable=False)
    ay_end: Mapped[date] = mapped_column(Date, nullable=False)
    fy_start: Mapped[date] = mapped_column(Date, nullable=False)
    fy_end: Mapped[date] = mapped_column(Date, nullable=False)
    prev_ay_code: Mapped[str | None] = mapped_column(
        String(20), ForeignKey(f"{SCHEMA}.assessment_year.code"), nullable=True
    )


class FinanceActVersion(StatutoryTimestampMixin, Base):
    __tablename__ = "finance_act_version"
    __table_args__ = {"schema": SCHEMA}

    ay_code: Mapped[str] = mapped_column(
        String(20), ForeignKey(f"{SCHEMA}.assessment_year.code"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    enacted_on: Mapped[date | None] = mapped_column(Date)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    rate_schedules: Mapped[list[RateSchedule]] = relationship(back_populates="finance_act_version")


class AssesseeClass(StatutoryTimestampMixin, Base):
    __tablename__ = "assessee_class"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    default_itr_form: Mapped[str | None] = mapped_column(String(20))
    pan_4th_chars: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("''"))


class TaxRegime(StatutoryTimestampMixin, Base):
    __tablename__ = "tax_regime"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    assessee_class_code: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.assessee_class.code"), nullable=False
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    election_irrevocable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    election_form: Mapped[str | None] = mapped_column(String(40))


class IncomeCharacter(StatutoryTimestampMixin, Base):
    __tablename__ = "income_character"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    surcharge_cap_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    rebate_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    setoff_group: Mapped[str | None] = mapped_column(String(40))
    loss_carry_years: Mapped[int | None] = mapped_column(SmallInteger)


class RateSchedule(StatutoryTimestampMixin, Base):
    __tablename__ = "rate_schedule"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    assessee_class_code: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.assessee_class.code"), nullable=False
    )
    regime_code: Mapped[str] = mapped_column(String(40), nullable=False)
    income_character_code: Mapped[str | None] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.income_character.code")
    )
    age_category: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'General'"))
    condition_expr: Mapped[str | None] = mapped_column(String(255))
    schedule_kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Slab'"))
    remarks: Mapped[str | None] = mapped_column(String(255))

    finance_act_version: Mapped[FinanceActVersion] = relationship(back_populates="rate_schedules")
    bands: Mapped[list[RateBand]] = relationship(
        back_populates="rate_schedule", order_by="RateBand.seq", cascade="all, delete-orphan"
    )


class RateBand(StatutoryTimestampMixin, Base):
    __tablename__ = "rate_band"
    __table_args__ = {"schema": SCHEMA}

    rate_schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.rate_schedule.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lower: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, server_default=text("0"))
    upper: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    fixed_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, server_default=text("0")
    )

    rate_schedule: Mapped[RateSchedule] = relationship(back_populates="bands")


class SurchargeSchedule(StatutoryTimestampMixin, Base):
    __tablename__ = "surcharge_schedule"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    assessee_class_code: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.assessee_class.code"), nullable=False
    )
    regime_code: Mapped[str | None] = mapped_column(String(40))
    marginal_relief_method: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'RerunAtThreshold'")
    )
    capped_characters: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))

    bands: Mapped[list[SurchargeBand]] = relationship(
        back_populates="surcharge_schedule", order_by="SurchargeBand.seq", cascade="all, delete-orphan"
    )


class SurchargeBand(StatutoryTimestampMixin, Base):
    __tablename__ = "surcharge_band"
    __table_args__ = {"schema": SCHEMA}

    surcharge_schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.surcharge_schedule.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lower: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, server_default=text("0"))
    upper: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)

    surcharge_schedule: Mapped[SurchargeSchedule] = relationship(back_populates="bands")


class CessRule(StatutoryTimestampMixin, Base):
    __tablename__ = "cess_rule"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    base: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'TaxPlusSurcharge'"))
    remarks: Mapped[str | None] = mapped_column(String(255))


class RebateRule(StatutoryTimestampMixin, Base):
    __tablename__ = "rebate_rule"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    section_code: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'87A'"))
    assessee_class_code: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.assessee_class.code"), nullable=False
    )
    regime_code: Mapped[str] = mapped_column(String(40), nullable=False)
    max_taxable_income: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    max_rebate_amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    marginal_relief_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    excluded_characters: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))


class Provision(StatutoryTimestampMixin, Base):
    __tablename__ = "provision"
    __table_args__ = {"schema": SCHEMA}

    section_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    act_reference: Mapped[str | None] = mapped_column(String(80))
    stage: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'PGBP'"))
    default_effect: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Add'"))
    itr_schedule: Mapped[str | None] = mapped_column(String(40))
    itr_field_path: Mapped[str | None] = mapped_column(String(120))
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class RulePack(StatutoryTimestampMixin, Base):
    __tablename__ = "rule_pack"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    assessee_class_code: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.assessee_class.code"), nullable=False
    )
    regime_code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(255))

    rules: Mapped[list[Rule]] = relationship(
        back_populates="rule_pack", order_by="Rule.seq", cascade="all, delete-orphan"
    )


class Rule(StatutoryTimestampMixin, Base):
    __tablename__ = "rule"
    __table_args__ = {"schema": SCHEMA}

    rule_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.rule_pack.id", ondelete="CASCADE"),
        nullable=False,
    )
    provision_section_code: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.provision.section_code"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluation_method: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'Manual'")
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    depends_on: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    rule_pack: Mapped[RulePack] = relationship(back_populates="rules")


class DeductionSection(StatutoryTimestampMixin, Base):
    __tablename__ = "deduction_section"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    cap_amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    cap_expr: Mapped[str | None] = mapped_column(String(255))
    qualifying_limit_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    regime_allowed: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    age_dependent_caps: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))


class DepreciationBlock(StatutoryTimestampMixin, Base):
    __tablename__ = "depreciation_block"
    __table_args__ = {"schema": SCHEMA}

    block_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    additional_depreciation_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))


class DueDateRule(StatutoryTimestampMixin, Base):
    __tablename__ = "due_date_rule"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    assessee_class_code: Mapped[str | None] = mapped_column(String(40))
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    due_month: Mapped[int | None] = mapped_column(SmallInteger)
    due_day: Mapped[int | None] = mapped_column(SmallInteger)
    percent_of_tax: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    label: Mapped[str | None] = mapped_column(String(120))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class InterestRule(StatutoryTimestampMixin, Base):
    __tablename__ = "interest_rule"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    section_code: Mapped[str] = mapped_column(String(20), nullable=False)
    rate_percent_per_month: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    remarks: Mapped[str | None] = mapped_column(String(255))


class ItrForm(StatutoryTimestampMixin, Base):
    __tablename__ = "itr_form"
    __table_args__ = {"schema": SCHEMA}

    finance_act_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    form_code: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    assessee_class_codes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    field_maps: Mapped[list[ItrFieldMap]] = relationship(
        back_populates="itr_form", cascade="all, delete-orphan"
    )


class ItrFieldMap(StatutoryTimestampMixin, Base):
    __tablename__ = "itr_field_map"
    __table_args__ = {"schema": SCHEMA}

    itr_form_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.itr_form.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_field: Mapped[str] = mapped_column(String(120), nullable=False)
    cbdt_json_path: Mapped[str] = mapped_column(String(255), nullable=False)
    transform: Mapped[str | None] = mapped_column(String(80))

    itr_form: Mapped[ItrForm] = relationship(back_populates="field_maps")
