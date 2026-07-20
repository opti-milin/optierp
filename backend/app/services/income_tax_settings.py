"""Income Tax Settings — per-company entity ITR policy (Phase 0).

Stored as a JSON blob under ``income_tax_settings`` on ``SystemSetting``.
PAN / TAN are derived from ``Company`` on read (single source of truth).
"""

import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.core import Company, SystemSetting
from app.schemas.compliance import IncomeTaxSettings
from app.schemas.core import SystemSettingUpsert
from app.services import settings as settings_service

INCOME_TAX_SETTINGS_KEY = "income_tax_settings"
_DERIVED = {"pan", "tan"}


async def _company_identity(
    db: AsyncSession, company_id: uuid.UUID | None
) -> tuple[str | None, str | None]:
    if company_id is None:
        return None, None
    row = await db.execute(select(Company.pan, Company.tan).where(Company.id == company_id))
    pair = row.one_or_none()
    if pair is None:
        return None, None
    return pair[0], pair[1]


async def get_income_tax_settings(
    db: AsyncSession, company_id: uuid.UUID | None
) -> IncomeTaxSettings:
    stored = IncomeTaxSettings()
    if company_id is not None:
        setting = await db.scalar(
            select(SystemSetting).where(
                SystemSetting.key == INCOME_TAX_SETTINGS_KEY,
                SystemSetting.company_id == company_id,
            )
        )
        if setting is not None and isinstance(setting.value, dict):
            try:
                stored = IncomeTaxSettings.model_validate(setting.value)
            except ValidationError:
                stored = IncomeTaxSettings()

    pan, tan = await _company_identity(db, company_id)
    stored.pan = pan
    stored.tan = tan
    return stored


async def save_income_tax_settings(
    db: AsyncSession, payload: IncomeTaxSettings, user: CurrentUser
) -> IncomeTaxSettings:
    await settings_service.upsert_setting(
        db,
        SystemSettingUpsert(
            key=INCOME_TAX_SETTINGS_KEY,
            value=payload.model_dump(mode="json", exclude=_DERIVED),
            company_id=user.company_id,
        ),
        user,
    )
    return await get_income_tax_settings(db, user.company_id)
