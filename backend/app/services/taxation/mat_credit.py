"""115JAA MAT credit ledger — append-only create / utilise."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser
from app.models.tax_corporate import MatCreditLedger
from app.schemas.taxation import MatCreditLedgerOut
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.kernel.mat import MatResult

# 115JAA credit typically available for 15 assessment years from creation.
_DEFAULT_CREDIT_LIFE_AYS = 15


def _ay_plus(ay_code: str, years: int) -> str | None:
    """Advance 'YYYY-YY' by N years; return None if unparseable."""
    try:
        start = int(ay_code.split("-")[0])
        end_two = int(ay_code.split("-")[1])
        new_start = start + years
        new_end = (end_two + years) % 100
        return f"{new_start}-{new_end:02d}"
    except (ValueError, IndexError):
        return None


async def available_mat_credit(
    db: AsyncSession, company_id: uuid.UUID, *, current_ay: str
) -> Decimal:
    """Net Created − Utilised − Expired for credits not yet expired."""
    rows = list(
        (
            await db.scalars(
                select(MatCreditLedger).where(MatCreditLedger.company_id == company_id)
            )
        ).all()
    )
    created = ZERO
    utilised = ZERO
    expired = ZERO
    for row in rows:
        amt = q(money(row.amount))
        if row.entry_kind == "Created":
            if row.expires_after_ay is not None and row.expires_after_ay < current_ay:
                expired = q(expired + amt)
            else:
                created = q(created + amt)
        elif row.entry_kind == "Utilised":
            utilised = q(utilised + amt)
        elif row.entry_kind == "Expired":
            expired = q(expired + amt)
    bal = q(created - utilised)
    return bal if bal > ZERO else ZERO


async def list_mat_credits(
    db: AsyncSession, company_id: uuid.UUID, *, ay_code: str | None = None
) -> list[MatCreditLedger]:
    stmt = (
        select(MatCreditLedger)
        .where(MatCreditLedger.company_id == company_id)
        .order_by(MatCreditLedger.ay_code.desc(), MatCreditLedger.creation.desc())
    )
    if ay_code:
        stmt = stmt.where(MatCreditLedger.ay_code == ay_code)
    return list((await db.scalars(stmt)).all())


async def persist_mat_result(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay_code: str,
    computation_id: uuid.UUID,
    run_id: uuid.UUID,
    mat: MatResult,
    user_id: uuid.UUID,
) -> list[MatCreditLedger]:
    """Append Created and/or Utilised rows from a MAT compare result."""
    out: list[MatCreditLedger] = []
    if mat.mat_credit_created > ZERO:
        out.append(
            MatCreditLedger(
                company_id=company_id,
                ay_code=ay_code,
                entry_kind="Created",
                amount=q(mat.mat_credit_created),
                tax_mat=mat.mat_tax,
                tax_normal=mat.normal_tax,
                expires_after_ay=_ay_plus(ay_code, _DEFAULT_CREDIT_LIFE_AYS),
                computation_id=computation_id,
                run_id=run_id,
                remarks="115JAA credit (MAT > normal)",
                owner=user_id,
                modified_by=user_id,
            )
        )
    if mat.mat_credit_utilised > ZERO:
        out.append(
            MatCreditLedger(
                company_id=company_id,
                ay_code=ay_code,
                entry_kind="Utilised",
                amount=q(mat.mat_credit_utilised),
                tax_mat=mat.mat_tax,
                tax_normal=mat.normal_tax,
                expires_after_ay=None,
                computation_id=computation_id,
                run_id=run_id,
                remarks="115JAA credit utilised against normal tax",
                owner=user_id,
                modified_by=user_id,
            )
        )
    for row in out:
        db.add(row)
    return out


def mat_credit_out(row: MatCreditLedger) -> MatCreditLedgerOut:
    return MatCreditLedgerOut.model_validate(row)


async def ensure_company(user: CurrentUser) -> uuid.UUID:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    return user.company_id
