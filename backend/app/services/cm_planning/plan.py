"""Contribution Margin Plan CRUD, seed, scenarios, compare, margin policy."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED
from app.models.cm_planning import CmPlan, CmPlanCost, CmPlanItem, CmPlanScenario
from app.models.selling import Quotation, QuotationItem, SalesOrder, SalesOrderItem
from app.models.stock import Item
from app.schemas.cm_planning import (
    CmActualComparison,
    CmActualComparisonRow,
    CmCompareFieldDelta,
    CmCompareRequest,
    CmCompareResponse,
    CmCompareScenarioColumn,
    CmMarginPolicyResult,
    CmPlanResponse,
    CmPlanScenarioCreate,
    CmPlanScenarioUpdate,
    CmPlanUpdate,
    CmPreviewRequest,
    CmScenarioOverrides,
)
from app.services.audit import log_audit
from app.services.cm_planning.estimate import (
    resolve_commission_rate,
    resolve_freight_amount,
    resolve_line_unit_costs,
)
from app.services.cm_planning.settings import get_planning_settings, load_planning_template
from app.services.cm_planning.waterfall import (
    DRIVER_TO_CM_CLASS,
    EstimateInput,
    LineEstimateIn,
    WaterfallResult,
    diff_waterfalls,
    estimate_from_lines,
)
from app.services.financial_reports.contribution_margin import contribution_margin as gl_contribution_margin

_ZERO = Decimal("0")
_ACTUAL_COMPARE_FIELDS: tuple[tuple[str, str], ...] = (
    ("revenue", "Revenue"),
    ("variable_cost", "Variable costs"),
    ("product_channel_fixed", "Product / Channel fixed"),
    ("segment_bu_fixed", "Segment / BU fixed"),
    ("corporate_overhead", "Corporate overhead"),
    ("cm1", "CM1"),
    ("cm2", "CM2"),
    ("cm3", "CM3"),
    ("operating_profit", "Operating profit"),
)


def _alloc_snapshot(allocations: dict[str, Decimal] | dict[str, Any]) -> dict[str, Any]:
    return {str(k): str(v) if isinstance(v, Decimal) else v for k, v in allocations.items()}


def _allocations_for_plan(plan: CmPlan, settings_allocations: dict[str, Decimal]) -> dict[str, Decimal]:
    if plan.allocations:
        return {str(k): Decimal(str(v)) for k, v in plan.allocations.items()}
    return dict(settings_allocations)


async def _drivers_for_plan(
    db: AsyncSession, plan: CmPlan, company_id: uuid.UUID, allocations: dict[str, Decimal]
) -> list[Any]:
    from app.services.cm_planning.structure import drivers_to_snapshot, resolve_drivers_for_plan

    snap = plan.cost_structure_snapshot
    if isinstance(snap, dict):
        snap = snap.get("drivers")
    drivers = await resolve_drivers_for_plan(
        db, company_id, snapshot=snap if isinstance(snap, list) else None, allocations=allocations
    )
    plan.cost_structure_snapshot = drivers_to_snapshot(drivers)
    return drivers


def build_actual_comparison_rows(
    estimated: dict[str, Decimal], actual: dict[str, Decimal]
) -> list[CmActualComparisonRow]:
    """Pure Est vs Actual rows (unit-testable). delta = actual − estimated."""
    rows: list[CmActualComparisonRow] = []
    for field, label in _ACTUAL_COMPARE_FIELDS:
        est = Decimal(str(estimated.get(field, _ZERO))).quantize(Decimal("0.01"))
        act = Decimal(str(actual.get(field, _ZERO))).quantize(Decimal("0.01"))
        delta = (act - est).quantize(Decimal("0.01"))
        delta_pct: Decimal | None = None
        if est != _ZERO:
            delta_pct = (delta * Decimal("100") / est).quantize(Decimal("0.01"))
        rows.append(
            CmActualComparisonRow(
                field=field,
                label=label,
                estimated=est,
                actual=act,
                delta=delta,
                delta_pct=delta_pct,
            )
        )
    return rows

_SERIES = "CM-PLAN-.YYYY.-"
ZERO = Decimal("0")


def _waterfall_to_scenario_fields(w: WaterfallResult) -> dict[str, Any]:
    return {
        "revenue": w.revenue,
        "material": w.material,
        "labor": w.labor,
        "freight": w.freight,
        "commission": w.commission,
        "packaging": w.packaging,
        "variable_cost": w.variable_cost,
        "product_channel_fixed": w.product_channel_fixed,
        "segment_bu_fixed": w.segment_bu_fixed,
        "corporate_overhead": w.corporate_overhead,
        "cm1": w.cm1,
        "cm2": w.cm2,
        "cm3": w.cm3,
        "operating_profit": w.operating_profit,
        "cm1_pct": w.cm1_pct,
        "cm2_pct": w.cm2_pct,
        "cm3_pct": w.cm3_pct,
        "operating_profit_pct": w.operating_profit_pct,
        "min_selling_total": w.min_selling_total,
    }


def _scenario_to_waterfall(s: CmPlanScenario) -> WaterfallResult:
    return WaterfallResult(
        revenue=Decimal(s.revenue),
        material=Decimal(s.material),
        labor=Decimal(s.labor),
        freight=Decimal(s.freight),
        commission=Decimal(s.commission),
        packaging=Decimal(s.packaging),
        variable_cost=Decimal(s.variable_cost),
        product_channel_fixed=Decimal(s.product_channel_fixed),
        segment_bu_fixed=Decimal(s.segment_bu_fixed),
        corporate_overhead=Decimal(s.corporate_overhead),
        cm1=Decimal(s.cm1),
        cm2=Decimal(s.cm2),
        cm3=Decimal(s.cm3),
        operating_profit=Decimal(s.operating_profit),
        cm1_pct=s.cm1_pct,
        cm2_pct=s.cm2_pct,
        cm3_pct=s.cm3_pct,
        operating_profit_pct=s.operating_profit_pct,
        min_selling_total=s.min_selling_total,
    )


async def _get_plan(db: AsyncSession, plan_id: uuid.UUID, company_id: uuid.UUID) -> CmPlan:
    plan = await db.scalar(
        select(CmPlan)
        .where(CmPlan.id == plan_id, CmPlan.company_id == company_id)
        .options(
            selectinload(CmPlan.scenarios).selectinload(CmPlanScenario.items),
            selectinload(CmPlan.scenarios).selectinload(CmPlanScenario.costs),
        )
    )
    if plan is None:
        raise NotFoundError("Contribution Margin Plan not found")
    return plan


async def get_plan(db: AsyncSession, plan_id: uuid.UUID, company_id: uuid.UUID) -> CmPlan:
    return await _get_plan(db, plan_id, company_id)


async def list_plans(
    db: AsyncSession,
    company_id: uuid.UUID,
    page: int,
    page_size: int,
) -> tuple[list[CmPlan], int]:
    from sqlalchemy import func

    total = await db.scalar(
        select(func.count()).select_from(CmPlan).where(CmPlan.company_id == company_id)
    )
    rows = (
        await db.scalars(
            select(CmPlan)
            .where(CmPlan.company_id == company_id)
            .order_by(CmPlan.creation.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return list(rows), int(total or 0)


async def _clear_scenario_children(db: AsyncSession, scenario: CmPlanScenario) -> None:
    """Delete child rows without lazy-loading (async-safe)."""
    from sqlalchemy import delete

    await db.execute(delete(CmPlanCost).where(CmPlanCost.cm_plan_scenario_id == scenario.id))
    await db.execute(delete(CmPlanItem).where(CmPlanItem.cm_plan_scenario_id == scenario.id))
    # Reset in-memory collections so later appends don't fight expired state
    if "costs" in scenario.__dict__:
        scenario.costs = []
    if "items" in scenario.__dict__:
        scenario.items = []
    await db.flush()


def _item_rows_to_lines(items: list[CmPlanItem] | Any) -> list[dict[str, Any]]:
    return [
        {
            "line_key": it.line_key,
            "item_id": it.item_id,
            "item_code": it.item_code,
            "item_name": it.item_name,
            "qty": it.qty,
            "selling_rate": it.selling_rate,
            "quotation_item_id": it.quotation_item_id,
            "sales_order_item_id": it.sales_order_item_id,
        }
        for it in items
    ]


async def _baseline_lines(db: AsyncSession, plan: CmPlan) -> list[dict[str, Any]]:
    """Lines from the baseline scenario.

    Prefer the in-memory collection; if it was cleared mid-session (e.g. after
    recomputing baseline), reload from the DB so sibling scenarios are not wiped.
    """
    baseline = next((s for s in plan.scenarios if s.is_baseline), None)
    if baseline is None:
        raise ValidationError("Missing baseline scenario")
    items = list(baseline.items or [])
    if not items:
        items = list(
            (
                await db.scalars(
                    select(CmPlanItem)
                    .where(CmPlanItem.cm_plan_scenario_id == baseline.id)
                    .order_by(CmPlanItem.idx)
                )
            ).all()
        )
    return _item_rows_to_lines(items)

async def _populate_scenario(
    db: AsyncSession,
    company_id: uuid.UUID,
    scenario: CmPlanScenario,
    *,
    lines: list[dict[str, Any]],
    customer_id: uuid.UUID | None,
    sales_partner_id: uuid.UUID | None,
    shipping_rule_id: uuid.UUID | None,
    freight_override: Decimal | None,
    material_cost_factor: Decimal,
    labor_cost_factor: Decimal,
    allocations: dict[str, Decimal],
    target_cm1_pct: Decimal,
    warnings: list[str],
    drivers: list[Any] | None = None,
    keep_manual: bool = False,
    pools: dict[str, dict[str, Any]] | None = None,
    basis_overrides: dict[str, Decimal] | None = None,
) -> None:
    from app.services.cm_planning.structure import drivers_to_snapshot, resolve_drivers_for_plan
    from app.services.cm_planning.types import DriverDef

    # Preserve manual cost rows when requested
    manual_rows: list[dict[str, Any]] = []
    if keep_manual and scenario.costs:
        for c in scenario.costs:
            if c.source == "manual":
                manual_rows.append(
                    {
                        "driver": c.driver,
                        "cm_class": c.cm_class,
                        "amount": c.amount,
                        "source": "manual",
                        "notes": c.notes,
                        "explanation": c.explanation,
                        "parent_driver": c.parent_driver,
                    }
                )

    await _clear_scenario_children(db, scenario)

    estimate_lines: list[LineEstimateIn] = []
    item_meta: list[dict[str, Any]] = []
    subtotal = ZERO

    for raw in lines:
        item_id = raw.get("item_id")
        item = await db.get(Item, item_id) if item_id else None
        if item is not None and item.company_id != company_id:
            item = None
        costs = await resolve_line_unit_costs(db, company_id, item)
        qty = Decimal(str(raw["qty"]))
        rate = Decimal(str(raw["selling_rate"]))
        subtotal += qty * rate
        line_key = str(raw.get("line_key") or raw.get("quotation_item_id") or uuid.uuid4())
        estimate_lines.append(
            LineEstimateIn(
                line_key=line_key,
                item_id=item_id,
                qty=qty,
                selling_rate=rate,
                material_per_unit=costs.material_per_unit,
                labor_per_unit=costs.labor_per_unit,
                packaging_per_unit=costs.packaging_per_unit,
            )
        )
        item_meta.append(
            {
                "line_key": line_key,
                "item_id": item_id,
                "item_code": item.item_code if item else raw.get("item_code"),
                "item_name": (item.item_name if item else raw.get("item_name")) or "Item",
                "quotation_item_id": raw.get("quotation_item_id"),
                "sales_order_item_id": raw.get("sales_order_item_id"),
                "material_source": costs.material_source,
                "labor_source": costs.labor_source,
            }
        )
        if item is None and item_id:
            warnings.append(f"Item {item_id} not found — zero material cost.")
        elif costs.material_source == "none":
            warnings.append(f"No cost base for line {line_key}.")

    freight, freight_src = await resolve_freight_amount(
        db,
        company_id,
        shipping_rule_id=shipping_rule_id,
        freight_override=freight_override,
        item_subtotal=subtotal,
    )
    commission_pct = await resolve_commission_rate(db, company_id, sales_partner_id)

    waterfall, details, eng = estimate_from_lines(
        EstimateInput(
            lines=estimate_lines,
            freight_total=freight,
            commission_rate_pct=commission_pct,
            material_cost_factor=material_cost_factor,
            labor_cost_factor=labor_cost_factor,
            allocations=allocations,
            target_cm1_pct=target_cm1_pct,
            drivers=drivers,
            pools=pools or {},
            basis_overrides=basis_overrides or {},
            source_meta={
                "freight": {"source": freight_src},
                "commission": {"source": "commission"},
            },
        )
    )
    warnings.extend(eng.warnings)

    for k, v in _waterfall_to_scenario_fields(waterfall).items():
        setattr(scenario, k, v)
    scenario.customer_id = customer_id
    scenario.sales_partner_id = sales_partner_id
    scenario.shipping_rule_id = shipping_rule_id
    await db.flush()

    expl_by_driver = {leaf.driver.code: leaf.explanation.to_dict() for leaf in eng.leaves}

    meta_by_key = {m["line_key"]: m for m in item_meta}
    cost_idx = 0
    for idx, detail in enumerate(details, start=1):
        meta = meta_by_key[detail["line_key"]]
        row = CmPlanItem(
            cm_plan_scenario_id=scenario.id,
            idx=idx,
            line_key=detail["line_key"],
            item_id=meta["item_id"],
            item_code=meta["item_code"],
            item_name=meta["item_name"],
            qty=detail["qty"],
            selling_rate=detail["selling_rate"],
            selling_amount=detail["selling_amount"],
            quotation_item_id=meta.get("quotation_item_id"),
            sales_order_item_id=meta.get("sales_order_item_id"),
            material=detail["material"],
            labor=detail["labor"],
            freight=detail["freight"],
            packaging=detail["packaging"],
            variable_cost=detail["variable_cost"],
            cm1=detail["cm1"],
            min_selling_rate=detail.get("min_selling_rate"),
        )
        db.add(row)
        if "items" in scenario.__dict__:
            scenario.items.append(row)
        await db.flush()

        for driver, amount, source in (
            ("material", detail["material"], meta["material_source"]),
            ("labor", detail["labor"], meta["labor_source"]),
            ("packaging", detail["packaging"], "rate_master"),
            ("freight", detail["freight"], freight_src),
        ):
            if amount == ZERO and source in ("none",):
                continue
            cost_idx += 1
            expl = dict(expl_by_driver.get(driver) or {})
            if expl:
                expl["allocated_amount"] = str(amount)
                expl["source"] = source
            cost_row = CmPlanCost(
                cm_plan_scenario_id=scenario.id,
                cm_plan_item_id=row.id,
                idx=cost_idx,
                driver=driver,
                cm_class=DRIVER_TO_CM_CLASS.get(driver, "variable_cost"),
                amount=amount,
                source=source,
                notes=None,
                explanation=expl or None,
                parent_driver=expl.get("parent_code") if expl else None,
            )
            db.add(cost_row)
            if "costs" in scenario.__dict__:
                scenario.costs.append(cost_row)

    # Header-level leaves from engine (skip line-scoped duplicates already written)
    line_drivers = {"material", "labor", "packaging", "freight"}
    for leaf in eng.leaves:
        if leaf.driver.code in line_drivers:
            continue
        if leaf.amount == ZERO and leaf.explanation.source in ("none", None):
            continue
        cost_idx += 1
        expl = leaf.explanation.to_dict()
        cost_row = CmPlanCost(
            cm_plan_scenario_id=scenario.id,
            cm_plan_item_id=None,
            idx=cost_idx,
            driver=leaf.driver.code,
            cm_class=leaf.driver.cm_class,
            amount=leaf.amount,
            source=leaf.explanation.source or leaf.driver.allocation_method or "allocation",
            explanation=expl,
            parent_driver=leaf.driver.parent_code,
            is_group=False,
        )
        db.add(cost_row)
        if "costs" in scenario.__dict__:
            scenario.costs.append(cost_row)

    # Group rollup rows for drill-down UI
    for code, amount in eng.group_amounts.items():
        ddef = next((d for d in (drivers or []) if getattr(d, "code", None) == code), None)
        if ddef is None and drivers:
            # drivers may be DriverDef list
            for d in drivers:
                if isinstance(d, DriverDef) and d.code == code:
                    ddef = d
                    break
        cost_idx += 1
        cost_row = CmPlanCost(
            cm_plan_scenario_id=scenario.id,
            cm_plan_item_id=None,
            idx=cost_idx,
            driver=code,
            cm_class=getattr(ddef, "cm_class", None) or code,
            amount=amount,
            source="rollup",
            parent_driver=getattr(ddef, "parent_code", None),
            is_group=True,
            explanation={
                "driver_code": code,
                "driver_label": getattr(ddef, "label", code),
                "method": "rollup",
                "method_label": "Sum of children",
                "allocated_amount": str(amount),
                "formula_display": f"Σ children = {amount}",
            },
        )
        db.add(cost_row)
        if "costs" in scenario.__dict__:
            scenario.costs.append(cost_row)

    for m in manual_rows:
        cost_idx += 1
        cost_row = CmPlanCost(
            cm_plan_scenario_id=scenario.id,
            cm_plan_item_id=None,
            idx=cost_idx,
            driver=m["driver"],
            cm_class=m["cm_class"],
            amount=m["amount"],
            source="manual",
            notes=m.get("notes"),
            explanation=m.get("explanation"),
            parent_driver=m.get("parent_driver"),
        )
        db.add(cost_row)
        if "costs" in scenario.__dict__:
            scenario.costs.append(cost_row)

    await db.flush()
    _ = resolve_drivers_for_plan, drivers_to_snapshot  # available to callers via structure module


def _lines_from_quotation(items: list[QuotationItem]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        rate = Decimal(it.rate or 0)
        if it.net_amount and it.qty:
            rate = Decimal(it.net_amount) / Decimal(it.qty)
        out.append(
            {
                "line_key": str(it.id),
                "item_id": it.item_id,
                "item_code": it.item_code,
                "item_name": it.item_name,
                "qty": it.qty,
                "selling_rate": rate,
                "quotation_item_id": it.id,
            }
        )
    return out


def _lines_from_sales_order(items: list[SalesOrderItem]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        rate = Decimal(it.rate or 0)
        if it.net_amount and it.qty:
            rate = Decimal(it.net_amount) / Decimal(it.qty)
        out.append(
            {
                "line_key": str(it.id),
                "item_id": it.item_id,
                "item_code": it.item_code,
                "item_name": it.item_name,
                "qty": it.qty,
                "selling_rate": rate,
                "sales_order_item_id": it.id,
            }
        )
    return out


async def seed_from_quotation(
    db: AsyncSession, quotation_id: uuid.UUID, user: CurrentUser
) -> CmPlan:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    company_id = user.company_id
    qtn = await db.scalar(
        select(Quotation)
        .where(Quotation.id == quotation_id, Quotation.company_id == company_id)
        .options(selectinload(Quotation.items))
    )
    if qtn is None:
        raise NotFoundError("Quotation not found")

    existing = await db.scalar(
        select(CmPlan).where(
            CmPlan.company_id == company_id,
            CmPlan.quotation_id == quotation_id,
            CmPlan.docstatus == DOCSTATUS_DRAFT,
        )
    )
    settings = await get_planning_settings(db, company_id)
    warnings: list[str] = []

    if existing is not None:
        plan = await _get_plan(db, existing.id, company_id)
        baseline = next((s for s in plan.scenarios if s.is_baseline), None)
        if baseline is None:
            raise ValidationError("Draft plan has no baseline scenario")
    else:
        name = await get_next_name(db, _SERIES, company_id)
        plan = CmPlan(
            company_id=company_id,
            name=name,
            quotation_id=quotation_id,
            template_id=settings.default_template,
            target_cm1_pct=settings.target_cm1_pct,
            target_cm2_pct=settings.target_cm2_pct,
            min_cm1_pct=settings.min_cm1_pct,
            submit_policy=settings.submit_policy,
            allocations=_alloc_snapshot(settings.allocations),
            owner=user.id,
            modified_by=user.id,
        )
        db.add(plan)
        await db.flush()
        baseline = CmPlanScenario(
            cm_plan_id=plan.id,
            name="Baseline",
            is_baseline=True,
            sort_order=0,
            overrides={},
        )
        db.add(baseline)
        await db.flush()

    alloc = _allocations_for_plan(plan, settings.allocations)
    drivers = await _drivers_for_plan(db, plan, company_id, alloc)
    await _populate_scenario(
        db,
        company_id,
        baseline,
        lines=_lines_from_quotation(list(qtn.items)),
        customer_id=qtn.customer_id,
        sales_partner_id=qtn.sales_partner_id,
        shipping_rule_id=getattr(qtn, "shipping_rule_id", None),
        freight_override=None,
        material_cost_factor=Decimal("1"),
        labor_cost_factor=Decimal("1"),
        allocations=alloc,
        target_cm1_pct=plan.target_cm1_pct,
        warnings=warnings,
        drivers=drivers,
    )
    plan.warnings = warnings
    plan.modified_by = user.id
    await log_audit(
        db,
        doctype="Contribution Margin Plan",
        document_id=plan.id,
        action="UPDATE",
        user_id=user.id,
        company_id=plan.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, company_id)


async def seed_from_sales_order(
    db: AsyncSession, sales_order_id: uuid.UUID, user: CurrentUser
) -> CmPlan:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    company_id = user.company_id
    so = await db.scalar(
        select(SalesOrder)
        .where(SalesOrder.id == sales_order_id, SalesOrder.company_id == company_id)
        .options(selectinload(SalesOrder.items))
    )
    if so is None:
        raise NotFoundError("Sales Order not found")

    settings = await get_planning_settings(db, company_id)
    warnings: list[str] = []
    # One open (draft) plan per SO — reuse if present
    existing = await db.scalar(
        select(CmPlan)
        .where(
            CmPlan.company_id == company_id,
            CmPlan.sales_order_id == sales_order_id,
            CmPlan.docstatus == DOCSTATUS_DRAFT,
        )
        .options(
            selectinload(CmPlan.scenarios).selectinload(CmPlanScenario.items),
            selectinload(CmPlan.scenarios).selectinload(CmPlanScenario.costs),
        )
    )
    if existing is not None:
        plan = existing
        baseline = next((s for s in plan.scenarios if s.is_baseline), None)
        if baseline is None:
            baseline = CmPlanScenario(
                cm_plan_id=plan.id, name="Baseline", is_baseline=True, sort_order=0, overrides={}
            )
            db.add(baseline)
            await db.flush()
        drivers = await _drivers_for_plan(
            db, plan, company_id, _allocations_for_plan(plan, settings.allocations)
        )
        await _populate_scenario(
            db,
            company_id,
            baseline,
            lines=_lines_from_sales_order(list(so.items)),
            customer_id=so.customer_id,
            sales_partner_id=so.sales_partner_id,
            shipping_rule_id=getattr(so, "shipping_rule_id", None),
            freight_override=None,
            material_cost_factor=Decimal("1"),
            labor_cost_factor=Decimal("1"),
            allocations=_allocations_for_plan(plan, settings.allocations),
            target_cm1_pct=plan.target_cm1_pct,
            warnings=warnings,
            drivers=drivers,
        )
        plan.warnings = warnings
        plan.modified_by = user.id
        await db.commit()
        return await _get_plan(db, plan.id, company_id)

    name = await get_next_name(db, _SERIES, company_id)
    plan = CmPlan(
        company_id=company_id,
        name=name,
        sales_order_id=sales_order_id,
        quotation_id=so.quotation_id,
        template_id=settings.default_template,
        target_cm1_pct=settings.target_cm1_pct,
        target_cm2_pct=settings.target_cm2_pct,
        min_cm1_pct=settings.min_cm1_pct,
        submit_policy=settings.submit_policy,
        allocations=_alloc_snapshot(settings.allocations),
        owner=user.id,
        modified_by=user.id,
    )
    db.add(plan)
    await db.flush()
    baseline = CmPlanScenario(
        cm_plan_id=plan.id, name="Baseline", is_baseline=True, sort_order=0, overrides={}
    )
    db.add(baseline)
    await db.flush()
    drivers = await _drivers_for_plan(
        db, plan, company_id, _allocations_for_plan(plan, settings.allocations)
    )
    await _populate_scenario(
        db,
        company_id,
        baseline,
        lines=_lines_from_sales_order(list(so.items)),
        customer_id=so.customer_id,
        sales_partner_id=so.sales_partner_id,
        shipping_rule_id=getattr(so, "shipping_rule_id", None),
        freight_override=None,
        material_cost_factor=Decimal("1"),
        labor_cost_factor=Decimal("1"),
        allocations=_allocations_for_plan(plan, settings.allocations),
        target_cm1_pct=plan.target_cm1_pct,
        warnings=warnings,
        drivers=drivers,
    )
    plan.warnings = warnings
    await log_audit(
        db,
        doctype="Contribution Margin Plan",
        document_id=plan.id,
        action="INSERT",
        user_id=user.id,
        company_id=plan.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, company_id)


def _merge_overrides(base: dict[str, Any] | None, patch: CmScenarioOverrides | None) -> dict[str, Any]:
    out = dict(base or {})
    if patch is None:
        return out
    data = patch.model_dump(exclude_unset=True, mode="json")
    for k, v in data.items():
        if k == "items":
            # Never persist an empty items[] — it blocks clarity and used to look like a rate was set.
            if v:
                out["items"] = v
            elif "items" in out:
                del out["items"]
        elif v is None and k in out:
            del out[k]
        elif v is not None:
            out[k] = v if not isinstance(v, uuid.UUID) else str(v)
    # normalize UUID fields to strings for JSONB
    for key in ("customer_id", "sales_partner_id", "shipping_rule_id"):
        if key in out and out[key] is not None:
            out[key] = str(out[key])
    return _json_safe(out)


def _json_safe(value: Any) -> Any:
    """Ensure overrides are JSONB-safe (no Decimal / UUID)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _uuid_or_none(val: Any) -> uuid.UUID | None:
    if val is None:
        return None
    return uuid.UUID(str(val))


