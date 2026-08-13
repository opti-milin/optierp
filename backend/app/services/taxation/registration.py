"""Tenant tax registration / regime election / policy override services."""

from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateError, NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models import statutory as stat
from app.models.core import Company
from app.models.tax_config import TaxPolicyOverride, TaxRegistration, TaxRegimeElection
from app.schemas.taxation import (
    TaxPolicyOverrideCreate,
    TaxPolicyOverrideOut,
    TaxRegistrationOut,
    TaxRegistrationUpsert,
    TaxRegimeElectionCreate,
    TaxRegimeElectionOut,
)

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def validate_pan_vs_class(pan: str | None, assessee_class_code: str, pan_4th_chars: str) -> None:
    """PAN 4th character must match the assessee class (e.g. C=Company, P=Individual)."""
    if not pan:
        return
    normalized = pan.strip().upper()
    if not _PAN_RE.match(normalized):
        raise ValidationError("Invalid PAN format", code="invalid_pan", field="pan")
    allowed = {c.upper() for c in (pan_4th_chars or "") if c.isalpha()}
    if allowed and normalized[3] not in allowed:
        raise ValidationError(
            f"PAN 4th character '{normalized[3]}' does not match assessee class "
            f"{assessee_class_code} (expected one of {sorted(allowed)})",
            code="pan_class_mismatch",
            field="pan",
        )


async def _class_row(db: AsyncSession, code: str) -> stat.AssesseeClass:
    row = await db.scalar(select(stat.AssesseeClass).where(stat.AssesseeClass.code == code))
    if row is None:
        raise ValidationError(
            f"Unknown assessee class '{code}'", code="unknown_assessee_class", field="assessee_class_code"
        )
    return row


async def get_registration(db: AsyncSession, company_id: uuid.UUID) -> TaxRegistration | None:
    return await db.scalar(
        select(TaxRegistration).where(TaxRegistration.company_id == company_id)
    )


async def get_or_bootstrap_registration(
    db: AsyncSession, company_id: uuid.UUID
) -> TaxRegistration:
    row = await get_registration(db, company_id)
    if row is not None:
        return row
    company = await db.get(Company, company_id)
    row = TaxRegistration(
        company_id=company_id,
        pan=(company.pan if company else None),
        tan=(company.tan if company else None),
        assessee_class_code="Company",
        residential_status="Resident",
    )
    db.add(row)
    await db.flush()
    await db.commit()
    await db.refresh(row)
    return row


async def upsert_registration(
    db: AsyncSession, payload: TaxRegistrationUpsert, user: CurrentUser
) -> TaxRegistration:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    cls = await _class_row(db, payload.assessee_class_code)
    pan = (payload.pan or "").strip().upper() or None
    validate_pan_vs_class(pan, payload.assessee_class_code, cls.pan_4th_chars)

    row = await get_registration(db, user.company_id)
    if row is None:
        row = TaxRegistration(company_id=user.company_id)
        db.add(row)
    row.pan = pan
    row.tan = (payload.tan or "").strip().upper() or None
    row.cin = payload.cin
    row.assessee_class_code = payload.assessee_class_code
    row.residential_status = payload.residential_status
    row.incorporation_date = payload.incorporation_date
    row.nature_of_business_codes = list(payload.nature_of_business_codes)
    row.jurisdiction = payload.jurisdiction
    row.default_assessment_year = payload.default_assessment_year
    row.itr_efile_provider = payload.itr_efile_provider
    row.remarks = payload.remarks
    row.modified_by = user.id
    if row.owner is None:
        row.owner = user.id
    await db.commit()
    await db.refresh(row)
    return row


async def list_elections(db: AsyncSession, company_id: uuid.UUID) -> list[TaxRegimeElection]:
    result = await db.scalars(
        select(TaxRegimeElection)
        .where(TaxRegimeElection.company_id == company_id)
        .order_by(TaxRegimeElection.ay_code.desc())
    )
    return list(result.all())


async def get_election(
    db: AsyncSession, company_id: uuid.UUID, ay_code: str
) -> TaxRegimeElection | None:
    return await db.scalar(
        select(TaxRegimeElection).where(
            TaxRegimeElection.company_id == company_id,
            TaxRegimeElection.ay_code == ay_code,
        )
    )


async def create_or_update_election(
    db: AsyncSession, payload: TaxRegimeElectionCreate, user: CurrentUser
) -> TaxRegimeElection:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    await _class_row(db, payload.assessee_class_code)

    regime = await db.scalar(
        select(stat.TaxRegime).where(
            stat.TaxRegime.code == payload.regime_code,
            stat.TaxRegime.assessee_class_code == payload.assessee_class_code,
        )
    )
    if regime is None:
        raise ValidationError(
            f"Regime '{payload.regime_code}' is not valid for {payload.assessee_class_code}",
            code="invalid_regime",
            field="regime_code",
        )

    existing = await get_election(db, user.company_id, payload.ay_code)
    if existing is not None and existing.irrevocable and existing.regime_code != payload.regime_code:
        raise DuplicateError(
            f"Regime election for {payload.ay_code} is irrevocable ({existing.regime_code})",
            code="election_irrevocable",
        )

    if existing is None:
        existing = TaxRegimeElection(company_id=user.company_id, ay_code=payload.ay_code)
        db.add(existing)
        existing.owner = user.id

    existing.regime_code = payload.regime_code
    existing.assessee_class_code = payload.assessee_class_code
    existing.elected_on = payload.elected_on or date.today()
    existing.form_ack_no = payload.form_ack_no
    existing.irrevocable = bool(regime.election_irrevocable or payload.irrevocable)
    existing.remarks = payload.remarks
    existing.modified_by = user.id
    await db.commit()
    await db.refresh(existing)
    return existing


async def list_overrides(
    db: AsyncSession, company_id: uuid.UUID, *, ay_code: str | None = None
) -> list[TaxPolicyOverride]:
    stmt = select(TaxPolicyOverride).where(TaxPolicyOverride.company_id == company_id)
    if ay_code:
        stmt = stmt.where(TaxPolicyOverride.ay_code == ay_code)
    result = await db.scalars(stmt.order_by(TaxPolicyOverride.ay_code, TaxPolicyOverride.target_code))
    return list(result.all())


async def create_override(
    db: AsyncSession, payload: TaxPolicyOverrideCreate, user: CurrentUser
) -> TaxPolicyOverride:
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    if not (payload.reason or "").strip():
        raise ValidationError("reason is required for overrides", code="reason_required", field="reason")
    row = TaxPolicyOverride(
        company_id=user.company_id,
        ay_code=payload.ay_code,
        override_kind=payload.override_kind,
        target_code=payload.target_code,
        reason=payload.reason.strip(),
        params=dict(payload.params),
        disabled=payload.disabled,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def registration_out(row: TaxRegistration) -> TaxRegistrationOut:
    return TaxRegistrationOut.model_validate(row)


def election_out(row: TaxRegimeElection) -> TaxRegimeElectionOut:
    return TaxRegimeElectionOut.model_validate(row)


def override_out(row: TaxPolicyOverride) -> TaxPolicyOverrideOut:
    return TaxPolicyOverrideOut.model_validate(row)


async def get_registration_or_404(db: AsyncSession, company_id: uuid.UUID) -> TaxRegistration:
    row = await get_or_bootstrap_registration(db, company_id)
    if row is None:
        raise NotFoundError("Tax registration not found")
    return row
