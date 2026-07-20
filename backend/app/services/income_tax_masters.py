"""Seed / resolve Income Tax Rate Tables + Tax Adjustment Categories.

Default MSME-lean masters covering Company / Proprietor / Firm / LLP ×
Normal / New regimes for recent assessment years. Idempotent per company.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import IncomeTaxRateTable, TaxAdjustmentCategory
from app.schemas.compliance import IncomeTaxSettings

# Assessment years commonly needed in demos / current filings.
_DEFAULT_AYS = ("2024-25", "2025-26", "2026-27")

# Lean statutory proxies — surcharge brackets / individual slabs deferred.
# Company New ≈ s.115BAA (22% + 10% surcharge + 4% cess).
# Company Normal ≈ domestic co. ≤ ₹400 Cr turnover (25% + 4% cess; surcharge 0 lean).
# Firm/LLP ≈ 30% + 4% cess.
# Proprietor ≈ flat proxy for worksheet math (real slab engine later).
_RATE_SPECS: list[tuple[str, str, str, str, str, str]] = [
    # entity, regime, tax, surcharge, cess, remarks
    ("Company", "Normal", "25", "0", "4", "Domestic company (lean ≤400 Cr; surcharge brackets deferred)"),
    ("Company", "New", "22", "10", "4", "s.115BAA lean"),
    ("Proprietor", "Normal", "30", "0", "4", "Lean flat proxy — individual slabs deferred"),
    ("Proprietor", "New", "30", "0", "4", "Lean new-regime proxy — slabs deferred"),
    ("Firm", "Normal", "30", "0", "4", "Partnership firm"),
    ("Firm", "New", "30", "0", "4", "Partnership firm (new regime lean)"),
    ("LLP", "Normal", "30", "0", "4", "LLP"),
    ("LLP", "New", "30", "0", "4", "LLP (new regime lean)"),
]

_ADJ_SPECS: list[tuple[str, str, str]] = [
    ("40(a)(i)", "TDS not deducted / paid on payments to non-residents", "Add"),
    ("40(a)(ia)", "TDS not deducted / paid (residents) — s.40(a)(ia)", "Add"),
    ("40A(3)", "Cash payments exceeding prescribed limit", "Add"),
    ("43B", "Statutory dues unpaid by due date (tax, PF, GST, etc.)", "Add"),
    ("37-personal", "Personal / capital / non-business expenditure", "Add"),
    ("Dep-add", "Depreciation — books excess over IT Act", "Add"),
    ("Dep-ded", "Depreciation — IT Act excess over books", "Deduct"),
    ("ICDS", "ICDS / accounting vs tax timing difference", "Add"),
    ("Prov-exp", "Provisions / contingencies disallowed", "Add"),
    ("Int-tax", "Interest on delayed payment of tax", "Add"),
    ("80G", "Donation deduction u/s 80G (Chapter VI-A)", "Deduct"),
    ("80JJAA", "Employment generation deduction u/s 80JJAA", "Deduct"),
    ("35", "Scientific research / weighted deduction u/s 35", "Deduct"),
    ("Exempt-inc", "Income exempt / taxed under other heads (remove from PGBP)", "Deduct"),
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
    """Pick the rate row matching AY + entity + regime (enabled only)."""
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


async def ensure_income_tax_masters(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, int]:
    """Idempotently seed default rate tables + adjustment categories."""
    rates_added = 0
    cats_added = 0

    for ay in _DEFAULT_AYS:
        for entity, regime, tax, surcharge, cess, remarks in _RATE_SPECS:
            existing = await db.scalar(
                select(IncomeTaxRateTable.id).where(
                    IncomeTaxRateTable.company_id == company_id,
                    IncomeTaxRateTable.assessment_year == ay,
                    IncomeTaxRateTable.entity_type == entity,
                    IncomeTaxRateTable.filing_regime == regime,
                )
            )
            if existing is not None:
                continue
            db.add(
                IncomeTaxRateTable(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    assessment_year=ay,
                    entity_type=entity,
                    filing_regime=regime,
                    tax_rate=Decimal(tax),
                    surcharge_rate=Decimal(surcharge),
                    cess_rate=Decimal(cess),
                    remarks=remarks,
                    disabled=False,
                    owner=user_id,
                    modified_by=user_id,
                )
            )
            rates_added += 1

    for code, name, direction in _ADJ_SPECS:
        existing = await db.scalar(
            select(TaxAdjustmentCategory.id).where(
                TaxAdjustmentCategory.company_id == company_id,
                TaxAdjustmentCategory.category_code == code,
            )
        )
        if existing is not None:
            continue
        db.add(
            TaxAdjustmentCategory(
                id=uuid.uuid4(),
                company_id=company_id,
                category_code=code,
                category_name=name,
                direction=direction,
                disabled=False,
                owner=user_id,
                modified_by=user_id,
            )
        )
        cats_added += 1

    await db.flush()
    return {"rate_tables": rates_added, "adjustment_categories": cats_added}