async def create_scenario(
    db: AsyncSession, plan_id: uuid.UUID, payload: CmPlanScenarioCreate, user: CurrentUser
) -> CmPlan:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    plan = await _get_plan(db, plan_id, user.company_id)
    if plan.docstatus != DOCSTATUS_DRAFT:
        raise ValidationError("Only draft plans accept new scenarios")
    settings = await get_planning_settings(db, user.company_id)
    if len(plan.scenarios) >= settings.max_scenarios_per_plan:
        raise ValidationError(
            f"Max {settings.max_scenarios_per_plan} scenarios per plan",
            code="CM_MAX_SCENARIOS",
        )

    clone_from = None
    if payload.clone_from_scenario_id:
        clone_from = next((s for s in plan.scenarios if s.id == payload.clone_from_scenario_id), None)
        if clone_from is None:
            raise NotFoundError("Clone source scenario not found")
    else:
        clone_from = next((s for s in plan.scenarios if s.is_baseline), None)
        if clone_from is None:
            raise ValidationError("Plan has no baseline to clone")

    overrides = _merge_overrides(clone_from.overrides if clone_from else {}, payload.overrides)
    scenario = CmPlanScenario(
        cm_plan_id=plan.id,
        name=payload.name,
        is_baseline=False,
        sort_order=max((s.sort_order for s in plan.scenarios), default=0) + 1,
        overrides=overrides,
    )
    db.add(scenario)
    await db.flush()
    await _recompute_scenario(db, plan, scenario, user)
    await log_audit(
        db,
        doctype="Contribution Margin Plan",
        document_id=plan.id,
        action="UPDATE",
        user_id=user.id,
        company_id=plan.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, user.company_id)


