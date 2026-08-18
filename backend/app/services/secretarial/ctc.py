"""Certified true copies — the end of the thread.

A CTC is the artefact that leaves the building: it goes to a bank, a buyer, a
government office. Two consequences follow, and both are enforced rather than trusted.

**Nothing is re-keyed.** The resolution text is pulled from the meeting or the passed
circular it came from. Re-typing a resolution into a certificate is how the certified
copy stops matching the minute book.

**Nothing is edited.** A CTC is issued once. If it was wrong, a new one is issued that
names the one it replaces and says why. A certified copy that quietly changed after
issue is worse than no copy at all, because somebody outside is relying on the first.
"""

from __future__ import annotations

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
    SecretarialCtc,
    SecretarialEntity,
    SecretarialMeeting,
    SecretarialPerson,
)
from app.services.audit import log_audit
from app.services.email import send_document_email
from app.services.pagination import paginate
from app.services.secretarial import documents as document_service
from app.services.secretarial.common import get_entity

_DOCTYPE = "Secretarial Certified True Copy"

PASSAGE_LABELS = {
    "board": "at a meeting of the Board of Directors",
    "committee": "at a meeting of the Committee",
    "agm": "at the Annual General Meeting",
    "egm": "at an Extraordinary General Meeting",
    "circular": "by circulation",
    "other": "",
}


