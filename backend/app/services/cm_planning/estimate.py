"""Resolve estimated cost drivers for CM planning lines."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cm_planning import CmCostRate
from app.models.selling import SalesPartner, ShippingRule
from app.models.stock import Item
from app.services.bom_explosion import find_item_bom
from app.services.manufacturing_common import resolve_valuation_rate
from app.services.shipping import shipping_amount_for

ZERO = Decimal("0")


@dataclass
class ResolvedLineCost:
    item_id: uuid.UUID | None
    material_per_unit: Decimal
    labor_per_unit: Decimal
    packaging_per_unit: Decimal
    material_source: str
    labor_source: str


async def resolve_line_unit_costs(
    db: AsyncSession,
    company_id: uuid.UUID,
    item: Item | None,
) -> ResolvedLineCost:
    if item is None:
        return ResolvedLineCost(None, ZERO, ZERO, ZERO, "none", "none")

    bom = await find_item_bom(db, company_id, item.id)
    if bom is not None and bom.quantity and bom.quantity > ZERO:
        mat = Decimal(bom.raw_material_cost or 0) / Decimal(bom.quantity)
        lab = Decimal(bom.operating_cost or 0) / Decimal(bom.quantity)
        return ResolvedLineCost(
            item.id,
            mat,
            lab,
            await _packaging_rate(db, company_id, item),
            "bom",
            "bom_operating",
        )

    val = await resolve_valuation_rate(db, item)
    return ResolvedLineCost(
        item.id,
        val,
        ZERO,
        await _packaging_rate(db, company_id, item),
        "valuation",
        "none",
    )


async def _packaging_rate(db: AsyncSession, company_id: uuid.UUID, item: Item) -> Decimal:
    # Prefer item-specific rate, then item-group, then any company packaging default
    q = await db.scalars(
        select(CmCostRate).where(
            CmCostRate.company_id == company_id,
            CmCostRate.driver == "packaging",
            CmCostRate.disabled.is_(False),
            CmCostRate.item_id == item.id,
        )
    )
    row = q.first()
    if row is not None:
        return Decimal(row.rate)
    if item.item_group_id is not None:
        q2 = await db.scalars(
            select(CmCostRate).where(
                CmCostRate.company_id == company_id,
                CmCostRate.driver == "packaging",
                CmCostRate.disabled.is_(False),
                CmCostRate.item_group_id == item.item_group_id,
            )
        )
        row2 = q2.first()
        if row2 is not None:
            return Decimal(row2.rate)
    return ZERO


async def resolve_commission_rate(
    db: AsyncSession, company_id: uuid.UUID, sales_partner_id: uuid.UUID | None
) -> Decimal:
    if sales_partner_id is None:
        return ZERO
    partner = await db.scalar(
        select(SalesPartner).where(
            SalesPartner.id == sales_partner_id,
            SalesPartner.company_id == company_id,
        )
    )
    if partner is None:
        return ZERO
    return Decimal(partner.commission_rate or 0)


async def resolve_freight_amount(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    shipping_rule_id: uuid.UUID | None,
    freight_override: Decimal | None,
    item_subtotal: Decimal,
) -> tuple[Decimal, str]:
    if freight_override is not None:
        return Decimal(freight_override), "manual"
    if shipping_rule_id is None:
        return ZERO, "none"
    rule = await db.scalar(
        select(ShippingRule).where(
            ShippingRule.id == shipping_rule_id,
            ShippingRule.company_id == company_id,
        )
    )
    if rule is None or rule.disabled:
        return ZERO, "none"
    return shipping_amount_for(rule, item_subtotal), "shipping_rule"