async def update_scenario(
    db: AsyncSession,
    plan_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: CmPlanScenarioUpdate,
    user: CurrentUser,
) -> CmPlan:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    plan = await _get_plan(db, plan_id, user.company_id)
    if plan.docstatus != DOCSTATUS_DRAFT:
        raise ValidationError("Only draft plans can be edited")
    scenario = next((s for s in plan.scenarios if s.id == scenario_id), None)
    if scenario is None:
        raise NotFoundError("Scenario not found")
    if payload.name is not None:
        scenario.name = payload.name
    if payload.overrides is not None:
        if scenario.is_baseline:
            # Baseline overrides still allowed for what-if on baseline itself,
            # but keep is_baseline; merge onto existing
            scenario.overrides = _merge_overrides(scenario.overrides, payload.overrides)
        else:
            scenario.overrides = _merge_overrides(scenario.overrides, payload.overrides)
    await _recompute_scenario(db, plan, scenario, user)
    await db.commit()
    return await _get_plan(db, plan.id, user.company_id)


async def _recompute_scenario(
    db: AsyncSession,
    plan: CmPlan,
    scenario: CmPlanScenario,
    user: CurrentUser,
    *,
    base_lines: list[dict[str, Any]] | None = None,
) -> None:
    settings = await get_planning_settings(db, plan.company_id)
    baseline = next((s for s in plan.scenarios if s.is_baseline), None)
    if baseline is None:
        raise ValidationError("Missing baseline")

    ov = scenario.overrides or {}
    # Copy so per-scenario rate/qty overrides don't mutate a shared snapshot.
    lines = [dict(row) for row in (base_lines if base_lines is not None else await _baseline_lines(db, plan))]

    # Header-level rate/qty apply to all lines first; per-line items[] can still override.
    header_rate = ov.get("selling_rate")
    header_qty = ov.get("qty")
    if header_rate is not None:
        rate = Decimal(str(header_rate))
        for line in lines:
            line["selling_rate"] = rate
    if header_qty is not None:
        qty = Decimal(str(header_qty))
        for line in lines:
            line["qty"] = qty

    item_overrides = {str(r.get("line_key")): r for r in (ov.get("items") or []) if r.get("line_key")}
    for line in lines:
        patch = item_overrides.get(line["line_key"])
        if not patch:
            continue
        if "qty" in patch and patch["qty"] is not None:
            line["qty"] = Decimal(str(patch["qty"]))
        if "selling_rate" in patch and patch["selling_rate"] is not None:
            line["selling_rate"] = Decimal(str(patch["selling_rate"]))

    customer_id = _uuid_or_none(ov.get("customer_id")) or baseline.customer_id
    partner_id = _uuid_or_none(ov.get("sales_partner_id")) or baseline.sales_partner_id
    shipping_id = _uuid_or_none(ov.get("shipping_rule_id")) or baseline.shipping_rule_id
    freight_override = (
        Decimal(str(ov["freight_amount"])) if ov.get("freight_amount") is not None else None
    )
    mat_f = Decimal(str(ov.get("material_cost_factor") or "1"))
    lab_f = Decimal(str(ov.get("labor_cost_factor") or "1"))
    warnings: list[str] = list(plan.warnings or []) if isinstance(plan.warnings, list) else []

    # Optional reprice via selling pricing ladder when customer changes
    if ov.get("reprice") and customer_id is not None:
        from datetime import date as date_cls

        from app.models.selling import Customer
        from app.services.pricing import apply_selling_pricing

        customer = await db.scalar(
            select(Customer).where(Customer.id == customer_id, Customer.company_id == plan.company_id)
        )
        for line in lines:
            if line.get("item_id") is None:
                continue
            item = await db.get(Item, line["item_id"])
            if item is None or item.company_id != plan.company_id:
                continue
            try:
                priced = await apply_selling_pricing(
                    db,
                    plan.company_id,
                    item=item,
                    customer=customer,
                    qty=Decimal(str(line["qty"])),
                    base_rate=Decimal(str(line["selling_rate"])),
                    on_date=date_cls.today(),
                )
                line["selling_rate"] = Decimal(str(priced.rate))
            except Exception:  # noqa: BLE001 — pricing miss is non-fatal
                warnings.append(f"Could not reprice line {line.get('line_key')}")

    await _populate_scenario(
        db,
        plan.company_id,
        scenario,
        lines=lines,
        customer_id=customer_id,
        sales_partner_id=partner_id,
        shipping_rule_id=shipping_id,
        freight_override=freight_override,
        material_cost_factor=mat_f,
        labor_cost_factor=lab_f,
        allocations=_allocations_for_plan(plan, settings.allocations),
        target_cm1_pct=plan.target_cm1_pct,
        warnings=warnings,
        drivers=await _drivers_for_plan(
            db, plan, plan.company_id, _allocations_for_plan(plan, settings.allocations)
        ),
        keep_manual=bool((scenario.overrides or {}).get("keep_manual")),
    )
    plan.modified_by = user.id
    await db.flush()


