"""Seed TaxAdjustmentProvision + RulePacks from JSON catalogue."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import (
    TaxAdjustmentCategory,
    TaxAdjustmentProvision,
    TaxAdjustmentRule,
    TaxAdjustmentRulePack,
)

_DATA = Path(__file__).resolve().parents[3] / "data" / "tax_adjustment_packs"


def _load_json(name: str) -> dict[str, Any]:
    path = _DATA / name
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


async def seed_tax_adjustment_catalogue(
    db: AsyncSession,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> dict[str, int]:
    """Idempotent seed of provisions, legacy categories, and AY rule packs."""
    counts = {
        "provisions": 0,
        "adjustment_categories": 0,
        "rule_packs": 0,
        "rules": 0,
    }
    provisions_doc = _load_json("provisions.json")
    rules_doc = _load_json("default_rules.json")

    prov_by_code: dict[str, TaxAdjustmentProvision] = {}
    for spec in provisions_doc.get("provisions", []):
        code = spec["section_code"]
        existing = await db.scalar(
            select(TaxAdjustmentProvision).where(
                TaxAdjustmentProvision.company_id == company_id,
                TaxAdjustmentProvision.section_code == code,
            )
        )
        if existing is None:
            existing = TaxAdjustmentProvision(
                id=uuid.uuid4(),
                company_id=company_id,
                section_code=code,
                title=spec["title"],
                act_reference=spec.get("act_reference"),
                stage=spec.get("stage", "PGBP"),
                default_effect=spec.get("default_effect", "Add"),
                applies_to_modes=spec.get("applies_to_modes", "EntityBooks"),
                regime_scope=spec.get("regime_scope", "Both"),
                itr_schedule_hint=spec.get("itr_schedule_hint"),
                owner=user_id,
                modified_by=user_id,
            )
            db.add(existing)
            counts["provisions"] += 1
            await db.flush()
        prov_by_code[code] = existing

        # Legacy category bridge (compatibility with old UI / FKs)
        legacy_code = spec.get("legacy_category_code")
        effect = spec.get("default_effect", "Add")
        if effect == "Informational" or not legacy_code:
            continue
        direction = "Deduct" if effect == "Deduct" else "Add"
        cat = await db.scalar(
            select(TaxAdjustmentCategory).where(
                TaxAdjustmentCategory.company_id == company_id,
                TaxAdjustmentCategory.category_code == legacy_code,
            )
        )
        if cat is None:
            cat = TaxAdjustmentCategory(
                id=uuid.uuid4(),
                company_id=company_id,
                category_code=legacy_code,
                category_name=spec["title"][:140],
                direction=direction,
                provision_id=existing.id,
                owner=user_id,
                modified_by=user_id,
            )
            db.add(cat)
            counts["adjustment_categories"] += 1
        elif cat.provision_id is None:
            cat.provision_id = existing.id

    await db.flush()

    ays = rules_doc.get("assessment_years") or []
    entity_regimes = rules_doc.get("entity_regimes") or []
    rule_specs = rules_doc.get("rules") or []

    for ay in ays:
        for er in entity_regimes:
            entity = er["entity_type"]
            regime = er["filing_regime"]
            pack = await db.scalar(
                select(TaxAdjustmentRulePack).where(
                    TaxAdjustmentRulePack.company_id == company_id,
                    TaxAdjustmentRulePack.assessment_year == ay,
                    TaxAdjustmentRulePack.entity_type == entity,
                    TaxAdjustmentRulePack.filing_regime == regime,
                )
            )
            if pack is None:
                pack = TaxAdjustmentRulePack(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    pack_name=f"{entity} {regime} {ay}",
                    assessment_year=ay,
                    entity_type=entity,
                    filing_regime=regime,
                    remarks="Seeded from tax_adjustment_packs/default_rules.json",
                    owner=user_id,
                    modified_by=user_id,
                )
                db.add(pack)
                await db.flush()
                counts["rule_packs"] += 1

            for rspec in rule_specs:
                # Filter by entity_types / regime_scope / modes on rule spec
                r_entities = rspec.get("entity_types")
                if r_entities and entity not in r_entities:
                    continue
                r_regime = rspec.get("regime_scope")
                if r_regime and r_regime not in ("Both", regime):
                    continue
                section = rspec["section_code"]
                prov = prov_by_code.get(section)
                if prov is None:
                    continue
                if prov.regime_scope not in ("Both", regime):
                    continue
                exists = await db.scalar(
                    select(TaxAdjustmentRule.id).where(
                        TaxAdjustmentRule.pack_id == pack.id,
                        TaxAdjustmentRule.rule_code == rspec["rule_code"],
                    )
                )
                if exists is not None:
                    continue
                db.add(
                    TaxAdjustmentRule(
                        id=uuid.uuid4(),
                        company_id=company_id,
                        pack_id=pack.id,
                        provision_id=prov.id,
                        rule_code=rspec["rule_code"],
                        evaluation_method=rspec.get("evaluation_method", "Manual"),
                        parameters=dict(rspec.get("parameters") or {}),
                        source_type=rspec.get("source_type", "Manual"),
                        source_config=dict(rspec.get("source_config") or {}),
                        depends_on=list(rspec.get("depends_on") or []),
                        allow_manual_override=bool(rspec.get("allow_manual_override", True)),
                        include_in_seed_lines=bool(rspec.get("include_in_seed_lines", False)),
                        sequence=int(rspec.get("sequence") or 0),
                        owner=user_id,
                        modified_by=user_id,
                    )
                )
                counts["rules"] += 1

    await db.flush()
    return counts
