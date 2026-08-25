"""Share capital, certificates, capital events and the s.186 register (Phase 5).

Thin, as every router in this codebase is: the state machines, the distinctive-number
allocator and the two statutory checks all live in
``services/secretarial/{capital,s186}.py``.

One convention worth naming: the write endpoints commit, because a transfer posting
touches the cap table, two certificates and the members register in a single transaction
and half of that reaching the database would be worse than none of it.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.secretarial_capital import (
    CapitalEventAllotIn,
    CapitalEventIn,
    CapitalEventOut,
    CapitalEventUpdateIn,
    CapTableOut,
    CertificateCancelIn,
    CertificateIssueIn,
    CertificateOut,
    DividendCheckOut,
    S186EntryIn,
    S186EntryOut,
    S186EntryUpdateIn,
    S186LimitIn,
    S186LimitOut,
    TransferApproveIn,
    TransferCreateIn,
    TransferOut,
    TransferPostIn,
    TransferRevertIn,
    TransferUpdateIn,
)
from app.services.secretarial import capital as service
from app.services.secretarial import s186 as s186_service
from app.services.secretarial.common import current_fy, get_entity

router = APIRouter(prefix="/secretarial/capital", tags=["secretarial: capital"])

TRANSFER_DOCTYPE = "Secretarial Share Transfer"
CERTIFICATE_DOCTYPE = "Secretarial Share Certificate"
EVENT_DOCTYPE = "Secretarial Capital Event"
S186_DOCTYPE = "Secretarial s.186 Entry"


# --- Cap table --------------------------------------------------------------------


@router.get(
    "/cap-table",
    response_model=CapTableOut,
    summary="Who holds what",
    description=(
        "Built from the live share certificates. `source` says what is behind the "
        "numbers — the share ledger in this company's books, the certificate register "
        "alone (a client whose books are kept elsewhere), or the declared opening "
        "holdings where nothing has been certificated yet."
    ),
)
async def cap_table(
    entity_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(CERTIFICATE_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    share_class: str | None = None,
) -> CapTableOut:
    assert current_user.company_id is not None
    entity = await get_entity(db, entity_id, current_user.company_id)
    return CapTableOut.model_validate(await service.cap_table(db, entity, share_class=share_class))


# --- Certificates -----------------------------------------------------------------


@router.post(
    "/certificates",
    response_model=CertificateOut,
    status_code=201,
    summary="Issue a share certificate (SH-1)",
    description=(
        "The distinctive number range is allocated by the server from a locked counter, "
        "never supplied by the caller — two certificates claiming the same distinctive "
        "numbers means two people hold paper for the same shares."
    ),
)
async def issue_certificate(
    payload: CertificateIssueIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(CERTIFICATE_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CertificateOut:
    certificate = await service.issue_certificate(db, current_user, **payload.model_dump())
    await db.commit()
    return CertificateOut.model_validate(certificate)


@router.get(
    "/certificates", response_model=ListResponse[CertificateOut], summary="Certificate register"
)
async def list_certificates(
    current_user: Annotated[CurrentUser, Depends(require_permission(CERTIFICATE_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    member_id: uuid.UUID | None = None,
    status: str | None = None,
    share_class: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[CertificateOut]:
    assert current_user.company_id is not None
    items, total = await service.list_certificates(
        db,
        current_user.company_id,
        entity_id=entity_id,
        member_id=member_id,
        status=status,
        share_class=share_class,
        page=page,
        page_size=page_size,
    )
    return ListResponse(
        items=[CertificateOut.model_validate(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/certificates/{certificate_id}/cancel",
    summary="Cancel a certificate, reissuing over the same numbers",
    description=(
        "A certificate is never edited. Cancelling records the date and the reason, and "
        "the replacement carries the same distinctive range — the range identifies the "
        "shares, not the paper. Pass `reissue: false` for a surrender, where no "
        "replacement is due."
    ),
)
async def cancel_certificate(
    certificate_id: uuid.UUID,
    payload: CertificateCancelIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(CERTIFICATE_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    cancelled, replacement = await service.cancel_certificate(
        db, current_user, certificate_id, **payload.model_dump()
    )
    await db.commit()
    return {
        "cancelled": CertificateOut.model_validate(cancelled),
        "replacement": CertificateOut.model_validate(replacement) if replacement else None,
    }


# --- SH-4 transfers ---------------------------------------------------------------


@router.post(
    "/transfers",
    response_model=TransferOut,
    status_code=201,
    summary="Lodge a share transfer (SH-4)",
    description="Records the instrument as received. Nothing moves until the board approves it.",
)
async def create_transfer(
    payload: TransferCreateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(TRANSFER_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TransferOut:
    transfer = await service.create_transfer(db, current_user, **payload.model_dump())
    await db.commit()
    return TransferOut.model_validate(transfer)


@router.get("/transfers", response_model=ListResponse[TransferOut], summary="Register of transfers")
async def list_transfers(
    current_user: Annotated[CurrentUser, Depends(require_permission(TRANSFER_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[TransferOut]:
    assert current_user.company_id is not None
    items, total = await service.list_transfers(
        db, current_user.company_id, entity_id=entity_id, status=status, page=page, page_size=page_size
    )
    return ListResponse(
        items=[TransferOut.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/transfers/{transfer_id}", response_model=TransferOut, summary="One transfer")
async def get_transfer(
    transfer_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(TRANSFER_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TransferOut:
    assert current_user.company_id is not None
    return TransferOut.model_validate(
        await service.get_transfer(db, transfer_id, current_user.company_id)
    )


@router.patch("/transfers/{transfer_id}", response_model=TransferOut, summary="Edit a draft transfer")
async def update_transfer(
    transfer_id: uuid.UUID,
    payload: TransferUpdateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(TRANSFER_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TransferOut:
    transfer = await service.update_transfer(
        db, current_user, transfer_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return TransferOut.model_validate(transfer)


@router.post(
    "/transfers/{transfer_id}/approve",
    response_model=TransferOut,
    summary="Attach the board approval (s.56)",
    description="Refused if the meeting has not been held — a transfer approved by a "
    "meeting that has not happened is a back-dated approval.",
)
async def approve_transfer(
    transfer_id: uuid.UUID,
    payload: TransferApproveIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(TRANSFER_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TransferOut:
    transfer = await service.approve_transfer(db, current_user, transfer_id, **payload.model_dump())
    await db.commit()
    return TransferOut.model_validate(transfer)


@router.post(
    "/transfers/{transfer_id}/post",
    response_model=TransferOut,
    summary="Move the shares",
    description=(
        "One transaction: the cap-table movement (where the books are here), the "
        "transferor's certificate cancelled, and the transferee's issued over the same "
        "distinctive range."
    ),
)
async def post_transfer(
    transfer_id: uuid.UUID,
    payload: TransferPostIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(TRANSFER_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TransferOut:
    transfer = await service.post_transfer(
        db,
        current_user,
        transfer_id,
        posted_on=payload.posted_on,
        surrender_certificate_id=payload.surrender_certificate_id,
        issue_certificate_to_transferee=payload.issue_certificate,
    )
    await db.commit()
    return TransferOut.model_validate(transfer)


@router.post(
    "/transfers/{transfer_id}/revert",
    response_model=TransferOut,
    summary="Revert a posted transfer, with a reason",
    description=(
        "Realigns the register and leaves the entire trail in place: the instrument, both "
        "certificates and the reversal all stay readable. The reason is mandatory in the "
        "service and again in the database."
    ),
)
async def revert_transfer(
    transfer_id: uuid.UUID,
    payload: TransferRevertIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(TRANSFER_DOCTYPE, "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TransferOut:
    transfer = await service.revert_transfer(
        db, current_user, transfer_id, reason=payload.reason, reverted_on=payload.reverted_on
    )
    await db.commit()
    return TransferOut.model_validate(transfer)


# --- Capital events ---------------------------------------------------------------


@router.post(
    "/events",
    response_model=CapitalEventOut,
    status_code=201,
    summary="Open a right issue, placement, ESOP grant or dividend",
)
async def create_event(
    payload: CapitalEventIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(EVENT_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CapitalEventOut:
    event = await service.create_event(db, current_user, payload.model_dump())
    await db.commit()
    return CapitalEventOut.model_validate(event)


@router.get("/events", response_model=ListResponse[CapitalEventOut], summary="Capital events")
async def list_events(
    current_user: Annotated[CurrentUser, Depends(require_permission(EVENT_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    event_type: str | None = None,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[CapitalEventOut]:
    assert current_user.company_id is not None
    items, total = await service.list_events(
        db,
        current_user.company_id,
        entity_id=entity_id,
        event_type=event_type,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ListResponse(
        items=[CapitalEventOut.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/events/{event_id}", response_model=CapitalEventOut, summary="Edit an open event")
async def update_event(
    event_id: uuid.UUID,
    payload: CapitalEventUpdateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(EVENT_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CapitalEventOut:
    event = await service.update_event(
        db, current_user, event_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return CapitalEventOut.model_validate(event)


@router.post(
    "/events/{event_id}/approve",
    response_model=CapitalEventOut,
    summary="Mark the offer authorised",
    description="A dividend is checked against s.123 first, and the verdict is frozen onto "
    "the event — what an inspection asks is what the board knew at the time.",
)
async def approve_event(
    event_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(EVENT_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CapitalEventOut:
    event = await service.approve_event(db, current_user, event_id)
    await db.commit()
    return CapitalEventOut.model_validate(event)


@router.post(
    "/events/{event_id}/allot",
    summary="Allot the shares and cut the certificates",
    description="Issues one certificate per allottee in the same transaction. An allotment "
    "whose certificates were never cut is the most common gap in a real minute book.",
)
async def allot_event(
    event_id: uuid.UUID,
    payload: CapitalEventAllotIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(EVENT_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    event, certificates = await service.allot_event(
        db,
        current_user,
        event_id,
        allotted_on=payload.allotted_on,
        shares_allotted=payload.shares_allotted,
        issue_certificates=payload.issue_certificates,
    )
    await db.commit()
    return {
        "event": CapitalEventOut.model_validate(event),
        "certificates": [CertificateOut.model_validate(c) for c in certificates],
    }


@router.post("/events/{event_id}/cancel", response_model=CapitalEventOut, summary="Cancel an event")
async def cancel_event(
    event_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(EVENT_DOCTYPE, "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    reason: str | None = None,
) -> CapitalEventOut:
    event = await service.cancel_event(db, current_user, event_id, reason=reason)
    await db.commit()
    return CapitalEventOut.model_validate(event)


# --- s.186 ------------------------------------------------------------------------


@router.get(
    "/s186/limit",
    response_model=S186LimitOut,
    summary="The s.186(2) ceiling, and how much of it is used",
    description=(
        "The higher of 60% of (paid-up capital + free reserves + securities premium) and "
        "100% of (free reserves + securities premium). A special resolution under "
        "s.186(3) lifts it. Where a figure is missing the verdict is `unknown`, never "
        "`within` — nobody should be told they are clear because a number is absent."
    ),
)
async def s186_limit(
    entity_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(S186_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    fy: str | None = None,
    refresh: bool = False,
) -> S186LimitOut:
    assert current_user.company_id is not None
    entity = await get_entity(db, entity_id, current_user.company_id)
    from datetime import date as _date

    status = await s186_service.limit_status(
        db, entity, fy or current_fy(_date.today(), entity.fy_end_mmdd), refresh=refresh
    )
    await db.commit()
    return S186LimitOut.model_validate(status)


@router.put(
    "/s186/limit",
    response_model=S186LimitOut,
    summary="Type the figures in, or link the special resolution",
)
async def set_s186_limit(
    entity_id: uuid.UUID,
    payload: S186LimitIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(S186_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> S186LimitOut:
    assert current_user.company_id is not None
    await s186_service.set_limit_overrides(db, current_user, entity_id, payload.model_dump())
    entity = await get_entity(db, entity_id, current_user.company_id)
    status = await s186_service.limit_status(db, entity, payload.fy)
    await db.commit()
    return S186LimitOut.model_validate(status)


@router.post(
    "/s186/entries",
    response_model=S186EntryOut,
    status_code=201,
    summary="Add to the s.186 register (MBP-2)",
    description="Refused above the ceiling unless a special resolution is on file, and the "
    "refusal carries the numbers so it can be acted on.",
)
async def create_s186_entry(
    payload: S186EntryIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(S186_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> S186EntryOut:
    entry = await s186_service.create_entry(db, current_user, payload.model_dump())
    await db.commit()
    return S186EntryOut.model_validate(entry)


@router.get(
    "/s186/entries", response_model=ListResponse[S186EntryOut], summary="The s.186 register"
)
async def list_s186_entries(
    current_user: Annotated[CurrentUser, Depends(require_permission(S186_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    entry_type: str | None = None,
    status: str | None = None,
    fy: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[S186EntryOut]:
    assert current_user.company_id is not None
    items, total = await s186_service.list_entries(
        db,
        current_user.company_id,
        entity_id=entity_id,
        entry_type=entry_type,
        status=status,
        fy=fy,
        page=page,
        page_size=page_size,
    )
    return ListResponse(
        items=[S186EntryOut.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/s186/entries/{entry_id}", response_model=S186EntryOut, summary="Record repayment or a revision"
)
async def update_s186_entry(
    entry_id: uuid.UUID,
    payload: S186EntryUpdateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(S186_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> S186EntryOut:
    entry = await s186_service.update_entry(
        db, current_user, entry_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return S186EntryOut.model_validate(entry)


# --- Dividend ---------------------------------------------------------------------


@router.get(
    "/dividend-check",
    response_model=DividendCheckOut,
    summary="Is there distributable profit for this dividend? (s.123)",
    description=(
        "Profit for the year plus accumulated profits, less accumulated losses. Returns "
        "`unknown` rather than `ok` when a figure is missing — an unlawful dividend is "
        "recoverable from the directors personally, so a false 'fine' costs far more than "
        "a false 'check this'. Transfer to reserves and the s.123(3) interim ceiling are "
        "not modelled, and the response says so."
    ),
)
async def dividend_check(
    entity_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(EVENT_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    fy: str | None = None,
    proposed: float | None = None,
) -> DividendCheckOut:
    from decimal import Decimal as _Decimal

    assert current_user.company_id is not None
    entity = await get_entity(db, entity_id, current_user.company_id)
    check = await s186_service.dividend_check(
        db,
        entity,
        fy or "",
        proposed=_Decimal(str(proposed)) if proposed is not None else None,
    )
    await db.commit()
    return DividendCheckOut.model_validate(check)
