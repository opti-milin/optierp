"""Read-only accessors for the statutory catalogue (process-level cache later)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import statutory as m


async def list_assessment_years(db: AsyncSession) -> list[m.AssessmentYear]:
    result = await db.scalars(select(m.AssessmentYear).order_by(m.AssessmentYear.code))
    return list(result.all())


async def list_assessee_classes(db: AsyncSession) -> list[m.AssesseeClass]:
    result = await db.scalars(select(m.AssesseeClass).order_by(m.AssesseeClass.code))
    return list(result.all())


async def list_income_characters(db: AsyncSession) -> list[m.IncomeCharacter]:
    result = await db.scalars(select(m.IncomeCharacter).order_by(m.IncomeCharacter.code))
    return list(result.all())


async def list_regimes(db: AsyncSession, *, assessee_class_code: str | None = None) -> list[m.TaxRegime]:
    stmt = select(m.TaxRegime).order_by(m.TaxRegime.assessee_class_code, m.TaxRegime.code)
    if assessee_class_code:
        stmt = stmt.where(m.TaxRegime.assessee_class_code == assessee_class_code)
    result = await db.scalars(stmt)
    return list(result.all())


async def get_current_finance_act(
    db: AsyncSession, *, ay_code: str
) -> m.FinanceActVersion | None:
    return await db.scalar(
        select(m.FinanceActVersion).where(
            m.FinanceActVersion.ay_code == ay_code,
            m.FinanceActVersion.is_current.is_(True),
        )
    )


async def list_rate_schedules(
    db: AsyncSession,
    *,
    ay_code: str,
    assessee_class_code: str | None = None,
    regime_code: str | None = None,
) -> list[m.RateSchedule]:
    fav = await get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    stmt = (
        select(m.RateSchedule)
        .where(m.RateSchedule.finance_act_version_id == fav.id)
        .options(selectinload(m.RateSchedule.bands))
        .order_by(m.RateSchedule.code)
    )
    if assessee_class_code:
        stmt = stmt.where(m.RateSchedule.assessee_class_code == assessee_class_code)
    if regime_code:
        stmt = stmt.where(m.RateSchedule.regime_code == regime_code)
    result = await db.scalars(stmt)
    return list(result.all())


async def list_surcharge_schedules(
    db: AsyncSession, *, ay_code: str, assessee_class_code: str | None = None
) -> list[m.SurchargeSchedule]:
    fav = await get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    stmt = (
        select(m.SurchargeSchedule)
        .where(m.SurchargeSchedule.finance_act_version_id == fav.id)
        .options(selectinload(m.SurchargeSchedule.bands))
        .order_by(m.SurchargeSchedule.code)
    )
    if assessee_class_code:
        stmt = stmt.where(m.SurchargeSchedule.assessee_class_code == assessee_class_code)
    result = await db.scalars(stmt)
    return list(result.all())


async def list_provisions(db: AsyncSession) -> list[m.Provision]:
    result = await db.scalars(
        select(m.Provision).where(m.Provision.disabled.is_(False)).order_by(m.Provision.section_code)
    )
    return list(result.all())


async def list_due_date_rules(db: AsyncSession, *, ay_code: str) -> list[m.DueDateRule]:
    fav = await get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    result = await db.scalars(
        select(m.DueDateRule)
        .where(m.DueDateRule.finance_act_version_id == fav.id)
        .order_by(m.DueDateRule.rule_kind, m.DueDateRule.seq)
    )
    return list(result.all())


async def list_rebate_rules(db: AsyncSession, *, ay_code: str) -> list[m.StatutoryRebateRule]:
    fav = await get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    result = await db.scalars(
        select(m.StatutoryRebateRule)
        .where(m.StatutoryRebateRule.finance_act_version_id == fav.id)
        .order_by(m.StatutoryRebateRule.code)
    )
    return list(result.all())


async def list_cess_rules(db: AsyncSession, *, ay_code: str) -> list[m.CessRule]:
    fav = await get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    result = await db.scalars(
        select(m.CessRule).where(m.CessRule.finance_act_version_id == fav.id).order_by(m.CessRule.code)
    )
    return list(result.all())


async def list_depreciation_blocks(db: AsyncSession) -> list[m.DepreciationBlock]:
    result = await db.scalars(select(m.DepreciationBlock).order_by(m.DepreciationBlock.block_code))
    return list(result.all())


async def list_itr_forms(
    db: AsyncSession,
    *,
    ay_code: str,
    form_code: str | None = None,
) -> list[m.ItrForm]:
    fav = await get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    stmt = (
        select(m.ItrForm)
        .where(m.ItrForm.finance_act_version_id == fav.id)
        .options(selectinload(m.ItrForm.field_maps))
        .order_by(m.ItrForm.form_code)
    )
    if form_code:
        stmt = stmt.where(m.ItrForm.form_code == form_code)
    result = await db.scalars(stmt)
    return list(result.all())


def money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    from decimal import ROUND_HALF_UP

    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(quantized, "f")


def fav_id_str(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None
