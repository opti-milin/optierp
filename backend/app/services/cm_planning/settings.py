"""CM planning settings + industry planning templates."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.core import SystemSetting
from app.schemas.cm_planning import CmPlanningSettings, CmPlanningTemplateInfo
from app.schemas.core import SystemSettingUpsert
from app.services import settings as settings_service
from app.services.audit import log_audit

CM_PLANNING_SETTINGS_KEY = "cm_planning"
_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "data" / "cm_planning_templates"


def list_planning_templates() -> list[CmPlanningTemplateInfo]:
    if not _TEMPLATES_DIR.is_dir():
        return []
    out: list[CmPlanningTemplateInfo] = []
    for path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "id" not in data:
            continue
        tgt = data.get("target_cm1_pct")
        out.append(
            CmPlanningTemplateInfo(
                id=data["id"],
                label=data.get("label") or data["id"],
                description=data.get("description"),
                target_cm1_pct=Decimal(str(tgt)) if tgt is not None else None,
            )
        )
    return out


def load_planning_template(template_id: str) -> dict[str, Any]:
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.is_file():
        raise NotFoundError(f"CM planning template '{template_id}' not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != template_id:
        raise ValidationError(f"Invalid CM planning template: {template_id}")
    return data


def _defaults() -> CmPlanningSettings:
    return CmPlanningSettings()


async def get_planning_settings(db: AsyncSession, company_id: uuid.UUID) -> CmPlanningSettings:
    setting = await db.scalar(
        select(SystemSetting).where(
            SystemSetting.key == CM_PLANNING_SETTINGS_KEY,
            SystemSetting.company_id == company_id,
        )
    )
    if setting is None or not setting.value:
        return _defaults()
    raw = setting.value if isinstance(setting.value, dict) else {}
    try:
        return CmPlanningSettings.model_validate(raw)
    except Exception:
        return _defaults()


async def save_planning_settings(
    db: AsyncSession, payload: CmPlanningSettings, user: CurrentUser
) -> CmPlanningSettings:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    await settings_service.upsert_setting(
        db,
        SystemSettingUpsert(
            key=CM_PLANNING_SETTINGS_KEY,
            value=payload.model_dump(mode="json"),
            company_id=user.company_id,
        ),
        user,
    )
    await log_audit(
        db,
        doctype="System Setting",
        document_id=None,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
    )
    # Compat shim: keep cost-structure fixed leaves in sync with allocation %
    from app.services.cm_planning import structure as structure_service

    await structure_service.sync_structure_from_allocations(
        db, user.company_id, payload.allocations, user
    )
    return payload


async def apply_planning_template(
    db: AsyncSession, company_id: uuid.UUID, template_id: str, user: CurrentUser
) -> CmPlanningSettings:
    data = load_planning_template(template_id)
    current = await get_planning_settings(db, company_id)
    alloc = data.get("allocations") or {}
    updated = CmPlanningSettings(
        target_cm1_pct=Decimal(str(data.get("target_cm1_pct", current.target_cm1_pct))),
        target_cm2_pct=current.target_cm2_pct,
        min_cm1_pct=Decimal(str(data.get("min_cm1_pct", current.min_cm1_pct))),
        submit_policy=current.submit_policy,
        default_template=template_id,
        max_scenarios_per_plan=current.max_scenarios_per_plan,
        allocations={
            "product_channel_fixed_pct_of_revenue": Decimal(
                str(alloc.get("product_channel_fixed_pct_of_revenue", 3))
            ),
            "segment_bu_fixed_pct_of_revenue": Decimal(
                str(alloc.get("segment_bu_fixed_pct_of_revenue", 2))
            ),
            "corporate_overhead_pct_of_revenue": Decimal(
                str(alloc.get("corporate_overhead_pct_of_revenue", 5))
            ),
        },
    )
    return await save_planning_settings(db, updated, user)
