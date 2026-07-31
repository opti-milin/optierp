"""Per-company module feature flags (SystemSetting JSON).

Lean Phase-6 gate so tenants who don't manufacture can hide Manufacturing from
nav/search. Default: manufacturing enabled.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import SystemSetting

MODULE_FLAGS_KEY = "module_flags"
MODULE_FLAGS_DEFAULTS: dict[str, bool] = {
    "manufacturing": True,
    "taxation": True,
}


def _sanitize(raw: dict) -> dict[str, bool]:
    value = dict(MODULE_FLAGS_DEFAULTS)
    for key in MODULE_FLAGS_DEFAULTS:
        if key in raw:
            value[key] = bool(raw[key])
    return value


async def get_module_flags(db: AsyncSession, company_id: uuid.UUID) -> dict[str, bool]:
    setting = await db.scalar(
        select(SystemSetting).where(
            SystemSetting.key == MODULE_FLAGS_KEY, SystemSetting.company_id == company_id
        )
    )
    if setting is not None and isinstance(setting.value, dict):
        return _sanitize(setting.value)
    return dict(MODULE_FLAGS_DEFAULTS)


async def update_module_flags(
    db: AsyncSession, company_id: uuid.UUID, updates: dict
) -> dict[str, bool]:
    setting = await db.scalar(
        select(SystemSetting).where(
            SystemSetting.key == MODULE_FLAGS_KEY, SystemSetting.company_id == company_id
        )
    )
    current = await get_module_flags(db, company_id)
    current.update({k: bool(v) for k, v in updates.items() if k in MODULE_FLAGS_DEFAULTS})
    if setting is None:
        setting = SystemSetting(key=MODULE_FLAGS_KEY, company_id=company_id, value=current)
        db.add(setting)
    else:
        setting.value = current
    await db.flush()
    await db.commit()
    return current
