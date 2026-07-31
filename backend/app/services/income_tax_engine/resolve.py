"""Resolve Tax Policy + linked masters for a company / AY / as-of date."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ValidationError
from app.models.compliance import (
    HealthEducationCessRule,
    IncomeTaxRateTable,
    IncomeTaxSlabSet,
    RebateRule,
    SpecialIncomeTaxRate,
    SurchargeRuleSet,
    TaxPolicy,
)
from app.services.income_tax_engine.context import (
    ResolvedTaxRules,
    SlabBand,
    SurchargeBand,
    ZERO,
    q,
)


def _in_effect(row: object, as_of: date | None) -> bool:
    if as_of is None:
        return True
    ef = getattr(row, "effective_from", None)
    et = getattr(row, "effective_to", None)
    if ef is not None and as_of < ef:
        return False
    if et is not None and as_of > et:
        return False
    return True


async def resolve_policy(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    entity_type: str,
    filing_regime: str,
    as_of: date | None = None,
    policy_id: uuid.UUID | None = None,
) -> TaxPolicy | None:
    if policy_id is not None:
        row = await db.scalar(
            select(TaxPolicy)
            .options(
                selectinload(TaxPolicy.rate_table),
                selectinload(TaxPolicy.slab_set).selectinload(IncomeTaxSlabSet.lines),
                selectinload(TaxPolicy.surcharge_set).selectinload(SurchargeRuleSet.brackets),
                selectinload(TaxPolicy.cess_rule),
                selectinload(TaxPolicy.rebate_rule),
            )
            .where(TaxPolicy.id == policy_id, TaxPolicy.company_id == company_id)
        )
        if row is None or row.disabled:
            raise ValidationError("Tax policy not found or disabled", field="policy_id")
        return row

    rows = (
        await db.scalars(
            select(TaxPolicy)
            .options(
                selectinload(TaxPolicy.rate_table),
                selectinload(TaxPolicy.slab_set).selectinload(IncomeTaxSlabSet.lines),
                selectinload(TaxPolicy.surcharge_set).selectinload(SurchargeRuleSet.brackets),
                selectinload(TaxPolicy.cess_rule),
                selectinload(TaxPolicy.rebate_rule),
            )
            .where(
                TaxPolicy.company_id == company_id,
                TaxPolicy.assessment_year == assessment_year,
                TaxPolicy.entity_type == entity_type,
                TaxPolicy.filing_regime == filing_regime,
                TaxPolicy.disabled.is_(False),
            )
        )
    ).all()
    effective = [r for r in rows if _in_effect(r, as_of)]
    if not effective:
        return None
    # Prefer the most recently effective_from (or any if all null)
    effective.sort(key=lambda r: r.effective_from or date.min, reverse=True)
    return effective[0]


def rules_from_policy(policy: TaxPolicy) -> ResolvedTaxRules:
    slabs: list[SlabBand] = []
    if policy.slab_set is not None and not policy.slab_set.disabled:
        for ln in sorted(policy.slab_set.lines, key=lambda x: x.idx):
            slabs.append(
                SlabBand(
                    from_amount=q(ln.from_amount),
                    to_amount=q(ln.to_amount) if ln.to_amount is not None else None,
                    rate_percent=q(ln.rate_percent),
                )
            )

    brackets: list[SurchargeBand] = []
    marginal = False
    if policy.surcharge_set is not None and not policy.surcharge_set.disabled:
        marginal = bool(policy.surcharge_set.marginal_relief_enabled)
        for b in sorted(policy.surcharge_set.brackets, key=lambda x: x.idx):
            brackets.append(
                SurchargeBand(
                    income_from=q(b.income_from),
                    income_to=q(b.income_to) if b.income_to is not None else None,
                    rate_percent=q(b.rate_percent),
                )
            )

    flat = ZERO
    rate_table_id = policy.rate_table_id
    if policy.rate_table is not None and not policy.rate_table.disabled:
        flat = q(policy.rate_table.tax_rate)

    cess_rate = ZERO
    cess_base = "TaxPlusSurcharge"
    if policy.cess_rule is not None and not policy.cess_rule.disabled:
        cess_rate = q(policy.cess_rule.cess_rate)
        cess_base = policy.cess_rule.cess_base or cess_base

    rebate_section = None
    rebate_max_taxable = ZERO
    rebate_max_amount = ZERO
    if policy.rebate_rule is not None and not policy.rebate_rule.disabled:
        rebate_section = policy.rebate_rule.section_code
        rebate_max_taxable = q(policy.rebate_rule.max_taxable_income)
        rebate_max_amount = q(policy.rebate_rule.max_rebate_amount)

    return ResolvedTaxRules(
        policy_id=policy.id,
        computation_method=policy.computation_method,
        ordinary_method=policy.ordinary_method or policy.computation_method,
        filing_regime=policy.filing_regime,
        entity_type=policy.entity_type,
        flat_tax_rate=flat,
        slabs=slabs,
        surcharge_brackets=brackets,
        marginal_relief_enabled=marginal,
        cess_rate=cess_rate,
        cess_base=cess_base,
        rebate_section=rebate_section,
        rebate_max_taxable=rebate_max_taxable,
        rebate_max_amount=rebate_max_amount,
        rate_table_id=rate_table_id,
    )


async def lookup_special_rate(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    filing_regime: str,
    income_category_code: str,
    as_of: date | None = None,
) -> SpecialIncomeTaxRate | None:
    rows = (
        await db.scalars(
            select(SpecialIncomeTaxRate).where(
                SpecialIncomeTaxRate.company_id == company_id,
                SpecialIncomeTaxRate.assessment_year == assessment_year,
                SpecialIncomeTaxRate.filing_regime == filing_regime,
                SpecialIncomeTaxRate.income_category_code == income_category_code,
                SpecialIncomeTaxRate.disabled.is_(False),
            )
        )
    ).all()
    effective = [r for r in rows if _in_effect(r, as_of)]
    return effective[0] if effective else None


# Re-exports used by seed / tests
__all__ = [
    "HealthEducationCessRule",
    "IncomeTaxRateTable",
    "IncomeTaxSlabSet",
    "RebateRule",
    "resolve_policy",
    "rules_from_policy",
    "lookup_special_rate",
    "SurchargeRuleSet",
]
