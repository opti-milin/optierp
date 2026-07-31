"""Shipping Rule (Phase 3) — freight charge + outbound transit days for CTP.

Flat ``shipping_amount``, waived when the line subtotal reaches ``free_above``.
Returned as an 'Actual' charge row (posted to the rule's account) appended to the
order's taxes/charges. ``transit_days`` feeds delivery-date estimation
(warehouse → customer).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.selling import ShippingRule
from app.schemas.accounts import TaxRowIn
from app.services.manufacturing_common import get_manufacturing_settings

ZERO = Decimal("0")


def shipping_amount_for(rule: Any, item_subtotal: Decimal) -> Decimal:
    """Pure: the freight amount given the rule and the order subtotal (unit-tested)."""
    if rule.free_above and rule.free_above > ZERO and item_subtotal >= rule.free_above:
        return ZERO
    return Decimal(rule.shipping_amount)


async def resolve_outbound_days(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    shipping_rule_id: uuid.UUID | None = None,
    outbound_days_override: int | None = None,
) -> int:
    """Calendar days from ready-to-dispatch to customer receipt.

    Precedence: explicit override → Shipping Rule.transit_days (when > 0) →
    Manufacturing Settings ``outbound_delivery_days`` → 0.

    A Shipping Rule with ``transit_days=0`` (the column default) means "not
    specified" and falls through to the company Manufacturing Settings so that
    setting remains useful when freight rules exist but have no transit set.
    """
    if outbound_days_override is not None:
        return max(0, int(outbound_days_override))
    if shipping_rule_id is not None:
        rule = await db.scalar(
            select(ShippingRule).where(
                ShippingRule.id == shipping_rule_id,
                ShippingRule.company_id == company_id,
            )
        )
        if rule is not None and not rule.disabled and int(rule.transit_days or 0) > 0:
            return int(rule.transit_days)
    settings = await get_manufacturing_settings(db, company_id)
    return max(0, int(settings.get("outbound_delivery_days") or 0))


async def shipping_tax_row(
    db: AsyncSession, company_id: uuid.UUID, shipping_rule_id: uuid.UUID, item_subtotal: Decimal
) -> TaxRowIn | None:
    """Resolve a shipping rule into an 'Actual' charge row, or None if no charge."""
    rule = await db.scalar(
        select(ShippingRule).where(
            ShippingRule.id == shipping_rule_id, ShippingRule.company_id == company_id
        )
    )
    if rule is None:
        raise NotFoundError("Shipping rule not found", code="ERR_SHIPPING_RULE")
    if rule.disabled:
        raise ValidationError("Shipping rule is disabled", field="shipping_rule_id")
    amount = shipping_amount_for(rule, item_subtotal)
    if amount <= ZERO:
        return None
    if rule.account_id is None:
        raise ValidationError(
            "Shipping rule has no account head", field="shipping_rule_id", code="ERR_SHIPPING_RULE"
        )
    return TaxRowIn(
        charge_type="Actual",
        rate=ZERO,
        tax_amount=amount,
        account_head_id=rule.account_id,
        description=f"Shipping ({rule.shipping_rule_name})",
    )
