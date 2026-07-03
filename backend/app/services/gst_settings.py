"""GST Settings — per-company GST configuration that the whole India-compliance layer reads.

Stored as a JSON blob under the ``gst_settings`` key of ``SystemSetting`` (same pattern as
the print/branding profile — no new table). The GSTIN and GST state are NOT stored here;
they are derived from ``Company.tax_id`` on read so there is a single source of truth.
"""

import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gst_states import gst_state_label_of
from app.core.security import CurrentUser
from app.models.core import Company, SystemSetting
from app.schemas.compliance import GstSettings
from app.schemas.core import SystemSettingUpsert
from app.services import settings as settings_service

GST_SETTINGS_KEY = "gst_settings"
# fields derived from the Company on read — never persisted in the blob
_DERIVED = {"gstin", "gst_state"}


async def _company_gstin(db: AsyncSession, company_id: uuid.UUID | None) -> str | None:
    if company_id is None:
        return None
    return await db.scalar(select(Company.tax_id).where(Company.id == company_id))


async def get_gst_settings(db: AsyncSession, company_id: uuid.UUID | None) -> GstSettings:
    """Load the company's stored GST policy (defaulting cleanly when unset/malformed) and
    fill the derived GSTIN + registered state from the Company.

    Reads the **company-scoped** row directly (NOT settings_service.get_setting, which would
    fall back to an instance-wide row) — a GST policy must never be inherited across tenants.
    """
    stored = GstSettings()
    if company_id is not None:
        setting = await db.scalar(
            select(SystemSetting).where(
                SystemSetting.key == GST_SETTINGS_KEY, SystemSetting.company_id == company_id
            )
        )
        if setting is not None and isinstance(setting.value, dict):
            try:
                stored = GstSettings.model_validate(setting.value)
            except ValidationError:
                stored = GstSettings()

    gstin = await _company_gstin(db, company_id)
    stored.gstin = gstin
    stored.gst_state = gst_state_label_of(gstin)
    return stored


async def save_gst_settings(
    db: AsyncSession, payload: GstSettings, user: CurrentUser
) -> GstSettings:
    """Persist the policy fields only (GSTIN/state are derived from the Company)."""
    await settings_service.upsert_setting(
        db,
        SystemSettingUpsert(
            key=GST_SETTINGS_KEY,
            value=payload.model_dump(mode="json", exclude=_DERIVED),
            company_id=user.company_id,
        ),
        user,
    )
    return await get_gst_settings(db, user.company_id)
