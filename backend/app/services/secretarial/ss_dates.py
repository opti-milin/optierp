"""Secretarial Standard date arithmetic, and the minutes-book counter.

The two things auditors actually check about a minute book are whether the notice
period was observed and whether the entries are consecutively numbered. Both are
computed here, once, and frozen onto the meeting when it is scheduled — recomputing a
notice period later from today's rules would quietly rewrite history.

Statutory basis, all of it draft content pending review like everything else in §2.12:

* Board meeting notice: 7 days (s.173(3)), servable at shorter notice with consent.
* General meeting notice: 21 clear days (s.101(1)).
* Draft minutes circulated within 15 days of the meeting (SS-1 ¶7.4 / SS-2).
* Minutes signed within 30 days of the meeting (s.118(1), SS-1 ¶7.6).
* Maximum 120 days between two consecutive board meetings (s.173(1)).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.secretarial import MinutesBookSequence

# Notice days by meeting type. "Clear days" for general meetings means the count
# excludes both the day of service and the day of the meeting, which is why the
# caller adds the two extra days rather than this table pretending 21 means 21.
NOTICE_DAYS = {
    "board": 7,
    "committee": 7,
    "partners": 7,
    "agm": 21,
    "egm": 21,
}

DRAFT_MINUTES_DAYS = 15
SIGNED_MINUTES_DAYS = 30
MAX_GAP_BETWEEN_BOARD_MEETINGS = 120


def notice_due_on(scheduled_on: date, meeting_type: str) -> date:
    """The latest date the notice may go out and still be in time.

    General meetings need *clear* days — neither the day of despatch nor the day of the
    meeting counts — so two days are added to the statutory number.
    """
    days = NOTICE_DAYS.get(meeting_type, 7)
    if meeting_type in ("agm", "egm"):
        days += 2
    return scheduled_on - timedelta(days=days)


def minutes_deadlines(held_on: date) -> tuple[date, date]:
    """(draft circulated by, signed by) — 15 and 30 days after the meeting."""
    return (
        held_on + timedelta(days=DRAFT_MINUTES_DAYS),
        held_on + timedelta(days=SIGNED_MINUTES_DAYS),
    )


def notice_compliance(
    *, scheduled_on: date, sent_on: date | None, meeting_type: str, shorter_notice: bool
) -> dict[str, object]:
    """Was the notice period observed? Returns a verdict a panel can render.

    Shorter notice is lawful with the right consents, so it reports as compliant *with a
    caveat* rather than as a breach — the point is to prompt for the consent letters,
    not to cry wolf.
    """
    due = notice_due_on(scheduled_on, meeting_type)
    required = NOTICE_DAYS.get(meeting_type, 7)

    if sent_on is None:
        return {
            "status": "pending",
            "due_on": due,
            "days_required": required,
            "message": f"Notice must go out by {due:%d %b %Y} ({required} days before the meeting).",
        }

    days_given = (scheduled_on - sent_on).days
    if sent_on <= due:
        return {
            "status": "ok",
            "due_on": due,
            "days_required": required,
            "days_given": days_given,
            "message": f"Notice served {days_given} days before the meeting.",
        }
    if shorter_notice:
        return {
            "status": "shorter_notice",
            "due_on": due,
            "days_required": required,
            "days_given": days_given,
            "message": (
                f"Served {days_given} days before the meeting, short of the {required} required. "
                "Consent to shorter notice must be on file from the requisite members or directors."
            ),
        }
    return {
        "status": "short",
        "due_on": due,
        "days_required": required,
        "days_given": days_given,
        "message": (
            f"Notice served only {days_given} days before the meeting, against {required} required. "
            "Either move the meeting or obtain consent to shorter notice."
        ),
    }


def minutes_compliance(
    *, held_on: date | None, draft_on: date | None, signed_on: date | None, today: date | None = None
) -> dict[str, object]:
    """Where the minutes stand against the 15/30-day deadlines."""
    if held_on is None:
        return {"status": "not_held", "message": "The meeting has not been held yet."}

    now = today or date.today()
    draft_due, signed_due = minutes_deadlines(held_on)

    if signed_on:
        late = signed_on > signed_due
        return {
            "status": "signed_late" if late else "signed",
            "draft_due_on": draft_due,
            "signed_due_on": signed_due,
            "message": (
                f"Minutes signed on {signed_on:%d %b %Y}"
                + (f", {(signed_on - signed_due).days} days after the 30-day deadline." if late else ", within time.")
            ),
        }

    overdue_signing = now > signed_due
    if draft_on:
        return {
            "status": "overdue_signing" if overdue_signing else "awaiting_signature",
            "draft_due_on": draft_due,
            "signed_due_on": signed_due,
            "message": (
                f"Draft circulated on {draft_on:%d %b %Y}. Signature due by {signed_due:%d %b %Y}"
                + (" — now overdue." if overdue_signing else ".")
            ),
        }

    overdue_draft = now > draft_due
    return {
        "status": "overdue_draft" if overdue_draft else "awaiting_draft",
        "draft_due_on": draft_due,
        "signed_due_on": signed_due,
        "message": (
            f"Draft minutes due by {draft_due:%d %b %Y}"
            + (" — now overdue." if overdue_draft else ".")
        ),
    }


def gap_warning(previous_board_meeting: date | None, scheduled_on: date) -> str | None:
    """s.173(1): no more than 120 days between consecutive board meetings."""
    if previous_board_meeting is None:
        return None
    gap = (scheduled_on - previous_board_meeting).days
    if gap > MAX_GAP_BETWEEN_BOARD_MEETINGS:
        return (
            f"{gap} days since the last board meeting on {previous_board_meeting:%d %b %Y}, "
            f"exceeding the {MAX_GAP_BETWEEN_BOARD_MEETINGS}-day maximum under s.173(1)."
        )
    return None


# --- Minutes-book numbering -------------------------------------------------------


def scope_for(meeting_type: str, committee_id: uuid.UUID | None = None) -> str:
    """Which minute book this meeting belongs in.

    Board, general and each committee keep separate books with separate numbering, so
    the scope key has to include the committee.
    """
    if meeting_type == "committee" and committee_id:
        return f"committee:{committee_id}"
    if meeting_type in ("agm", "egm"):
        return "general"
    if meeting_type == "partners":
        return "partners"
    return "board"


async def allocate_minutes_number(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    entity_id: uuid.UUID,
    scope: str,
    pages: int = 1,
) -> tuple[int, int, int]:
    """Take the next entry number and page range. Gap-free, serialised.

    ``SELECT ... FOR UPDATE`` on the counter row, inside the caller's transaction. Not
    ``MAX()+1``: two people signing minutes at the same moment would otherwise both read
    the same maximum and produce a duplicate entry number, which is precisely the
    integrity failure consecutive numbering exists to make visible.

    Called only when minutes are *signed*, so an abandoned draft never burns a number.
    """
    row = (
        await db.execute(
            select(MinutesBookSequence)
            .where(
                MinutesBookSequence.company_id == company_id,
                MinutesBookSequence.entity_id == entity_id,
                MinutesBookSequence.scope == scope,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is None:
        row = MinutesBookSequence(
            company_id=company_id, entity_id=entity_id, scope=scope, next_entry_no=1, next_page_no=1
        )
        db.add(row)
        await db.flush()

    entry_no = row.next_entry_no
    page_from = row.next_page_no
    page_to = page_from + max(pages, 1) - 1

    row.next_entry_no = entry_no + 1
    row.next_page_no = page_to + 1
    await db.flush()
    return entry_no, page_from, page_to


async def peek_minutes_number(
    db: AsyncSession, *, company_id: uuid.UUID, entity_id: uuid.UUID, scope: str
) -> tuple[int, int]:
    """What the next numbers *would* be, for display. Consumes nothing."""
    row = await db.scalar(
        select(MinutesBookSequence).where(
            MinutesBookSequence.company_id == company_id,
            MinutesBookSequence.entity_id == entity_id,
            MinutesBookSequence.scope == scope,
        )
    )
    return (row.next_entry_no, row.next_page_no) if row else (1, 1)
