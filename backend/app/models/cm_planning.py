"""Contribution Margin Plan — pre-sales planning worksheet + scenarios.

Not a GL report. Linked to Quotation / Sales Order. See
docs/plans/cm_planning_engine.plan.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
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


class CmPlan(Base, DocumentMixin, CompanyScopedMixin):
    """Header for a pre-sales contribution margin plan."""

    __tablename__ = "cm_plans"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_cm_plan_name"),
        Index("ix_cm_plans_company_docstatus", "company_id", "docstatus"),
        Index("ix_cm_plans_quotation", "quotation_id"),
        Index("ix_cm_plans_sales_order", "sales_order_id"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    quotation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="SET NULL")
    )
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="SET NULL")
    )
    template_id: Mapped[str | None] = mapped_column(String(60))
    target_cm1_pct: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    target_cm2_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    min_cm1_pct: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    submit_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="warn", server_default=text("'warn'")
    )
    # Snapshot of allocation % used when seeding / last policy save (falls back to settings).
    allocations: Mapped[dict | None] = mapped_column(JSONB)
    # Frozen cost-driver tree (method × basis × hierarchy) at last recompute.
    cost_structure_snapshot: Mapped[list | dict | None] = mapped_column(JSONB)
    remarks: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list | dict | None] = mapped_column(JSONB)

    scenarios: Mapped[list["CmPlanScenario"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="CmPlanScenario.sort_order",
        lazy="selectin",
    )


class CmPlanScenario(Base, DocumentMixin):
    """Baseline or what-if scenario under a CM Plan."""

    __tablename__ = "cm_plan_scenarios"
    __table_args__ = (Index("ix_cm_plan_scenarios_plan", "cm_plan_id"),)

    cm_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cm_plans.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    is_baseline: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    overrides: Mapped[dict | None] = mapped_column(JSONB)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    sales_partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_partners.id", ondelete="SET NULL")
    )
    shipping_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shipping_rules.id", ondelete="SET NULL")
    )
    revenue: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    material: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    labor: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    freight: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    commission: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    packaging: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    variable_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    product_channel_fixed: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    segment_bu_fixed: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    corporate_overhead: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    cm1: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    cm2: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    cm3: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    operating_profit: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    cm1_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    cm2_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    cm3_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    operating_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    min_selling_total: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped[CmPlan] = relationship(back_populates="scenarios")
    items: Mapped[list["CmPlanItem"]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="CmPlanItem.idx",
        lazy="selectin",
    )
    costs: Mapped[list["CmPlanCost"]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="CmPlanCost.idx",
        lazy="selectin",
    )


class CmPlanItem(Base, DocumentMixin):
    __tablename__ = "cm_plan_items"
    __table_args__ = (Index("ix_cm_plan_items_scenario", "cm_plan_scenario_id"),)

    cm_plan_scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cm_plan_scenarios.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    line_key: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL")
    )
    item_code: Mapped[str | None] = mapped_column(String(140))
    item_name: Mapped[str] = mapped_column(String(140), nullable=False)
    qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    selling_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    selling_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    quotation_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sales_order_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    material: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    labor: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    freight: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    packaging: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    variable_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    cm1: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    min_selling_rate: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))

    scenario: Mapped[CmPlanScenario] = relationship(back_populates="items")


class CmPlanCost(Base, DocumentMixin):
    __tablename__ = "cm_plan_costs"
    __table_args__ = (Index("ix_cm_plan_costs_scenario", "cm_plan_scenario_id"),)

    cm_plan_scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cm_plan_scenarios.id", ondelete="CASCADE"), nullable=False
    )
    cm_plan_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cm_plan_items.id", ondelete="CASCADE")
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    driver: Mapped[str] = mapped_column(String(40), nullable=False)
    cm_class: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    source: Mapped[str] = mapped_column(
        String(40), nullable=False, default="manual", server_default=text("'manual'")
    )
    rate: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    notes: Mapped[str | None] = mapped_column(String(255))
    explanation: Mapped[dict | None] = mapped_column(JSONB)
    parent_driver: Mapped[str | None] = mapped_column(String(60))
    is_group: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    scenario: Mapped[CmPlanScenario] = relationship(back_populates="costs")


class CmCostStructure(Base, DocumentMixin, CompanyScopedMixin):
    """Effective-dated company cost-driver configuration."""

    __tablename__ = "cm_cost_structures"
    __table_args__ = (Index("ix_cm_cost_structures_company_active", "company_id", "is_active"),)

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(60))
    effective_from: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    drivers: Mapped[list["CmCostDriver"]] = relationship(
        back_populates="structure",
        cascade="all, delete-orphan",
        order_by="CmCostDriver.sort_order",
        lazy="selectin",
    )


class CmCostDriver(Base, DocumentMixin):
    """One node (group or leaf) in a cost structure tree."""

    __tablename__ = "cm_cost_drivers"
    __table_args__ = (
        UniqueConstraint("cost_structure_id", "code", name="uq_cm_cost_driver_code"),
        Index("ix_cm_cost_drivers_structure", "cost_structure_id"),
    )

    cost_structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cm_cost_structures.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(60))
    is_group: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    cm_class: Mapped[str] = mapped_column(String(40), nullable=False)
    allocation_method: Mapped[str | None] = mapped_column(String(40))
    allocation_basis: Mapped[str | None] = mapped_column(String(40))
    method_params: Mapped[dict | None] = mapped_column(JSONB)
    basis_params: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str | None] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="header", server_default=text("'header'"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    structure: Mapped[CmCostStructure] = relationship(back_populates="drivers")


class CmPlanVarianceRun(Base, DocumentMixin, CompanyScopedMixin):
    """Stored plan-vs-GL actual comparison for history."""

    __tablename__ = "cm_plan_variance_runs"
    __table_args__ = (Index("ix_cm_plan_variance_plan", "cm_plan_id"),)

    cm_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cm_plans.id", ondelete="CASCADE"), nullable=False
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    from_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    to_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rows: Mapped[list | dict | None] = mapped_column(JSONB)
    warnings: Mapped[list | dict | None] = mapped_column(JSONB)


class CmCostRate(Base, DocumentMixin, CompanyScopedMixin):
    """Simple master — packaging (and similar) per-unit rates for planning."""

    __tablename__ = "cm_cost_rates"
    __table_args__ = (
        UniqueConstraint("company_id", "rate_name", name="uq_cm_cost_rate_name"),
        Index("ix_cm_cost_rates_company", "company_id"),
    )

    rate_name: Mapped[str] = mapped_column(String(140), nullable=False)
    driver: Mapped[str] = mapped_column(
        String(40), nullable=False, default="packaging", server_default=text("'packaging'")
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE")
    )
    item_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_groups.id", ondelete="CASCADE")
    )
    rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    uom: Mapped[str | None] = mapped_column(String(140))
    disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