async def update_plan(
    db: AsyncSession, plan_id: uuid.UUID, payload: CmPlanUpdate, user: CurrentUser
) -> CmPlan:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    plan = await _get_plan(db, plan_id, user.company_id)
    if plan.docstatus != DOCSTATUS_DRAFT:
        raise ValidationError("Only draft plans can be edited")

    data = payload.model_dump(exclude_unset=True)
    recompute = bool(data.pop("recompute", True))
    if "target_cm1_pct" in data and data["target_cm1_pct"] is not None:
        plan.target_cm1_pct = data["target_cm1_pct"]
    if "target_cm2_pct" in data:
        plan.target_cm2_pct = data["target_cm2_pct"]
    if "min_cm1_pct" in data and data["min_cm1_pct"] is not None:
        plan.min_cm1_pct = data["min_cm1_pct"]
    if "submit_policy" in data and data["submit_policy"] is not None:
        plan.submit_policy = data["submit_policy"]
    if "remarks" in data:
        plan.remarks = data["remarks"]
    if "cost_drivers" in data and data["cost_drivers"] is not None:
        from app.services.cm_planning.compat import allocations_from_drivers
        from app.services.cm_planning.structure import drivers_to_snapshot
        from app.services.cm_planning.types import DriverDef

        normalized = [DriverDef.from_dict(raw) for raw in data["cost_drivers"]]
        plan.cost_structure_snapshot = drivers_to_snapshot(normalized)
        plan.allocations = _alloc_snapshot(allocations_from_drivers(normalized))
    elif "allocations" in data and data["allocations"] is not None:
        from app.services.cm_planning.compat import sync_allocations_into_drivers
        from app.services.cm_planning.structure import drivers_to_snapshot, snapshot_to_drivers

        plan.allocations = _alloc_snapshot(data["allocations"])
        snap = plan.cost_structure_snapshot
        if isinstance(snap, list) and snap:
            synced = sync_allocations_into_drivers(
                snapshot_to_drivers(snap), plan.allocations or {}
            )
            plan.cost_structure_snapshot = drivers_to_snapshot(synced)

    plan.modified_by = user.id
    if recompute:
        # Snapshot baseline lines BEFORE clearing any scenario — otherwise
        # recomputing baseline empties in-memory items and siblings get ₹0.
        base_lines = await _baseline_lines(db, plan)
        if not base_lines:
            raise ValidationError(
                "Baseline has no line items — re-seed from Quotation before recomputing",
                code="CM_BASELINE_EMPTY",
            )
        ordered = sorted(plan.scenarios, key=lambda s: (0 if s.is_baseline else 1, s.sort_order))
        for scenario in ordered:
            await _recompute_scenario(db, plan, scenario, user, base_lines=base_lines)

    await log_audit(
        db,
        doctype="Contribution Margin Plan",
        document_id=plan.id,
        action="UPDATE",
        user_id=user.id,
        company_id=plan.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, user.company_id)


