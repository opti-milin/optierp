"""Persons and their appointments.

Two rules enforced here rather than in the database, because both need a message a
user can act on:

* A DIN is unique within the tenant. Two rows for the same director would silently
  break every cross-entity batch (MBP-1, DIR-8) that the person graph exists to enable.
* An appointment cannot overlap itself. Re-appointing someone who was never ceased is
  almost always a data-entry slip, and catching it here keeps the register honest.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateError, NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialAppointment,
    SecretarialEntity,
    SecretarialPerson,
)
from app.schemas.secretarial import (
    AppointmentCreate,
    AppointmentUpdate,
    CeaseAppointmentIn,
    PersonCreate,
    PersonEntityLink,
    PersonUpdate,
)
from app.services.audit import log_audit, serialize_document
from app.services.pagination import paginate
from app.services.secretarial.common import get_entity

_PERSON_DOCTYPE = "Secretarial Person"
_APPOINTMENT_DOCTYPE = "Secretarial Appointment"


async def get_person(
    db: AsyncSession, person_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialPerson:
    person = await db.get(SecretarialPerson, person_id)
    if person is None or person.company_id != company_id:
        raise NotFoundError("Person not found")
    return person


async def _assert_din_free(
    db: AsyncSession, company_id: uuid.UUID, din: str | None, *, exclude_id: uuid.UUID | None = None
) -> None:
    if not din:
        return
    stmt = select(SecretarialPerson.id, SecretarialPerson.full_name).where(
        SecretarialPerson.company_id == company_id, SecretarialPerson.din == din
    )
    if exclude_id:
        stmt = stmt.where(SecretarialPerson.id != exclude_id)
    row = (await db.execute(stmt)).first()
    if row is not None:
        raise DuplicateError(f"DIN {din} is already held by {row.full_name}")


async def create_person(
    db: AsyncSession, payload: PersonCreate, user: CurrentUser
) -> SecretarialPerson:
    assert user.company_id is not None
    await _assert_din_free(db, user.company_id, payload.din)

    person = SecretarialPerson(
        **payload.model_dump(),
        company_id=user.company_id,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(person)
    await db.flush()
    await log_audit(
        db,
        doctype=_PERSON_DOCTYPE,
        document_id=person.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after=serialize_document(person),
    )
    await db.commit()
    return person


async def update_person(
    db: AsyncSession, person_id: uuid.UUID, payload: PersonUpdate, user: CurrentUser
) -> SecretarialPerson:
    assert user.company_id is not None
    person = await get_person(db, person_id, user.company_id)
    data = payload.model_dump(exclude_unset=True)
    if "din" in data:
        await _assert_din_free(db, user.company_id, data["din"], exclude_id=person_id)

    before = serialize_document(person)
    for field, value in data.items():
        setattr(person, field, value)
    person.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_PERSON_DOCTYPE,
        document_id=person.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(person),
    )
    await db.commit()
    return person


async def list_persons(
    db: AsyncSession,
    company_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
    *,
    search: str | None = None,
    entity_id: uuid.UUID | None = None,
    role_type: str | None = None,
    active_only: bool = False,
) -> tuple[list[SecretarialPerson], int]:
    stmt = select(SecretarialPerson).where(SecretarialPerson.company_id == company_id)

    if entity_id or role_type or active_only:
        sub = select(SecretarialAppointment.person_id).where(
            SecretarialAppointment.company_id == company_id
        )
        if entity_id:
            sub = sub.where(SecretarialAppointment.entity_id == entity_id)
        if role_type:
            sub = sub.where(SecretarialAppointment.role_type == role_type)
        if active_only:
            sub = sub.where(SecretarialAppointment.ceased_on.is_(None))
        stmt = stmt.where(SecretarialPerson.id.in_(sub))

    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                SecretarialPerson.full_name.ilike(like),
                SecretarialPerson.din.ilike(like),
                SecretarialPerson.pan.ilike(like),
            )
        )
    return await paginate(db, stmt.order_by(SecretarialPerson.full_name), page, page_size)


async def person_entities(
    db: AsyncSession, person_id: uuid.UUID, company_id: uuid.UUID
) -> list[PersonEntityLink]:
    """Every office this person holds across the tenant — the DIN-centric view that
    makes batch declarations possible."""
    await get_person(db, person_id, company_id)
    stmt = (
        select(SecretarialAppointment, SecretarialEntity.entity_name)
        .join(SecretarialEntity, SecretarialEntity.id == SecretarialAppointment.entity_id)
        .where(
            SecretarialAppointment.person_id == person_id,
            SecretarialAppointment.company_id == company_id,
        )
        .order_by(SecretarialAppointment.appointed_on.desc())
    )
    return [
        PersonEntityLink(
            entity_id=appointment.entity_id,
            entity_name=entity_name,
            role_type=appointment.role_type,
            designation=appointment.designation,
            appointed_on=appointment.appointed_on,
            ceased_on=appointment.ceased_on,
        )
        for appointment, entity_name in (await db.execute(stmt)).all()
    ]


# --- Appointments ----------------------------------------------------------------


async def get_appointment(
    db: AsyncSession, appointment_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialAppointment:
    appointment = await db.get(SecretarialAppointment, appointment_id)
    if appointment is None or appointment.company_id != company_id:
        raise NotFoundError("Appointment not found")
    return appointment


_ROLES_BY_KIND = {
    "company": {"director", "kmp", "auditor", "secretary"},
    "llp": {"designated_partner", "partner", "auditor"},
}


async def create_appointment(
    db: AsyncSession, payload: AppointmentCreate, user: CurrentUser
) -> SecretarialAppointment:
    assert user.company_id is not None
    entity = await get_entity(db, payload.entity_id, user.company_id)
    person = await get_person(db, payload.person_id, user.company_id)

    allowed = _ROLES_BY_KIND.get(entity.kind, set())
    if payload.role_type not in allowed:
        raise ValidationError(
            f"A {entity.kind} cannot have a {payload.role_type.replace('_', ' ')}; "
            f"expected one of {', '.join(sorted(allowed))}",
            field="role_type",
        )

    if payload.role_type in ("director", "designated_partner") and not person.din:
        raise ValidationError(
            f"{person.full_name} needs a DIN before being appointed as "
            f"{payload.role_type.replace('_', ' ')}",
            field="person_id",
        )

    if person.is_disqualified and payload.ceased_on is None:
        raise ValidationError(
            f"{person.full_name} is marked disqualified under s.164 and cannot be appointed",
            field="person_id",
        )

    overlapping = await db.scalar(
        select(SecretarialAppointment.id).where(
            SecretarialAppointment.entity_id == payload.entity_id,
            SecretarialAppointment.person_id == payload.person_id,
            SecretarialAppointment.role_type == payload.role_type,
            SecretarialAppointment.ceased_on.is_(None),
        )
    )
    if overlapping:
        raise DuplicateError(
            f"{person.full_name} already holds an open {payload.role_type.replace('_', ' ')} "
            f"appointment here — cease it before creating a new one"
        )

    appointment = SecretarialAppointment(
        **payload.model_dump(),
        company_id=user.company_id,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(appointment)
    await db.flush()
    await log_audit(
        db,
        doctype=_APPOINTMENT_DOCTYPE,
        document_id=appointment.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after=serialize_document(appointment),
    )
    await db.commit()
    return appointment


async def update_appointment(
    db: AsyncSession, appointment_id: uuid.UUID, payload: AppointmentUpdate, user: CurrentUser
) -> SecretarialAppointment:
    assert user.company_id is not None
    appointment = await get_appointment(db, appointment_id, user.company_id)
    before = serialize_document(appointment)
    data = payload.model_dump(exclude_unset=True)

    ceased = data.get("ceased_on", appointment.ceased_on)
    appointed = data.get("appointed_on", appointment.appointed_on)
    if ceased and appointed and ceased < appointed:
        raise ValidationError("Cessation cannot precede appointment", field="ceased_on")

    for field, value in data.items():
        setattr(appointment, field, value)
    appointment.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_APPOINTMENT_DOCTYPE,
        document_id=appointment.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(appointment),
    )
    await db.commit()
    return appointment


async def cease_appointment(
    db: AsyncSession, appointment_id: uuid.UUID, payload: CeaseAppointmentIn, user: CurrentUser
) -> SecretarialAppointment:
    """Close an office. Never a delete — the register must still show who held it."""
    assert user.company_id is not None
    appointment = await get_appointment(db, appointment_id, user.company_id)
    if appointment.ceased_on is not None:
        raise ValidationError("This appointment is already closed", field="ceased_on")
    if payload.ceased_on < appointment.appointed_on:
        raise ValidationError("Cessation cannot precede appointment", field="ceased_on")

    before = serialize_document(appointment)
    appointment.ceased_on = payload.ceased_on
    appointment.cessation_reason = payload.cessation_reason
    appointment.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_APPOINTMENT_DOCTYPE,
        document_id=appointment.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(appointment),
    )
    await db.commit()
    return appointment


async def list_appointments(
    db: AsyncSession,
    company_id: uuid.UUID,
    entity_id: uuid.UUID,
    *,
    role_type: str | None = None,
    include_ceased: bool = True,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[tuple[SecretarialAppointment, str]], int]:
    stmt = (
        select(SecretarialAppointment, SecretarialPerson.full_name)
        .join(SecretarialPerson, SecretarialPerson.id == SecretarialAppointment.person_id)
        .where(
            SecretarialAppointment.company_id == company_id,
            SecretarialAppointment.entity_id == entity_id,
        )
    )
    if role_type:
        stmt = stmt.where(SecretarialAppointment.role_type == role_type)
    if not include_ceased:
        stmt = stmt.where(SecretarialAppointment.ceased_on.is_(None))
    stmt = stmt.order_by(
        SecretarialAppointment.ceased_on.is_(None).desc(),
        SecretarialAppointment.appointed_on.desc(),
    )

    # paginate() returns scalars; this query selects a row tuple, so page manually.
    from sqlalchemy import func

    total = (
        await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = (await db.execute(stmt.limit(page_size).offset((page - 1) * page_size))).all()
    return [(row[0], row[1]) for row in rows], int(total)
