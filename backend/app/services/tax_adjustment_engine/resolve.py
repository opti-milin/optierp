"""Resolve TaxAdjustmentRulePack for AY × entity × regime."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.compliance import TaxAdjustmentProvision, TaxAdjustmentRulePack
from app.services.tax_adjustment_engine.context import (
    ResolvedAdjustmentPack,
    ResolvedAdjustmentRule,
)


async def resolve_adjustment_pack(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    entity_type: str,
    filing_regime: str = "Normal",
    as_of: date | None = None,
    pack_id: uuid.UUID | None = None,
) -> ResolvedAdjustmentPack | None:
    pack: TaxAdjustmentRulePack | None = None
    if pack_id is not None:
        pack = await db.scalar(
            select(TaxAdjustmentRulePack)
            .options(selectinload(TaxAdjustmentRulePack.rules))
            .where(
                TaxAdjustmentRulePack.id == pack_id,
                TaxAdjustmentRulePack.company_id == company_id,
                TaxAdjustmentRulePack.disabled.is_(False),
            )
        )
    if pack is None:
        stmt = (
            select(TaxAdjustmentRulePack)
            .options(selectinload(TaxAdjustmentRulePack.rules))
            .where(
                TaxAdjustmentRulePack.company_id == company_id,
                TaxAdjustmentRulePack.assessment_year == assessment_year,
                TaxAdjustmentRulePack.entity_type == entity_type,
                TaxAdjustmentRulePack.filing_regime == filing_regime,
                TaxAdjustmentRulePack.disabled.is_(False),
            )
        )
        if as_of is not None:
            stmt = stmt.where(
                or_(
                    TaxAdjustmentRulePack.effective_from.is_(None),
                    TaxAdjustmentRulePack.effective_from <= as_of,
                ),
                or_(
                    TaxAdjustmentRulePack.effective_to.is_(None),
                    TaxAdjustmentRulePack.effective_to >= as_of,
                ),
            )
        pack = await db.scalar(stmt)

    if pack is None:
        return None

    prov_ids = {r.provision_id for r in pack.rules}
    prov_map: dict[uuid.UUID, TaxAdjustmentProvision] = {}
    if prov_ids:
        rows = (
            await db.execute(
                select(TaxAdjustmentProvision).where(TaxAdjustmentProvision.id.in_(prov_ids))
            )
        ).scalars().all()
        prov_map = {p.id: p for p in rows}

    rules: list[ResolvedAdjustmentRule] = []
    for r in sorted(pack.rules, key=lambda x: x.sequence):
        if r.disabled:
            continue
        p = prov_map.get(r.provision_id)
        if p is None or p.disabled:
            continue
        if p.regime_scope not in ("Both", filing_regime):
            continue
        rules.append(
            ResolvedAdjustmentRule(
                rule_id=r.id,
                rule_code=r.rule_code,
                provision_id=r.provision_id,
                section_code=p.section_code,
                title=p.title,
                stage=p.stage,
                default_effect=p.default_effect,
                evaluation_method=r.evaluation_method,
                parameters=dict(r.parameters or {}),
                source_type=r.source_type,
                source_config=dict(r.source_config or {}),
                depends_on=list(r.depends_on or []),
                allow_manual_override=bool(r.allow_manual_override),
                include_in_seed_lines=bool(r.include_in_seed_lines),
                sequence=r.sequence,
                itr_schedule_hint=p.itr_schedule_hint,
            )
        )
    return ResolvedAdjustmentPack(
        pack_id=pack.id,
        assessment_year=pack.assessment_year,
        entity_type=pack.entity_type,
        filing_regime=pack.filing_regime,
        rules=rules,
    )