async def actual_comparison(
    db: AsyncSession,
    plan_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    from_date: date,
    to_date: date,
    scenario_id: uuid.UUID | None = None,
) -> CmActualComparison:
    """Compare a plan scenario (default: baseline) to GL Contribution Margin actuals."""
    plan = await _get_plan(db, plan_id, company_id)
    if scenario_id is not None:
        scenario = next((s for s in plan.scenarios if s.id == scenario_id), None)
        if scenario is None:
            raise NotFoundError("Scenario not found")
    else:
        scenario = next((s for s in plan.scenarios if s.is_baseline), None)
        if scenario is None:
            raise ValidationError("Plan has no baseline scenario")

    estimated = {
        "revenue": Decimal(scenario.revenue or 0),
        "variable_cost": Decimal(scenario.variable_cost or 0),
        "product_channel_fixed": Decimal(scenario.product_channel_fixed or 0),
        "segment_bu_fixed": Decimal(scenario.segment_bu_fixed or 0),
        "corporate_overhead": Decimal(scenario.corporate_overhead or 0),
        "cm1": Decimal(scenario.cm1 or 0),
        "cm2": Decimal(scenario.cm2 or 0),
        "cm3": Decimal(scenario.cm3 or 0),
        "operating_profit": Decimal(scenario.operating_profit or 0),
    }
    report = await gl_contribution_margin(
        db, company_id, from_date=from_date, to_date=to_date
    )
    actual = {
        "revenue": report.revenue,
        "variable_cost": report.variable_cost,
        "product_channel_fixed": report.product_channel_fixed,
        "segment_bu_fixed": report.segment_bu_fixed,
        "corporate_overhead": report.corporate_overhead,
        "cm1": report.cm1,
        "cm2": report.cm2,
        "cm3": report.cm3,
        "operating_profit": report.operating_profit,
    }
    warnings = list(report.warnings or [])
    warnings.append(
        "Estimated figures are from the CM Plan scenario (deal-level). "
        "Actuals are company GL totals for the period — scale differs unless filtered later."
    )
    rows = build_actual_comparison_rows(estimated, actual)
    from app.models.cm_planning import CmPlanVarianceRun

    run = CmPlanVarianceRun(
        company_id=company_id,
        cm_plan_id=plan.id,
        scenario_id=scenario.id,
        from_date=datetime.combine(from_date, datetime.min.time(), tzinfo=timezone.utc),
        to_date=datetime.combine(to_date, datetime.max.time(), tzinfo=timezone.utc),
        rows=[r.model_dump(mode="json") for r in rows],
        warnings=warnings,
    )
    db.add(run)
    await db.flush()
    return CmActualComparison(
        plan_id=plan.id,
        scenario_id=scenario.id,
        scenario_name=scenario.name,
        from_date=from_date,
        to_date=to_date,
        rows=rows,
        warnings=warnings,
    )


