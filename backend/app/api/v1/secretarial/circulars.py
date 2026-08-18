"""Resolutions by circulation, and certified true copies."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.secretarial_governance import (
    CircularCirculateIn,
    CircularCreate,
    CircularListItem,
    CircularResponse,
    ConsentResponseOut,
    CtcEmailIn,
    CtcIssueIn,
    CtcOut,
    EligibilityOut,
    RatifyIn,
    TallyOut,
)
from app.services.secretarial import circulation as circulation_service
from app.services.secretarial import circular as service
from app.services.secretarial import ctc as ctc_service

router = APIRouter(prefix="/secretarial", tags=["secretarial: governance"])

CIRCULAR_DOCTYPE = "Secretarial Circular Resolution"
CTC_DOCTYPE = "Secretarial Certified True Copy"


@router.post(
    "/circulars",
    response_model=CircularResponse,
    status_code=201,
    summary="Draft a resolution by circulation",
    description="The Rule 5 restricted-matter check runs immediately, so an author finds "
    "out while they are still writing rather than after lining up seven directors.",
)
async def create_circular(
    payload: CircularCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CircularResponse:
    return CircularResponse.model_validate(
        await service.create_circular(
            db,
            current_user,
            entity_id=payload.entity_id,
            title=payload.title,
            resolution_text=payload.resolution_text,
            description=payload.description,
            consent_rule=payload.consent_rule,
            reference_no=payload.reference_no,
        )
    )


@router.get("/circulars", response_model=ListResponse[CircularListItem], summary="List circulars")
async def list_circulars(
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    status: str | None = None,
    fy: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[CircularListItem]:
    assert current_user.company_id is not None
    items, total = await service.list_circulars(
        db, current_user.company_id, entity_id=entity_id, status=status, fy=fy, page=page, page_size=page_size
    )
    return ListResponse(
        items=[CircularListItem.model_validate(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/circulars/{circular_id}", response_model=CircularResponse, summary="Fetch one")
async def get_circular(
    circular_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CircularResponse:
    assert current_user.company_id is not None
    return CircularResponse.model_validate(
        await service.get_circular(db, circular_id, current_user.company_id)
    )


@router.post(
    "/circulars/{circular_id}/check-eligibility",
    response_model=EligibilityOut,
    summary="Re-run the Rule 5 check",
)
async def check_eligibility(
    circular_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EligibilityOut:
    return EligibilityOut.model_validate(
        await service.recheck_eligibility(db, circular_id, current_user)
    )


@router.post(
    "/circulars/{circular_id}/circulate",
    response_model=CircularResponse,
    summary="Send it to the directors",
    description=(
        "Blocked outright if the Rule 5 check flags a restricted matter — passing one of "
        "those by circulation produces a void resolution. A professional may override, "
        "but only with a reason, which is recorded permanently. Directors interested "
        "under s.184 are excluded from the tally denominator, not merely asked to abstain."
    ),
)
async def circulate(
    circular_id: uuid.UUID,
    payload: CircularCirculateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CircularResponse:
    circular = await service.circulate(
        db,
        circular_id,
        current_user,
        person_ids=payload.person_ids,
        interested_person_ids=payload.interested_person_ids,
        expires_at=payload.expires_at,
        override_rule5=payload.override_rule5,
        override_reason=payload.override_reason,
    )
    if payload.send_email:
        await circulation_service.send_consent_links(db, circular_id, current_user)
    return CircularResponse.model_validate(circular)


@router.get(
    "/circulars/{circular_id}/tally",
    response_model=TallyOut,
    summary="The live count against the consent rule",
)
async def tally(
    circular_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyOut:
    assert current_user.company_id is not None
    return TallyOut.model_validate(await service.tally(db, circular_id, current_user.company_id))


@router.get(
    "/circulars/{circular_id}/responses",
    response_model=list[ConsentResponseOut],
    summary="Per-director timeline",
)
async def responses(
    circular_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ConsentResponseOut]:
    assert current_user.company_id is not None
    rows = await service.responses(db, circular_id, current_user.company_id)
    out = []
    for row, name in rows:
        item = ConsentResponseOut.model_validate(row)
        item.person_name = name
        out.append(item)
    return out


@router.post(
    "/circulars/{circular_id}/refresh",
    response_model=CircularResponse,
    summary="Re-settle the outcome",
    description="Applies the tally to the status. The outcome is always computed, never "
    "set by a caller.",
)
async def refresh(
    circular_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CircularResponse:
    return CircularResponse.model_validate(
        await service.refresh_status(db, circular_id, current_user)
    )


@router.post(
    "/circulars/{circular_id}/ratify",
    summary="Place a passed resolution on a board agenda",
    description="If no suitable meeting exists, one is created. A passed circular that "
    "nobody remembers to note is the failure this closes.",
)
async def ratify(
    circular_id: uuid.UUID,
    payload: RatifyIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(CIRCULAR_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    circular, meeting = await service.ratify(
        db, circular_id, current_user, meeting_id=payload.meeting_id
    )
    return {
        "circular": CircularResponse.model_validate(circular).model_dump(mode="json"),
        "meeting_id": str(meeting.id),
        "meeting_scheduled_at": meeting.scheduled_at.isoformat(),
        "created_meeting": payload.meeting_id is None,
    }


# --- Certified true copies --------------------------------------------------------


@router.get(
    "/ctcs/prefill",
    summary="Everything the composer can fill in for itself",
    description="Pulls the resolution text and date from the meeting or passed circular. "
    "Re-typing a resolution into a certificate is how the certified copy stops matching "
    "the minute book.",
)
async def prefill(
    current_user: Annotated[CurrentUser, Depends(require_permission(CTC_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    passage_mode: Annotated[str, Query(pattern="^(board|circular|agm|egm|committee|other)$")],
    meeting_id: uuid.UUID | None = None,
    circular_id: uuid.UUID | None = None,
    agenda_item_id: uuid.UUID | None = None,
) -> dict:
    assert current_user.company_id is not None
    out = await ctc_service.prefill(
        db,
        current_user.company_id,
        passage_mode=passage_mode,
        meeting_id=meeting_id,
        circular_id=circular_id,
        agenda_item_id=agenda_item_id,
    )
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in out.items()}


@router.post(
    "/ctcs",
    response_model=CtcOut,
    status_code=201,
    summary="Issue a certified true copy",
    description="Append-only. A correction is a new issuance naming the one it replaces "
    "and why — a certified copy that quietly changed after issue is worse than none.",
)
async def issue_ctc(
    payload: CtcIssueIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(CTC_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CtcOut:
    ctc = await ctc_service.issue(
        db,
        current_user,
        entity_id=payload.entity_id,
        passage_mode=payload.passage_mode,
        resolution_text=payload.resolution_text,
        certified_on=payload.certified_on,
        passed_on=payload.passed_on,
        meeting_id=payload.meeting_id,
        circular_id=payload.circular_id,
        place=payload.place,
        issued_to=payload.issued_to,
        purpose=payload.purpose,
        signatories=[s.model_dump() for s in payload.signatories],
        supersedes_id=payload.supersedes_id,
        superseded_reason=payload.superseded_reason,
    )
    return CtcOut.model_validate(ctc)


@router.get(
    "/ctcs",
    response_model=ListResponse[CtcOut],
    summary="The issuance log",
    description="Per-entity register of what was certified, to whom and when.",
)
async def issuance_log(
    current_user: Annotated[CurrentUser, Depends(require_permission(CTC_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    search: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[CtcOut]:
    assert current_user.company_id is not None
    items, total = await ctc_service.issuance_log(
        db, current_user.company_id, entity_id=entity_id, search=search, page=page, page_size=page_size
    )
    return ListResponse(
        items=[CtcOut.model_validate(c) for c in items], total=total, page=page, page_size=page_size
    )


@router.post(
    "/ctcs/{ctc_id}/email", summary="Email the certificate with its PDF attached"
)
async def email_ctc(
    ctc_id: uuid.UUID,
    payload: CtcEmailIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(CTC_DOCTYPE, "email"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    status = await ctc_service.email_ctc(
        db, ctc_id, current_user, to=payload.to, message=payload.message
    )
    return {"status": status, "recipients": payload.to}
