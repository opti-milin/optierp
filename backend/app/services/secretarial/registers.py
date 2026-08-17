"""Statutory registers — members, committees, group links, related parties, BO,
auditors, charges, DSCs.

These are structurally alike (entity-scoped, effective-dated, filter → search → export),
so they share one generic CRUD core rather than eight near-identical modules. The one
piece of real logic is ``sync_related_parties``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, TypeVar

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialAppointment,
    SecretarialAuditor,
    SecretarialBeneficialOwner,
    SecretarialCharge,
    SecretarialCommittee,
    SecretarialCommitteeMember,
    SecretarialDsc,
    SecretarialGroupLink,
    SecretarialMember,
    SecretarialPerson,
    SecretarialRelatedParty,
)
from app.schemas.secretarial import RelatedPartySyncResult
from app.services.audit import log_audit, serialize_document
from app.services.pagination import paginate
from app.services.secretarial.common import get_entity, fy_period

T = TypeVar("T")

# Register model → the doctype name its permissions and audit entries use.
REGISTER_DOCTYPES: dict[type, str] = {
    SecretarialMember: "Secretarial Member",
    SecretarialCommittee: "Secretarial Committee",
    SecretarialGroupLink: "Secretarial Group Link",
    SecretarialRelatedParty: "Secretarial Related Party",
    SecretarialBeneficialOwner: "Secretarial Beneficial Owner",
    SecretarialAuditor: "Secretarial Auditor",
    SecretarialCharge: "Secretarial Charge",
    SecretarialDsc: "Secretarial DSC",
}

# Columns a free-text search hits, per model.
_SEARCH_FIELDS: dict[type, tuple[str, ...]] = {
    SecretarialMember: ("member_name", "folio_no", "pan"),
    SecretarialCommittee: ("committee_name", "committee_type"),
    SecretarialGroupLink: ("related_entity_name", "related_cin"),
    SecretarialRelatedParty: ("party_name", "relationship_note"),
    SecretarialBeneficialOwner: ("person_name", "declaration_ref"),
    SecretarialAuditor: ("firm_name", "registration_no"),
    SecretarialCharge: ("holder_name", "charge_type", "srn"),
    SecretarialDsc: ("holder_name", "serial_no", "issuing_authority"),
}

# Which pair of columns carries the effective-dating, so an FY filter can be generic.
_VALIDITY_FIELDS: dict[type, tuple[str, str]] = {
    SecretarialMember: ("joined_on", "ceased_on"),
    SecretarialCommittee: ("constituted_on", "dissolved_on"),
    SecretarialGroupLink: ("valid_from", "valid_to"),
    SecretarialRelatedParty: ("valid_from", "valid_to"),
    SecretarialBeneficialOwner: ("valid_from", "valid_to"),
    SecretarialAuditor: ("appointed_on", "ceased_on"),
    SecretarialDsc: ("issued_on", "expires_on"),
}


async def _get_row(db: AsyncSession, model: type, row_id: uuid.UUID, company_id: uuid.UUID) -> Any:
    row = await db.get(model, row_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError(f"{REGISTER_DOCTYPES.get(model, 'Record')} not found")
    return row


async def create_row(
    db: AsyncSession, model: type, payload: Any, user: CurrentUser, *, commit: bool = True
) -> Any:
    assert user.company_id is not None
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else dict(payload)
    data.pop("members", None)  # committee children are handled by their own helper

    if "entity_id" in data and data["entity_id"] is not None:
        await get_entity(db, data["entity_id"], user.company_id)

    row = model(**data, company_id=user.company_id, owner=user.id, modified_by=user.id)
    db.add(row)
    await db.flush()
    await log_audit(
        db,
        doctype=REGISTER_DOCTYPES.get(model, model.__name__),
        document_id=row.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after=serialize_document(row),
    )
    if commit:
        await db.commit()
    return row


async def update_row(
    db: AsyncSession, model: type, row_id: uuid.UUID, payload: Any, user: CurrentUser
) -> Any:
    assert user.company_id is not None
    row = await _get_row(db, model, row_id, user.company_id)
    before = serialize_document(row)
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else dict(payload)
    data.pop("members", None)
    data.pop("entity_id", None)  # a register row never migrates between entities
    for field, value in data.items():
        setattr(row, field, value)
    row.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=REGISTER_DOCTYPES.get(model, model.__name__),
        document_id=row.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(row),
    )
    await db.commit()
    return row


async def delete_row(db: AsyncSession, model: type, row_id: uuid.UUID, user: CurrentUser) -> None:
    assert user.company_id is not None
    row = await _get_row(db, model, row_id, user.company_id)
    before = serialize_document(row)
    await db.delete(row)
    await log_audit(
        db,
        doctype=REGISTER_DOCTYPES.get(model, model.__name__),
        document_id=row_id,
        action="DELETE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
    )
    await db.commit()


async def list_rows(
    db: AsyncSession,
    model: type,
    company_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    fy: str | None = None,
    active_only: bool = False,
    extra_filters: dict[str, Any] | None = None,
) -> tuple[list[Any], int]:
    """The shared register query: filter → search → (FY overlap) → page."""
    stmt = select(model).where(model.company_id == company_id)
    if entity_id is not None and hasattr(model, "entity_id"):
        stmt = stmt.where(model.entity_id == entity_id)

    for field, value in (extra_filters or {}).items():
        if value is not None and hasattr(model, field):
            stmt = stmt.where(getattr(model, field) == value)

    if search and model in _SEARCH_FIELDS:
        like = f"%{search.strip()}%"
        clauses = [
            getattr(model, field).ilike(like)
            for field in _SEARCH_FIELDS[model]
            if hasattr(model, field)
        ]
        if clauses:
            stmt = stmt.where(or_(*clauses))

    validity = _VALIDITY_FIELDS.get(model)
    if validity:
        start_col, end_col = (getattr(model, validity[0]), getattr(model, validity[1]))
        if fy:
            # A row belongs to an FY if its validity window overlaps that FY at all.
            period_start, period_end = fy_period(fy)
            stmt = stmt.where(
                or_(start_col.is_(None), start_col <= period_end),
                or_(end_col.is_(None), end_col >= period_start),
            )
        if active_only:
            stmt = stmt.where(or_(end_col.is_(None), end_col >= date.today()))

    order_col = next(
        (getattr(model, f) for f in _SEARCH_FIELDS.get(model, ()) if hasattr(model, f)),
        model.creation,
    )
    return await paginate(db, stmt.order_by(order_col), page, page_size)


# --- Committees (parent + children) ----------------------------------------------


async def create_committee(db: AsyncSession, payload: Any, user: CurrentUser) -> SecretarialCommittee:
    assert user.company_id is not None
    committee = await create_row(db, SecretarialCommittee, payload, user, commit=False)
    for member in getattr(payload, "members", []) or []:
        db.add(
            SecretarialCommitteeMember(
                company_id=user.company_id,
                committee_id=committee.id,
                person_id=member.person_id,
                is_chair=member.is_chair,
                valid_from=member.valid_from,
                valid_to=member.valid_to,
                owner=user.id,
                modified_by=user.id,
            )
        )
    await db.flush()
    await db.commit()
    return committee


async def committee_members(
    db: AsyncSession, committee_id: uuid.UUID, company_id: uuid.UUID
) -> list[tuple[SecretarialCommitteeMember, str]]:
    stmt = (
        select(SecretarialCommitteeMember, SecretarialPerson.full_name)
        .join(SecretarialPerson, SecretarialPerson.id == SecretarialCommitteeMember.person_id)
        .where(
            SecretarialCommitteeMember.committee_id == committee_id,
            SecretarialCommitteeMember.company_id == company_id,
        )
        .order_by(SecretarialCommitteeMember.is_chair.desc(), SecretarialPerson.full_name)
    )
    return [(row[0], row[1]) for row in (await db.execute(stmt)).all()]


async def set_committee_members(
    db: AsyncSession, committee_id: uuid.UUID, members: list[Any], user: CurrentUser
) -> list[SecretarialCommitteeMember]:
    assert user.company_id is not None
    committee = await _get_row(db, SecretarialCommittee, committee_id, user.company_id)
    if sum(1 for m in members if m.is_chair) > 1:
        raise ValidationError("A committee can have only one chair", field="members")

    existing = (
        (
            await db.execute(
                select(SecretarialCommitteeMember).where(
                    SecretarialCommitteeMember.committee_id == committee_id
                )
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        await db.delete(row)

    created = []
    for member in members:
        row = SecretarialCommitteeMember(
            company_id=user.company_id,
            committee_id=committee.id,
            person_id=member.person_id,
            is_chair=member.is_chair,
            valid_from=member.valid_from,
            valid_to=member.valid_to,
            owner=user.id,
            modified_by=user.id,
        )
        db.add(row)
        created.append(row)
    await db.flush()
    await log_audit(
        db,
        doctype="Secretarial Committee",
        document_id=committee.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"members": len(created)},
    )
    await db.commit()
    return created


# --- Related-party sync -----------------------------------------------------------


async def sync_related_parties(
    db: AsyncSession, entity_id: uuid.UUID, user: CurrentUser
) -> RelatedPartySyncResult:
    """Rebuild the s.188 working list from master data.

    Rows a user typed by hand (``is_manual``) are never touched — the sync owns only
    what it created. That distinction is why the column exists: without it, a refresh
    would silently delete a related party someone added for a reason the system has no
    way to know about.
    """
    assert user.company_id is not None
    await get_entity(db, entity_id, user.company_id)

    derived: dict[tuple[str, str], dict[str, Any]] = {}

    appointments = (
        await db.execute(
            select(SecretarialAppointment, SecretarialPerson.full_name)
            .join(SecretarialPerson, SecretarialPerson.id == SecretarialAppointment.person_id)
            .where(
                SecretarialAppointment.company_id == user.company_id,
                SecretarialAppointment.entity_id == entity_id,
                SecretarialAppointment.ceased_on.is_(None),
            )
        )
    ).all()
    for appointment, person_name in appointments:
        basis = "kmp" if appointment.role_type == "kmp" else "director"
        if appointment.role_type in ("auditor", "secretary"):
            continue
        derived[(person_name, basis)] = {
            "party_name": person_name,
            "basis": basis,
            "relationship_note": appointment.designation or appointment.role_type.replace("_", " "),
            "person_id": appointment.person_id,
            "valid_from": appointment.appointed_on,
        }

    links = (
        (
            await db.execute(
                select(SecretarialGroupLink).where(
                    SecretarialGroupLink.company_id == user.company_id,
                    SecretarialGroupLink.entity_id == entity_id,
                    SecretarialGroupLink.valid_to.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for link in links:
        derived[(link.related_entity_name, "group")] = {
            "party_name": link.related_entity_name,
            "basis": "group",
            "relationship_note": link.relation.replace("_", " ").title(),
            "related_entity_id": link.related_entity_id,
            "valid_from": link.valid_from,
        }

    members = (
        (
            await db.execute(
                select(SecretarialMember).where(
                    SecretarialMember.company_id == user.company_id,
                    SecretarialMember.entity_id == entity_id,
                    SecretarialMember.ceased_on.is_(None),
                    SecretarialMember.shares_held > 0,
                )
            )
        )
        .scalars()
        .all()
    )
    total_shares = sum(float(m.shares_held or 0) for m in members)
    for member in members:
        # Only members with real influence belong on an RPT list; 2% of a cap table
        # is not a related party.
        if total_shares and float(member.shares_held) / total_shares < 0.20:
            continue
        derived[(member.member_name, "member")] = {
            "party_name": member.member_name,
            "basis": "member",
            "relationship_note": "Holds 20% or more of share capital",
            "person_id": member.person_id,
            "valid_from": member.joined_on,
        }

    current = (
        (
            await db.execute(
                select(SecretarialRelatedParty).where(
                    SecretarialRelatedParty.company_id == user.company_id,
                    SecretarialRelatedParty.entity_id == entity_id,
                )
            )
        )
        .scalars()
        .all()
    )
    current_map = {(row.party_name, row.basis): row for row in current}
    manual_count = sum(1 for row in current if row.is_manual)

    added = updated = removed = 0
    for key, data in derived.items():
        row = current_map.get(key)
        if row is None:
            db.add(
                SecretarialRelatedParty(
                    company_id=user.company_id,
                    entity_id=entity_id,
                    is_manual=False,
                    owner=user.id,
                    modified_by=user.id,
                    **data,
                )
            )
            added += 1
        elif not row.is_manual:
            for field, value in data.items():
                setattr(row, field, value)
            row.modified_by = user.id
            updated += 1

    for key, row in current_map.items():
        if key not in derived and not row.is_manual:
            await db.delete(row)
            removed += 1

    await db.flush()
    await log_audit(
        db,
        doctype="Secretarial Related Party",
        document_id=entity_id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"added": added, "updated": updated, "removed": removed},
    )
    await db.commit()

    total = (
        await db.execute(
            select(func.count()).where(
                SecretarialRelatedParty.company_id == user.company_id,
                SecretarialRelatedParty.entity_id == entity_id,
            )
        )
    ).scalar_one()

    return RelatedPartySyncResult(
        added=added,
        updated=updated,
        removed=removed,
        kept_manual=manual_count,
        total=int(total),
    )
