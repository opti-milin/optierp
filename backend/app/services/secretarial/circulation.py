"""Tracked circulation — provable service of board papers.

This is the category's trust anchor. "The notice was sent" is worth little; "this
director opened it at 14:12 on 3 March and acknowledged it at 14:15" is what settles an
argument. So every recipient gets their own tokenised link and their own timeline, and
the whole thing exports to CSV because that is what gets attached to a file.

Directors never log in — see ``app/core/portal.py`` for why and how.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.portal import default_expiry, generate_token
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialCirculation,
    SecretarialCirculationRecipient,
    SecretarialConsentResponse,
    SecretarialDocument,
    SecretarialMeeting,
    SecretarialPerson,
    SecretarialPortalToken,
)
from app.services.audit import log_audit
from app.services.email import send_document_email
from app.services.secretarial.common import get_entity

_DOCTYPE = "Secretarial Circulation"


def _portal_url(kind: str, raw_token: str) -> str:
    """The link a director receives. ``/p/c/`` views papers, ``/p/r/`` records consent.

    Absolute, because it is clicked from an email client rather than from inside the app.
    """
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/p/{kind}/{raw_token}"


async def _issue_token(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    entity_id: uuid.UUID,
    person_id: uuid.UUID,
    purpose: str,
    target_doctype: str,
    target_id: uuid.UUID,
    user: CurrentUser | None = None,
) -> str:
    """Mint a single-purpose capability and return the raw token — the only moment it
    exists in plaintext anywhere."""
    raw, hashed = generate_token()
    db.add(
        SecretarialPortalToken(
            company_id=company_id,
            purpose=purpose,
            token_hash=hashed,
            entity_id=entity_id,
            person_id=person_id,
            target_doctype=target_doctype,
            target_id=target_id,
            expires_at=default_expiry(),
            owner=user.id if user else None,
            modified_by=user.id if user else None,
        )
    )
    await db.flush()
    return raw


async def circulate_meeting_papers(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    user: CurrentUser,
    *,
    document_ids: list[uuid.UUID] | None = None,
    person_ids: list[uuid.UUID] | None = None,
    subject: str | None = None,
    message: str | None = None,
    send_email: bool = True,
) -> SecretarialCirculation:
    """Send the papers to each participant with their own tracked link."""
    assert user.company_id is not None
    meeting = await db.get(SecretarialMeeting, meeting_id)
    if meeting is None or meeting.company_id != user.company_id:
        raise NotFoundError("Meeting not found")
    entity = await get_entity(db, meeting.entity_id, user.company_id)

    from app.services.secretarial import meeting as meeting_service

    if person_ids is None:
        person_ids = await meeting_service.eligible_participants(db, meeting)
    if not person_ids:
        raise ValidationError("There is nobody to circulate to")

    if document_ids is None:
        document_ids = list(
            (
                await db.execute(
                    select(SecretarialDocument.id).where(
                        SecretarialDocument.source_doctype == "Secretarial Meeting",
                        SecretarialDocument.source_id == meeting_id,
                        SecretarialDocument.is_current.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

    circulation = SecretarialCirculation(
        company_id=user.company_id,
        meeting_id=meeting_id,
        entity_id=meeting.entity_id,
        subject=subject or f"Papers for the meeting on {meeting.scheduled_at:%d %B %Y}",
        message=message,
        document_ids=[str(d) for d in document_ids],
        sent_at=datetime.now(UTC),
        owner=user.id,
        modified_by=user.id,
    )
    db.add(circulation)
    await db.flush()

    people = {
        p.id: p
        for p in (
            (await db.execute(select(SecretarialPerson).where(SecretarialPerson.id.in_(person_ids))))
            .scalars()
            .all()
        )
    }

    sent = 0
    for person_id in person_ids:
        person = people.get(person_id)
        recipient = SecretarialCirculationRecipient(
            company_id=user.company_id,
            circulation_id=circulation.id,
            person_id=person_id,
            email=person.email if person else None,
            status="pending",
            sent_at=datetime.now(UTC),
            owner=user.id,
            modified_by=user.id,
        )
        db.add(recipient)
        await db.flush()

        raw = await _issue_token(
            db,
            company_id=user.company_id,
            entity_id=meeting.entity_id,
            person_id=person_id,
            purpose="circulation_view",
            target_doctype="Secretarial Circulation Recipient",
            target_id=recipient.id,
            user=user,
        )

        if send_email and person and person.email:
            link = _portal_url("c", raw)
            body = (
                f"Dear {person.full_name},\n\n"
                f"{circulation.subject}\n\n"
                f"{message or ''}\n\n"
                f"You can view the papers and acknowledge receipt here:\n{link}\n\n"
                f"The link is personal to you and does not require a login.\n\n"
                f"— {entity.entity_name}"
            )
            log = await send_document_email(
                db,
                company_id=user.company_id,
                to=[person.email],
                subject=circulation.subject,
                body=body,
                reference_doctype=_DOCTYPE,
                reference_id=circulation.id,
                user_id=user.id,
            )
            if log.status == "Sent":
                sent += 1

    if meeting.status == "scheduled":
        meeting.status = "circulated"
    meeting.notice_sent_on = meeting.notice_sent_on or datetime.now(UTC).date()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=circulation.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"recipients": len(person_ids), "emails_sent": sent},
    )
    await db.commit()
    return circulation


async def send_consent_links(
    db: AsyncSession, circular_id: uuid.UUID, user: CurrentUser, *, send_email: bool = True
) -> int:
    """Email each director their personal consent link for a circulating resolution."""
    assert user.company_id is not None
    from app.services.secretarial import circular as circular_service

    circular = await circular_service.get_circular(db, circular_id, user.company_id)
    if circular.status != "circulating":
        raise ValidationError("This resolution is not open for responses")
    entity = await get_entity(db, circular.entity_id, user.company_id)

    rows = await db.execute(
        select(SecretarialConsentResponse, SecretarialPerson)
        .join(SecretarialPerson, SecretarialPerson.id == SecretarialConsentResponse.person_id)
        .where(SecretarialConsentResponse.circular_id == circular_id)
    )

    sent = 0
    for response, person in rows.all():
        if response.is_interested:
            # An interested director is not asked to vote, so they get no link at all.
            continue
        raw = await _issue_token(
            db,
            company_id=user.company_id,
            entity_id=circular.entity_id,
            person_id=person.id,
            purpose="consent_respond",
            target_doctype="Secretarial Consent Response",
            target_id=response.id,
            user=user,
        )
        if send_email and person.email:
            link = _portal_url("r", raw)
            deadline = (
                f"\nPlease respond by {circular.expires_at:%d %B %Y}.\n"
                if circular.expires_at
                else ""
            )
            body = (
                f"Dear {person.full_name},\n\n"
                f"A resolution is circulated for your approval under Section 175 of the "
                f"Companies Act, 2013.\n\n"
                f"{circular.title}\n"
                f"Reference: {circular.reference_no}\n"
                f"{deadline}\n"
                f"Record your consent or dissent here:\n{link}\n\n"
                f"The link is personal to you and does not require a login.\n\n"
                f"— {entity.entity_name}"
            )
            log = await send_document_email(
                db,
                company_id=user.company_id,
                to=[person.email],
                subject=f"For your consent: {circular.title}",
                body=body,
                reference_doctype="Secretarial Circular Resolution",
                reference_id=circular.id,
                user_id=user.id,
            )
            if log.status == "Sent":
                sent += 1
        response.sent_at = datetime.now(UTC)

    await db.commit()
    return sent


async def mark_viewed(db: AsyncSession, recipient_id: uuid.UUID) -> None:
    row = await db.get(SecretarialCirculationRecipient, recipient_id)
    if row is not None and row.status == "pending":
        row.status = "viewed"
        row.viewed_at = datetime.now(UTC)
        await db.flush()


async def acknowledge(db: AsyncSession, recipient_id: uuid.UUID) -> SecretarialCirculationRecipient:
    row = await db.get(SecretarialCirculationRecipient, recipient_id)
    if row is None:
        raise NotFoundError("Circulation record not found")
    if row.status != "acknowledged":
        row.status = "acknowledged"
        row.acknowledged_at = datetime.now(UTC)
        row.viewed_at = row.viewed_at or datetime.now(UTC)
        await db.flush()
    return row


async def recipients(
    db: AsyncSession, circulation_id: uuid.UUID, company_id: uuid.UUID
) -> list[tuple[SecretarialCirculationRecipient, str]]:
    circulation = await db.get(SecretarialCirculation, circulation_id)
    if circulation is None or circulation.company_id != company_id:
        raise NotFoundError("Circulation not found")
    rows = await db.execute(
        select(SecretarialCirculationRecipient, SecretarialPerson.full_name)
        .join(SecretarialPerson, SecretarialPerson.id == SecretarialCirculationRecipient.person_id)
        .where(SecretarialCirculationRecipient.circulation_id == circulation_id)
        .order_by(SecretarialPerson.full_name)
    )
    return [(r[0], r[1]) for r in rows.all()]


async def audit_export(
    db: AsyncSession, circulation_id: uuid.UUID, company_id: uuid.UUID
) -> tuple[str, str]:
    """The evidence file. Returns (csv, filename).

    Deliberately flat and boring: this gets attached to a compliance file and read by
    someone who has never seen the application.
    """
    circulation = await db.get(SecretarialCirculation, circulation_id)
    if circulation is None or circulation.company_id != company_id:
        raise NotFoundError("Circulation not found")
    rows = await recipients(db, circulation_id, company_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Circulation", circulation.subject])
    writer.writerow(["Sent at", circulation.sent_at.isoformat() if circulation.sent_at else ""])
    writer.writerow([])
    writer.writerow(["Recipient", "Email", "Status", "Sent at", "First viewed", "Acknowledged", "Resends"])
    for row, name in rows:
        writer.writerow(
            [
                name,
                row.email or "",
                row.status,
                row.sent_at.isoformat() if row.sent_at else "",
                row.viewed_at.isoformat() if row.viewed_at else "",
                row.acknowledged_at.isoformat() if row.acknowledged_at else "",
                row.resend_count,
            ]
        )
    stamp = (circulation.sent_at or datetime.now(UTC)).strftime("%Y%m%d")
    return buffer.getvalue(), f"circulation-audit-{stamp}.csv"


async def revoke_tokens_for_person(
    db: AsyncSession, person_id: uuid.UUID, company_id: uuid.UUID, reason: str
) -> int:
    """Kill every live link held by one person.

    Called when a directorship ends. A former director holding a working consent link is
    exactly the hole the expiry date alone does not close.
    """
    rows = list(
        (
            await db.execute(
                select(SecretarialPortalToken).where(
                    SecretarialPortalToken.company_id == company_id,
                    SecretarialPortalToken.person_id == person_id,
                    SecretarialPortalToken.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for token in rows:
        token.revoked_at = datetime.now(UTC)
        token.revoked_reason = reason
    if rows:
        await db.flush()
        await db.commit()
    return len(rows)
