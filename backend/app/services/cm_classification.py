"""Contribution Margin classification — industry templates + settings.

Templates live under ``backend/data/cm_templates/*.json``. Applying a template
sets ``Account.cm_class`` on matching P&L leaf accounts (never Balance Sheet).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.accounts import CM_CLASSES, Account
from app.models.core import SystemSetting
from app.schemas.accounts import (
    CmApplyTemplateResult,
    CmSettings,
    CmTemplateInfo,
    CmUnclassifiedAccount,
)
from app.schemas.core import SystemSettingUpsert
from app.services import settings as settings_service
from app.services.audit import log_audit

CM_SETTINGS_KEY = "contribution_margin"
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "data" / "cm_templates"


def _load_template_file(template_id: str) -> dict[str, Any]:
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.is_file():
        raise NotFoundError(f"CM template '{template_id}' not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != template_id:
        raise ValidationError(f"Invalid CM template file: {template_id}")
    return data


def list_cm_templates() -> list[CmTemplateInfo]:
    if not _TEMPLATES_DIR.is_dir():
        return []
    out: list[CmTemplateInfo] = []
    for path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "id" not in data:
            continue
        rules = data.get("rules") or []
        out.append(
            CmTemplateInfo(
                id=data["id"],
                label=data.get("label") or data["id"],
                description=data.get("description"),
                rule_count=len(rules) if isinstance(rules, list) else 0,
            )
        )
    return out


def match_cm_class(account: Account, rules: list[dict[str, Any]]) -> str | None:
    """First matching rule wins. Rules may key on account_type, account_category,
    and/or name_contains (case-insensitive substring list — all must match if
    provided as a list of alternatives? Plan said name_contains is OR within list).
    """
    name_l = (account.account_name or "").lower()
    for rule in rules:
        cm_class = rule.get("cm_class")
        if cm_class not in CM_CLASSES:
            continue
        match = rule.get("match") or {}
        if not isinstance(match, dict):
            continue
        ok = True
        if "account_type" in match:
            if (account.account_type or "") != match["account_type"]:
                ok = False
        if ok and "account_category" in match:
            if (account.account_category or "") != match["account_category"]:
                ok = False
        if ok and "name_contains" in match:
            needles = match["name_contains"]
            if isinstance(needles, str):
                needles = [needles]
            if not isinstance(needles, list) or not needles:
                ok = False
            elif not any(str(n).lower() in name_l for n in needles):
                ok = False
        if ok and match:
            return str(cm_class)
    return None


async def get_cm_settings(db: AsyncSession, company_id: uuid.UUID | None) -> CmSettings:
    if company_id is None:
        return CmSettings()
    setting = await db.scalar(
        select(SystemSetting).where(
            SystemSetting.key == CM_SETTINGS_KEY,
            SystemSetting.company_id == company_id,
        )
    )
    if setting is not None and isinstance(setting.value, dict):
        try:
            return CmSettings.model_validate(setting.value)
        except PydanticValidationError:
            return CmSettings()
    return CmSettings()


async def save_cm_settings(
    db: AsyncSession, payload: CmSettings, user: CurrentUser
) -> CmSettings:
    if payload.unclassified_policy not in ("bucket", "exclude", "error"):
        raise ValidationError(
            "unclassified_policy must be bucket, exclude, or error",
            field="unclassified_policy",
        )
    await settings_service.upsert_setting(
        db,
        SystemSettingUpsert(
            key=CM_SETTINGS_KEY,
            value=payload.model_dump(mode="json"),
            company_id=user.company_id,
        ),
        user,
    )
    return await get_cm_settings(db, user.company_id)


async def apply_cm_template(
    db: AsyncSession,
    company_id: uuid.UUID,
    template_id: str,
    user: CurrentUser,
    *,
    overwrite: bool = False,
) -> CmApplyTemplateResult:
    data = _load_template_file(template_id)
    rules = data.get("rules") or []
    if not isinstance(rules, list):
        raise ValidationError("Template rules must be a list")

    accounts = (
        await db.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.report_type == "Profit and Loss",
                Account.is_group.is_(False),
                Account.disabled.is_(False),
            )
        )
    ).scalars().all()

    matched = 0
    updated = 0
    skipped = 0
    for account in accounts:
        suggested = match_cm_class(account, rules)
        if suggested is None:
            continue
        matched += 1
        if account.cm_class is not None and not overwrite:
            skipped += 1
            continue
        if account.cm_class == suggested:
            skipped += 1
            continue
        account.cm_class = suggested
        updated += 1
        await log_audit(
            db,
            doctype="Account",
            document_id=account.id,
            action="UPDATE",
            user_id=user.id,
            company_id=company_id,
        )

    settings = await get_cm_settings(db, company_id)
    settings.applied_template = template_id
    await settings_service.upsert_setting(
        db,
        SystemSettingUpsert(
            key=CM_SETTINGS_KEY,
            value=settings.model_dump(mode="json"),
            company_id=company_id,
        ),
        user,
    )
    await db.commit()
    return CmApplyTemplateResult(
        template_id=template_id,
        matched=matched,
        updated=updated,
        skipped=skipped,
    )


async def get_unclassified_pnl_accounts(
    db: AsyncSession, company_id: uuid.UUID
) -> list[CmUnclassifiedAccount]:
    rows = (
        await db.execute(
            select(Account)
            .where(
                Account.company_id == company_id,
                Account.report_type == "Profit and Loss",
                Account.is_group.is_(False),
                Account.disabled.is_(False),
                Account.cm_class.is_(None),
            )
            .order_by(Account.path)
        )
    ).scalars().all()
    return [
        CmUnclassifiedAccount(
            account_id=a.id,
            account_name=a.account_name,
            root_type=a.root_type,
            account_type=a.account_type,
            account_category=a.account_category,
            path=str(a.path),
        )
        for a in rows
    ]
