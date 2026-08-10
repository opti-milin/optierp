"""Idempotent loader for statutory JSON packs into schema ``statutory``.

Must run as ``erp_owner`` (or any role with write on ``statutory``). The app
role ``erp_app`` has SELECT only and cannot load packs.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import statutory as m
from app.services.taxation.catalogue.pack_validator import PackValidationError, validate_pack

PACK_ROOT = Path(__file__).resolve().parents[4] / "data" / "statutory" / "in"


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


async def _upsert_ay(db: AsyncSession, ay: dict[str, Any]) -> m.AssessmentYear:
    row = await db.scalar(select(m.AssessmentYear).where(m.AssessmentYear.code == ay["code"]))
    if row is None:
        row = m.AssessmentYear(code=ay["code"])
        db.add(row)
    row.ay_start = _date(ay["ay_start"])  # type: ignore[assignment]
    row.ay_end = _date(ay["ay_end"])  # type: ignore[assignment]
    row.fy_start = _date(ay["fy_start"])  # type: ignore[assignment]
    row.fy_end = _date(ay["fy_end"])  # type: ignore[assignment]
    row.prev_ay_code = ay.get("prev_ay_code")
    await db.flush()
    return row


async def _load_common(db: AsyncSession, common: dict[str, Any]) -> None:
    for item in common.get("assessee_classes", []):
        row = await db.scalar(select(m.AssesseeClass).where(m.AssesseeClass.code == item["code"]))
        if row is None:
            row = m.AssesseeClass(code=item["code"])
            db.add(row)
        row.title = item["title"]
        row.default_itr_form = item.get("default_itr_form")
        row.pan_4th_chars = item.get("pan_4th_chars") or ""

    for item in common.get("income_characters", []):
        row = await db.scalar(select(m.IncomeCharacter).where(m.IncomeCharacter.code == item["code"]))
        if row is None:
            row = m.IncomeCharacter(code=item["code"])
            db.add(row)
        row.title = item["title"]
        row.surcharge_cap_percent = _dec(item.get("surcharge_cap_percent"))
        row.rebate_eligible = bool(item.get("rebate_eligible", True))
        row.setoff_group = item.get("setoff_group")
        row.loss_carry_years = item.get("loss_carry_years")

    for item in common.get("depreciation_blocks", []):
        row = await db.scalar(
            select(m.DepreciationBlock).where(m.DepreciationBlock.block_code == item["block_code"])
        )
        if row is None:
            row = m.DepreciationBlock(block_code=item["block_code"])
            db.add(row)
        row.title = item["title"]
        row.rate_percent = _dec(item["rate_percent"])  # type: ignore[assignment]
        row.additional_depreciation_eligible = bool(item.get("additional_depreciation_eligible", False))
        row.remarks = item.get("remarks")

    for item in common.get("provisions", []):
        row = await db.scalar(select(m.Provision).where(m.Provision.section_code == item["section_code"]))
        if row is None:
            row = m.Provision(section_code=item["section_code"])
            db.add(row)
        row.title = item["title"]
        row.act_reference = item.get("act_reference")
        row.stage = item.get("stage") or "PGBP"
        row.default_effect = item.get("default_effect") or "Add"
        row.itr_schedule = item.get("itr_schedule")
        row.itr_field_path = item.get("itr_field_path")
        row.disabled = bool(item.get("disabled", False))

    await db.flush()


async def _get_or_create_fav(
    db: AsyncSession, *, ay_code: str, fav: dict[str, Any]
) -> m.FinanceActVersion:
    row = await db.scalar(
        select(m.FinanceActVersion).where(
            m.FinanceActVersion.ay_code == ay_code,
            m.FinanceActVersion.version == fav["version"],
        )
    )
    if row is None:
        row = m.FinanceActVersion(ay_code=ay_code, version=fav["version"])
        db.add(row)
    row.enacted_on = _date(fav.get("enacted_on"))
    row.source_ref = fav.get("source_ref")
    row.is_current = bool(fav.get("is_current", True))
    await db.flush()
    return row


async def _replace_children_for_fav(db: AsyncSession, fav_id: Any) -> None:
    """Delete version-scoped children so reload is idempotent and clean."""
    sched_ids = (
        await db.scalars(select(m.RateSchedule.id).where(m.RateSchedule.finance_act_version_id == fav_id))
    ).all()
    if sched_ids:
        await db.execute(delete(m.RateBand).where(m.RateBand.rate_schedule_id.in_(sched_ids)))
        await db.execute(delete(m.RateSchedule).where(m.RateSchedule.id.in_(sched_ids)))

    sur_ids = (
        await db.scalars(
            select(m.SurchargeSchedule.id).where(m.SurchargeSchedule.finance_act_version_id == fav_id)
        )
    ).all()
    if sur_ids:
        await db.execute(delete(m.SurchargeBand).where(m.SurchargeBand.surcharge_schedule_id.in_(sur_ids)))
        await db.execute(delete(m.SurchargeSchedule).where(m.SurchargeSchedule.id.in_(sur_ids)))

    await db.execute(delete(m.CessRule).where(m.CessRule.finance_act_version_id == fav_id))
    await db.execute(
        delete(m.StatutoryRebateRule).where(m.StatutoryRebateRule.finance_act_version_id == fav_id)
    )
    await db.execute(delete(m.DeductionSection).where(m.DeductionSection.finance_act_version_id == fav_id))
    await db.execute(delete(m.DueDateRule).where(m.DueDateRule.finance_act_version_id == fav_id))
    await db.execute(delete(m.InterestRule).where(m.InterestRule.finance_act_version_id == fav_id))

    pack_ids = (
        await db.scalars(select(m.RulePack.id).where(m.RulePack.finance_act_version_id == fav_id))
    ).all()
    if pack_ids:
        await db.execute(delete(m.Rule).where(m.Rule.rule_pack_id.in_(pack_ids)))
        await db.execute(delete(m.RulePack).where(m.RulePack.id.in_(pack_ids)))

    form_ids = (
        await db.scalars(select(m.ItrForm.id).where(m.ItrForm.finance_act_version_id == fav_id))
    ).all()
    if form_ids:
        await db.execute(delete(m.ItrFieldMap).where(m.ItrFieldMap.itr_form_id.in_(form_ids)))
        await db.execute(delete(m.ItrForm).where(m.ItrForm.id.in_(form_ids)))

    await db.flush()


async def load_pack(db: AsyncSession, pack: dict[str, Any], *, common: dict[str, Any]) -> str:
    validate_pack(pack, common=common)
    await _load_common(db, common)

    # Assessment years must be inserted in dependency order for prev_ay FK.
    await _upsert_ay(db, pack["ay"])
    for regime in pack.get("regimes", []):
        existing = await db.scalar(
            select(m.TaxRegime).where(
                m.TaxRegime.code == regime["code"],
                m.TaxRegime.assessee_class_code == regime["assessee_class_code"],
            )
        )
        if existing is None:
            existing = m.TaxRegime(
                code=regime["code"],
                assessee_class_code=regime["assessee_class_code"],
            )
            db.add(existing)
        existing.title = regime["title"]
        existing.is_default = bool(regime.get("is_default", False))
        existing.election_irrevocable = bool(regime.get("election_irrevocable", False))
        existing.election_form = regime.get("election_form")
    await db.flush()

    fav = await _get_or_create_fav(db, ay_code=pack["ay"]["code"], fav=pack["finance_act_version"])
    await _replace_children_for_fav(db, fav.id)

    for sched in pack.get("rate_schedules", []):
        row = m.RateSchedule(
            finance_act_version_id=fav.id,
            code=sched["code"],
            assessee_class_code=sched["assessee_class_code"],
            regime_code=sched["regime_code"],
            income_character_code=sched.get("income_character_code"),
            age_category=sched.get("age_category") or "General",
            condition_expr=sched.get("condition_expr"),
            schedule_kind=sched.get("schedule_kind") or "Slab",
            remarks=sched.get("remarks"),
        )
        db.add(row)
        await db.flush()
        for band in sched.get("bands") or []:
            db.add(
                m.RateBand(
                    rate_schedule_id=row.id,
                    seq=int(band["seq"]),
                    lower=_dec(band["lower"]) or Decimal("0"),
                    upper=_dec(band.get("upper")),
                    rate_percent=_dec(band["rate_percent"]) or Decimal("0"),
                    fixed_amount=_dec(band.get("fixed_amount")) or Decimal("0"),
                )
            )

    for sched in pack.get("surcharge_schedules", []):
        row = m.SurchargeSchedule(
            finance_act_version_id=fav.id,
            code=sched["code"],
            assessee_class_code=sched["assessee_class_code"],
            regime_code=sched.get("regime_code"),
            marginal_relief_method=sched.get("marginal_relief_method") or "RerunAtThreshold",
            capped_characters=list(sched.get("capped_characters") or []),
            remarks=sched.get("remarks"),
        )
        db.add(row)
        await db.flush()
        for band in sched.get("bands") or []:
            db.add(
                m.SurchargeBand(
                    surcharge_schedule_id=row.id,
                    seq=int(band["seq"]),
                    lower=_dec(band["lower"]) or Decimal("0"),
                    upper=_dec(band.get("upper")),
                    rate_percent=_dec(band["rate_percent"]) or Decimal("0"),
                )
            )

    for item in pack.get("cess_rules", []):
        db.add(
            m.CessRule(
                finance_act_version_id=fav.id,
                code=item["code"],
                rate_percent=_dec(item["rate_percent"]) or Decimal("0"),
                base=item.get("base") or "TaxPlusSurcharge",
                remarks=item.get("remarks"),
            )
        )

    for item in pack.get("rebate_rules", []):
        db.add(
            m.StatutoryRebateRule(
                finance_act_version_id=fav.id,
                code=item["code"],
                section_code=item.get("section_code") or "87A",
                assessee_class_code=item["assessee_class_code"],
                regime_code=item["regime_code"],
                max_taxable_income=_dec(item["max_taxable_income"]) or Decimal("0"),
                max_rebate_amount=_dec(item["max_rebate_amount"]) or Decimal("0"),
                marginal_relief_enabled=bool(item.get("marginal_relief_enabled", False)),
                excluded_characters=list(item.get("excluded_characters") or []),
                remarks=item.get("remarks"),
            )
        )

    for item in pack.get("deduction_sections", []):
        db.add(
            m.DeductionSection(
                finance_act_version_id=fav.id,
                section_code=item["section_code"],
                title=item["title"],
                cap_amount=_dec(item.get("cap_amount")),
                cap_expr=item.get("cap_expr"),
                qualifying_limit_percent=_dec(item.get("qualifying_limit_percent")),
                regime_allowed=list(item.get("regime_allowed") or []),
                age_dependent_caps=dict(item.get("age_dependent_caps") or {}),
                remarks=item.get("remarks"),
            )
        )

    for item in pack.get("due_date_rules", []):
        db.add(
            m.DueDateRule(
                finance_act_version_id=fav.id,
                code=item["code"],
                rule_kind=item["rule_kind"],
                assessee_class_code=item.get("assessee_class_code"),
                seq=int(item.get("seq") or 0),
                due_month=item.get("due_month"),
                due_day=item.get("due_day"),
                percent_of_tax=_dec(item.get("percent_of_tax")),
                label=item.get("label"),
                params=dict(item.get("params") or {}),
            )
        )

    for item in pack.get("interest_rules", []):
        db.add(
            m.InterestRule(
                finance_act_version_id=fav.id,
                code=item["code"],
                section_code=item["section_code"],
                rate_percent_per_month=_dec(item["rate_percent_per_month"]) or Decimal("0"),
                params=dict(item.get("params") or {}),
                remarks=item.get("remarks"),
            )
        )

    for rp in pack.get("rule_packs", []):
        pack_row = m.RulePack(
            finance_act_version_id=fav.id,
            code=rp["code"],
            assessee_class_code=rp["assessee_class_code"],
            regime_code=rp["regime_code"],
            title=rp["title"],
            remarks=rp.get("remarks"),
        )
        db.add(pack_row)
        await db.flush()
        for rule in rp.get("rules") or []:
            db.add(
                m.Rule(
                    rule_pack_id=pack_row.id,
                    provision_section_code=rule["provision_section_code"],
                    rule_code=rule["rule_code"],
                    evaluation_method=rule.get("evaluation_method") or "Manual",
                    params=dict(rule.get("params") or {}),
                    depends_on=list(rule.get("depends_on") or []),
                    seq=int(rule.get("seq") or 0),
                    disabled=bool(rule.get("disabled", False)),
                )
            )

    for form in pack.get("itr_forms", []):
        form_row = m.ItrForm(
            finance_act_version_id=fav.id,
            form_code=form["form_code"],
            schema_version=form["schema_version"],
            title=form["title"],
            assessee_class_codes=list(form.get("assessee_class_codes") or []),
        )
        db.add(form_row)
        await db.flush()
        for fmap in form.get("field_maps") or []:
            db.add(
                m.ItrFieldMap(
                    itr_form_id=form_row.id,
                    canonical_field=fmap["canonical_field"],
                    cbdt_json_path=fmap["cbdt_json_path"],
                    transform=fmap.get("transform"),
                )
            )

    await db.flush()
    return f"{pack['ay']['code']}:{fav.version}"


def discover_ay_dirs(root: Path | None = None) -> list[Path]:
    base = root or PACK_ROOT
    return sorted(
        p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "pack.json").exists()
    )


async def load_all_packs(db: AsyncSession, *, root: Path | None = None) -> list[str]:
    base = root or PACK_ROOT
    common_path = base / "_common.json"
    if not common_path.exists():
        raise PackValidationError(f"missing {common_path}")
    common = json.loads(common_path.read_text(encoding="utf-8"))

    # Load AYs in order so prev_ay_code FKs resolve.
    loaded: list[str] = []
    for ay_dir in discover_ay_dirs(base):
        pack = json.loads((ay_dir / "pack.json").read_text(encoding="utf-8"))
        loaded.append(await load_pack(db, pack, common=common))
    return loaded
