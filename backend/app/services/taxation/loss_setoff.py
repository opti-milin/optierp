"""Loss carry-forward ledger and set-off persistence — Phase 6."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.tax_corporate import TaxLossCarryForward, TaxLossSetoffEntry
from app.schemas.taxation import TaxLossCarryForwardCreate, TaxLossCarryForwardOut, TaxLossSetoffEntryOut
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.kernel.setoff import BroughtForwardLoss, SetoffApplication


async def list_losses(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    ay_code: str | None = None,
    remaining_only: bool = False,
) -> list[TaxLossCarryForward]:
    stmt = (
        select(TaxLossCarryForward)
        .where(TaxLossCarryForward.company_id == company_id)
        .order_by(TaxLossCarryForward.origin_ay_code, TaxLossCarryForward.loss_kind)
    )
    if ay_code:
        stmt = stmt.where(TaxLossCarryForward.origin_ay_code == ay_code)
    if remaining_only:
        stmt = stmt.where(TaxLossCarryForward.amount_remaining > 0)
    return list((await db.scalars(stmt)).all())


async def _active_setoff_totals_by_ledger(
    db: AsyncSession,
    company_id: uuid.UUID,
    computation_id: uuid.UUID,
) -> dict[uuid.UUID, Decimal]:
    """Sum of set-off amounts for this computation that have not been reversed."""
    rows = list(
        (
            await db.scalars(
                select(TaxLossSetoffEntry).where(
                    TaxLossSetoffEntry.company_id == company_id,
                    TaxLossSetoffEntry.computation_id == computation_id,
                )
            )
        ).all()
    )
    totals: dict[uuid.UUID, Decimal] = {}
    for row in rows:
        if bool((row.explanation or {}).get("reversed")):
            continue
        totals[row.ledger_id] = q(totals.get(row.ledger_id, ZERO) + money(row.amount_set_off))
    return totals


async def available_bf_losses(
    db: AsyncSession,
    company_id: uuid.UUID,
    current_ay: str,
    *,
    for_computation_id: uuid.UUID | None = None,
) -> list[BroughtForwardLoss]:
    """Brought-forward losses still available to set off.

    When ``for_computation_id`` is set, amounts already set off by that worksheet are
    added back so a recompute sees the same pool the previous run used. Without this,
    every saved run permanently shrinks the pool, the input hash drifts, and Review
    falsely reports the worksheet as stale.
    """
    add_back = (
        await _active_setoff_totals_by_ledger(db, company_id, for_computation_id)
        if for_computation_id is not None
        else {}
    )
    # Include zero-remaining rows when we may be adding set-offs back for a recompute.
    rows = await list_losses(
        db, company_id, remaining_only=for_computation_id is None
    )
    out: list[BroughtForwardLoss] = []
    for row in rows:
        if row.expires_after_ay is not None and row.expires_after_ay < current_ay:
            continue
        remaining = q(money(row.amount_remaining) + add_back.get(row.id, ZERO))
        if remaining <= ZERO:
            continue
        out.append(
            BroughtForwardLoss(
                ledger_id=str(row.id),
                setoff_group=row.setoff_group,
                loss_kind=row.loss_kind,
                amount_remaining=remaining,
                origin_ay_code=row.origin_ay_code,
                expires_after_ay=row.expires_after_ay,
            )
        )
    return out


async def reverse_setoffs_for_computation(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    computation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> int:
    """Restore ledger remaining for this worksheet's prior set-offs before a new run.

    Entries stay append-only; each is stamped ``reversed`` in ``explanation`` so a later
    recompute does not add the same amount back twice.
    """
    rows = list(
        (
            await db.scalars(
                select(TaxLossSetoffEntry).where(
                    TaxLossSetoffEntry.company_id == company_id,
                    TaxLossSetoffEntry.computation_id == computation_id,
                )
            )
        ).all()
    )
    reversed_count = 0
    for entry in rows:
        explanation = dict(entry.explanation or {})
        if explanation.get("reversed"):
            continue
        ledger = await db.get(TaxLossCarryForward, entry.ledger_id)
        if ledger is None or ledger.company_id != company_id:
            raise NotFoundError(f"Loss ledger {entry.ledger_id} not found")
        use = q(money(entry.amount_set_off))
        ledger.amount_remaining = q(money(ledger.amount_remaining) + use)
        ledger.modified_by = user_id
        explanation["reversed"] = True
        explanation["reversed_by_run_prepare"] = True
        entry.explanation = explanation
        flag_modified(entry, "explanation")
        entry.modified_by = user_id
        reversed_count += 1
    return reversed_count


async def create_loss(
    db: AsyncSession, payload: TaxLossCarryForwardCreate, user: CurrentUser
) -> TaxLossCarryForward:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    amt = q(money(payload.amount))
    if amt <= ZERO:
        raise ValidationError("amount must be positive", field="amount", code="invalid_amount")
    row = TaxLossCarryForward(
        company_id=user.company_id,
        origin_ay_code=payload.origin_ay_code,
        expires_after_ay=payload.expires_after_ay,
        setoff_group=payload.setoff_group,
        loss_kind=payload.loss_kind,
        entry_kind="Created",
        amount=amt,
        amount_remaining=amt,
        computation_id=payload.computation_id,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def persist_setoff_applications(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay_code: str,
    computation_id: uuid.UUID,
    run_id: uuid.UUID,
    applications: tuple[SetoffApplication, ...],
    user_id: uuid.UUID,
) -> list[TaxLossSetoffEntry]:
    """Append set-off entries and reduce ledger remaining (no DELETE on ledger)."""
    out: list[TaxLossSetoffEntry] = []
    for app in applications:
        ledger = await db.get(TaxLossCarryForward, uuid.UUID(app.ledger_id))
        if ledger is None or ledger.company_id != company_id:
            raise NotFoundError(f"Loss ledger {app.ledger_id} not found")
        use = q(money(app.amount))
        if use > money(ledger.amount_remaining):
            raise ValidationError(
                "Set-off exceeds remaining loss",
                code="setoff_exceeds_remaining",
            )
        ledger.amount_remaining = q(money(ledger.amount_remaining) - use)
        ledger.modified_by = user_id
        entry = TaxLossSetoffEntry(
            company_id=company_id,
            ay_code=ay_code,
            computation_id=computation_id,
            run_id=run_id,
            ledger_id=ledger.id,
            against_character=app.against_character,
            amount_set_off=use,
            sequence=app.sequence,
            explanation={
                "loss_kind": app.loss_kind,
                "ledger_id": app.ledger_id,
            },
            owner=user_id,
            modified_by=user_id,
        )
        db.add(entry)
        out.append(entry)
    return out


async def create_current_year_loss_if_any(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay_code: str,
    computation_id: uuid.UUID,
    taxable_income: Decimal,
    user_id: uuid.UUID,
    expires_after_ay: str | None = None,
) -> TaxLossCarryForward | None:
    """When taxable income is negative, append a Business CF row for the AY."""
    loss_amt = q(-money(taxable_income)) if taxable_income < ZERO else ZERO
    if loss_amt <= ZERO:
        return None
    row = TaxLossCarryForward(
        company_id=company_id,
        origin_ay_code=ay_code,
        expires_after_ay=expires_after_ay,
        setoff_group="ORDINARY",
        loss_kind="Business",
        entry_kind="Created",
        amount=loss_amt,
        amount_remaining=loss_amt,
        computation_id=computation_id,
        remarks="Auto from computation run (negative taxable income)",
        owner=user_id,
        modified_by=user_id,
    )
    db.add(row)
    return row


def loss_out(row: TaxLossCarryForward) -> TaxLossCarryForwardOut:
    return TaxLossCarryForwardOut.model_validate(row)


def setoff_out(row: TaxLossSetoffEntry) -> TaxLossSetoffEntryOut:
    return TaxLossSetoffEntryOut.model_validate(row)


async def list_setoffs(
    db: AsyncSession, company_id: uuid.UUID, *, ay_code: str | None = None
) -> list[TaxLossSetoffEntry]:
    stmt = (
        select(TaxLossSetoffEntry)
        .where(TaxLossSetoffEntry.company_id == company_id)
        .order_by(TaxLossSetoffEntry.ay_code, TaxLossSetoffEntry.sequence)
    )
    if ay_code:
        stmt = stmt.where(TaxLossSetoffEntry.ay_code == ay_code)
    return list((await db.scalars(stmt)).all())
