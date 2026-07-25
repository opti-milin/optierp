"""Shared helpers for the Manufacturing module (BOM + Work Order services)."""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.core import UOM, SystemSetting
from app.models.stock import Bin, Item

ZERO = Decimal("0")

# ERPNext-style naming series for the Manufacturing documents.
MFG_NAMING_SERIES = {
    "BOM": "MFG-BOM-.YYYY.-",
    "Work Order": "MFG-WO-.YYYY.-",
    "Job Card": "MFG-JC-.YYYY.-",
    "Production Plan": "MFG-PP-.YYYY.-",
    "Subcontract Job": "MFG-SCJ-.YYYY.-",
}

# Per-company Manufacturing defaults — lean slice of ERPNext's Manufacturing Settings,
# stored as one SystemSetting JSON value (no settings doctype / no migration).
MFG_SETTINGS_KEY = "manufacturing_settings"
FULFILLMENT_MODES = frozenset({"off", "warn", "block"})

MFG_SETTINGS_DEFAULTS: dict = {
    "default_source_warehouse_id": None,  # where raws are consumed from
    "default_wip_warehouse_id": None,  # optional WIP staging warehouse
    "default_fg_warehouse_id": None,  # where finished goods land
    "over_production_percentage": "0",  # allow finishing up to qty × (1 + pct/100)
    "capacity_planning_enabled": False,  # soft workstation overload warnings on WO submit
    # SO/Quotation fulfillability gate: off | warn (default) | block
    "order_fulfillment_mode": "warn",
}


def _sanitize_mfg_settings(raw: dict) -> dict:
    """Distrust the stored JSON: the generic PUT /settings endpoint can write anything to
    this key, so every read re-validates. Warehouse ids must parse as UUIDs (else None);
    the over-production percentage must parse as a Decimal and is clamped to [0, 100]."""
    value = dict(MFG_SETTINGS_DEFAULTS)
    for key in (
        "default_source_warehouse_id",
        "default_wip_warehouse_id",
        "default_fg_warehouse_id",
    ):
        candidate = raw.get(key)
        try:
            value[key] = str(uuid.UUID(str(candidate))) if candidate else None
        except (ValueError, AttributeError, TypeError):
            value[key] = None
    try:
        pct = Decimal(str(raw.get("over_production_percentage") or "0"))
    except ArithmeticError:
        pct = ZERO
    value["over_production_percentage"] = str(min(max(pct, ZERO), Decimal("100")))
    value["capacity_planning_enabled"] = bool(raw.get("capacity_planning_enabled", False))
    mode = str(raw.get("order_fulfillment_mode") or "warn").strip().lower()
    value["order_fulfillment_mode"] = mode if mode in FULFILLMENT_MODES else "warn"
    return value


async def get_manufacturing_settings(db: AsyncSession, company_id: uuid.UUID) -> dict:
    """The company's Manufacturing defaults, filled with the documented defaults."""
    setting = await db.scalar(
        select(SystemSetting).where(
            SystemSetting.key == MFG_SETTINGS_KEY, SystemSetting.company_id == company_id
        )
    )
    if setting is not None and isinstance(setting.value, dict):
        return _sanitize_mfg_settings(setting.value)
    return dict(MFG_SETTINGS_DEFAULTS)


async def update_manufacturing_settings(
    db: AsyncSession, company_id: uuid.UUID, updates: dict
) -> dict:
    """Upsert the company's Manufacturing defaults (only known keys are stored)."""
    setting = await db.scalar(
        select(SystemSetting).where(
            SystemSetting.key == MFG_SETTINGS_KEY, SystemSetting.company_id == company_id
        )
    )
    current = await get_manufacturing_settings(db, company_id)
    current.update({k: v for k, v in updates.items() if k in MFG_SETTINGS_DEFAULTS})
    if setting is None:
        setting = SystemSetting(key=MFG_SETTINGS_KEY, company_id=company_id, value=current)
        db.add(setting)
    else:
        setting.value = current
    await db.flush()
    await db.commit()
    return current


async def require_expense_account(
    db: AsyncSession, account_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    """The operating / additional-cost account credited by a Manufacture or Repack entry
    must be a real, enabled EXPENSE account of this company (e.g. "Expenses Included In
    Valuation") — anything else would let a stock-level user fabricate credits to income /
    liability / bank accounts through the manufacturing GL leg."""
    from app.models.accounts import Account

    account = await db.get(Account, account_id)
    if account is None or account.company_id != company_id:
        raise NotFoundError("Operating cost account not found")
    if account.disabled or account.is_group:
        raise ValidationError(
            "The operating cost account must be an enabled, non-group account",
            field="operating_cost_account_id",
        )
    if account.root_type != "Expense":
        raise ValidationError(
            "The operating cost account must be an Expense account "
            "(e.g. 'Expenses Included In Valuation')",
            field="operating_cost_account_id",
        )


async def require_whole_number_qty(
    db: AsyncSession, item: Item, qty: Decimal, *, field: str = "qty"
) -> None:
    """Reject a fractional quantity for an item whose stock UOM must be a whole number
    (e.g. Nos/Unit/Set) — you can't manufacture 2.5 appliances."""
    uom = await db.scalar(select(UOM).where(UOM.uom_name == item.stock_uom))
    if uom is not None and uom.must_be_whole_number and qty != qty.to_integral_value():
        raise ValidationError(
            f"Quantity must be a whole number — '{item.item_code}' is counted in "
            f"{item.stock_uom}",
            field=field,
        )


async def resolve_valuation_rate(db: AsyncSession, item: Item) -> Decimal:
    """The component's current valuation rate (per stock unit), for BOM costing.

    Prefers the live company-wide moving average from the item's Bins
    (Σ stock_value ÷ Σ actual_qty over warehouses with positive stock); falls back to the
    item master's ``valuation_rate`` (opening default), then ``last_purchase_rate``, then
    ``standard_rate``. This is the *estimated* cost snapshot — the *actual* cost is captured
    at manufacture time from the real consumed valuation.
    """
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Bin.stock_value), ZERO),
                func.coalesce(func.sum(Bin.actual_qty), ZERO),
            ).where(Bin.item_id == item.id, Bin.actual_qty > ZERO)
        )
    ).one()
    total_value, total_qty = row
    if total_qty and total_qty > ZERO:
        return Decimal(total_value) / Decimal(total_qty)
    if item.valuation_rate and item.valuation_rate > ZERO:
        return item.valuation_rate
    if item.last_purchase_rate and item.last_purchase_rate > ZERO:
        return item.last_purchase_rate
    return item.standard_rate or ZERO


async def item_available_qty(
    db: AsyncSession,
    item_id: uuid.UUID,
    warehouse_id: uuid.UUID | None,
    *,
    deduct_reserved: bool = False,
) -> Decimal:
    """On-hand quantity for an item — at a specific warehouse, or company-wide (sum of all
    Bins with positive stock) when no warehouse is given.

    When ``deduct_reserved`` is True (order fulfillment checks), free qty is
    ``actual_qty − reserved_qty`` so soft SO reservations reduce available stock.
    """
    if deduct_reserved:
        qty_expr = func.coalesce(func.sum(Bin.actual_qty - Bin.reserved_qty), ZERO)
    else:
        qty_expr = func.coalesce(func.sum(Bin.actual_qty), ZERO)
    stmt = select(qty_expr).where(Bin.item_id == item_id)
    if warehouse_id is not None:
        stmt = stmt.where(Bin.warehouse_id == warehouse_id)
    return Decimal((await db.execute(stmt)).scalar_one())
