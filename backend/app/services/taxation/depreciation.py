"""Tax depreciation register — block WDV with <180-day half-rate from assets."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser
from app.models import statutory as stat
from app.models.assets import Asset, AssetCategory
from app.models.base import DOCSTATUS_SUBMITTED
from app.models.tax_corporate import TaxDepreciationMovement, TaxDepreciationRegister
from app.schemas.taxation import (
    TaxDepreciationRegisterOut,
    TaxDepreciationSyncRequest,
)
from app.services.taxation.kernel.money import ZERO, money, percent_of, q


def is_half_rate(put_to_use: date, fy_end: date) -> bool:
    """IT Act: if asset used for < 180 days in the FY, depreciation at half the rate."""
    days = (fy_end - put_to_use).days + 1
    return days < 180


def compute_block_depreciation(
    *,
    opening_wdv: Decimal,
    additions_full: Decimal,
    additions_half: Decimal,
    deletions: Decimal,
    rate_percent: Decimal,
    additional_eligible: bool = False,
    additional_rate_percent: Decimal = Decimal("20"),
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (depreciation, additional_depreciation, closing_wdv)."""
    opening = q(money(opening_wdv))
    full_add = q(money(additions_full))
    half_add = q(money(additions_half))
    dels = q(money(deletions))
    rate = money(rate_percent)

    base_full = q(opening + full_add - dels)
    if base_full < ZERO:
        base_full = ZERO
    dep_full = q(percent_of(base_full, rate))
    dep_half = q(percent_of(half_add, rate) / Decimal("2"))
    depreciation = q(dep_full + dep_half)

    additional = ZERO
    if additional_eligible and (full_add + half_add) > ZERO:
        # Additional dep on new plant additions (full rate on full-year additions;
        # half of additional rate when half-rate applies).
        add_full = q(percent_of(full_add, additional_rate_percent))
        add_half = q(percent_of(half_add, additional_rate_percent) / Decimal("2"))
        additional = q(add_full + add_half)

    closing = q(opening + full_add + half_add - dels - depreciation - additional)
    if closing < ZERO:
        closing = ZERO
    return depreciation, additional, closing


async def _ay_dates(db: AsyncSession, ay_code: str) -> tuple[date, date]:
    ay = await db.scalar(select(stat.AssessmentYear).where(stat.AssessmentYear.code == ay_code))
    if ay is None:
        raise ValidationError(f"Unknown AY {ay_code}", field="ay_code", code="unknown_ay")
    return ay.fy_start, ay.fy_end


async def _block_rate(db: AsyncSession, block_code: str) -> stat.DepreciationBlock:
    row = await db.scalar(
        select(stat.DepreciationBlock).where(stat.DepreciationBlock.block_code == block_code)
    )
    if row is None:
        raise ValidationError(
            f"Unknown depreciation block {block_code}",
            field="block_code",
            code="unknown_block",
        )
    return row


async def list_registers(
    db: AsyncSession, company_id: uuid.UUID, *, ay_code: str | None = None
) -> list[TaxDepreciationRegister]:
    stmt = (
        select(TaxDepreciationRegister)
        .where(TaxDepreciationRegister.company_id == company_id)
        .options(selectinload(TaxDepreciationRegister.movements))
        .order_by(TaxDepreciationRegister.block_code)
    )
    if ay_code:
        stmt = stmt.where(TaxDepreciationRegister.ay_code == ay_code)
    return list((await db.scalars(stmt)).all())


async def total_depreciation(
    db: AsyncSession, company_id: uuid.UUID, ay_code: str
) -> Decimal:
    rows = await list_registers(db, company_id, ay_code=ay_code)
    return q(sum((money(r.depreciation_amount + r.additional_depreciation_amount) for r in rows), ZERO))


