"""Entities and per-tenant settings.

The one behaviour worth knowing: ``ensure_bootstrap`` runs on first open of the module
and, for a ``business``-profile tenant with no entity yet, creates one from the tenant
``Company`` with ``linked_company_id`` set. An MSME therefore never meets the concept of
a "secretarial entity" — they just see their own company already filled in. A practice
tenant gets no auto-entity; their entities are their clients.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateError, ValidationError
from app.core.security import CurrentUser
from app.models.core import Company
from app.models.secretarial import SecretarialEntity, SecretarialSettings
from app.schemas.secretarial import EntityCreate, EntityUpdate, SettingsUpdate
from app.services.audit import log_audit, serialize_document
from app.services.pagination import paginate
from app.services.secretarial.common import get_entity

_DOCTYPE = "Secretarial Entity"


async def get_settings(db: AsyncSession, company_id: uuid.UUID) -> SecretarialSettings:
    """Settings row for this tenant, created with defaults on first read."""
    settings = await db.scalar(
        select(SecretarialSettings).where(SecretarialSettings.company_id == company_id)
    )
    if settings is None:
        settings = SecretarialSettings(company_id=company_id)
        db.add(settings)
        await db.flush()
    return settings


async def update_settings(
    db: AsyncSession, company_id: uuid.UUID, payload: SettingsUpdate, user: CurrentUser
) -> SecretarialSettings:
    settings = await get_settings(db, company_id)
    before = serialize_document(settings)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    await db.flush()
    await log_audit(
        db,
        doctype="Secretarial Settings",
        document_id=settings.id,
        action="UPDATE",
        user_id=user.id,
        company_id=company_id,
        data_before=before,
        data_after=serialize_document(settings),
    )
    await db.commit()
    return settings


async def ensure_bootstrap(
    db: AsyncSession, company_id: uuid.UUID, user: CurrentUser
) -> SecretarialSettings:
    """Idempotent first-run setup: settings row, plus the self entity in business mode."""
    settings = await get_settings(db, company_id)
    if settings.profile != "business":
        await db.commit()
        return settings

    existing = await db.scalar(
        select(func.count())
        .select_from(SecretarialEntity)
        .where(SecretarialEntity.company_id == company_id)
    )
    if existing:
        await db.commit()
        return settings

    company = await db.get(Company, company_id)
    if company is None:
        await db.commit()
        return settings

    entity = SecretarialEntity(
        company_id=company_id,
        entity_name=company.company_name,
        kind="company",
        entity_class="private",
        pan=company.pan,
        tan=company.tan,
        gstin=company.tax_id,
        incorporated_on=company.date_of_establishment,
        linked_company_id=company_id,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entity)
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=entity.id,
        action="INSERT",
        user_id=user.id,
        company_id=company_id,
        data_after=serialize_document(entity),
    )
    await db.commit()
    return settings


def _validate_identifiers(payload: EntityCreate | EntityUpdate, kind: str) -> None:
    """A company is identified by CIN, an LLP by LLPIN — not interchangeably."""
    if kind == "llp" and getattr(payload, "cin", None):
        raise ValidationError("An LLP is identified by LLPIN, not CIN", field="cin")
    if kind == "company" and getattr(payload, "llpin", None):
        raise ValidationError("A company is identified by CIN, not LLPIN", field="llpin")


async def create_entity(
    db: AsyncSession, payload: EntityCreate, user: CurrentUser
) -> SecretarialEntity:
    assert user.company_id is not None
    _validate_identifiers(payload, payload.kind)

    if payload.kind == "llp" and payload.entity_class != "llp":
        payload = payload.model_copy(update={"entity_class": "llp"})

    for field in ("cin", "llpin"):
        value = getattr(payload, field)
        if value:
            clash = await db.scalar(
                select(SecretarialEntity.id).where(
                    SecretarialEntity.company_id == user.company_id,
                    getattr(SecretarialEntity, field) == value,
                )
            )
            if clash:
                raise DuplicateError(f"An entity with this {field.upper()} already exists")

    entity = SecretarialEntity(
        **payload.model_dump(),
        company_id=user.company_id,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entity)
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=entity.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after=serialize_document(entity),
    )
    await db.commit()
    return entity


async def update_entity(
    db: AsyncSession, entity_id: uuid.UUID, payload: EntityUpdate, user: CurrentUser
) -> SecretarialEntity:
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)
    _validate_identifiers(payload, entity.kind)
    before = serialize_document(entity)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entity, field, value)
    entity.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=entity.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(entity),
    )
    await db.commit()
    return entity


async def list_entities(
    db: AsyncSession,
    company_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
    *,
    search: str | None = None,
    kind: str | None = None,
    status: str | None = None,
) -> tuple[list[SecretarialEntity], int]:
    stmt = select(SecretarialEntity).where(SecretarialEntity.company_id == company_id)
    if kind:
        stmt = stmt.where(SecretarialEntity.kind == kind)
    if status:
        stmt = stmt.where(SecretarialEntity.status == status)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            SecretarialEntity.entity_name.ilike(like)
            | SecretarialEntity.cin.ilike(like)
            | SecretarialEntity.llpin.ilike(like)
        )
    return await paginate(db, stmt.order_by(SecretarialEntity.entity_name), page, page_size)


async def fetch_entity(
    db: AsyncSession, entity_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialEntity:
    return await get_entity(db, entity_id, company_id)


async def delete_entity(db: AsyncSession, entity_id: uuid.UUID, user: CurrentUser) -> None:
    """Deletion is only for mistakes — an entity with statutory history is closed,
    not removed. The status field carries ``struck_off`` / ``closed`` for that."""
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)
    if entity.linked_company_id is not None:
        raise ValidationError(
            "This entity represents your own company and cannot be deleted; "
            "change its status instead"
        )
    before = serialize_document(entity)
    await db.delete(entity)
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=entity_id,
        action="DELETE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
    )
    await db.commit()


def default_fy_for(entity: SecretarialEntity, on: date | None = None) -> str:
    from app.services.secretarial.common import current_fy

    return current_fy(on or date.today(), entity.fy_end_mmdd)