async def get_ctc(db: AsyncSession, ctc_id: uuid.UUID, company_id: uuid.UUID) -> SecretarialCtc:
    row = await db.get(SecretarialCtc, ctc_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Certified true copy not found")
    return row


async def prefill(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    passage_mode: str,
    meeting_id: uuid.UUID | None = None,
    circular_id: uuid.UUID | None = None,
    agenda_item_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Everything the composer can fill in for itself.

    A blank certificate form invites transcription errors, so the source record supplies
    the resolution text, the date it was passed and the available signatories.
    """
    out: dict[str, Any] = {"passage_mode": passage_mode}

    if passage_mode == "circular":
        if not circular_id:
            raise ValidationError("Which circular resolution?", field="circular_id")
        circular = await db.get(SecretarialCircular, circular_id)
        if circular is None or circular.company_id != company_id:
            raise NotFoundError("Circular resolution not found")
        if circular.status not in ("passed", "ratified"):
            raise ValidationError(
                f"Only a passed resolution can be certified; this one is {circular.status}"
            )
        out.update(
            {
                "entity_id": circular.entity_id,
                "resolution_text": circular.resolution_text,
                "passed_on": (circular.decided_at or circular.circulated_at or datetime.now(UTC)).date(),
                "passage_description": (
                    f"passed by circulation under reference {circular.reference_no}"
                    + (f" on {circular.decided_at:%d %B %Y}" if circular.decided_at else "")
                ),
                "title": circular.title,
            }
        )
    else:
        if not meeting_id:
            raise ValidationError("Which meeting?", field="meeting_id")
        meeting = await db.get(SecretarialMeeting, meeting_id)
        if meeting is None or meeting.company_id != company_id:
            raise NotFoundError("Meeting not found")

        resolution_text = ""
        if agenda_item_id:
            item = await db.get(SecretarialAgendaItem, agenda_item_id)
            if item is None or item.meeting_id != meeting_id:
                raise NotFoundError("Agenda item not found on that meeting")
            resolution_text = item.resolution_text or item.title
        else:
            items = (
                (
                    await db.execute(
                        select(SecretarialAgendaItem)
                        .where(
                            SecretarialAgendaItem.meeting_id == meeting_id,
                            SecretarialAgendaItem.resolution_text.isnot(None),
                        )
                        .order_by(SecretarialAgendaItem.seq)
                    )
                )
                .scalars()
                .all()
            )
            resolution_text = "\n\n".join(i.resolution_text for i in items if i.resolution_text)

        out.update(
            {
                "entity_id": meeting.entity_id,
                "resolution_text": resolution_text,
                "passed_on": (meeting.held_at or meeting.scheduled_at).date(),
                "passage_description": (
                    f"passed {PASSAGE_LABELS.get(passage_mode, '')} held on "
                    f"{(meeting.held_at or meeting.scheduled_at):%d %B %Y}"
                ),
                "title": meeting.title or "",
            }
        )

    entity_id = out["entity_id"]
    signatories = (
        await db.execute(
            select(SecretarialPerson.id, SecretarialPerson.full_name, SecretarialPerson.din, SecretarialAppointment.designation)
            .join(SecretarialAppointment, SecretarialAppointment.person_id == SecretarialPerson.id)
            .where(
                SecretarialAppointment.entity_id == entity_id,
                SecretarialAppointment.ceased_on.is_(None),
                SecretarialAppointment.role_type.in_(("director", "designated_partner", "secretary")),
            )
            .order_by(SecretarialAppointment.is_signing.desc(), SecretarialPerson.full_name)
        )
    ).all()
    out["available_signatories"] = [
        {"person_id": str(s.id), "name": s.full_name, "din": s.din or "", "designation": s.designation or "Director"}
        for s in signatories
    ]
    return out


async def issue(
    db: AsyncSession,
    user: CurrentUser,
    *,
    entity_id: uuid.UUID,
    passage_mode: str,
    resolution_text: str,
    certified_on: date | None = None,
    passed_on: date | None = None,
    meeting_id: uuid.UUID | None = None,
    circular_id: uuid.UUID | None = None,
    place: str | None = None,
    issued_to: str | None = None,
    purpose: str | None = None,
    signatories: list[dict[str, Any]] | None = None,
    supersedes_id: uuid.UUID | None = None,
    superseded_reason: str | None = None,
) -> SecretarialCtc:
    """Issue a certified true copy and render its document. Append-only."""
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)

    if not (resolution_text or "").strip():
        raise ValidationError("A certificate needs the text of the resolution", field="resolution_text")
    if not signatories:
        raise ValidationError(
            "At least one signatory must certify the copy", field="signatories"
        )
    if supersedes_id and not superseded_reason:
        raise ValidationError(
            "Say why the earlier certificate is being replaced — it is recorded permanently",
            field="superseded_reason",
        )

    if supersedes_id:
        await get_ctc(db, supersedes_id, user.company_id)

    # Serialised on the parent entity: `unique(entity_id, issuance_no)` means two
    # concurrent issues would otherwise race for the same number and one would fail with
    # an IntegrityError in front of whoever pressed the button.
    await db.execute(
        select(SecretarialEntity.id).where(SecretarialEntity.id == entity_id).with_for_update()
    )
    next_no = (
        await db.execute(
            select(func.coalesce(func.max(SecretarialCtc.issuance_no), 0)).where(
                SecretarialCtc.entity_id == entity_id
            )
        )
    ).scalar_one() + 1

    certified = certified_on or date.today()
    ctc = SecretarialCtc(
        company_id=user.company_id,
        entity_id=entity_id,
        issuance_no=next_no,
        passage_mode=passage_mode,
        meeting_id=meeting_id,
        circular_id=circular_id,
        resolution_text=resolution_text,
        passed_on=passed_on,
        certified_on=certified,
        place=place,
        issued_to=issued_to,
        purpose=purpose,
        signatories=signatories,
        issued_at=datetime.now(UTC),
        supersedes_id=supersedes_id,
        superseded_reason=superseded_reason,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(ctc)
    await db.flush()

    passage_description = _describe(passage_mode, passed_on)
    doc = await document_service.generate(
        db,
        user,
        entity_id=entity_id,
        pack_code="certified-true-copy",
        fragment="ctc",
        form_data={
            "passage_description": passage_description,
            "resolution_text": resolution_text,
            "certified_on": certified.strftime("%d %B %Y"),
            "place": place or "",
            "issued_to": issued_to or "",
            "purpose": purpose or "",
        },
        extra_blocks=[{"type": "signature_grid", "signatories": signatories}],
        title=f"Certified True Copy #{next_no} — {entity.entity_name}",
        source_doctype=_DOCTYPE,
        source_id=ctc.id,
        document_date=certified,
        commit=False,
    )
    # Certified the moment it is issued — there is no draft stage for a CTC.
    doc.status = "issued"
    doc.issued_at = datetime.now(UTC)
    ctc.document_id = doc.id
    await db.flush()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=ctc.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "issuance_no": next_no,
            "passage_mode": passage_mode,
            "issued_to": issued_to,
            "supersedes": str(supersedes_id) if supersedes_id else None,
        },
    )
    await db.commit()
    return ctc


def _describe(passage_mode: str, passed_on: date | None) -> str:
    label = PASSAGE_LABELS.get(passage_mode, "")
    when = f" on {passed_on:%d %B %Y}" if passed_on else ""
    if passage_mode == "circular":
        return f"passed by circulation{when}"
    return f"passed {label}{when}".strip()


async def email_ctc(
    db: AsyncSession, ctc_id: uuid.UUID, user: CurrentUser, *, to: list[str], message: str | None = None
) -> str:
    """Send the certificate out, attaching the rendered PDF."""
    assert user.company_id is not None
    ctc = await get_ctc(db, ctc_id, user.company_id)
    entity = await get_entity(db, ctc.entity_id, user.company_id)
    if not ctc.document_id:
        raise ValidationError("This certificate has no rendered document")

    content, filename, media_type = await document_service.render(
        db, ctc.document_id, user.company_id, "pdf"
    )
    body = (
        f"{message or ''}\n\n"
        f"Please find attached a certified true copy of the resolution "
        f"{_describe(ctc.passage_mode, ctc.passed_on)}.\n\n"
        f"Certificate number {ctc.issuance_no}, certified on {ctc.certified_on:%d %B %Y}.\n\n"
        f"— {entity.entity_name}"
    ).strip()

    log = await send_document_email(
        db,
        company_id=user.company_id,
        to=to,
        subject=f"Certified true copy #{ctc.issuance_no} — {entity.entity_name}",
        body=body,
        attachments=[(filename, content if isinstance(content, bytes) else content.encode(), media_type)],
        reference_doctype=_DOCTYPE,
        reference_id=ctc.id,
        user_id=user.id,
    )
    await db.commit()
    return log.status


async def issuance_log(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialCtc], int]:
    """The per-entity register of what was certified, to whom and when."""
    stmt = select(SecretarialCtc).where(SecretarialCtc.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialCtc.entity_id == entity_id)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            SecretarialCtc.issued_to.ilike(like) | SecretarialCtc.resolution_text.ilike(like)
        )
    return await paginate(db, stmt.order_by(SecretarialCtc.issued_at.desc()), page, page_size)