async def compare_scenarios(
    db: AsyncSession, plan_id: uuid.UUID, payload: CmCompareRequest, company_id: uuid.UUID
) -> CmCompareResponse:
    plan = await _get_plan(db, plan_id, company_id)
    by_id = {s.id: s for s in plan.scenarios}
    selected = []
    for sid in payload.scenario_ids:
        if sid not in by_id:
            raise NotFoundError(f"Scenario {sid} not found")
        selected.append(by_id[sid])
    baseline = next((s for s in plan.scenarios if s.is_baseline), selected[0])
    base_w = _scenario_to_waterfall(baseline)
    columns: list[CmCompareScenarioColumn] = []
    for s in selected:
        w = _scenario_to_waterfall(s)
        deltas: list[CmCompareFieldDelta] = []
        if s.id != baseline.id:
            diff = diff_waterfalls(base_w, w, other_label="scenario")
            for row in diff["rows"]:
                deltas.append(
                    CmCompareFieldDelta(
                        field=row["field"],
                        baseline=row["baseline"],
                        scenario=row["scenario"],
                        delta=row["delta"],
                        delta_pct=row["delta_pct"],
                    )
                )
        columns.append(
            CmCompareScenarioColumn(
                scenario_id=s.id,
                name=s.name,
                is_baseline=s.is_baseline,
                revenue=s.revenue,
                variable_cost=s.variable_cost,
                cm1=s.cm1,
                cm2=s.cm2,
                cm3=s.cm3,
                operating_profit=s.operating_profit,
                cm1_pct=s.cm1_pct,
                min_selling_total=s.min_selling_total,
                deltas_vs_baseline=deltas,
            )
        )
    return CmCompareResponse(plan_id=plan.id, columns=columns)


