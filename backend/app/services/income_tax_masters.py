"""Seed / resolve Income Tax rule masters (policy-driven engine).

Seeds flat rate tables, slab sets, surcharge packs, cess, 87A rebates,
special rates, and Tax Policy rows that wire them together. No statutory
numbers are hardcoded in the computation engine — only here as seed data.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.compliance import (
    HealthEducationCessRule,
    IncomeTaxRateTable,
    IncomeTaxSlabLine,
    IncomeTaxSlabSet,
    RebateRule,
    SpecialIncomeTaxRate,
    SurchargeBracket,
    SurchargeRuleSet,
    TaxPolicy,
)
from app.schemas.compliance import IncomeTaxSettings
from app.services.income_tax_engine.resolve import resolve_policy, rules_from_policy

_DEFAULT_AYS = ("2024-25", "2025-26", "2026-27")

# Flat-rate entities (Company / Firm / LLP): (entity, regime, tax%, surcharge%, cess%, remarks)
_FLAT_RATE_SPECS: list[tuple[str, str, str, str, str, str]] = [
    ("Company", "Normal", "25", "0", "4", "Domestic company (lean ≤400 Cr)"),
    ("Company", "New", "22", "10", "4", "s.115BAA lean"),
    ("Firm", "Normal", "30", "0", "4", "Partnership firm"),
    ("Firm", "New", "30", "0", "4", "Partnership firm (new regime lean)"),
    ("LLP", "Normal", "30", "0", "4", "LLP"),
    ("LLP", "New", "30", "0", "4", "LLP (new regime lean)"),
]

_NORMAL_SLABS: list[tuple[str, str | None, str]] = [
    ("0", "250000", "0"),
    ("250000", "500000", "5"),
    ("500000", "1000000", "20"),
    ("1000000", None, "30"),
]
_NEW_SLABS: list[tuple[str, str | None, str]] = [
    ("0", "300000", "0"),
    ("300000", "700000", "5"),
    ("700000", "1000000", "10"),
    ("1000000", "1200000", "15"),
    ("1200000", "1500000", "20"),
    ("1500000", None, "30"),
]

_SLAB_ENTITIES = ("Proprietor", "Individual")

# Lean individual surcharge brackets (income thresholds → surcharge % of tax)
_INDIVIDUAL_SURCHARGE: list[tuple[str, str | None, str]] = [
    ("0", "5000000", "0"),
    ("5000000", "10000000", "10"),
    ("10000000", "20000000", "15"),
    ("20000000", None, "25"),
]

# 87A seed: (regime, max_taxable, max_rebate)
_REBATE_87A: list[tuple[str, str, str]] = [
    ("Normal", "500000", "12500"),
    ("New", "700000", "25000"),
]

# Special rates (lean proxies)
_SPECIAL_RATES: list[tuple[str, str, str]] = [
    ("LTCG_EQUITY", "10", "Long-term capital gains on equity (lean)"),
    ("STCG_EQUITY", "15", "Short-term capital gains on equity (lean)"),
    ("LOTTERY", "30", "Winnings from lottery / crossword (lean)"),
    ("CRYPTO", "30", "Virtual digital assets (lean)"),
]

_ADJ_SPECS: list[tuple[str, str, str]] = [
    # Deprecated — catalogue seed in tax_adjustment_engine.catalogue_seed supersedes.
]


async def resolve_rate_table(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    settings: IncomeTaxSettings | None = None,
    entity_type: str | None = None,
    filing_regime: str | None = None,
) -> IncomeTaxRateTable | None:
    entity = entity_type or (settings.entity_type if settings else "Company")
    regime = filing_regime or (settings.filing_regime if settings else "Normal")
    return await db.scalar(
        select(IncomeTaxRateTable).where(
            IncomeTaxRateTable.company_id == company_id,
            IncomeTaxRateTable.assessment_year == assessment_year,
            IncomeTaxRateTable.entity_type == entity,
            IncomeTaxRateTable.filing_regime == regime,
            IncomeTaxRateTable.disabled.is_(False),
        )
    )


async def resolve_tax_rules(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    settings: IncomeTaxSettings | None = None,
    entity_type: str | None = None,
    filing_regime: str | None = None,
    as_of=None,
    policy_id: uuid.UUID | None = None,
):
    """Resolve Tax Policy → ResolvedTaxRules (engine input)."""
    entity = entity_type or (settings.entity_type if settings else "Company")
    regime = filing_regime or (settings.filing_regime if settings else "Normal")
    policy = await resolve_policy(
        db,
        company_id=company_id,
        assessment_year=assessment_year,
        entity_type=entity,
        filing_regime=regime,
        as_of=as_of,
        policy_id=policy_id,
    )
    if policy is None:
        return None, None
    return policy, rules_from_policy(policy)


def _slab_bands(regime: str) -> list[tuple[str, str | None, str]]:
    return _NEW_SLABS if regime == "New" else _NORMAL_SLABS


async def _ensure_flat_rate(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay: str,
    entity: str,
    regime: str,
    tax: str,
    remarks: str,
    user_id: uuid.UUID | None,
) -> IncomeTaxRateTable:
    row = await db.scalar(
        select(IncomeTaxRateTable).where(
            IncomeTaxRateTable.company_id == company_id,
            IncomeTaxRateTable.assessment_year == ay,
            IncomeTaxRateTable.entity_type == entity,
            IncomeTaxRateTable.filing_regime == regime,
        )
    )
    if row is None:
        row = IncomeTaxRateTable(
            id=uuid.uuid4(),
            company_id=company_id,
            assessment_year=ay,
            entity_type=entity,
            filing_regime=regime,
            tax_rate=Decimal(tax),
            remarks=remarks,
            owner=user_id,
            modified_by=user_id,
        )
        db.add(row)
        await db.flush()
    return row


async def _ensure_slab_set(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay: str,
    entity: str,
    regime: str,
    user_id: uuid.UUID | None,
) -> IncomeTaxSlabSet:
    set_name = f"{entity} {regime} {ay}"
    row = await db.scalar(
        select(IncomeTaxSlabSet)
        .options(selectinload(IncomeTaxSlabSet.lines))
        .where(
            IncomeTaxSlabSet.company_id == company_id,
            IncomeTaxSlabSet.set_name == set_name,
        )
    )
    if row is None:
        row = IncomeTaxSlabSet(
            id=uuid.uuid4(),
            company_id=company_id,
            set_name=set_name,
            assessment_year=ay,
            entity_type=entity,
            filing_regime=regime,
            remarks=f"{entity} — lean {regime.lower()}-regime slabs",
            owner=user_id,
            modified_by=user_id,
        )
        db.add(row)
        await db.flush()
    if not row.lines:
        for i, (frm, to, rate_pct) in enumerate(_slab_bands(regime)):
            db.add(
                IncomeTaxSlabLine(
                    id=uuid.uuid4(),
                    slab_set_id=row.id,
                    idx=i,
                    from_amount=Decimal(frm),
                    to_amount=Decimal(to) if to is not None else None,
                    rate_percent=Decimal(rate_pct),
                    owner=user_id,
                    modified_by=user_id,
                )
            )
        await db.flush()
    return row


async def _ensure_surcharge_set(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay: str,
    entity: str,
    regime: str,
    flat_rate: str,
    brackets: list[tuple[str, str | None, str]] | None,
    marginal_relief: bool,
    user_id: uuid.UUID | None,
) -> SurchargeRuleSet:
    set_name = f"SUR-{entity}-{regime}-{ay}"
    row = await db.scalar(
        select(SurchargeRuleSet)
        .options(selectinload(SurchargeRuleSet.brackets))
        .where(
            SurchargeRuleSet.company_id == company_id,
            SurchargeRuleSet.set_name == set_name,
        )
    )
    if row is None:
        row = SurchargeRuleSet(
            id=uuid.uuid4(),
            company_id=company_id,
            set_name=set_name,
            assessment_year=ay,
            entity_type=entity,
            filing_regime=regime,
            marginal_relief_enabled=marginal_relief,
            owner=user_id,
            modified_by=user_id,
        )
        db.add(row)
        await db.flush()
    else:
        # Re-seed may create the parent before brackets/flags are finalized — keep in sync.
        row.marginal_relief_enabled = marginal_relief
        row.modified_by = user_id
    if not row.brackets:
        bands = brackets or [("0", None, flat_rate)]
        for i, (frm, to, rate_pct) in enumerate(bands):
            db.add(
                SurchargeBracket(
                    id=uuid.uuid4(),
                    rule_set_id=row.id,
                    idx=i,
                    income_from=Decimal(frm),
                    income_to=Decimal(to) if to is not None else None,
                    rate_percent=Decimal(rate_pct),
                    owner=user_id,
                    modified_by=user_id,
                )
            )
        await db.flush()
    return row


async def _ensure_cess(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay: str,
    entity: str,
    regime: str,
    cess: str,
    user_id: uuid.UUID | None,
) -> HealthEducationCessRule:
    rule_name = f"CESS-{entity}-{regime}-{ay}"
    row = await db.scalar(
        select(HealthEducationCessRule).where(
            HealthEducationCessRule.company_id == company_id,
            HealthEducationCessRule.rule_name == rule_name,
        )
    )
    if row is None:
        row = HealthEducationCessRule(
            id=uuid.uuid4(),
            company_id=company_id,
            rule_name=rule_name,
            assessment_year=ay,
            entity_type=entity,
            filing_regime=regime,
            cess_rate=Decimal(cess),
            cess_base="TaxPlusSurcharge",
            owner=user_id,
            modified_by=user_id,
        )
        db.add(row)
        await db.flush()
    return row


async def _ensure_rebate(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay: str,
    regime: str,
    max_taxable: str,
    max_rebate: str,
    user_id: uuid.UUID | None,
) -> RebateRule:
    row = await db.scalar(
        select(RebateRule).where(
            RebateRule.company_id == company_id,
            RebateRule.assessment_year == ay,
            RebateRule.section_code == "87A",
            RebateRule.filing_regime == regime,
        )
    )
    if row is None:
        row = RebateRule(
            id=uuid.uuid4(),
            company_id=company_id,
            section_code="87A",
            assessment_year=ay,
            filing_regime=regime,
            max_taxable_income=Decimal(max_taxable),
            max_rebate_amount=Decimal(max_rebate),
            remarks="Section 87A lean seed",
            owner=user_id,
            modified_by=user_id,
        )
        db.add(row)
        await db.flush()
    return row


async def _ensure_policy(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay: str,
    entity: str,
    regime: str,
    method: str,
    ordinary: str,
    rate_table_id: uuid.UUID | None,
    slab_set_id: uuid.UUID | None,
    surcharge_set_id: uuid.UUID | None,
    cess_rule_id: uuid.UUID | None,
    rebate_rule_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> TaxPolicy:
    row = await db.scalar(
        select(TaxPolicy).where(
            TaxPolicy.company_id == company_id,
            TaxPolicy.assessment_year == ay,
            TaxPolicy.entity_type == entity,
            TaxPolicy.filing_regime == regime,
        )
    )
    if row is None:
        row = TaxPolicy(
            id=uuid.uuid4(),
            company_id=company_id,
            assessment_year=ay,
            entity_type=entity,
            filing_regime=regime,
            computation_method=method,
            ordinary_method=ordinary,
            rate_table_id=rate_table_id,
            slab_set_id=slab_set_id,
            surcharge_set_id=surcharge_set_id,
            cess_rule_id=cess_rule_id,
            rebate_rule_id=rebate_rule_id,
            owner=user_id,
            modified_by=user_id,
        )
        db.add(row)
        await db.flush()
    else:
        # Keep links fresh on re-seed
        row.computation_method = method
        row.ordinary_method = ordinary
        row.rate_table_id = rate_table_id or row.rate_table_id
        row.slab_set_id = slab_set_id or row.slab_set_id
        row.surcharge_set_id = surcharge_set_id or row.surcharge_set_id
        row.cess_rule_id = cess_rule_id or row.cess_rule_id
        row.rebate_rule_id = rebate_rule_id or row.rebate_rule_id
    return row


async def ensure_income_tax_masters(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Idempotent seed of all income-tax rule masters + tax policies."""
    counts = {
        "rate_tables": 0,
        "slab_sets": 0,
        "surcharge_sets": 0,
        "cess_rules": 0,
        "rebate_rules": 0,
        "special_rates": 0,
        "policies": 0,
        "adjustment_categories": 0,
    }

    for ay in _DEFAULT_AYS:
        # Flat entities
        for entity, regime, tax, surcharge, cess, remarks in _FLAT_RATE_SPECS:
            before = await db.scalar(
                select(IncomeTaxRateTable.id).where(
                    IncomeTaxRateTable.company_id == company_id,
                    IncomeTaxRateTable.assessment_year == ay,
                    IncomeTaxRateTable.entity_type == entity,
                    IncomeTaxRateTable.filing_regime == regime,
                )
            )
            rate = await _ensure_flat_rate(
                db,
                company_id=company_id,
                ay=ay,
                entity=entity,
                regime=regime,
                tax=tax,
                remarks=remarks,
                user_id=user_id,
            )
            if before is None:
                counts["rate_tables"] += 1

            sur = await _ensure_surcharge_set(
                db,
                company_id=company_id,
                ay=ay,
                entity=entity,
                regime=regime,
                flat_rate=surcharge,
                brackets=None,
                marginal_relief=False,
                user_id=user_id,
            )
            cess_rule = await _ensure_cess(
                db,
                company_id=company_id,
                ay=ay,
                entity=entity,
                regime=regime,
                cess=cess,
                user_id=user_id,
            )
            await _ensure_policy(
                db,
                company_id=company_id,
                ay=ay,
                entity=entity,
                regime=regime,
                method="FlatRate",
                ordinary="FlatRate",
                rate_table_id=rate.id,
                slab_set_id=None,
                surcharge_set_id=sur.id,
                cess_rule_id=cess_rule.id,
                rebate_rule_id=None,
                user_id=user_id,
            )
            counts["policies"] += 1

        # Slab entities
        for entity in _SLAB_ENTITIES:
            for regime, max_taxable, max_rebate in _REBATE_87A:
                before_ss = await db.scalar(
                    select(IncomeTaxSlabSet.id).where(
                        IncomeTaxSlabSet.company_id == company_id,
                        IncomeTaxSlabSet.set_name == f"{entity} {regime} {ay}",
                    )
                )
                slab = await _ensure_slab_set(
                    db,
                    company_id=company_id,
                    ay=ay,
                    entity=entity,
                    regime=regime,
                    user_id=user_id,
                )
                if before_ss is None:
                    counts["slab_sets"] += 1

                sur = await _ensure_surcharge_set(
                    db,
                    company_id=company_id,
                    ay=ay,
                    entity=entity,
                    regime=regime,
                    flat_rate="0",
                    brackets=_INDIVIDUAL_SURCHARGE,
                    marginal_relief=True,
                    user_id=user_id,
                )
                cess_rule = await _ensure_cess(
                    db,
                    company_id=company_id,
                    ay=ay,
                    entity=entity,
                    regime=regime,
                    cess="4",
                    user_id=user_id,
                )
                rebate = await _ensure_rebate(
                    db,
                    company_id=company_id,
                    ay=ay,
                    regime=regime,
                    max_taxable=max_taxable,
                    max_rebate=max_rebate,
                    user_id=user_id,
                )
                await _ensure_policy(
                    db,
                    company_id=company_id,
                    ay=ay,
                    entity=entity,
                    regime=regime,
                    method="SlabBased",
                    ordinary="SlabBased",
                    rate_table_id=None,
                    slab_set_id=slab.id,
                    surcharge_set_id=sur.id,
                    cess_rule_id=cess_rule.id,
                    rebate_rule_id=rebate.id,
                    user_id=user_id,
                )
                counts["policies"] += 1

        # Special rates (both regimes)
        for regime in ("Normal", "New"):
            for code, rate_pct, desc in _SPECIAL_RATES:
                exists = await db.scalar(
                    select(SpecialIncomeTaxRate.id).where(
                        SpecialIncomeTaxRate.company_id == company_id,
                        SpecialIncomeTaxRate.assessment_year == ay,
                        SpecialIncomeTaxRate.income_category_code == code,
                        SpecialIncomeTaxRate.filing_regime == regime,
                    )
                )
                if exists is not None:
                    continue
                db.add(
                    SpecialIncomeTaxRate(
                        id=uuid.uuid4(),
                        company_id=company_id,
                        income_category_code=code,
                        assessment_year=ay,
                        filing_regime=regime,
                        rate_percent=Decimal(rate_pct),
                        description=desc,
                        owner=user_id,
                        modified_by=user_id,
                    )
                )
                counts["special_rates"] += 1

    from app.services.tax_adjustment_engine.catalogue_seed import seed_tax_adjustment_catalogue

    adj_counts = await seed_tax_adjustment_catalogue(db, company_id, user_id)
    counts["adjustment_categories"] = adj_counts.get("adjustment_categories", 0)
    counts["adjustment_provisions"] = adj_counts.get("provisions", 0)
    counts["adjustment_rule_packs"] = adj_counts.get("rule_packs", 0)
    counts["adjustment_rules"] = adj_counts.get("rules", 0)

    await db.flush()
    return counts
