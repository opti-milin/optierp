"""Meetings: scheduling, agenda, attendance, packs, circulation, SS panel."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.secretarial_governance import (
    AgendaItemIn,
    AgendaItemOut,
    AttendanceIn,
    AttendanceOut,
    CirculateIn,
    CirculationOut,
    CirculationRecipientOut,
    DocumentListItem,
    GeneratePackIn,
    MeetingCreate,
    MeetingListItem,
    MeetingTransitionIn,
    MeetingUpdate,
)
from app.services.secretarial import circulation as circulation_service
from app.services.secretarial import meeting as service

router = APIRouter(prefix="/secretarial/meetings", tags=["secretarial: meetings"])

DOCTYPE = "Secretarial Meeting"


@router.post("", response_model=MeetingListItem, status_code=201, summary="Schedule a meeting")
async def create_meeting(
    payload: MeetingCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MeetingListItem:
    meeting = await service.create_meeting(
        db,
        current_user,
        entity_id=payload.entity_id,
        meeting_type=payload.meeting_type,
        scheduled_at=payload.scheduled_at,
        venue=payload.venue,
        committee_id=payload.committee_id,
        title=payload.title,
        mode=payload.mode,
        chairperson_id=payload.chairperson_id,
        fy=payload.fy,
    )
    return MeetingListItem.model_validate(meeting)


@router.get("", response_model=ListResponse[MeetingListItem], summary="List meetings")
async def list_meetings(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    fy: str | None = None,
    meeting_type: str | None = None,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[MeetingListItem]:
    assert current_user.company_id is not None
    items, total = await service.list_meetings(
        db,
        current_user.company_id,
        entity_id=entity_id,
        fy=fy,
        meeting_type=meeting_type,
        status=status,
        page=page,
        page_size=page_size,
    )
    # A practice tenant lists meetings across several client entities, so the row has to
    # say which one it belongs to. Resolved here in one query rather than joined in the
    # service, which is shared with the single-entity callers that already know.
    names = await service.entity_names(db, {m.entity_id for m in items})
    out = []
    for m in items:
        row = MeetingListItem.model_validate(m)
        row.entity_name = names.get(m.entity_id)
        out.append(row)

    return ListResponse(items=out, total=total, page=page, page_size=page_size)


@router.get("/{meeting_id}", response_model=MeetingListItem, summary="Fetch one meeting")
async def get_meeting(
    meeting_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MeetingListItem:
    assert current_user.company_id is not None
    return MeetingListItem.model_validate(
        await service.get_meeting(db, meeting_id, current_user.company_id)
    )


@router.patch("/{meeting_id}", response_model=MeetingListItem, summary="Update a meeting")
async def update_meeting(
    meeting_id: uuid.UUID,
    payload: MeetingUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MeetingListItem:
    return MeetingListItem.model_validate(
        await service.update_meeting(
            db, meeting_id, payload.model_dump(exclude_unset=True), current_user
        )
    )


@router.get(
    "/{meeting_id}/compliance",
    summary="The SS-1 / SS-2 panel",
    description="Notice period, the 15/30-day minutes deadlines, minutes-book numbering, "
    "and the 120-day gap check under s.173(1).",
)
async def compliance_panel(
    meeting_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    assert current_user.company_id is not None
    return await service.compliance_panel(db, meeting_id, current_user.company_id)


@router.post(
    "/{meeting_id}/transition",
    response_model=MeetingListItem,
    summary="Advance the meeting",
    description="draft → scheduled → circulated → held → minutes_draft → minutes_signed "
    "→ closed. The minutes-book number is allocated at minutes_signed and nowhere else, "
    "so an abandoned draft never leaves a gap in the book.",
)
async def transition(
    meeting_id: uuid.UUID,
    payload: MeetingTransitionIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MeetingListItem:
    return MeetingListItem.model_validate(
        await service.transition(
            db,
            meeting_id,
            payload.target,
            current_user,
            on_date=payload.on_date,
            pages=payload.pages,
        )
    )


# --- Agenda -----------------------------------------------------------------------


@router.get("/{meeting_id}/agenda", response_model=list[AgendaItemOut], summary="Read the agenda")
async def get_agenda(
    meeting_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgendaItemOut]:
    assert current_user.company_id is not None
    rows = await service.get_agenda(db, meeting_id, current_user.company_id)
    return [AgendaItemOut.model_validate(r) for r in rows]


@router.put(
    "/{meeting_id}/agenda",
    response_model=list[AgendaItemOut],
    summary="Replace the agenda",
    description="Ratification items added by the circular workflow are preserved — they "
    "are not typed by hand and must not be lost in a rewrite.",
)
async def set_agenda(
    meeting_id: uuid.UUID,
    items: list[AgendaItemIn],
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgendaItemOut]:
    rows = await service.set_agenda(
        db, meeting_id, [i.model_dump() for i in items], current_user
    )
    return [AgendaItemOut.model_validate(r) for r in rows]


# --- Attendance -------------------------------------------------------------------


@router.get(
    "/{meeting_id}/attendance", response_model=list[AttendanceOut], summary="Read attendance"
)
async def get_attendance(
    meeting_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AttendanceOut]:
    assert current_user.company_id is not None
    rows = await service.get_attendance(db, meeting_id, current_user.company_id)
    out = []
    for row, name in rows:
        item = AttendanceOut.model_validate(row)
        item.person_name = name
        out.append(item)
    return out


@router.put(
    "/{meeting_id}/attendance",
    response_model=list[AttendanceOut],
    summary="Record attendance",
    description="Recomputes whether the quorum was met (s.174: one third of total "
    "strength or two directors, whichever is higher).",
)
async def set_attendance(
    meeting_id: uuid.UUID,
    rows: list[AttendanceIn],
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AttendanceOut]:
    saved = await service.set_attendance(
        db, meeting_id, [r.model_dump(mode="json") for r in rows], current_user
    )
    return [AttendanceOut.model_validate(r) for r in saved]


# --- Papers and circulation -------------------------------------------------------


@router.post(
    "/{meeting_id}/generate-pack",
    response_model=list[DocumentListItem],
    summary="Generate notice, minutes and attendance",
    description="All three render from the same agenda, which is what keeps their "
    "numbering and wording identical.",
)
async def generate_pack(
    meeting_id: uuid.UUID,
    payload: GeneratePackIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[DocumentListItem]:
    docs = await service.generate_pack(db, meeting_id, current_user, payload.fragments)
    return [DocumentListItem.model_validate(d) for d in docs]


@router.post(
    "/{meeting_id}/circulate",
    response_model=CirculationOut,
    summary="Send the papers to each participant",
    description="Every recipient gets a personal tokenised link and their own timeline: "
    "pending → viewed → acknowledged, with timestamps. Directors do not log in.",
)
async def circulate(
    meeting_id: uuid.UUID,
    payload: CirculateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CirculationOut:
    circ = await circulation_service.circulate_meeting_papers(
        db,
        meeting_id,
        current_user,
        document_ids=payload.document_ids,
        person_ids=payload.person_ids,
        subject=payload.subject,
        message=payload.message,
        send_email=payload.send_email,
    )
    return CirculationOut.model_validate(circ)


@router.get(
    "/circulations/{circulation_id}/recipients",
    response_model=list[CirculationRecipientOut],
    summary="Who has opened and acknowledged",
)
async def recipients(
    circulation_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[CirculationRecipientOut]:
    assert current_user.company_id is not None
    rows = await circulation_service.recipients(db, circulation_id, current_user.company_id)
    out = []
    for row, name in rows:
        item = CirculationRecipientOut.model_validate(row)
        item.person_name = name
        out.append(item)
    return out


@router.get(
    "/circulations/{circulation_id}/audit-export",
    summary="Download the circulation evidence as CSV",
    description="Deliberately flat and boring — it gets attached to a compliance file and "
    "read by someone who has never seen this application.",
)
async def audit_export(
    circulation_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "print"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Response:
    assert current_user.company_id is not None
    csv_text, filename = await circulation_service.audit_export(
        db, circulation_id, current_user.company_id
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
