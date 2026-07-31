"""Pydantic schemas for Contribution Margin Planning."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel


class CmPlanningSettings(BaseModel):
    target_cm1_pct: Decimal = Decimal("35")
    target_cm2_pct: Decimal | None = Decimal("25")
    min_cm1_pct: Decimal = Decimal("15")
    submit_policy: str = Field(default="warn", pattern="^(off|warn|block)$")
    default_template: str | None = "manufacturing"
    max_scenarios_per_plan: int = Field(default=10, ge=2, le=50)
    allocations: dict[str, Decimal] = Field(
        default_factory=lambda: {
            "product_channel_fixed_pct_of_revenue": Decimal("3"),
            "segment_bu_fixed_pct_of_revenue": Decimal("2"),
            "corporate_overhead_pct_of_revenue": Decimal("5"),
        }
    )


class CmPlanningTemplateInfo(BaseModel):
    id: str
    label: str
    description: str | None = None
    target_cm1_pct: Decimal | None = None


class CmScenarioOverrides(BaseModel):
    customer_id: uuid.UUID | None = None
    sales_partner_id: uuid.UUID | None = None
    shipping_rule_id: uuid.UUID | None = None
    freight_amount: Decimal | None = None
    # Header-level: apply the same rate/qty to every baseline line (preferred over empty items[]).
    selling_rate: Decimal | None = Field(default=None, ge=0)
    qty: Decimal | None = Field(default=None, gt=0)
    material_cost_factor: Decimal | None = Field(default=None, gt=0)
    labor_cost_factor: Decimal | None = Field(default=None, gt=0)
    additional_discount_percentage: Decimal | None = None
    reprice: bool = False
    items: list[dict[str, Any]] = Field(default_factory=list)


class CmPlanCostOut(ORMModel):
    id: uuid.UUID
    idx: int
    driver: str
    cm_class: str
    amount: Decimal
    source: str
    rate: Decimal | None = None
    qty: Decimal | None = None
    notes: str | None = None
    cm_plan_item_id: uuid.UUID | None = None
    explanation: dict[str, Any] | None = None
    parent_driver: str | None = None
    is_group: bool = False


class CmCostDriverIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=140)
    cm_class: str
    is_group: bool = False
    parent_code: str | None = None
    allocation_method: str | None = None
    allocation_basis: str | None = None
    method_params: dict[str, Any] = Field(default_factory=dict)
    basis_params: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    scope: str = "header"
    sort_order: int = 0
    enabled: bool = True
    is_system: bool = False


class CmCostStructureOut(BaseModel):
    id: str
    name: str
    template_id: str | None = None
    effective_from: str | None = None
    is_active: bool = True
    drivers: list[dict[str, Any]] = Field(default_factory=list)
    allocations: dict[str, str] = Field(default_factory=dict)


class CmCostStructureUpdate(BaseModel):
    name: str | None = None
    template_id: str | None = None
    drivers: list[CmCostDriverIn]


class CmPlanItemOut(ORMModel):
    id: uuid.UUID
    idx: int
    line_key: str
    item_id: uuid.UUID | None = None
    item_code: str | None = None
    item_name: str
    qty: Decimal
    selling_rate: Decimal
    selling_amount: Decimal
    material: Decimal
    labor: Decimal
    freight: Decimal
    packaging: Decimal
    variable_cost: Decimal
    cm1: Decimal
    min_selling_rate: Decimal | None = None


class CmPlanScenarioOut(ORMModel):
    id: uuid.UUID
    name: str
    is_baseline: bool
    sort_order: int
    overrides: dict[str, Any] | None = None
    customer_id: uuid.UUID | None = None
    sales_partner_id: uuid.UUID | None = None
    shipping_rule_id: uuid.UUID | None = None
    revenue: Decimal
    material: Decimal
    labor: Decimal
    freight: Decimal
    commission: Decimal
    packaging: Decimal
    variable_cost: Decimal
    product_channel_fixed: Decimal
    segment_bu_fixed: Decimal
    corporate_overhead: Decimal
    cm1: Decimal
    cm2: Decimal
    cm3: Decimal
    operating_profit: Decimal
    cm1_pct: Decimal | None = None
    cm2_pct: Decimal | None = None
    cm3_pct: Decimal | None = None
    operating_profit_pct: Decimal | None = None
    min_selling_total: Decimal | None = None
    applied_at: datetime | None = None
    items: list[CmPlanItemOut] = Field(default_factory=list)
    costs: list[CmPlanCostOut] = Field(default_factory=list)


class CmPlanListItem(DocumentMeta):
    name: str
    quotation_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    template_id: str | None = None
    target_cm1_pct: Decimal
    min_cm1_pct: Decimal
    submit_policy: str


class CmPlanUpdate(BaseModel):
    """Edit draft-plan policy (targets, mins, allocations, cost drivers). Optionally recompute."""

    target_cm1_pct: Decimal | None = Field(default=None, ge=0, le=100)
    target_cm2_pct: Decimal | None = Field(default=None, ge=0, le=100)
    min_cm1_pct: Decimal | None = Field(default=None, ge=0, le=100)
    submit_policy: str | None = Field(default=None, pattern="^(off|warn|block)$")
    allocations: dict[str, Decimal] | None = None
    # Full cost-driver tree for this plan (overrides company structure for recomputes).
    cost_drivers: list[CmCostDriverIn] | None = None
    remarks: str | None = None
    recompute: bool = True


class CmPlanResponse(DocumentMeta):
    name: str
    quotation_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    template_id: str | None = None
    target_cm1_pct: Decimal
    target_cm2_pct: Decimal | None = None
    min_cm1_pct: Decimal
    submit_policy: str
    allocations: dict[str, Any] | None = None
    cost_structure_snapshot: list[dict[str, Any]] | dict[str, Any] | None = None
    remarks: str | None = None
    warnings: list[str] | dict[str, Any] | None = None
    scenarios: list[CmPlanScenarioOut] = Field(default_factory=list)


class CmPlanScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    clone_from_scenario_id: uuid.UUID | None = None
    overrides: CmScenarioOverrides | None = None


class CmPlanScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=140)
    overrides: CmScenarioOverrides | None = None


class CmCompareRequest(BaseModel):
    scenario_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)


class CmCompareFieldDelta(BaseModel):
    field: str
    baseline: Decimal
    scenario: Decimal
    delta: Decimal
    delta_pct: Decimal | None = None


class CmCompareScenarioColumn(BaseModel):
    scenario_id: uuid.UUID
    name: str
    is_baseline: bool
    revenue: Decimal
    variable_cost: Decimal
    cm1: Decimal
    cm2: Decimal
    cm3: Decimal
    operating_profit: Decimal
    cm1_pct: Decimal | None = None
    min_selling_total: Decimal | None = None
    deltas_vs_baseline: list[CmCompareFieldDelta] = Field(default_factory=list)


class CmCompareResponse(BaseModel):
    plan_id: uuid.UUID
    columns: list[CmCompareScenarioColumn]


class CmPreviewLineIn(BaseModel):
    item_id: uuid.UUID | None = None
    item_name: str | None = None
    qty: Decimal = Field(gt=0)
    rate: Decimal = Field(ge=0)
    line_key: str | None = None


class CmPreviewRequest(BaseModel):
    customer_id: uuid.UUID | None = None
    sales_partner_id: uuid.UUID | None = None
    shipping_rule_id: uuid.UUID | None = None
    freight_amount: Decimal | None = None
    material_cost_factor: Decimal = Field(default=Decimal("1"), gt=0)
    labor_cost_factor: Decimal = Field(default=Decimal("1"), gt=0)
    items: list[CmPreviewLineIn] = Field(min_length=1)
    template_id: str | None = None


class CmMarginPolicyResult(BaseModel):
    ok: bool
    policy: str
    cm1_pct: Decimal | None = None
    min_cm1_pct: Decimal
    message: str | None = None


class CmActualComparisonRow(BaseModel):
    field: str
    label: str
    estimated: Decimal
    actual: Decimal
    delta: Decimal
    delta_pct: Decimal | None = None


class CmActualComparison(BaseModel):
    plan_id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_name: str
    from_date: date
    to_date: date
    rows: list[CmActualComparisonRow]
    warnings: list[str] = Field(default_factory=list)