async def sync_from_assets(
    db: AsyncSession, payload: TaxDepreciationSyncRequest, user: CurrentUser
) -> list[TaxDepreciationRegister]:
    """Rebuild AY registers from assets whose category has tax_block_code."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    fy_start, fy_end = await _ay_dates(db, payload.ay_code)

    assets = list(
        (
            await db.scalars(
                select(Asset)
                .where(
                    Asset.company_id == user.company_id,
                    Asset.docstatus == DOCSTATUS_SUBMITTED,
                )
                .options(selectinload(Asset.category))
            )
        ).all()
    )

    # Group movements by block
    by_block: dict[str, list[tuple[Asset, Decimal, bool, date]]] = {}
    for asset in assets:
        cat: AssetCategory | None = asset.category
        block_code = getattr(cat, "tax_block_code", None) if cat else None
        if not block_code:
            continue
        put = asset.available_for_use_date
        amount = q(money(asset.gross_purchase_amount))
        if amount <= ZERO:
            continue
        # Addition in this FY if put-to-use in FY; else treat as opening if before FY
        if put > fy_end:
            continue
        half = is_half_rate(put, fy_end) if put >= fy_start else False
        by_block.setdefault(block_code, []).append((asset, amount, half, put))

    # Opening WDV from prior AY closing (if present)
    prior = None
    ay = await db.scalar(
        select(stat.AssessmentYear).where(stat.AssessmentYear.code == payload.ay_code)
    )
    if ay and ay.prev_ay_code:
        prior = ay.prev_ay_code

    existing = await list_registers(db, user.company_id, ay_code=payload.ay_code)
    for reg in existing:
        await db.delete(reg)
    await db.flush()

    out: list[TaxDepreciationRegister] = []
    for block_code, items in sorted(by_block.items()):
        block = await _block_rate(db, block_code)
        opening = ZERO
        if prior:
            prev_reg = await db.scalar(
                select(TaxDepreciationRegister).where(
                    TaxDepreciationRegister.company_id == user.company_id,
                    TaxDepreciationRegister.ay_code == prior,
                    TaxDepreciationRegister.block_code == block_code,
                )
            )
            if prev_reg is not None:
                opening = q(money(prev_reg.closing_wdv))

        full = ZERO
        half = ZERO
        deletions = ZERO
        movements: list[TaxDepreciationMovement] = []

        for asset, amount, is_half, put in items:
            if put < fy_start:
                # Already in block before FY — counted in opening from prior; if no prior, add opening
                if prior is None or opening == ZERO:
                    opening = q(opening + amount)
                    movements.append(
                        TaxDepreciationMovement(
                            company_id=user.company_id,
                            movement_type="Opening",
                            amount=amount,
                            asset_id=asset.id,
                            put_to_use_date=put,
                            half_rate=False,
                            owner=user.id,
                            modified_by=user.id,
                        )
                    )
                # Disposal in this FY
                if asset.disposal_date and fy_start <= asset.disposal_date <= fy_end:
                    deletions = q(deletions + amount)
                    movements.append(
                        TaxDepreciationMovement(
                            company_id=user.company_id,
                            movement_type="Deletion",
                            amount=amount,
                            asset_id=asset.id,
                            put_to_use_date=put,
                            half_rate=False,
                            owner=user.id,
                            modified_by=user.id,
                        )
                    )
                continue

            if is_half:
                half = q(half + amount)
            else:
                full = q(full + amount)
            movements.append(
                TaxDepreciationMovement(
                    company_id=user.company_id,
                    movement_type="Addition",
                    amount=amount,
                    asset_id=asset.id,
                    put_to_use_date=put,
                    half_rate=is_half,
                    owner=user.id,
                    modified_by=user.id,
                )
            )
            if asset.disposal_date and fy_start <= asset.disposal_date <= fy_end:
                deletions = q(deletions + amount)
                movements.append(
                    TaxDepreciationMovement(
                        company_id=user.company_id,
                        movement_type="Deletion",
                        amount=amount,
                        asset_id=asset.id,
                        put_to_use_date=put,
                        half_rate=False,
                        owner=user.id,
                        modified_by=user.id,
                    )
                )

        if payload.opening_wdv_overrides and block_code in payload.opening_wdv_overrides:
            opening = q(money(payload.opening_wdv_overrides[block_code]))

        dep, addl, closing = compute_block_depreciation(
            opening_wdv=opening,
            additions_full=full,
            additions_half=half,
            deletions=deletions,
            rate_percent=block.rate_percent,
            additional_eligible=bool(block.additional_depreciation_eligible),
        )
        reg = TaxDepreciationRegister(
            company_id=user.company_id,
            ay_code=payload.ay_code,
            block_code=block_code,
            rate_percent=block.rate_percent,
            opening_wdv=opening,
            additions_full=full,
            additions_half=half,
            deletions=deletions,
            depreciation_amount=dep,
            additional_depreciation_amount=addl,
            closing_wdv=closing,
            owner=user.id,
            modified_by=user.id,
        )
        db.add(reg)
        await db.flush()
        for mov in movements:
            mov.register_id = reg.id
            db.add(mov)
        out.append(reg)

    await db.commit()
    return await list_registers(db, user.company_id, ay_code=payload.ay_code)


def register_out(row: TaxDepreciationRegister) -> TaxDepreciationRegisterOut:
    return TaxDepreciationRegisterOut.model_validate(row)
