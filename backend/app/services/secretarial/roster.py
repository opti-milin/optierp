"""The practice roster — one row per client a firm works on.

Plan §2.3. The roster spans tenants (a delegated client's records live in the client's
own tenant), and RLS allows exactly one tenant per query, so the roster is a *projection*
owned by the firm rather than a live join.

Refresh is idempotent and runs on every read of the roster, which keeps it correct
without a job. What the on-demand refresh **cannot** do is read counts out of another
tenant — for delegated clients those come from ``refresh_delegated_metrics``, which the
nightly job calls with one tenant context per client. Until it has run, a delegated row
shows identity and engagement status with zero counts, which is honest rather than wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.security import CurrentUser
from app.models.secretarial import (
    PracticeClientIndex,
    SecretarialComplianceItem,
    SecretarialEngagement,
    SecretarialEntity,
)
from app.models.secretarial.compliance import CLOSED_STATUSES
from app.schemas.secretarial import PracticeClientUpdate
from app.services.pagination import paginate


async def _own_entity_rows(
    db: AsyncSession, company_id: uuid.UUID
) -> list[tuple[SecretarialEntity, str]]:
    """Entities this tenant owns: its own company, plus clients it holds directly."""
    entities = (
        (
            await db.execute(
                select(SecretarialEntity).where(SecretarialEntity.company_id == company_id)
            )
        )
        .scalars()
        .all()
    )
    return [(e, "own" if e.linked_company_id is not None else "managed") for e in entities]


async def _entity_metrics(
    db: AsyncSession, entity_id: uuid.UUID, company_id: uuid.UUID
) -> tuple[date | None, int, int]:
    """(next due date, overdue count, open count) for one entity in the current tenant."""
    open_filter = (
        SecretarialComplianceItem.company_id == company_id,
        SecretarialComplianceItem.entity_id == entity_id,
        SecretarialComplianceItem.status.notin_(CLOSED_STATUSES),
    )
    row = (
        await db.execute(
            select(
                func.min(SecretarialComplianceItem.due_on),
                func.count(),
                func.count()
                .filter(SecretarialComplianceItem.due_on < date.today())
                .label("overdue"),
            ).where(*open_filter)
        )
    ).one()
    return row[0], int(row[2] or 0), int(row[1] or 0)


async def refresh(db: AsyncSession, company_id: uuid.UUID) -> int:
    """Upsert the roster for this tenant. Returns the number of rows touched."""
    now = datetime.now(UTC)
    existing = {
        row.entity_id: row
        for row in (
            (
                await db.execute(
                    select(PracticeClientIndex).where(PracticeClientIndex.company_id == company_id)
                )
            )
            .scalars()
            .all()
        )
    }

    seen: set[uuid.UUID] = set()
    touched = 0

    for entity, relationship in await _own_entity_rows(db, company_id):
        seen.add(entity.id)
        next_due, overdue, open_count = await _entity_metrics(db, entity.id, company_id)
        row = existing.get(entity.id)
        if row is None:
            row = PracticeClientIndex(
                company_id=company_id,
                entity_id=entity.id,
                owner_company_id=company_id,
                relationship_type=relationship,
                entity_name=entity.entity_name,
                entity_kind=entity.kind,
                registration_no=entity.registration_no,
                onboarding_state="active" if entity.status == "active" else "dormant",
                active_from=entity.incorporated_on or date.today(),
            )
            db.add(row)
        # Identity and metrics are derived; lifecycle fields the user set are not
        # touched here.
        row.relationship_type = relationship
        row.entity_name = entity.entity_name
        row.entity_kind = entity.kind
        row.registration_no = entity.registration_no
        row.next_due_on = next_due
        row.overdue_count = overdue
        row.open_item_count = open_count
        row.refreshed_at = now
        touched += 1

    # Delegated clients: identity comes off the engagement, counts come later.
    engagements = (
        (
            await db.execute(
                select(SecretarialEngagement).where(
                    SecretarialEngagement.firm_company_id == company_id,
                    SecretarialEngagement.status.in_(("pending", "active", "suspended")),
                )
            )
        )
        .scalars()
        .all()
    )
    for engagement in engagements:
        seen.add(engagement.entity_id)
        row = existing.get(engagement.entity_id)
        if row is None:
            row = PracticeClientIndex(
                company_id=company_id,
                entity_id=engagement.entity_id,
                owner_company_id=engagement.client_company_id,
                relationship_type="delegated",
                entity_name=engagement.entity_name,
                entity_kind=engagement.entity_kind,
                registration_no=engagement.entity_registration_no,
                onboarding_state="active" if engagement.status == "active" else "onboarding",
                active_from=engagement.starts_on or date.today(),
            )
            db.add(row)
        row.engagement_id = engagement.id
        row.relationship_type = "delegated"
        row.entity_name = engagement.entity_name
        row.entity_kind = engagement.entity_kind
        row.registration_no = engagement.entity_registration_no
        row.owner_company_id = engagement.client_company_id
        row.refreshed_at = now
        touched += 1

    # A client whose engagement ended stops being on the roster, but the row is kept
    # with its history — exited, not deleted, because it is billing evidence.
    for entity_id, row in existing.items():
        if entity_id not in seen and row.onboarding_state != "exited":
            row.onboarding_state = "exited"
            row.active_to = row.active_to or date.today()
            row.billable = False
            row.refreshed_at = now
            touched += 1

    await db.flush()
    await db.commit()
    return touched


async def list_clients(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    onboarding_state: str | None = None,
    relationship: str | None = None,
    assigned_to: uuid.UUID | None = None,
) -> tuple[list[PracticeClientIndex], int]:
    stmt = select(PracticeClientIndex).where(PracticeClientIndex.company_id == company_id)
    if onboarding_state:
        stmt = stmt.where(PracticeClientIndex.onboarding_state == onboarding_state)
    if relationship:
        stmt = stmt.where(PracticeClientIndex.relationship_type == relationship)
    if assigned_to:
        stmt = stmt.where(PracticeClientIndex.assigned_to_user_id == assigned_to)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            PracticeClientIndex.entity_name.ilike(like)
            | PracticeClientIndex.registration_no.ilike(like)
        )
    stmt = stmt.order_by(
        PracticeClientIndex.next_due_on.is_(None),
        PracticeClientIndex.next_due_on,
        PracticeClientIndex.entity_name,
    )
    return await paginate(db, stmt, page, page_size)


async def update_client(
    db: AsyncSession, client_id: uuid.UUID, payload: PracticeClientUpdate, user: CurrentUser
) -> PracticeClientIndex:
    assert user.company_id is not None
    row = await db.get(PracticeClientIndex, client_id)
    if row is None or row.company_id != user.company_id:
        raise NotFoundError("Client not found on this roster")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.modified_by = user.id
    await db.flush()
    await db.commit()
    return row


async def counts_by_state(db: AsyncSession, company_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await db.execute(
            select(PracticeClientIndex.onboarding_state, func.count())
            .where(PracticeClientIndex.company_id == company_id)
            .group_by(PracticeClientIndex.onboarding_state)
        )
    ).all()
    return {state: int(count) for state, count in rows}
