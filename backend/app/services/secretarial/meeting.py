"""Meetings: scheduling, agenda, attendance, packs, and the minutes lifecycle.

The state machine is `draft → scheduled → circulated → held → minutes_draft →
minutes_signed → closed`, and it is enforced here rather than trusted from the client.
Two transitions do real work:

* **scheduled** freezes the SS notice and minutes deadlines onto the row.
* **minutes_signed** consumes the minutes-book number — the only place it is consumed,
  and only once, so an abandoned draft never leaves a gap in the book.

Notice, minutes and attendance all render from the same ordered agenda list. That is
what guarantees item 4 in the notice is item 4 in the minutes, without anyone re-keying
it.
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
    SecretarialAttendance,
    SecretarialCommitteeMember,
    SecretarialDocument,
    SecretarialEntity,
    SecretarialMeeting,
    SecretarialPerson,
)
from app.services.audit import log_audit, serialize_document
from app.services.pagination import paginate
from app.services.secretarial import blocks as block_lib
from app.services.secretarial import documents as document_service
from app.services.secretarial import ss_dates
from app.services.secretarial.common import current_fy, get_entity

_DOCTYPE = "Secretarial Meeting"

# What may follow what. Anything not listed is refused with the reason.
_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"scheduled", "cancelled"},
    "scheduled": {"circulated", "held", "cancelled"},
    "circulated": {"held", "cancelled"},
    "held": {"minutes_draft"},
    "minutes_draft": {"minutes_signed"},
    "minutes_signed": {"closed"},
    "closed": set(),
    "cancelled": set(),
}

# Which pack renders each meeting type's papers.
_PACK_BY_TYPE = {
    "board": "board-meeting",
    "committee": "board-meeting",
    "agm": "agm",
    "egm": "agm",
    "partners": "llp-partners-resolution",
}


async def get_meeting(
    db: AsyncSession, meeting_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialMeeting:
    meeting = await db.get(SecretarialMeeting, meeting_id)
    if meeting is None or meeting.company_id != company_id:
        raise NotFoundError("Meeting not found")
    return meeting


def _require_transition(meeting: SecretarialMeeting, target: str) -> None:
    allowed = _TRANSITIONS.get(meeting.status, set())
    if target not in allowed:
        nice = ", ".join(sorted(allowed)) or "nothing — this meeting is closed"
        raise ValidationError(
            f"A meeting that is '{meeting.status}' cannot move to '{target}'. Next: {nice}.",
            field="status",
        )


async def create_meeting(
    db: AsyncSession,
    user: CurrentUser,
    *,
    entity_id: uuid.UUID,
    meeting_type: str,
    scheduled_at: datetime,
    venue: str | None = None,
    committee_id: uuid.UUID | None = None,
    title: str | None = None,
    mode: str = "physical",
    chairperson_id: uuid.UUID | None = None,
    fy: str | None = None,
    commit: bool = True,
) -> SecretarialMeeting:
    """Create a meeting and freeze its statutory dates."""
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)

    if meeting_type == "partners" and entity.kind != "llp":
        raise ValidationError("A partners' meeting belongs to an LLP", field="meeting_type")
    if meeting_type in ("board", "agm", "egm") and entity.kind == "llp":
        raise ValidationError(
            "An LLP holds partners' meetings, not board or general meetings", field="meeting_type"
        )
    if meeting_type == "committee" and committee_id is None:
        raise ValidationError("Which committee is meeting?", field="committee_id")

    scheduled_on = scheduled_at.date()
    fy = fy or current_fy(scheduled_on, entity.fy_end_mmdd)

    # Next serial in this entity's book of meetings of this type.
    #
    # MAX+1 under a lock, not COUNT+1: a cancelled meeting keeps its serial (the notice
    # for it went out under that number), so counting live rows re-issues a number that
    # is already taken and trips `unique(entity_id, meeting_type, serial_no)`. The lock
    # on the parent entity serialises concurrent creates for the same book, mirroring
    # how the minutes-book counter is allocated in `ss_dates`.
    await db.execute(
        select(SecretarialEntity.id).where(SecretarialEntity.id == entity_id).with_for_update()
    )
    serial = (
        (
            await db.execute(
                select(func.max(SecretarialMeeting.serial_no)).where(
                    SecretarialMeeting.entity_id == entity_id,
                    SecretarialMeeting.meeting_type == meeting_type,
                )
            )
        ).scalar_one_or_none()
        or 0
    ) + 1

    draft_due, signed_due = ss_dates.minutes_deadlines(scheduled_on)
    meeting = SecretarialMeeting(
        company_id=user.company_id,
        entity_id=entity_id,
        meeting_type=meeting_type,
        committee_id=committee_id,
        serial_no=serial,
        title=title,
        fy=fy,
        scheduled_at=scheduled_at,
        venue=venue,
        mode=mode,
        chairperson_id=chairperson_id,
        status="draft",
        notice_days_required=ss_dates.NOTICE_DAYS.get(meeting_type, 7),
        notice_due_on=ss_dates.notice_due_on(scheduled_on, meeting_type),
        minutes_draft_due_on=draft_due,
        minutes_signed_due_on=signed_due,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(meeting)
    await db.flush()

    # Participants come from the master, scoped to the committee where relevant — this
    # is what structurally prevents committee papers reaching the wrong directors.
    for person_id in await eligible_participants(db, meeting):
        db.add(
            SecretarialAttendance(
                company_id=user.company_id,
                meeting_id=meeting.id,
                person_id=person_id,
                status="present",
                is_chairperson=(person_id == chairperson_id),
                owner=user.id,
                modified_by=user.id,
            )
        )

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=meeting.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after=serialize_document(meeting),
    )
    # Callers that create a meeting as one step of a larger operation (ratifying a
    # circular, say) pass commit=False so the meeting is not left behind on its own if
    # a later step fails.
    if commit:
        await db.commit()
    return meeting


async def eligible_participants(db: AsyncSession, meeting: SecretarialMeeting) -> list[uuid.UUID]:
    """Who belongs at this meeting, from the master data.

    A committee meeting is limited to its constitution; a board meeting is every serving
    director. Deriving it rather than letting the user pick is what makes wrong-recipient
    packs structurally impossible rather than merely unlikely.
    """
    if meeting.meeting_type == "committee" and meeting.committee_id:
        rows = await db.execute(
            select(SecretarialCommitteeMember.person_id).where(
                SecretarialCommitteeMember.committee_id == meeting.committee_id,
                SecretarialCommitteeMember.valid_to.is_(None),
            )
        )
        return list(rows.scalars().all())

    roles = ("designated_partner", "partner") if meeting.meeting_type == "partners" else ("director",)
    rows = await db.execute(
        select(SecretarialAppointment.person_id).where(
            SecretarialAppointment.entity_id == meeting.entity_id,
            SecretarialAppointment.ceased_on.is_(None),
            SecretarialAppointment.role_type.in_(roles),
        )
    )
    return list(rows.scalars().all())


# --- Agenda -----------------------------------------------------------------------


async def set_agenda(
    db: AsyncSession, meeting_id: uuid.UUID, items: list[dict[str, Any]], user: CurrentUser
) -> list[SecretarialAgendaItem]:
    """Replace the agenda. Refused once minutes are signed — the book is closed."""
    assert user.company_id is not None
    meeting = await get_meeting(db, meeting_id, user.company_id)
    if meeting.status in ("minutes_signed", "closed"):
        raise ValidationError("The minutes are signed; this agenda can no longer be changed")

    existing = (
        (
            await db.execute(
                select(SecretarialAgendaItem).where(SecretarialAgendaItem.meeting_id == meeting_id)
            )
        )
        .scalars()
        .all()
    )
    # Ratification items are inserted by the circular workflow, not typed by hand, so
    # they survive an agenda rewrite.
    keep = {row.id for row in existing if row.circular_id is not None}
    for row in existing:
        if row.id not in keep:
            await db.delete(row)
    await db.flush()

    created: list[SecretarialAgendaItem] = []
    seq = 1
    for row in sorted((r for r in existing if r.id in keep), key=lambda r: r.seq):
        row.seq = seq
        seq += 1
        created.append(row)

    for item in items:
        agenda = SecretarialAgendaItem(
            company_id=user.company_id,
            meeting_id=meeting_id,
            seq=seq,
            title=item["title"],
            body=item.get("body"),
            resolution_text=item.get("resolution_text"),
            resolution_kind=item.get("resolution_kind"),
            # "ratification" is a claim about provenance — that this item exists because
            # a circular resolution needs noting — and it is backed by `circular_id`,
            # which only the circular workflow sets. A hand-typed item may not award
            # itself that badge, so anything arriving here is demoted to manual.
            source="manual" if item.get("source") == "ratification" else item.get("source", "manual"),
            pack_code=item.get("pack_code"),
            owner=user.id,
            modified_by=user.id,
        )
        db.add(agenda)
        created.append(agenda)
        seq += 1

    await db.flush()
    await db.commit()
    return created


async def get_agenda(
    db: AsyncSession, meeting_id: uuid.UUID, company_id: uuid.UUID
) -> list[SecretarialAgendaItem]:
    await get_meeting(db, meeting_id, company_id)
    return list(
        (
            await db.execute(
                select(SecretarialAgendaItem)
                .where(SecretarialAgendaItem.meeting_id == meeting_id)
                .order_by(SecretarialAgendaItem.seq)
            )
        )
        .scalars()
        .all()
    )


# --- Attendance -------------------------------------------------------------------


async def set_attendance(
    db: AsyncSession, meeting_id: uuid.UUID, rows: list[dict[str, Any]], user: CurrentUser
) -> list[SecretarialAttendance]:
    assert user.company_id is not None
    meeting = await get_meeting(db, meeting_id, user.company_id)
    if meeting.status in ("minutes_signed", "closed"):
        raise ValidationError("The minutes are signed; attendance can no longer be changed")

    by_person = {
        row.person_id: row
        for row in (
            (
                await db.execute(
                    select(SecretarialAttendance).where(SecretarialAttendance.meeting_id == meeting_id)
                )
            )
            .scalars()
            .all()
        )
    }
    out: list[SecretarialAttendance] = []
    for item in rows:
        person_id = uuid.UUID(str(item["person_id"]))
        row = by_person.get(person_id)
        if row is None:
            row = SecretarialAttendance(
                company_id=user.company_id,
                meeting_id=meeting_id,
                person_id=person_id,
                owner=user.id,
                modified_by=user.id,
            )
            db.add(row)
        row.status = item.get("status", "present")
        row.joined_via = item.get("joined_via")
        row.is_chairperson = bool(item.get("is_chairperson", False))
        row.remarks = item.get("remarks")
        out.append(row)

    await db.flush()

    present = sum(1 for r in out if r.status in ("present", "video"))

    # s.174(1) measures the quorum against the board's *total strength*, not against
    # whoever happens to be in the sheet that was submitted. Counting the payload would
    # let a two-person submission on a nine-director board report a quorum that was
    # never there — so the denominator is read from the appointments register.
    strength = (
        await db.execute(
            select(func.count()).select_from(SecretarialAppointment).where(
                SecretarialAppointment.entity_id == meeting.entity_id,
                SecretarialAppointment.ceased_on.is_(None),
                SecretarialAppointment.role_type.in_(("director", "designated_partner", "partner")),
            )
        )
    ).scalar_one()

    meeting.quorum_met = present >= (meeting.quorum_required or _default_quorum(strength))
    await db.flush()
    await db.commit()
    return out


def _default_quorum(total_strength: int) -> int:
    """s.174(1): one third of total strength or two directors, whichever is higher.

    ``total_strength`` is the number of directors in office, not the number who attended.
    """
    return max(2, -(-total_strength // 3))


async def get_attendance(
    db: AsyncSession, meeting_id: uuid.UUID, company_id: uuid.UUID
) -> list[tuple[SecretarialAttendance, str]]:
    await get_meeting(db, meeting_id, company_id)
    rows = await db.execute(
        select(SecretarialAttendance, SecretarialPerson.full_name)
        .join(SecretarialPerson, SecretarialPerson.id == SecretarialAttendance.person_id)
        .where(SecretarialAttendance.meeting_id == meeting_id)
        .order_by(SecretarialAttendance.is_chairperson.desc(), SecretarialPerson.full_name)
    )
    return [(r[0], r[1]) for r in rows.all()]


# --- Papers -----------------------------------------------------------------------


async def generate_pack(
    db: AsyncSession, meeting_id: uuid.UUID, user: CurrentUser, fragments: list[str] | None = None
) -> list[SecretarialDocument]:
    """Render notice, minutes and attendance from the one agenda.

    They are generated together and from the same source precisely so their numbering
    and wording cannot drift apart.
    """
    assert user.company_id is not None
    meeting = await get_meeting(db, meeting_id, user.company_id)
    entity = await get_entity(db, meeting.entity_id, user.company_id)
    agenda = await get_agenda(db, meeting_id, user.company_id)
    attendance = await get_attendance(db, meeting_id, user.company_id)

    if not agenda:
        raise ValidationError("Add at least one agenda item before generating the papers")

    pack_code = _PACK_BY_TYPE.get(meeting.meeting_type, "board-meeting")
    wanted = fragments or ["notice", "minutes_narration", "attendance"]

    chair = next((name for row, name in attendance if row.is_chairperson), "the Chairperson")
    ordinal = _ordinal(meeting.serial_no or 1)
    form_data = {
        "meeting_no": ordinal,
        "meeting_date": meeting.scheduled_at.strftime("%d %B %Y"),
        "meeting_time": meeting.scheduled_at.strftime("%I:%M %p").lstrip("0"),
        "venue": meeting.venue or "the registered office",
        "notice_date": (meeting.notice_sent_on or date.today()).strftime("%d %B %Y"),
        "chairperson": chair,
        "fy_label": meeting.fy,
    }

    agenda_payload = [
        {"title": a.title, "body": a.body, "resolution_text": a.resolution_text} for a in agenda
    ]

    made: list[SecretarialDocument] = []
    for fragment in wanted:
        # The agenda is appended as blocks rather than substituted as a string, so it
        # renders as a real numbered list in every output format.
        if fragment == "notice":
            extra = block_lib.agenda_blocks(agenda_payload, style="notice")
        elif fragment == "minutes_narration":
            extra = block_lib.agenda_blocks(agenda_payload, style="minutes")
        elif fragment == "attendance":
            extra = [
                {
                    "type": "table",
                    "columns": ["Sr.", "Name", "Status", "Signature"],
                    "rows": [
                        [str(i), name, row.status.replace("_", " ").title(), ""]
                        for i, (row, name) in enumerate(attendance, start=1)
                    ],
                }
            ]
        else:
            extra = []

        titles = {
            "notice": f"Notice — {ordinal} {_type_label(meeting.meeting_type)}",
            "minutes_narration": f"Minutes — {ordinal} {_type_label(meeting.meeting_type)}",
            "attendance": f"Attendance — {ordinal} {_type_label(meeting.meeting_type)}",
        }

        doc = await document_service.generate(
            db,
            user,
            entity_id=entity.id,
            pack_code=pack_code,
            fragment=fragment,
            form_data=form_data,
            extra_blocks=extra,
            title=titles.get(fragment),
            source_doctype=_DOCTYPE,
            source_id=meeting.id,
            document_date=meeting.scheduled_at.date(),
            commit=False,
        )
        made.append(doc)

    await db.commit()
    return made


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _type_label(meeting_type: str) -> str:
    return {
        "board": "Board Meeting",
        "committee": "Committee Meeting",
        "agm": "Annual General Meeting",
        "egm": "Extraordinary General Meeting",
        "partners": "Meeting of Partners",
    }.get(meeting_type, "Meeting")


# --- Lifecycle --------------------------------------------------------------------


async def transition(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    target: str,
    user: CurrentUser,
    *,
    on_date: date | None = None,
    pages: int = 1,
) -> SecretarialMeeting:
    """Advance the meeting. The only place minutes numbers are allocated."""
    assert user.company_id is not None
    meeting = await get_meeting(db, meeting_id, user.company_id)
    _require_transition(meeting, target)
    before = serialize_document(meeting)
    today = on_date or date.today()

    if target == "scheduled":
        meeting.notice_sent_on = meeting.notice_sent_on or today
        check = ss_dates.notice_compliance(
            scheduled_on=meeting.scheduled_at.date(),
            sent_on=meeting.notice_sent_on,
            meeting_type=meeting.meeting_type,
            shorter_notice=meeting.shorter_notice,
        )
        if check["status"] == "short":
            raise ValidationError(str(check["message"]), field="notice_sent_on")

    elif target == "held":
        # `on_date` is honoured here, not just on the notice and minutes steps. A meeting
        # is routinely marked held some days after it happened — while writing the minutes
        # — and stamping it with today would put the wrong date on the minute book and
        # start the SS-1 fifteen/thirty-day clocks from the wrong day. The scheduled
        # time of day is kept, since that is the time the notice named.
        if meeting.held_at is None:
            meeting.held_at = (
                datetime.combine(on_date, meeting.scheduled_at.timetz())
                if on_date is not None
                else datetime.now(UTC)
            )
        draft_due, signed_due = ss_dates.minutes_deadlines(meeting.held_at.date())
        meeting.minutes_draft_due_on = draft_due
        meeting.minutes_signed_due_on = signed_due

    elif target == "minutes_draft":
        meeting.minutes_draft_on = meeting.minutes_draft_on or today

    elif target == "minutes_signed":
        if meeting.minutes_entry_no is None:
            scope = ss_dates.scope_for(meeting.meeting_type, meeting.committee_id)
            entry, page_from, page_to = await ss_dates.allocate_minutes_number(
                db,
                company_id=user.company_id,
                entity_id=meeting.entity_id,
                scope=scope,
                pages=pages,
            )
            meeting.minutes_entry_no = entry
            meeting.minutes_page_from = page_from
            meeting.minutes_page_to = page_to
        meeting.minutes_signed_on = meeting.minutes_signed_on or today

    meeting.status = target
    meeting.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=meeting.id,
        action="SUBMIT" if target != "cancelled" else "CANCEL",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(meeting),
    )
    await db.commit()
    return meeting


async def compliance_panel(
    db: AsyncSession, meeting_id: uuid.UUID, company_id: uuid.UUID
) -> dict[str, Any]:
    """The SS-1/SS-2 panel: notice, minutes deadlines, numbering, meeting gap."""
    meeting = await get_meeting(db, meeting_id, company_id)
    scope = ss_dates.scope_for(meeting.meeting_type, meeting.committee_id)
    next_entry, next_page = await ss_dates.peek_minutes_number(
        db, company_id=company_id, entity_id=meeting.entity_id, scope=scope
    )

    previous = (
        await db.execute(
            select(func.max(SecretarialMeeting.scheduled_at)).where(
                SecretarialMeeting.entity_id == meeting.entity_id,
                SecretarialMeeting.meeting_type == meeting.meeting_type,
                SecretarialMeeting.scheduled_at < meeting.scheduled_at,
                SecretarialMeeting.status.notin_(("cancelled", "draft")),
            )
        )
    ).scalar_one_or_none()

    return {
        "notice": ss_dates.notice_compliance(
            scheduled_on=meeting.scheduled_at.date(),
            sent_on=meeting.notice_sent_on,
            meeting_type=meeting.meeting_type,
            shorter_notice=meeting.shorter_notice,
        ),
        "minutes": ss_dates.minutes_compliance(
            held_on=meeting.held_at.date() if meeting.held_at else None,
            draft_on=meeting.minutes_draft_on,
            signed_on=meeting.minutes_signed_on,
        ),
        "minutes_book": {
            "scope": scope,
            "allocated_entry_no": meeting.minutes_entry_no,
            "allocated_pages": (
                f"{meeting.minutes_page_from}-{meeting.minutes_page_to}"
                if meeting.minutes_page_from
                else None
            ),
            "next_entry_no": next_entry,
            "next_page_no": next_page,
        },
        "gap_warning": ss_dates.gap_warning(
            previous.date() if previous else None, meeting.scheduled_at.date()
        ),
        "quorum": {"required": meeting.quorum_required, "met": meeting.quorum_met},
    }


async def update_meeting(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    patch: dict[str, Any],
    user: CurrentUser,
) -> SecretarialMeeting:
    """Edit a meeting's particulars, keeping the statutory clock honest.

    Two things a blind ``setattr`` loop got wrong. Once the minutes are signed the row
    *is* the minute book, so it stops being editable — a correction there is a fresh
    entry, not a quiet overwrite. And moving the date has to move the notice and minutes
    deadlines with it; leaving them frozen at values computed for the old date turns the
    SS panel into a green light that means nothing.
    """
    assert user.company_id is not None
    meeting = await get_meeting(db, meeting_id, user.company_id)

    if meeting.status in ("minutes_signed", "closed", "cancelled"):
        raise ValidationError(
            f"This meeting is {meeting.status.replace('_', ' ')} and can no longer be edited. "
            "Record a correction at the next meeting instead."
        )

    before = serialize_document(meeting)
    for field, value in patch.items():
        setattr(meeting, field, value)

    if "scheduled_at" in patch and patch["scheduled_at"] is not None:
        scheduled_on = meeting.scheduled_at.date()
        draft_due, signed_due = ss_dates.minutes_deadlines(scheduled_on)
        meeting.notice_due_on = ss_dates.notice_due_on(scheduled_on, meeting.meeting_type)
        meeting.minutes_draft_due_on = draft_due
        meeting.minutes_signed_due_on = signed_due

    meeting.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=meeting.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(meeting),
    )
    await db.commit()
    return meeting


async def entity_names(
    db: AsyncSession, entity_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Entity id → name, for list rows that span more than one client."""
    if not entity_ids:
        return {}
    rows = await db.execute(
        select(SecretarialEntity.id, SecretarialEntity.entity_name).where(
            SecretarialEntity.id.in_(entity_ids)
        )
    )
    return {row.id: row.entity_name for row in rows}


async def list_meetings(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    fy: str | None = None,
    meeting_type: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialMeeting], int]:
    stmt = select(SecretarialMeeting).where(SecretarialMeeting.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialMeeting.entity_id == entity_id)
    if fy:
        stmt = stmt.where(SecretarialMeeting.fy == fy)
    if meeting_type:
        stmt = stmt.where(SecretarialMeeting.meeting_type == meeting_type)
    if status:
        stmt = stmt.where(SecretarialMeeting.status == status)
    return await paginate(db, stmt.order_by(SecretarialMeeting.scheduled_at.desc()), page, page_size)