def compare_to_csv(result: CmCompareResponse) -> str:
    """Export scenario compare as CSV (metric rows × scenario columns)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["metric"] + [c.name for c in result.columns]
    writer.writerow(header)

    def row(label: str, attr: str) -> None:
        writer.writerow([label] + [getattr(c, attr) for c in result.columns])

    row("revenue", "revenue")
    row("variable_cost", "variable_cost")
    row("cm1", "cm1")
    row("cm1_pct", "cm1_pct")
    row("cm2", "cm2")
    row("cm3", "cm3")
    row("operating_profit", "operating_profit")
    row("min_selling_total", "min_selling_total")
    return buf.getvalue()


async def delete_scenario(
    db: AsyncSession, plan_id: uuid.UUID, scenario_id: uuid.UUID, user: CurrentUser
) -> CmPlan:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    plan = await _get_plan(db, plan_id, user.company_id)
    if plan.docstatus != DOCSTATUS_DRAFT:
        raise ValidationError("Only draft plans allow scenario deletion")
    scenario = next((s for s in plan.scenarios if s.id == scenario_id), None)
    if scenario is None:
        raise NotFoundError("Scenario not found")
    if scenario.is_baseline:
        raise ValidationError("Cannot delete the baseline scenario", code="CM_BASELINE_LOCKED")
    await _clear_scenario_children(db, scenario)
    await db.delete(scenario)
    plan.modified_by = user.id
    await log_audit(
        db,
        doctype="Contribution Margin Plan",
        document_id=plan.id,
        action="UPDATE",
        user_id=user.id,
        company_id=plan.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, user.company_id)


async def apply_scenario_to_quotation(
    db: AsyncSession, plan_id: uuid.UUID, scenario_id: uuid.UUID, user: CurrentUser
) -> CmPlan:
    """Copy scenario qty/rates onto the linked draft Quotation and recalculate taxes/totals."""
    if user.company_id is None:
        raise ValidationError("An active company is required")
    plan = await _get_plan(db, plan_id, user.company_id)
    if plan.quotation_id is None:
        raise ValidationError("Plan is not linked to a Quotation")
    scenario = next((s for s in plan.scenarios if s.id == scenario_id), None)
    if scenario is None:
        raise NotFoundError("Scenario not found")
    qtn = await db.scalar(
        select(Quotation)
        .where(Quotation.id == plan.quotation_id, Quotation.company_id == user.company_id)
        .options(selectinload(Quotation.items), selectinload(Quotation.taxes))
    )
    if qtn is None:
        raise NotFoundError("Quotation not found")
    if qtn.docstatus != DOCSTATUS_DRAFT:
        raise ValidationError("Quotation must be draft to apply scenario rates")

    by_qitem = {it.quotation_item_id: it for it in scenario.items if it.quotation_item_id}
    by_item = {it.item_id: it for it in scenario.items if it.item_id is not None}
    applied = 0
    for q_item in qtn.items:
        src = by_qitem.get(q_item.id)
        if src is None and q_item.item_id is not None:
            src = by_item.get(q_item.item_id)
        if src is None:
            continue
        q_item.qty = src.qty
        q_item.rate = src.selling_rate
        # Clear line discounts so the applied selling rate is the net rate.
        q_item.discount_percentage = ZERO
        q_item.discount_amount = ZERO
        q_item.price_list_rate = src.selling_rate
        applied += 1

    if applied == 0:
        raise ValidationError(
            "No Quotation lines matched this scenario — re-seed the plan from the Quotation",
            code="CM_APPLY_NO_MATCH",
        )

    if scenario.sales_partner_id is not None:
        qtn.sales_partner_id = scenario.sales_partner_id
    if scenario.shipping_rule_id is not None:
        qtn.shipping_rule_id = scenario.shipping_rule_id

    _recalculate_quotation_totals(qtn)

    scenario.applied_at = datetime.now(timezone.utc)
    qtn.modified_by = user.id
    await log_audit(
        db,
        doctype="Quotation",
        document_id=qtn.id,
        action="UPDATE",
        user_id=user.id,
        company_id=qtn.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, user.company_id)


def _recalculate_quotation_totals(qtn: Quotation) -> None:
    """Re-run taxes & totals after CM rates were written onto draft lines."""
    from app.services.taxes_and_totals import ItemRow, TaxRow, calculate_taxes_and_totals

    engine_items = [
        ItemRow(
            qty=Decimal(row.qty or 0),
            rate=Decimal(row.rate or 0),
            price_list_rate=Decimal(row.price_list_rate or row.rate or 0),
            discount_percentage=Decimal(row.discount_percentage or 0),
            discount_amount=Decimal(row.discount_amount or 0),
        )
        for row in qtn.items
    ]
    engine_taxes = [
        TaxRow(
            charge_type=t.charge_type,
            rate=Decimal(t.rate or 0),
            tax_amount=Decimal(t.tax_amount or 0),
            row_id=t.row_id,
            account_head_id=t.account_head_id,
            included_in_print_rate=bool(t.included_in_print_rate),
        )
        for t in (qtn.taxes or [])
    ]
    totals = calculate_taxes_and_totals(
        engine_items,
        engine_taxes,
        conversion_rate=Decimal(qtn.conversion_rate or 1),
        apply_discount_on=qtn.apply_discount_on or "Grand Total",
        additional_discount_percentage=Decimal(qtn.additional_discount_percentage or 0),
        discount_amount=Decimal(qtn.discount_amount or 0),
    )
    for row, eng in zip(qtn.items, engine_items, strict=True):
        row.rate = eng.rate
        row.amount = eng.amount
        row.base_rate = eng.base_rate
        row.base_amount = eng.base_amount
        row.net_amount = eng.net_amount
        row.base_net_amount = eng.base_net_amount
        row.price_list_rate = eng.price_list_rate or eng.rate
        row.base_price_list_rate = eng.base_price_list_rate
        row.discount_percentage = eng.discount_percentage
        row.discount_amount = eng.discount_amount
    for tax, eng in zip(qtn.taxes or [], engine_taxes, strict=True):
        tax.tax_amount = eng.tax_amount
        tax.total = eng.total
        tax.base_tax_amount = eng.base_tax_amount
        tax.base_total = eng.base_total
    qtn.total_qty = totals.total_qty
    qtn.total = totals.total
    qtn.base_total = totals.base_total
    qtn.net_total = totals.net_total
    qtn.base_net_total = totals.base_net_total
    qtn.total_taxes_and_charges = totals.total_taxes_and_charges
    qtn.base_total_taxes_and_charges = totals.base_total_taxes_and_charges
    qtn.discount_amount = totals.discount_amount
    qtn.grand_total = totals.grand_total
    qtn.base_grand_total = totals.base_grand_total
    qtn.rounded_total = totals.rounded_total
    qtn.rounding_adjustment = totals.rounding_adjustment


async def submit_plan(db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser) -> CmPlan:
    plan = await _get_plan(db, plan_id, user.company_id)  # type: ignore[arg-type]
    if plan.docstatus != DOCSTATUS_DRAFT:
        raise ValidationError("Only draft plans can be submitted")
    plan.docstatus = DOCSTATUS_SUBMITTED
    plan.modified_by = user.id
    await log_audit(
        db,
        doctype="Contribution Margin Plan",
        document_id=plan.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=plan.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, user.company_id)  # type: ignore[arg-type]


async def cancel_plan(db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser) -> CmPlan:
    plan = await _get_plan(db, plan_id, user.company_id)  # type: ignore[arg-type]
    if plan.docstatus != DOCSTATUS_SUBMITTED:
        raise ValidationError("Only submitted plans can be cancelled")
    plan.docstatus = DOCSTATUS_CANCELLED
    plan.modified_by = user.id
    await log_audit(
        db,
        doctype="Contribution Margin Plan",
        document_id=plan.id,
        action="CANCEL",
        user_id=user.id,
        company_id=plan.company_id,
    )
    await db.commit()
    return await _get_plan(db, plan.id, user.company_id)  # type: ignore[arg-type]


def evaluate_margin_policy(
    *,
    policy: str,
    cm1_pct: Decimal | None,
    min_cm1_pct: Decimal,
    raise_on_block: bool = True,
    cm2_pct: Decimal | None = None,
    target_cm2_pct: Decimal | None = None,
) -> CmMarginPolicyResult:
    """Pure margin gate used by Quotation/SO submit (and unit tests)."""
    if cm1_pct is None:
        return CmMarginPolicyResult(
            ok=True,
            policy=policy,
            cm1_pct=None,
            min_cm1_pct=min_cm1_pct,
            message="Baseline CM% unavailable",
        )
    ok = Decimal(cm1_pct) >= Decimal(min_cm1_pct)
    msg = None if ok else f"CM1 {cm1_pct}% is below minimum {min_cm1_pct}%"
    if ok and target_cm2_pct is not None and cm2_pct is not None:
        if Decimal(cm2_pct) < Decimal(target_cm2_pct):
            ok = False
            msg = f"CM2 {cm2_pct}% is below target {target_cm2_pct}%"
    if not ok and policy == "block" and raise_on_block:
        raise ValidationError(msg or "CM below minimum", code="CM_MARGIN_BLOCK")
    return CmMarginPolicyResult(
        ok=ok,
        policy=policy,
        cm1_pct=cm1_pct,
        min_cm1_pct=min_cm1_pct,
        message=msg,
    )


async def _latest_plan_for_source(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    quotation_id: uuid.UUID | None = None,
    sales_order_id: uuid.UUID | None = None,
) -> CmPlan | None:
    filters = [
        CmPlan.company_id == company_id,
        CmPlan.docstatus.in_([DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED]),
    ]
    if quotation_id is not None:
        filters.append(CmPlan.quotation_id == quotation_id)
    elif sales_order_id is not None:
        filters.append(CmPlan.sales_order_id == sales_order_id)
    else:
        return None
    return await db.scalar(
        select(CmPlan)
        .where(*filters)
        .options(selectinload(CmPlan.scenarios))
        .order_by(CmPlan.creation.desc())
    )


async def assert_margin_policy_for_quotation(
    db: AsyncSession, quotation_id: uuid.UUID, company_id: uuid.UUID
) -> CmMarginPolicyResult:
    settings = await get_planning_settings(db, company_id)
    plan = await _latest_plan_for_source(db, company_id, quotation_id=quotation_id)
    if plan is None:
        return CmMarginPolicyResult(
            ok=True,
            policy=settings.submit_policy,
            cm1_pct=None,
            min_cm1_pct=settings.min_cm1_pct,
            message="No CM plan — policy skipped",
        )
    baseline = next((s for s in plan.scenarios if s.is_baseline), None)
    return evaluate_margin_policy(
        policy=plan.submit_policy,
        cm1_pct=baseline.cm1_pct if baseline else None,
        min_cm1_pct=plan.min_cm1_pct,
    )


async def assert_margin_policy_for_sales_order(
    db: AsyncSession, sales_order_id: uuid.UUID, company_id: uuid.UUID
) -> CmMarginPolicyResult:
    settings = await get_planning_settings(db, company_id)
    plan = await _latest_plan_for_source(db, company_id, sales_order_id=sales_order_id)
    if plan is None:
        return CmMarginPolicyResult(
            ok=True,
            policy=settings.submit_policy,
            cm1_pct=None,
            min_cm1_pct=settings.min_cm1_pct,
            message="No CM plan — policy skipped",
        )
    baseline = next((s for s in plan.scenarios if s.is_baseline), None)
    return evaluate_margin_policy(
        policy=plan.submit_policy,
        cm1_pct=baseline.cm1_pct if baseline else None,
        min_cm1_pct=plan.min_cm1_pct,
    )


async def preview_estimate(
    db: AsyncSession, payload: CmPreviewRequest, company_id: uuid.UUID
) -> dict[str, Any]:
    settings = await get_planning_settings(db, company_id)
    if payload.template_id:
        tpl = load_planning_template(payload.template_id)
        alloc = {
            k: Decimal(str(v)) for k, v in (tpl.get("allocations") or {}).items()
        } or dict(settings.allocations)
        target = Decimal(str(tpl.get("target_cm1_pct", settings.target_cm1_pct)))
    else:
        alloc = dict(settings.allocations)
        target = settings.target_cm1_pct

    warnings: list[str] = []
    lines = [
        {
            "line_key": row.line_key or str(i),
            "item_id": row.item_id,
            "item_name": row.item_name or "Item",
            "qty": row.qty,
            "selling_rate": row.rate,
        }
        for i, row in enumerate(payload.items)
    ]
    # ephemeral scenario shell
    scenario = CmPlanScenario(
        id=uuid.uuid4(),
        cm_plan_id=uuid.uuid4(),
        name="Preview",
        is_baseline=True,
        sort_order=0,
        overrides={},
    )
    # Attach empty collections for populate
    scenario.items = []
    scenario.costs = []
    # Use in-memory populate without persisting plan — call estimate path directly
    estimate_lines: list[LineEstimateIn] = []
    subtotal = ZERO
    for raw in lines:
        item = await db.get(Item, raw["item_id"]) if raw["item_id"] else None
        if item is not None and item.company_id != company_id:
            item = None
        costs = await resolve_line_unit_costs(db, company_id, item)
        qty = Decimal(str(raw["qty"]))
        rate = Decimal(str(raw["selling_rate"]))
        subtotal += qty * rate
        estimate_lines.append(
            LineEstimateIn(
                line_key=raw["line_key"],
                item_id=raw["item_id"],
                qty=qty,
                selling_rate=rate,
                material_per_unit=costs.material_per_unit,
                labor_per_unit=costs.labor_per_unit,
                packaging_per_unit=costs.packaging_per_unit,
            )
        )
    freight, _ = await resolve_freight_amount(
        db,
        company_id,
        shipping_rule_id=payload.shipping_rule_id,
        freight_override=payload.freight_amount,
        item_subtotal=subtotal,
    )
    commission_pct = await resolve_commission_rate(db, company_id, payload.sales_partner_id)
    waterfall, details, eng = estimate_from_lines(
        EstimateInput(
            lines=estimate_lines,
            freight_total=freight,
            commission_rate_pct=commission_pct,
            material_cost_factor=payload.material_cost_factor,
            labor_cost_factor=payload.labor_cost_factor,
            allocations=alloc,
            target_cm1_pct=target,
        )
    )
    return {
        "waterfall": _waterfall_to_scenario_fields(waterfall),
        "lines": details,
        "warnings": warnings + eng.warnings,
        "explanations": [leaf.explanation.to_dict() for leaf in eng.leaves],
    }


def to_response(plan: CmPlan) -> CmPlanResponse:
    return CmPlanResponse.model_validate(plan)
