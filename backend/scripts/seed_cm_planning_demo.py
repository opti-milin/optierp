"""Idempotent CM Planning demo seed — quotation + plans + rich cost-rule trees.

Usage (Compose Postgres)::

    docker compose exec backend python -m scripts.seed_cm_planning_demo \\
      --database-url "postgresql+asyncpg://erp_owner:milin@postgres:5432/erp"

Also refreshes cost-driver trees on ``CM-PLAN-2026-00001`` / ``00002`` when present
(``--refresh-rules``, default on).
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.database import set_company_context
from app.core.security import CurrentUser
from app.models.cm_planning import CmCostRate, CmPlan
from app.models.core import Company, User
from app.models.selling import Customer, Quotation
from app.models.stock import Item
from app.schemas.buying import OrderItemIn
from app.schemas.cm_planning import CmCostDriverIn, CmPlanScenarioCreate, CmPlanUpdate, CmScenarioOverrides
from app.schemas.selling import QuotationCreate
from app.services import quotation as qtn_service
from app.services.cm_planning import plan as cm_plan
from app.services.cm_planning.compat import flatten_template_drivers


# Plan 00001 — balanced manufacturing mix (%, fixed, rate×qty)
_DEMO_RULES_PLAN_01: list[dict[str, Any]] = [
    {
        "code": "variable_cost",
        "label": "Variable Cost",
        "is_group": True,
        "cm_class": "variable_cost",
        "children": [
            {
                "code": "material",
                "label": "Material",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "bom_material",
                "scope": "line",
            },
            {
                "code": "labor",
                "label": "Labor",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "bom_operating",
                "scope": "line",
            },
            {
                "code": "freight",
                "label": "Freight",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "shipping_rule",
                "scope": "header",
            },
            {
                "code": "commission",
                "label": "Commission",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "sales_partner",
                "scope": "header",
            },
            {
                "code": "packaging",
                "label": "Packaging",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "cost_rate",
                "scope": "line",
            },
            {
                "code": "demo_consumables",
                "label": "Demo consumables",
                "cm_class": "variable_cost",
                "allocation_method": "fixed_amount",
                "method_params": {"amount": 350},
                "scope": "header",
                "is_system": False,
            },
        ],
    },
    {
        "code": "product_channel_group",
        "label": "Product / Channel",
        "is_group": True,
        "cm_class": "product_channel_fixed",
        "children": [
            {
                "code": "product_channel_fixed",
                "label": "Channel allocation",
                "cm_class": "product_channel_fixed",
                "allocation_method": "percentage",
                "allocation_basis": "revenue",
                "method_params": {"pct": 4},
                "scope": "plan",
                "is_system": False,
            },
            {
                "code": "demo_sku_support",
                "label": "SKU support (fixed)",
                "cm_class": "product_channel_fixed",
                "allocation_method": "fixed_amount",
                "method_params": {"amount": 800},
                "scope": "plan",
                "is_system": False,
            },
        ],
    },
    {
        "code": "segment_bu_group",
        "label": "Segment / BU",
        "is_group": True,
        "cm_class": "segment_bu_fixed",
        "children": [
            {
                "code": "segment_bu_fixed",
                "label": "BU allocation",
                "cm_class": "segment_bu_fixed",
                "allocation_method": "percentage",
                "allocation_basis": "revenue",
                "method_params": {"pct": 2},
                "scope": "plan",
                "is_system": False,
            },
            {
                "code": "demo_handling",
                "label": "Handling (₹ / qty)",
                "cm_class": "segment_bu_fixed",
                "allocation_method": "rate_times_basis",
                "allocation_basis": "quantity",
                "method_params": {"rate": 12},
                "scope": "plan",
                "is_system": False,
            },
        ],
    },
    {
        "code": "corporate_overhead",
        "label": "Corporate Overhead",
        "is_group": True,
        "cm_class": "corporate_overhead",
        "children": [
            {
                "code": "corporate_overhead_default",
                "label": "Corporate OH %",
                "cm_class": "corporate_overhead",
                "allocation_method": "percentage",
                "allocation_basis": "revenue",
                "method_params": {"pct": 5},
                "scope": "plan",
                "is_system": False,
            },
            {
                "code": "demo_hq_admin",
                "label": "HQ admin (fixed)",
                "cm_class": "corporate_overhead",
                "allocation_method": "fixed_amount",
                "method_params": {"amount": 1200},
                "scope": "plan",
                "is_system": False,
            },
        ],
    },
]

# Plan 00002 — heavier channel + corporate (different mix for compare)
_DEMO_RULES_PLAN_02: list[dict[str, Any]] = [
    {
        "code": "variable_cost",
        "label": "Variable Cost",
        "is_group": True,
        "cm_class": "variable_cost",
        "children": [
            {
                "code": "material",
                "label": "Material",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "bom_material",
                "scope": "line",
            },
            {
                "code": "labor",
                "label": "Labor",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "bom_operating",
                "scope": "line",
            },
            {
                "code": "freight",
                "label": "Freight",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "shipping_rule",
                "scope": "header",
            },
            {
                "code": "commission",
                "label": "Commission",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "sales_partner",
                "scope": "header",
            },
            {
                "code": "packaging",
                "label": "Packaging",
                "cm_class": "variable_cost",
                "allocation_method": "from_source",
                "source": "cost_rate",
                "scope": "line",
            },
            {
                "code": "demo_warranty_reserve",
                "label": "Warranty reserve",
                "cm_class": "variable_cost",
                "allocation_method": "percentage",
                "allocation_basis": "revenue",
                "method_params": {"pct": 1.5},
                "scope": "header",
                "is_system": False,
            },
        ],
    },
    {
        "code": "product_channel_group",
        "label": "Product / Channel",
        "is_group": True,
        "cm_class": "product_channel_fixed",
        "children": [
            {
                "code": "product_channel_fixed",
                "label": "Channel allocation",
                "cm_class": "product_channel_fixed",
                "allocation_method": "percentage",
                "allocation_basis": "revenue",
                "method_params": {"pct": 7},
                "scope": "plan",
                "is_system": False,
            },
            {
                "code": "demo_promo",
                "label": "Promo / listing fees",
                "cm_class": "product_channel_fixed",
                "allocation_method": "fixed_amount",
                "method_params": {"amount": 2500},
                "scope": "plan",
                "is_system": False,
            },
        ],
    },
    {
        "code": "segment_bu_group",
        "label": "Segment / BU",
        "is_group": True,
        "cm_class": "segment_bu_fixed",
        "children": [
            {
                "code": "segment_bu_fixed",
                "label": "BU allocation",
                "cm_class": "segment_bu_fixed",
                "allocation_method": "percentage",
                "allocation_basis": "revenue",
                "method_params": {"pct": 3.5},
                "scope": "plan",
                "is_system": False,
            },
            {
                "code": "demo_field_support",
                "label": "Field support (₹ / qty)",
                "cm_class": "segment_bu_fixed",
                "allocation_method": "rate_times_basis",
                "allocation_basis": "quantity",
                "method_params": {"rate": 25},
                "scope": "plan",
                "is_system": False,
            },
        ],
    },
    {
        "code": "corporate_overhead",
        "label": "Corporate Overhead",
        "is_group": True,
        "cm_class": "corporate_overhead",
        "children": [
            {
                "code": "corporate_overhead_default",
                "label": "Corporate OH %",
                "cm_class": "corporate_overhead",
                "allocation_method": "percentage",
                "allocation_basis": "revenue",
                "method_params": {"pct": 8},
                "scope": "plan",
                "is_system": False,
            },
            {
                "code": "demo_shared_services",
                "label": "Shared services (fixed)",
                "cm_class": "corporate_overhead",
                "allocation_method": "fixed_amount",
                "method_params": {"amount": 2000},
                "scope": "plan",
                "is_system": False,
            },
        ],
    },
]


def _drivers_payload(tree: list[dict[str, Any]]) -> list[CmCostDriverIn]:
    return [CmCostDriverIn(**d.to_dict()) for d in flatten_template_drivers(tree)]


async def apply_demo_cost_rules(
    db: AsyncSession,
    actor: CurrentUser,
    plan_name: str,
    tree: list[dict[str, Any]],
) -> None:
    plan = await db.scalar(
        select(CmPlan).where(CmPlan.company_id == actor.company_id, CmPlan.name == plan_name)
    )
    if plan is None:
        print(f"CM planning seed: {plan_name} not found — skip rules.")
        return
    if plan.docstatus != 0:
        print(f"CM planning seed: {plan_name} is not draft — skip rules.")
        return
    await cm_plan.update_plan(
        db,
        plan.id,
        CmPlanUpdate(cost_drivers=_drivers_payload(tree), recompute=True),
        actor,
    )
    refreshed = await cm_plan.get_plan(db, plan.id, actor.company_id)  # type: ignore[arg-type]
    snap = refreshed.cost_structure_snapshot or []
    n = len(snap) if isinstance(snap, list) else 0
    baseline = next((s for s in refreshed.scenarios if s.is_baseline), None)
    print(
        f"CM planning seed: {plan_name} — {n} cost rules; "
        f"CM1={getattr(baseline, 'cm1', '?')} CM2={getattr(baseline, 'cm2', '?')} "
        f"CM3={getattr(baseline, 'cm3', '?')} OP={getattr(baseline, 'operating_profit', '?')}"
    )


async def seed_cm_planning(
    db: AsyncSession,
    actor: CurrentUser,
    company_id: uuid.UUID,
    *,
    refresh_rules: bool = True,
) -> None:
    """Create packaging rate + draft QTN + CM plan with scenarios; refresh demo rule trees."""
    await set_company_context(db, company_id)

    item = await db.scalar(
        select(Item).where(Item.company_id == company_id, Item.item_code == "FG-GEARBOX")
    )
    if item is None:
        print("CM planning seed: FG-GEARBOX not found — run manufacturing seed first.")
        return

    customer = await db.scalar(select(Customer).where(Customer.company_id == company_id).limit(1))
    if customer is None:
        print("CM planning seed: no Customer — skip.")
        return

    existing_rate = await db.scalar(
        select(CmCostRate).where(
            CmCostRate.company_id == company_id,
            CmCostRate.rate_name == "FG-GEARBOX packaging",
        )
    )
    if existing_rate is None:
        db.add(
            CmCostRate(
                company_id=company_id,
                rate_name="FG-GEARBOX packaging",
                driver="packaging",
                rate=Decimal("25"),
                uom=None,
                item_id=item.id,
                item_group_id=None,
                disabled=False,
                owner=actor.id,
                modified_by=actor.id,
            )
        )
        await db.flush()
        print("CM planning seed: created CM Cost Rate 'FG-GEARBOX packaging' @ ₹25.")

    qtn = await db.scalar(
        select(Quotation)
        .where(
            Quotation.company_id == company_id,
            Quotation.docstatus == 0,
            Quotation.remarks.ilike("%CM planning demo%"),
        )
        .options(selectinload(Quotation.items))
        .order_by(Quotation.creation.desc())
    )
    if qtn is None:
        qtn = await qtn_service.create_quotation(
            db,
            QuotationCreate(
                customer_id=customer.id,
                posting_date=date.today(),
                remarks="CM planning demo — FG-GEARBOX @ ₹1,800 (above BOM cost)",
                items=[
                    OrderItemIn(
                        item_id=item.id,
                        qty=Decimal("20"),
                        rate=Decimal("1800"),
                    )
                ],
            ),
            actor,
        )
        print(f"CM planning seed: created draft Quotation {qtn.name}.")
    else:
        print(f"CM planning seed: reusing draft Quotation {qtn.name}.")

    existing_plan = await db.scalar(
        select(CmPlan).where(
            CmPlan.company_id == company_id,
            CmPlan.quotation_id == qtn.id,
            CmPlan.docstatus == 0,
        )
    )
    if existing_plan is not None:
        plan = await cm_plan.get_plan(db, existing_plan.id, company_id)
        if len(plan.scenarios) < 3:
            names = {s.name for s in plan.scenarios}
            wanted = [
                ("Price -5%", CmScenarioOverrides(selling_rate=Decimal("1710"))),
                ("Freight 70", CmScenarioOverrides(freight_amount=Decimal("70"))),
                ("Material +10%", CmScenarioOverrides(material_cost_factor=Decimal("1.10"))),
            ]
            for name, overrides in wanted:
                if name in names:
                    continue
                plan = await cm_plan.create_scenario(
                    db,
                    plan.id,
                    CmPlanScenarioCreate(name=name, overrides=overrides),
                    actor,
                )
                print(f"CM planning seed: added scenario '{name}'.")
        else:
            print(f"CM planning seed: plan {plan.name} already has {len(plan.scenarios)} scenarios.")
    else:
        plan = await cm_plan.seed_from_quotation(db, qtn.id, actor)
        print(f"CM planning seed: seeded plan {plan.name}.")
        for name, overrides in [
            ("Price -5%", CmScenarioOverrides(selling_rate=Decimal("1710"))),
            ("Freight 70", CmScenarioOverrides(freight_amount=Decimal("70"))),
            ("Material +10%", CmScenarioOverrides(material_cost_factor=Decimal("1.10"))),
        ]:
            plan = await cm_plan.create_scenario(
                db,
                plan.id,
                CmPlanScenarioCreate(name=name, overrides=overrides),
                actor,
            )
            print(f"CM planning seed: added scenario '{name}'.")

    if refresh_rules:
        named = (
            await db.scalars(
                select(CmPlan)
                .where(
                    CmPlan.company_id == company_id,
                    CmPlan.name.in_(("CM-PLAN-2026-00001", "CM-PLAN-2026-00002")),
                )
                .order_by(CmPlan.name)
            )
        ).all()
        if len(named) >= 1:
            await apply_demo_cost_rules(db, actor, named[0].name, _DEMO_RULES_PLAN_01)
        if len(named) >= 2:
            await apply_demo_cost_rules(db, actor, named[1].name, _DEMO_RULES_PLAN_02)
        elif plan is not None and plan.name not in {n.name for n in named}:
            await apply_demo_cost_rules(db, actor, plan.name, _DEMO_RULES_PLAN_01)

    print(
        f"CM planning seed: ready — Selling → CM Plans → {plan.name} "
        f"({len(plan.scenarios)} scenarios)."
    )


async def _run(
    database_url: str, company_name: str, admin_email: str, refresh_rules: bool
) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        company = await db.scalar(select(Company).where(Company.company_name == company_name))
        if company is None:
            raise SystemExit(f"Company '{company_name}' not found")
        admin = await db.scalar(select(User).where(User.email == admin_email.lower()))
        if admin is None:
            raise SystemExit(f"User '{admin_email}' not found")
        actor = CurrentUser(
            {
                "sub": str(admin.id),
                "email": admin.email,
                "company_id": str(company.id),
                "roles": ["System Manager"],
            }
        )
        await seed_cm_planning(db, actor, company.id, refresh_rules=refresh_rules)
        await db.commit()
    await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--database-url",
        required=True,
        help="Owner-role asyncpg URL (erp_owner)",
    )
    p.add_argument("--company-name", default="Mango Appliances Demo")
    p.add_argument("--admin-email", default="admin@example.com")
    p.add_argument(
        "--refresh-rules",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Install demo CM1/CM2/CM3/Corporate rule trees on plans 00001/00002",
    )
    args = p.parse_args()
    asyncio.run(_run(args.database_url, args.company_name, args.admin_email, args.refresh_rules))


if __name__ == "__main__":
    main()
