"""Resolutions by circulation — the richest state machine in the module.

`draft → circulating → passed | failed | expired → ratified`, and the outcome is always
**computed from the responses**, never set by a caller. That matters: a tally is
evidence, and evidence that a user can type in is not evidence.

Two guardrails sit on the `draft → circulating` edge:

* Rule 5 — matters the Board may only decide at a meeting are blocked outright
  (``eligibility.py``), because passing one by circulation produces a void resolution.
* Section 184 — a director interested in the contract does not count towards the tally,
  so they are excluded from the denominator rather than merely asked to abstain.

The tally resolves as early as it can. Once enough directors have consented the
resolution passes immediately; once enough have declined that it cannot possibly
succeed it fails immediately, rather than waiting out the clock for a foregone
conclusion.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialAgendaItem,
    SecretarialAppointment,
    SecretarialCircular,
    SecretarialConsentResponse,
    SecretarialEntity,
    SecretarialMeeting,
    SecretarialPerson,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.secretarial import eligibility
from app.services.secretarial.common import current_fy, get_entity

_DOCTYPE = "Secretarial Circular Resolution"

DECIDED = ("passed", "failed", "expired", "ratified", "cancelled")


async def get_circular(
    db: AsyncSession, circular_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialCircular:
    row = await db.get(SecretarialCircular, circular_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Circular resolution not found")
    return row


async def create_circular(
    db: AsyncSession,
    user: CurrentUser,
    *,
    entity_id: uuid.UUID,
    title: str,
    resolution_text: str,
    description: str | None = None,
    consent_rule: str = "majority",
    reference_no: str | None = None,
) -> SecretarialCircular:
    """Draft a circular and run the Rule 5 check immediately.

    Checking at draft time rather than at send time means the author finds out while
    they are still writing, not after they have lined up seven directors.
    """
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)
    if entity.kind == "llp":
        raise ValidationError(
            "Resolutions by circulation under s.175 apply to companies; an LLP follows its "
            "LLP Agreement instead",
            field="entity_id",
        )

    result = eligibility.check(title, resolution_text, description)

    circular = SecretarialCircular(
        company_id=user.company_id,
        entity_id=entity_id,
        title=title,
        description=description,
        resolution_text=resolution_text,
        reference_no=reference_no or await _next_reference(db, entity_id, user.company_id),
        fy=current_fy(date.today(), entity.fy_end_mmdd),
        consent_rule=consent_rule,
        status="draft",
        eligibility_checked_at=datetime.now(UTC),
        eligibility_result=result,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(circular)
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=circular.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"title": title, "eligible": result["eligible"]},
    )
    await db.commit()
    return circular


async def _next_reference(db: AsyncSession, entity_id: uuid.UUID, company_id: uuid.UUID) -> str:
    """Next free reference for this entity, allocated under a lock.

    Counting rows would re-issue a reference the moment one is deleted, and two
    simultaneous drafts would both read the same count; `unique(entity_id, reference_no)`
    turns either into an IntegrityError. So: lock the entity, take MAX+1 of what has
    actually been issued this year.
    """
    await db.execute(
        select(SecretarialEntity.id).where(SecretarialEntity.id == entity_id).with_for_update()
    )
    prefix = f"CR/{date.today():%Y}/"
    latest = (
        await db.execute(
            select(func.max(SecretarialCircular.reference_no)).where(
                SecretarialCircular.entity_id == entity_id,
                SecretarialCircular.reference_no.like(prefix + "%"),
            )
        )
    ).scalar_one_or_none()
    n = (int(latest.rsplit("/", 1)[1]) if latest else 0) + 1
    return f"{prefix}{n:03d}"


async def recheck_eligibility(
    db: AsyncSession, circular_id: uuid.UUID, user: CurrentUser
) -> dict[str, Any]:
    """Re-run the Rule 5 check after an edit."""
    assert user.company_id is not None
    circular = await get_circular(db, circular_id, user.company_id)
    result = eligibility.check(circular.title, circular.resolution_text, circular.description)
    circular.eligibility_result = result
    circular.eligibility_checked_at = datetime.now(UTC)
    await db.flush()
    await db.commit()
    return result


async def circulate(
    db: AsyncSession,
    circular_id: uuid.UUID,
    user: CurrentUser,
    *,
    person_ids: list[uuid.UUID] | None = None,
    interested_person_ids: list[uuid.UUID] | None = None,
    expires_at: datetime | None = None,
    override_rule5: bool = False,
    override_reason: str | None = None,
) -> SecretarialCircular:
    """Send it out. This is the irreversible step the Rule 5 gate protects."""
    assert user.company_id is not None
    circular = await get_circular(db, circular_id, user.company_id)
    if circular.status != "draft":
        raise ValidationError(f"This circular is already {circular.status}", field="status")

    result = circular.eligibility_result or eligibility.check(
        circular.title, circular.resolution_text, circular.description
    )
    if not result.get("eligible"):
        if not override_rule5:
            # Blocking, before the send, with the statutory reason attached.
            raise ValidationError(str(result["message"]), field="resolution_text")
        if not override_reason:
            raise ValidationError(
                "Overriding a Rule 5 block requires a reason, which is recorded permanently",
                field="override_reason",
            )
        result = {
            **result,
            "overridden": True,
            "override_reason": override_reason,
            "override_note": eligibility.override_note(result.get("blocked_matters", [])),
            "overridden_by": str(user.id),
            "overridden_at": datetime.now(UTC).isoformat(),
        }
        circular.eligibility_result = result

    # Everyone serving unless the caller narrows it.
    if person_ids is None:
        person_ids = list(
            (
                await db.execute(
                    select(SecretarialAppointment.person_id).where(
                        SecretarialAppointment.entity_id == circular.entity_id,
                        SecretarialAppointment.ceased_on.is_(None),
                        SecretarialAppointment.role_type == "director",
                    )
                )
            )
            .scalars()
            .all()
        )
    if not person_ids:
        raise ValidationError("There are no serving directors to circulate to")

    interested = set(interested_person_ids or [])
    for person_id in person_ids:
        db.add(
            SecretarialConsentResponse(
                company_id=user.company_id,
                circular_id=circular.id,
                person_id=person_id,
                status="pending",
                is_interested=person_id in interested,
                sent_at=datetime.now(UTC),
                owner=user.id,
                modified_by=user.id,
            )
        )

    circular.status = "circulating"
    circular.circulated_at = datetime.now(UTC)
    circular.expires_at = expires_at
    circular.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=circular.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "recipients": len(person_ids),
            "interested_excluded": len(interested),
            "rule5_overridden": bool(result.get("overridden")),
        },
    )
    await db.commit()
    return circular


# --- Tally ------------------------------------------------------------------------


def _threshold(rule: str, entitled: int) -> int:
    """How many consents are needed.

    s.175 requires approval by a majority of the directors *entitled to vote*, so an
    interested director changes the denominator, not just their own vote.
    """
    if entitled <= 0:
        return 0
    if rule == "unanimous":
        return entitled
    if rule == "two_thirds":
        return math.ceil(entitled * 2 / 3)
    return entitled // 2 + 1


async def tally(
    db: AsyncSession, circular_id: uuid.UUID, company_id: uuid.UUID
) -> dict[str, Any]:
    """The live count, and what it means. Pure read — decides nothing."""
    circular = await get_circular(db, circular_id, company_id)
    rows = list(
        (
            await db.execute(
                select(SecretarialConsentResponse).where(
                    SecretarialConsentResponse.circular_id == circular_id
                )
            )
        )
        .scalars()
        .all()
    )

    entitled = [r for r in rows if not r.is_interested]
    consented = sum(1 for r in entitled if r.status == "consented")
    declined = sum(1 for r in entitled if r.status == "declined")
    abstained = sum(1 for r in entitled if r.status == "abstained")
    pending = sum(1 for r in entitled if r.status in ("pending", "viewed"))

    needed = _threshold(circular.consent_rule, len(entitled))
    # An abstention is not a consent, so it counts against the achievable maximum.
    still_possible = consented + pending

    return {
        "rule": circular.consent_rule,
        "entitled": len(entitled),
        "interested_excluded": len(rows) - len(entitled),
        "consented": consented,
        "declined": declined,
        "abstained": abstained,
        "pending": pending,
        "needed": needed,
        "reached": consented >= needed,
        "impossible": still_possible < needed,
        "expires_at": circular.expires_at,
        "status": circular.status,
    }


async def _settle(
    db: AsyncSession, circular: SecretarialCircular, user: CurrentUser | None = None
) -> SecretarialCircular:
    """Apply the tally to the status. The only writer of passed/failed."""
    if circular.status != "circulating":
        return circular

    counts = await tally(db, circular.id, circular.company_id)
    if counts["reached"]:
        circular.status = "passed"
        circular.decided_at = datetime.now(UTC)
    elif counts["impossible"]:
        circular.status = "failed"
        circular.decided_at = datetime.now(UTC)
    elif circular.expires_at and circular.expires_at <= datetime.now(UTC):
        circular.status = "expired"
        circular.decided_at = datetime.now(UTC)

    if circular.status != "circulating":
        await db.flush()
        await log_audit(
            db,
            doctype=_DOCTYPE,
            document_id=circular.id,
            action="UPDATE",
            user_id=user.id if user else None,
            company_id=circular.company_id,
            data_after={"status": circular.status, "tally": counts},
        )
    return circular


async def record_response(
    db: AsyncSession,
    *,
    circular_id: uuid.UUID,
    person_id: uuid.UUID,
    status: str,
    comments: str | None = None,
    ip_address: str | None = None,
) -> tuple[SecretarialConsentResponse, SecretarialCircular]:
    """Record one director's decision and re-settle the circular.

    Called from the portal, where there is no logged-in user — hence no ``CurrentUser``.
    A decision is recorded once: changing a cast vote would make the audit trail a
    fiction, so a second attempt is refused.
    """
    if status not in ("consented", "declined", "abstained"):
        raise ValidationError("A response must be consent, dissent or abstention", field="status")

    response = await db.scalar(
        select(SecretarialConsentResponse).where(
            SecretarialConsentResponse.circular_id == circular_id,
            SecretarialConsentResponse.person_id == person_id,
        )
    )
    if response is None:
        raise NotFoundError("You are not on the circulation list for this resolution")

    circular = await db.get(SecretarialCircular, circular_id)
    if circular is None:
        raise NotFoundError("Circular resolution not found")
    if circular.status != "circulating":
        raise ValidationError(
            f"This resolution is no longer open for responses — it is {circular.status}."
        )
    if response.status in ("consented", "declined", "abstained"):
        raise ValidationError(
            "Your response has already been recorded and cannot be changed. "
            "Contact the company if it needs correcting."
        )
    if response.is_interested:
        raise ValidationError(
            "You are recorded as interested in this matter under s.184, so you do not vote on it."
        )

    response.status = status
    response.responded_at = datetime.now(UTC)
    response.responded_ip = ip_address
    response.comments = comments
    await db.flush()

    await _settle(db, circular)
    await db.commit()
    return response, circular


async def mark_viewed(db: AsyncSession, response_id: uuid.UUID) -> None:
    """First open of the link. Never moves a decided response backwards."""
    response = await db.get(SecretarialConsentResponse, response_id)
    if response is not None and response.status == "pending":
        response.status = "viewed"
        response.viewed_at = datetime.now(UTC)
        await db.flush()


async def refresh_status(
    db: AsyncSession, circular_id: uuid.UUID, user: CurrentUser
) -> SecretarialCircular:
    assert user.company_id is not None
    circular = await get_circular(db, circular_id, user.company_id)
    await _settle(db, circular, user)
    await db.commit()
    return circular


async def sweep_expired(db: AsyncSession, company_id: uuid.UUID, *, now: datetime | None = None) -> int:
    """Close out circulars whose deadline has passed. Run by the nightly job."""
    moment = now or datetime.now(UTC)
    rows = list(
        (
            await db.execute(
                select(SecretarialCircular).where(
                    SecretarialCircular.company_id == company_id,
                    SecretarialCircular.status == "circulating",
                    SecretarialCircular.expires_at.isnot(None),
                    SecretarialCircular.expires_at <= moment,
                )
            )
        )
        .scalars()
        .all()
    )
    for circular in rows:
        await _settle(db, circular)
    if rows:
        await db.commit()
    return len(rows)


# --- Ratification -----------------------------------------------------------------


async def ratify(
    db: AsyncSession,
    circular_id: uuid.UUID,
    user: CurrentUser,
    *,
    meeting_id: uuid.UUID | None = None,
) -> tuple[SecretarialCircular, SecretarialMeeting]:
    """Put a passed circular on a board agenda to be noted.

    If no suitable meeting exists, one is created — that is the point. A passed circular
    that nobody remembers to note is the failure mode this closes, and requiring the
    user to go and schedule a meeting first is exactly where it gets forgotten.
    """
    assert user.company_id is not None
    circular = await get_circular(db, circular_id, user.company_id)
    if circular.status != "passed":
        raise ValidationError(
            f"Only a passed resolution is ratified; this one is {circular.status}", field="status"
        )
    if circular.ratified_meeting_id:
        raise ValidationError("This resolution has already been placed for ratification")

    from app.services.secretarial import meeting as meeting_service

    if meeting_id:
        meeting = await meeting_service.get_meeting(db, meeting_id, user.company_id)
        if meeting.status in ("minutes_signed", "closed", "cancelled"):
            raise ValidationError("That meeting is closed; choose another or let one be created")
    else:
        meeting = await db.scalar(
            select(SecretarialMeeting)
            .where(
                SecretarialMeeting.entity_id == circular.entity_id,
                SecretarialMeeting.meeting_type == "board",
                SecretarialMeeting.status.in_(("draft", "scheduled", "circulated")),
                SecretarialMeeting.scheduled_at >= datetime.now(UTC),
            )
            .order_by(SecretarialMeeting.scheduled_at)
        )
        if meeting is None:
            meeting = await meeting_service.create_meeting(
                db,
                user,
                entity_id=circular.entity_id,
                meeting_type="board",
                scheduled_at=datetime.now(UTC).replace(microsecond=0),
                title="Board Meeting — to note resolutions passed by circulation",
                # Part of this ratification, not a standalone act: if the agenda item or
                # the status update below fails, the meeting must roll back with them.
                commit=False,
            )

    next_seq = (
        await db.execute(
            select(func.coalesce(func.max(SecretarialAgendaItem.seq), 0)).where(
                SecretarialAgendaItem.meeting_id == meeting.id
            )
        )
    ).scalar_one() + 1

    db.add(
        SecretarialAgendaItem(
            company_id=user.company_id,
            meeting_id=meeting.id,
            seq=next_seq,
            title=f"To note the resolution passed by circulation — {circular.title}",
            body=(
                f"Resolution reference {circular.reference_no}, circulated on "
                f"{circular.circulated_at:%d %B %Y} and passed on "
                f"{circular.decided_at:%d %B %Y}."
                if circular.circulated_at and circular.decided_at
                else None
            ),
            resolution_text=circular.resolution_text,
            source="ratification",
            circular_id=circular.id,
            owner=user.id,
            modified_by=user.id,
        )
    )

    circular.ratified_meeting_id = meeting.id
    circular.ratified_on = date.today()
    circular.status = "ratified"
    circular.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=circular.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"ratified_meeting_id": str(meeting.id), "agenda_seq": next_seq},
    )
    await db.commit()
    return circular, meeting


async def responses(
    db: AsyncSession, circular_id: uuid.UUID, company_id: uuid.UUID
) -> list[tuple[SecretarialConsentResponse, str]]:
    await get_circular(db, circular_id, company_id)
    rows = await db.execute(
        select(SecretarialConsentResponse, SecretarialPerson.full_name)
        .join(SecretarialPerson, SecretarialPerson.id == SecretarialConsentResponse.person_id)
        .where(SecretarialConsentResponse.circular_id == circular_id)
        .order_by(SecretarialPerson.full_name)
    )
    return [(r[0], r[1]) for r in rows.all()]


async def list_circulars(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    status: str | None = None,
    fy: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialCircular], int]:
    stmt = select(SecretarialCircular).where(SecretarialCircular.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialCircular.entity_id == entity_id)
    if status:
        stmt = stmt.where(SecretarialCircular.status == status)
    if fy:
        stmt = stmt.where(SecretarialCircular.fy == fy)
    return await paginate(db, stmt.order_by(SecretarialCircular.creation.desc()), page, page_size)
