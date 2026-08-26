"""Share transfers, certificates and capital events — Phase 5.

The rule this whole file is arranged around: **there is one cap table, and it is the
accounts one.** ``app/services/share_transfer.py`` already derives every holder's balance
from an append-only ledger. Nothing here keeps a second copy of who owns what. What it
keeps is the legal overlay — the SH-4 instrument, the board approval behind it, the
distinctive numbers, and the certificate that gets cancelled and reissued when shares move.

Three things are done in one transaction when a transfer is posted, because doing any one
of them without the others leaves the register lying:

1. the accounts-side movement is created and submitted (skipped for a managed client,
   which has no books here),
2. the transferor's certificate is cancelled,
3. the transferee's certificate is issued over the transferred distinctive range.

A revert undoes exactly those three and records why. It does not delete anything: the
instrument, both certificates and the reversal all stay readable, because "this transfer
was reversed on 12 June because the consideration never cleared" is the answer an
inspection wants, and a deleted row cannot give it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.accounts import ShareTransfer
from app.models.secretarial import (
    SecretarialCapitalEvent,
    SecretarialCircular,
    SecretarialDistinctiveSequence,
    SecretarialEntity,
    SecretarialMeeting,
    SecretarialMember,
    SecretarialShareCertificate,
    SecretarialShareTransferDetail,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.secretarial.common import get_entity

TRANSFER_DOCTYPE = "Secretarial Share Transfer"
CERTIFICATE_DOCTYPE = "Secretarial Share Certificate"
EVENT_DOCTYPE = "Secretarial Capital Event"

# Which transitions the state machine permits. Everything else is a 422 that names both
# states, so the message is useful rather than "invalid transition".
_TRANSFER_NEXT: dict[str, tuple[str, ...]] = {
    "draft": ("board_approved",),
    "board_approved": ("issued_posted", "draft"),
    "issued_posted": ("reverted",),
    "reverted": (),
}

_EVENT_NEXT: dict[str, tuple[str, ...]] = {
    "draft": ("approved", "cancelled"),
    "approved": ("allotted", "cancelled"),
    "allotted": (),
    "cancelled": (),
}


# --- Distinctive numbers ----------------------------------------------------------


async def allocate_distinctive_range(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    entity_id: uuid.UUID,
    share_class: str,
    count: int,
) -> tuple[int, int]:
    """Take the next ``count`` distinctive numbers. Gap-free, serialised.

    ``SELECT … FOR UPDATE`` on the counter row inside the caller's transaction — the same
    mechanism as the minutes book, for the same reason. Two allotments reading the same
    maximum would issue overlapping ranges, and overlapping distinctive numbers mean two
    certificates claim the same shares. The exclusion constraint in `0100` would catch
    it, but catching it as a 500 in front of whoever pressed the button is not the plan.
    """
    if count < 1:
        raise ValidationError("A certificate has to cover at least one share")

    row = (
        await db.execute(
            select(SecretarialDistinctiveSequence)
            .where(
                SecretarialDistinctiveSequence.entity_id == entity_id,
                SecretarialDistinctiveSequence.share_class == share_class,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is None:
        row = SecretarialDistinctiveSequence(
            company_id=company_id, entity_id=entity_id, share_class=share_class, next_number=1
        )
        db.add(row)
        await db.flush()

    start = row.next_number
    end = start + count - 1
    row.next_number = end + 1
    await db.flush()
    return start, end


async def peek_distinctive(
    db: AsyncSession, *, entity_id: uuid.UUID, share_class: str
) -> int:
    """The next unallocated number, for display. Consumes nothing."""
    row = await db.scalar(
        select(SecretarialDistinctiveSequence.next_number).where(
            SecretarialDistinctiveSequence.entity_id == entity_id,
            SecretarialDistinctiveSequence.share_class == share_class,
        )
    )
    return int(row) if row else 1


async def _reserve_up_to(
    db: AsyncSession, *, company_id: uuid.UUID, entity_id: uuid.UUID, share_class: str, upto: int
) -> None:
    """Push the counter past ``upto`` after an out-of-band range was recorded.

    Only the seeder and a data import take this path: they place certificates at known
    historical ranges, and the counter has to end up ahead of them or the next allotment
    collides.
    """
    row = (
        await db.execute(
            select(SecretarialDistinctiveSequence)
            .where(
                SecretarialDistinctiveSequence.entity_id == entity_id,
                SecretarialDistinctiveSequence.share_class == share_class,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = SecretarialDistinctiveSequence(
            company_id=company_id, entity_id=entity_id, share_class=share_class, next_number=upto + 1
        )
        db.add(row)
    elif row.next_number <= upto:
        row.next_number = upto + 1
    await db.flush()


# --- Certificates -----------------------------------------------------------------


async def get_certificate(
    db: AsyncSession, certificate_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialShareCertificate:
    row = await db.get(SecretarialShareCertificate, certificate_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Share certificate not found")
    return row


async def _next_certificate_no(db: AsyncSession, entity_id: uuid.UUID) -> int:
    # Serialised on the parent entity, exactly as CTC issuance is: `unique(entity_id,
    # certificate_no)` would otherwise turn a race into an IntegrityError on screen.
    await db.execute(
        select(SecretarialEntity.id).where(SecretarialEntity.id == entity_id).with_for_update()
    )
    current = (
        await db.execute(
            select(func.coalesce(func.max(SecretarialShareCertificate.certificate_no), 0)).where(
                SecretarialShareCertificate.entity_id == entity_id
            )
        )
    ).scalar_one()
    return int(current) + 1


async def issue_certificate(
    db: AsyncSession,
    user: CurrentUser,
    *,
    entity_id: uuid.UUID,
    no_of_shares: Decimal,
    share_class: str = "Equity",
    member_id: uuid.UUID | None = None,
    holder_name: str | None = None,
    folio_no: str | None = None,
    face_value: Decimal | None = None,
    amount_paid_up: Decimal | None = None,
    issue_type: str = "original",
    issued_on: date | None = None,
    deferred: bool = False,
    capital_event_id: uuid.UUID | None = None,
    supersedes_id: uuid.UUID | None = None,
    distinctive: tuple[int, int] | None = None,
    notes: str | None = None,
) -> SecretarialShareCertificate:
    """Issue one certificate, allocating its distinctive range unless one is given.

    ``distinctive`` is passed only when the range already belongs to these shares — a
    transfer or a replacement carries the original numbers forward, because the range
    identifies the shares themselves and not the piece of paper.
    """
    assert user.company_id is not None
    await get_entity(db, entity_id, user.company_id)

    member = None
    if member_id:
        member = await db.get(SecretarialMember, member_id)
        if member is None or member.entity_id != entity_id:
            raise NotFoundError("Member not found on this entity")
        holder_name = holder_name or member.member_name
        folio_no = folio_no or member.folio_no

    if not holder_name:
        raise ValidationError("A certificate needs a holder", field="holder_name")

    shares = int(Decimal(no_of_shares))
    if Decimal(shares) != Decimal(no_of_shares):
        raise ValidationError("Distinctive numbers only exist for whole shares", field="no_of_shares")

    if distinctive is None:
        distinctive = await allocate_distinctive_range(
            db,
            company_id=user.company_id,
            entity_id=entity_id,
            share_class=share_class,
            count=shares,
        )
    else:
        span = distinctive[1] - distinctive[0] + 1
        if span != shares:
            raise ValidationError(
                f"Distinctive range {distinctive[0]}–{distinctive[1]} covers {span} shares, "
                f"but the certificate is for {shares}"
            )
        await _reserve_up_to(
            db,
            company_id=user.company_id,
            entity_id=entity_id,
            share_class=share_class,
            upto=distinctive[1],
        )

    certificate = SecretarialShareCertificate(
        company_id=user.company_id,
        entity_id=entity_id,
        certificate_no=await _next_certificate_no(db, entity_id),
        member_id=member_id,
        holder_name=holder_name,
        folio_no=folio_no,
        share_class=share_class,
        no_of_shares=Decimal(shares),
        face_value=face_value if face_value is not None else (member.nominal_value if member else None),
        amount_paid_up=amount_paid_up,
        distinctive_from=distinctive[0],
        distinctive_to=distinctive[1],
        issue_type=issue_type,
        issued_on=issued_on or (None if deferred else date.today()),
        deferred=deferred,
        status="issued",
        supersedes_id=supersedes_id,
        capital_event_id=capital_event_id,
        notes=notes,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(certificate)
    await db.flush()

    await log_audit(
        db,
        doctype=CERTIFICATE_DOCTYPE,
        document_id=certificate.id,
        action="issue",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "certificate_no": certificate.certificate_no,
            "holder": holder_name,
            "shares": str(certificate.no_of_shares),
            "distinctive": f"{distinctive[0]}-{distinctive[1]}",
            "issue_type": issue_type,
        },
    )
    return certificate


async def cancel_certificate(
    db: AsyncSession,
    user: CurrentUser,
    certificate_id: uuid.UUID,
    *,
    reason: str,
    cancelled_on: date | None = None,
    reissue: bool = True,
    reissue_to_member_id: uuid.UUID | None = None,
    reissue_issue_type: str = "duplicate",
) -> tuple[SecretarialShareCertificate, SecretarialShareCertificate | None]:
    """Cancel a certificate, optionally reissuing over the same distinctive range.

    Cancelling is a state change with a date and a stated reason, never a delete — the
    cancelled certificate is what proves the outstanding one is legitimate.

    ``reissue=False`` is the surrender case: shares bought back or consolidated away, where
    no replacement paper is due and the distinctive range simply stops being live.
    """
    assert user.company_id is not None
    certificate = await get_certificate(db, certificate_id, user.company_id)
    if certificate.status != "issued":
        raise ValidationError(
            f"Certificate {certificate.certificate_no} is already {certificate.status}"
        )
    if not reason or not reason.strip():
        raise ValidationError("Say why the certificate is being cancelled", field="reason")

    certificate.status = "cancelled"
    certificate.cancelled_on = cancelled_on or date.today()
    certificate.cancelled_reason = reason.strip()
    certificate.modified_by = user.id
    # Flush the cancellation before the replacement is inserted: the exclusion constraint
    # is `WHERE status = 'issued'`, so the old row has to leave that predicate first or
    # the reissue collides with the certificate it replaces.
    await db.flush()

    replacement = None
    if reissue:
        holder = reissue_to_member_id or certificate.member_id
        replacement = await issue_certificate(
            db,
            user,
            entity_id=certificate.entity_id,
            no_of_shares=certificate.no_of_shares,
            share_class=certificate.share_class,
            member_id=holder,
            holder_name=None if holder else certificate.holder_name,
            folio_no=certificate.folio_no,
            face_value=certificate.face_value,
            amount_paid_up=certificate.amount_paid_up,
            issue_type=reissue_issue_type,
            issued_on=cancelled_on or date.today(),
            capital_event_id=certificate.capital_event_id,
            supersedes_id=certificate.id,
            distinctive=(certificate.distinctive_from, certificate.distinctive_to),
            notes=f"Replaces certificate {certificate.certificate_no}: {reason.strip()}",
        )

    await log_audit(
        db,
        doctype=CERTIFICATE_DOCTYPE,
        document_id=certificate.id,
        action="cancel",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "reason": reason.strip(),
            "replacement_no": replacement.certificate_no if replacement else None,
        },
    )
    return certificate, replacement


async def list_certificates(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    member_id: uuid.UUID | None = None,
    status: str | None = None,
    share_class: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialShareCertificate], int]:
    stmt = select(SecretarialShareCertificate).where(
        SecretarialShareCertificate.company_id == company_id
    )
    if entity_id:
        stmt = stmt.where(SecretarialShareCertificate.entity_id == entity_id)
    if member_id:
        stmt = stmt.where(SecretarialShareCertificate.member_id == member_id)
    if status:
        stmt = stmt.where(SecretarialShareCertificate.status == status)
    if share_class:
        stmt = stmt.where(SecretarialShareCertificate.share_class == share_class)
    stmt = stmt.order_by(SecretarialShareCertificate.certificate_no.desc())
    return await paginate(db, stmt, page, page_size)


# --- SH-4 transfers ---------------------------------------------------------------


async def get_transfer(
    db: AsyncSession, transfer_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialShareTransferDetail:
    row = await db.get(SecretarialShareTransferDetail, transfer_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Share transfer not found")
    return row


def _check_transition(current: str, target: str) -> None:
    if target not in _TRANSFER_NEXT.get(current, ()):
        raise ValidationError(
            f"A transfer that is {current} cannot become {target}. "
            f"From {current} the next step is: "
            + (", ".join(_TRANSFER_NEXT.get(current, ())) or "nothing — it is final")
        )


async def _resolve_party(
    db: AsyncSession, entity_id: uuid.UUID, member_id: uuid.UUID | None, name: str | None
) -> tuple[SecretarialMember | None, str, str | None]:
    if member_id:
        member = await db.get(SecretarialMember, member_id)
        if member is None or member.entity_id != entity_id:
            raise NotFoundError("Member not found on this entity")
        return member, member.member_name, member.folio_no
    if not name:
        raise ValidationError("Name a member or give a name")
    return None, name, None


async def create_transfer(
    db: AsyncSession,
    user: CurrentUser,
    *,
    entity_id: uuid.UUID,
    no_of_shares: Decimal,
    executed_on: date,
    share_class: str = "Equity",
    transferor_member_id: uuid.UUID | None = None,
    transferee_member_id: uuid.UUID | None = None,
    transferor_name: str | None = None,
    transferee_name: str | None = None,
    face_value: Decimal | None = None,
    consideration: Decimal = Decimal("0"),
    stamp_duty: Decimal | None = None,
    lodged_on: date | None = None,
    notes: str | None = None,
) -> SecretarialShareTransferDetail:
    """Record the SH-4 as lodged. Nothing moves yet — the board has not approved it."""
    assert user.company_id is not None
    await get_entity(db, entity_id, user.company_id)

    _, from_name, from_folio = await _resolve_party(
        db, entity_id, transferor_member_id, transferor_name
    )
    _, to_name, to_folio = await _resolve_party(db, entity_id, transferee_member_id, transferee_name)

    if transferor_member_id and transferor_member_id == transferee_member_id:
        raise ValidationError("A member cannot transfer shares to themselves")

    await db.execute(
        select(SecretarialEntity.id).where(SecretarialEntity.id == entity_id).with_for_update()
    )
    next_no = (
        await db.execute(
            select(
                func.coalesce(func.max(SecretarialShareTransferDetail.instrument_no), 0)
            ).where(SecretarialShareTransferDetail.entity_id == entity_id)
        )
    ).scalar_one() + 1

    transfer = SecretarialShareTransferDetail(
        company_id=user.company_id,
        entity_id=entity_id,
        instrument_no=next_no,
        transferor_member_id=transferor_member_id,
        transferee_member_id=transferee_member_id,
        transferor_name=from_name,
        transferee_name=to_name,
        transferor_folio=from_folio,
        transferee_folio=to_folio,
        share_class=share_class,
        no_of_shares=no_of_shares,
        face_value=face_value,
        consideration=consideration,
        stamp_duty=stamp_duty,
        executed_on=executed_on,
        lodged_on=lodged_on,
        status="draft",
        notes=notes,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(transfer)
    await db.flush()

    await log_audit(
        db,
        doctype=TRANSFER_DOCTYPE,
        document_id=transfer.id,
        action="create",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "instrument_no": next_no,
            "from": from_name,
            "to": to_name,
            "shares": str(no_of_shares),
        },
    )
    return transfer


async def update_transfer(
    db: AsyncSession, user: CurrentUser, transfer_id: uuid.UUID, changes: dict[str, Any]
) -> SecretarialShareTransferDetail:
    assert user.company_id is not None
    transfer = await get_transfer(db, transfer_id, user.company_id)
    if transfer.status != "draft":
        raise ValidationError(
            "Only a draft instrument can be edited. Approve, post or revert it instead."
        )
    for field, value in changes.items():
        if value is not None and hasattr(transfer, field):
            setattr(transfer, field, value)
    transfer.modified_by = user.id
    await db.flush()
    return transfer


async def approve_transfer(
    db: AsyncSession,
    user: CurrentUser,
    transfer_id: uuid.UUID,
    *,
    board_meeting_id: uuid.UUID | None = None,
    board_agenda_item_id: uuid.UUID | None = None,
    circular_id: uuid.UUID | None = None,
    approved_on: date | None = None,
) -> SecretarialShareTransferDetail:
    """Attach the board decision that registers the transfer under s.56.

    The authority is checked to exist and to be a *decision*, not merely a scheduled
    meeting: a transfer approved by a meeting that has not been held yet is a
    back-dated approval, which is the thing the register exists to make visible.
    """
    assert user.company_id is not None
    transfer = await get_transfer(db, transfer_id, user.company_id)
    _check_transition(transfer.status, "board_approved")

    if board_meeting_id:
        meeting = await db.get(SecretarialMeeting, board_meeting_id)
        if meeting is None or meeting.company_id != user.company_id:
            raise NotFoundError("Meeting not found")
        if meeting.status in ("draft", "scheduled", "circulated", "cancelled"):
            raise ValidationError(
                f"That meeting has not been held yet (it is {meeting.status}). A transfer "
                "cannot be approved by a meeting that has not happened."
            )
        transfer.board_meeting_id = board_meeting_id
        transfer.board_agenda_item_id = board_agenda_item_id
        approved_on = approved_on or (meeting.held_at.date() if meeting.held_at else None)
    elif circular_id:
        circular = await db.get(SecretarialCircular, circular_id)
        if circular is None or circular.company_id != user.company_id:
            raise NotFoundError("Circular resolution not found")
        if circular.status not in ("passed", "ratified"):
            raise ValidationError(
                f"That circular resolution is {circular.status}, not passed."
            )
        transfer.circular_id = circular_id
        approved_on = approved_on or (circular.decided_at.date() if circular.decided_at else None)
    else:
        raise ValidationError(
            "A transfer is approved by a board meeting or a passed circular resolution"
        )

    transfer.status = "board_approved"
    transfer.approved_on = approved_on or date.today()
    transfer.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=TRANSFER_DOCTYPE,
        document_id=transfer.id,
        action="approve",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"approved_on": str(transfer.approved_on)},
    )
    return transfer


async def post_transfer(
    db: AsyncSession,
    user: CurrentUser,
    transfer_id: uuid.UUID,
    *,
    posted_on: date | None = None,
    surrender_certificate_id: uuid.UUID | None = None,
    issue_certificate_to_transferee: bool = True,
) -> SecretarialShareTransferDetail:
    """Move the shares: cap table, old certificate, new certificate — one transaction.

    Where the entity's books are in this account the accounts-side ``ShareTransfer`` is
    created and submitted, so the derived balances change there and nowhere else. Where
    they are not, the certificates alone carry the register, and ``share_transfer_id``
    stays null to say which mode this row is in.
    """
    assert user.company_id is not None
    transfer = await get_transfer(db, transfer_id, user.company_id)
    _check_transition(transfer.status, "issued_posted")

    entity = await get_entity(db, transfer.entity_id, user.company_id)
    shares = int(Decimal(transfer.no_of_shares))
    if Decimal(shares) != Decimal(transfer.no_of_shares):
        raise ValidationError("Distinctive numbers only exist for whole shares")

    # 1. The certificate being given up decides the distinctive range that travels.
    surrendered = None
    if surrender_certificate_id:
        surrendered = await get_certificate(db, surrender_certificate_id, user.company_id)
        if surrendered.entity_id != transfer.entity_id:
            raise NotFoundError("That certificate belongs to another entity")
        if surrendered.status != "issued":
            raise ValidationError(
                f"Certificate {surrendered.certificate_no} is {surrendered.status} and cannot be surrendered"
            )
        if Decimal(surrendered.no_of_shares) != Decimal(transfer.no_of_shares):
            raise ValidationError(
                f"Certificate {surrendered.certificate_no} covers "
                f"{surrendered.no_of_shares} shares but the transfer is for "
                f"{transfer.no_of_shares}. Split the certificate first."
            )
        surrendered.status = "cancelled"
        surrendered.cancelled_on = posted_on or date.today()
        surrendered.cancelled_reason = (
            f"Transferred to {transfer.transferee_name} under instrument "
            f"SH-4/{transfer.instrument_no}"
        )
        surrendered.modified_by = user.id
        await db.flush()  # leave the exclusion predicate before the replacement lands
        transfer.distinctive_from = surrendered.distinctive_from
        transfer.distinctive_to = surrendered.distinctive_to
        transfer.surrendered_certificate_id = surrendered.id

    # 2. The cap-table movement, where there is a cap table.
    if entity.linked_company_id:
        transfer.share_transfer_id = await _post_accounts_transfer(db, user, transfer, entity)

    # 3. The transferee's certificate, over the same numbers where they travelled.
    if issue_certificate_to_transferee:
        issued = await issue_certificate(
            db,
            user,
            entity_id=transfer.entity_id,
            no_of_shares=transfer.no_of_shares,
            share_class=transfer.share_class,
            member_id=transfer.transferee_member_id,
            holder_name=transfer.transferee_name if not transfer.transferee_member_id else None,
            folio_no=transfer.transferee_folio,
            face_value=transfer.face_value,
            issue_type="renewed" if surrendered else "original",
            issued_on=posted_on or date.today(),
            supersedes_id=surrendered.id if surrendered else None,
            distinctive=(
                (surrendered.distinctive_from, surrendered.distinctive_to) if surrendered else None
            ),
            notes=f"Issued on transfer under instrument SH-4/{transfer.instrument_no}",
        )
        transfer.issued_certificate_id = issued.id
        if not surrendered:
            transfer.distinctive_from = issued.distinctive_from
            transfer.distinctive_to = issued.distinctive_to

    # 4. Realign the Phase-1 declared holdings so the members register does not contradict
    #    the certificates sitting next to it on screen.
    await _shift_declared_holdings(db, transfer, sign=1)

    transfer.status = "issued_posted"
    transfer.posted_on = posted_on or date.today()
    transfer.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=TRANSFER_DOCTYPE,
        document_id=transfer.id,
        action="post",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "posted_on": str(transfer.posted_on),
            "surrendered_certificate": surrendered.certificate_no if surrendered else None,
            "issued_certificate": transfer.issued_certificate_id and str(transfer.issued_certificate_id),
            "cap_table_row": transfer.share_transfer_id and str(transfer.share_transfer_id),
        },
    )
    return transfer


async def _post_accounts_transfer(
    db: AsyncSession,
    user: CurrentUser,
    transfer: SecretarialShareTransferDetail,
    entity: SecretarialEntity,
) -> uuid.UUID | None:
    """Create and submit the accounts-side movement, if both members are mapped.

    Deliberately quiet about a partial mapping: a member with no ``shareholder_id`` is
    the normal state for an entity whose secretarial register was set up before its
    books, and refusing the whole transfer over it would be the wrong trade. The row
    records that no cap-table movement was made, and the register still balances.
    """
    from app.models.accounts import ShareType  # local: avoids a module-level accounts import

    if not (transfer.transferor_member_id and transfer.transferee_member_id):
        return None

    from_member = await db.get(SecretarialMember, transfer.transferor_member_id)
    to_member = await db.get(SecretarialMember, transfer.transferee_member_id)
    if not (from_member and to_member and from_member.shareholder_id and to_member.shareholder_id):
        return None

    share_type_id = await db.scalar(
        select(ShareType.id).where(
            ShareType.company_id == entity.linked_company_id,
            ShareType.share_type_name == transfer.share_class,
        )
    )
    if share_type_id is None:
        return None

    shares = int(Decimal(transfer.no_of_shares))
    rate = (
        (Decimal(transfer.consideration) / Decimal(shares))
        if shares and transfer.consideration
        else Decimal("0")
    )
    row = ShareTransfer(
        company_id=entity.linked_company_id,
        name=f"SH-4/{transfer.instrument_no}",
        transfer_type="Transfer",
        from_shareholder_id=from_member.shareholder_id,
        to_shareholder_id=to_member.shareholder_id,
        share_type_id=share_type_id,
        no_of_shares=shares,
        rate=rate,
        amount=Decimal(transfer.consideration or 0),
        transfer_date=transfer.posted_on or transfer.executed_on,
        status="Submitted",
        docstatus=1,
        remarks=f"Registered under instrument SH-4/{transfer.instrument_no}",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(row)
    await db.flush()
    return row.id


async def _shift_declared_holdings(
    db: AsyncSession, transfer: SecretarialShareTransferDetail, *, sign: int
) -> None:
    """Move the Phase-1 ``shares_held`` snapshot by the transferred amount.

    ``sign=1`` on posting, ``sign=-1`` on revert. These columns are the *opening*
    position for an entity that has a transfer ledger — keeping them aligned is a
    display concern, not a source of truth, which is why nothing is validated against
    them here.
    """
    delta = Decimal(transfer.no_of_shares) * sign
    for member_id, direction in (
        (transfer.transferor_member_id, -1),
        (transfer.transferee_member_id, 1),
    ):
        if not member_id:
            continue
        member = await db.get(SecretarialMember, member_id)
        if member is None:
            continue
        member.shares_held = (member.shares_held or Decimal(0)) + delta * direction
        if member.share_class is None:
            member.share_class = transfer.share_class
    await db.flush()


async def revert_transfer(
    db: AsyncSession,
    user: CurrentUser,
    transfer_id: uuid.UUID,
    *,
    reason: str,
    reverted_on: date | None = None,
) -> SecretarialShareTransferDetail:
    """Undo a posted transfer, leaving the whole trail in place.

    The cap-table row is cancelled (the accounts service already recomputes balances from
    the submitted rows, so there is nothing to reverse by hand), the issued certificate is
    cancelled, and the surrendered one is reissued to its original holder over the same
    distinctive range. Every row involved stays readable, and the reason is stored where
    an inspection will find it.
    """
    assert user.company_id is not None
    transfer = await get_transfer(db, transfer_id, user.company_id)
    _check_transition(transfer.status, "reverted")
    if not reason or not reason.strip():
        raise ValidationError("A reversal has to say why", field="reason")

    when = reverted_on or date.today()

    if transfer.issued_certificate_id:
        issued = await get_certificate(db, transfer.issued_certificate_id, user.company_id)
        if issued.status == "issued":
            issued.status = "cancelled"
            issued.cancelled_on = when
            issued.cancelled_reason = f"Transfer SH-4/{transfer.instrument_no} reverted: {reason.strip()}"
            issued.modified_by = user.id
            await db.flush()

    if transfer.surrendered_certificate_id:
        original = await get_certificate(db, transfer.surrendered_certificate_id, user.company_id)
        await issue_certificate(
            db,
            user,
            entity_id=transfer.entity_id,
            no_of_shares=original.no_of_shares,
            share_class=original.share_class,
            member_id=original.member_id,
            holder_name=original.holder_name if not original.member_id else None,
            folio_no=original.folio_no,
            face_value=original.face_value,
            amount_paid_up=original.amount_paid_up,
            issue_type="renewed",
            issued_on=when,
            supersedes_id=original.id,
            distinctive=(original.distinctive_from, original.distinctive_to),
            notes=(
                f"Reissued to {original.holder_name} on reversal of SH-4/"
                f"{transfer.instrument_no}: {reason.strip()}"
            ),
        )

    if transfer.share_transfer_id:
        row = await db.get(ShareTransfer, transfer.share_transfer_id)
        if row is not None and row.status == "Submitted":
            # Balances are derived from submitted rows, so cancelling is the reversal.
            row.status = "Cancelled"
            row.docstatus = 2
            row.modified_by = user.id
            await db.flush()

    await _shift_declared_holdings(db, transfer, sign=-1)

    transfer.status = "reverted"
    transfer.reverted_on = when
    transfer.reverted_reason = reason.strip()
    transfer.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=TRANSFER_DOCTYPE,
        document_id=transfer.id,
        action="revert",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"reason": reason.strip(), "reverted_on": str(when)},
    )
    return transfer


async def list_transfers(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialShareTransferDetail], int]:
    stmt = select(SecretarialShareTransferDetail).where(
        SecretarialShareTransferDetail.company_id == company_id
    )
    if entity_id:
        stmt = stmt.where(SecretarialShareTransferDetail.entity_id == entity_id)
    if status:
        stmt = stmt.where(SecretarialShareTransferDetail.status == status)
    stmt = stmt.order_by(SecretarialShareTransferDetail.instrument_no.desc())
    return await paginate(db, stmt, page, page_size)


# --- Capital events ---------------------------------------------------------------


async def get_event(
    db: AsyncSession, event_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialCapitalEvent:
    row = await db.get(SecretarialCapitalEvent, event_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Capital event not found")
    return row


async def create_event(
    db: AsyncSession, user: CurrentUser, payload: dict[str, Any]
) -> SecretarialCapitalEvent:
    assert user.company_id is not None
    entity_id = payload["entity_id"]
    await get_entity(db, entity_id, user.company_id)

    event = SecretarialCapitalEvent(
        company_id=user.company_id, status="draft", owner=user.id, modified_by=user.id, **payload
    )
    if event.total_amount is None and event.shares_offered and event.price_per_share:
        event.total_amount = Decimal(event.shares_offered) * Decimal(event.price_per_share)
    db.add(event)
    await db.flush()

    await log_audit(
        db,
        doctype=EVENT_DOCTYPE,
        document_id=event.id,
        action="create",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"event_type": event.event_type, "title": event.title},
    )
    return event


async def update_event(
    db: AsyncSession, user: CurrentUser, event_id: uuid.UUID, changes: dict[str, Any]
) -> SecretarialCapitalEvent:
    assert user.company_id is not None
    event = await get_event(db, event_id, user.company_id)
    if event.status in ("allotted", "cancelled"):
        raise ValidationError(f"An event that is {event.status} is closed to editing")
    for field, value in changes.items():
        if hasattr(event, field):
            setattr(event, field, value)
    event.modified_by = user.id
    await db.flush()
    return event


async def approve_event(
    db: AsyncSession, user: CurrentUser, event_id: uuid.UUID
) -> SecretarialCapitalEvent:
    """Mark the offer as authorised — and, for a dividend, check s.123 first.

    The verdict is frozen onto the row. A dividend approved when the figures said it was
    covered stays approved on that basis even if the accounts are later restated; what an
    inspection asks is what the board knew, not what today's ledger says.
    """
    assert user.company_id is not None
    event = await get_event(db, event_id, user.company_id)
    if "approved" not in _EVENT_NEXT.get(event.status, ()):
        raise ValidationError(f"An event that is {event.status} cannot be approved")

    if not (event.board_meeting_id or event.general_meeting_id or event.circular_id):
        raise ValidationError(
            "Name the meeting or circular resolution that authorised this — an offer "
            "with no resolution behind it is not an offer"
        )

    if event.event_type == "dividend":
        from app.services.secretarial import s186 as s186_service

        entity = await get_entity(db, event.entity_id, user.company_id)
        check = await s186_service.dividend_check(
            db, entity, event.fy or "", proposed=event.total_amount
        )
        if check["verdict"] == "exceeded":
            raise ValidationError(
                "s.123: the proposed dividend exceeds distributable profit — "
                + "; ".join(check["reasons"])
            )
        event.solvency_check = s186_service.as_jsonb(check)

    event.status = "approved"
    event.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=EVENT_DOCTYPE,
        document_id=event.id,
        action="approve",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"solvency_check": event.solvency_check},
    )
    return event


async def allot_event(
    db: AsyncSession,
    user: CurrentUser,
    event_id: uuid.UUID,
    *,
    allotted_on: date,
    shares_allotted: Decimal | None = None,
    issue_certificates: bool = True,
) -> tuple[SecretarialCapitalEvent, list[SecretarialShareCertificate]]:
    """Allot the shares and issue a certificate to each allottee.

    The allottee list on the event is the offer as made; the certificates are the shares
    as issued. Issuing them here rather than by hand afterwards is the point — an
    allotment whose certificates were never cut is the most common gap in a real minute
    book, and this makes it impossible to reach that state by forgetting.
    """
    assert user.company_id is not None
    event = await get_event(db, event_id, user.company_id)
    if "allotted" not in _EVENT_NEXT.get(event.status, ()):
        raise ValidationError(
            f"An event that is {event.status} cannot be allotted — approve it first"
        )

    certificates: list[SecretarialShareCertificate] = []
    allottees = event.allottees or []

    if issue_certificates and event.event_type != "dividend":
        if not allottees:
            raise ValidationError(
                "No allottees on this event. Add them before allotting, or allot without "
                "issuing certificates."
            )
        for allottee in allottees:
            shares = Decimal(str(allottee.get("shares") or 0))
            if shares <= 0:
                continue
            certificate = await issue_certificate(
                db,
                user,
                entity_id=event.entity_id,
                no_of_shares=shares,
                share_class=event.share_class or "Equity",
                member_id=(
                    uuid.UUID(str(allottee["member_id"])) if allottee.get("member_id") else None
                ),
                holder_name=allottee.get("name"),
                face_value=event.face_value,
                amount_paid_up=(
                    Decimal(str(allottee.get("amount"))) if allottee.get("amount") else None
                ),
                issue_type="original",
                issued_on=allotted_on,
                capital_event_id=event.id,
                notes=f"Allotted under {event.title}",
            )
            certificates.append(certificate)

    total = shares_allotted or sum(
        (Decimal(str(a.get("shares") or 0)) for a in allottees), Decimal(0)
    )
    event.shares_allotted = total or None
    event.allotted_on = allotted_on
    event.status = "allotted"
    if event.paid_up_before is not None and total and event.face_value:
        event.paid_up_after = Decimal(event.paid_up_before) + total * Decimal(event.face_value)
    event.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=EVENT_DOCTYPE,
        document_id=event.id,
        action="allot",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "allotted_on": str(allotted_on),
            "shares_allotted": str(total),
            "certificates": [c.certificate_no for c in certificates],
        },
    )
    return event, certificates


async def cancel_event(
    db: AsyncSession, user: CurrentUser, event_id: uuid.UUID, *, reason: str | None = None
) -> SecretarialCapitalEvent:
    assert user.company_id is not None
    event = await get_event(db, event_id, user.company_id)
    if "cancelled" not in _EVENT_NEXT.get(event.status, ()):
        raise ValidationError(
            f"An event that is {event.status} cannot be cancelled — shares have already moved"
        )
    event.status = "cancelled"
    event.notes = "\n".join(filter(None, [event.notes, f"Cancelled: {reason}" if reason else None]))
    event.modified_by = user.id
    await db.flush()
    return event


async def list_events(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    event_type: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialCapitalEvent], int]:
    stmt = select(SecretarialCapitalEvent).where(SecretarialCapitalEvent.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialCapitalEvent.entity_id == entity_id)
    if event_type:
        stmt = stmt.where(SecretarialCapitalEvent.event_type == event_type)
    if status:
        stmt = stmt.where(SecretarialCapitalEvent.status == status)
    stmt = stmt.order_by(SecretarialCapitalEvent.creation.desc())
    return await paginate(db, stmt, page, page_size)


# --- Cap table --------------------------------------------------------------------


async def cap_table(
    db: AsyncSession, entity: SecretarialEntity, *, share_class: str | None = None
) -> dict[str, Any]:
    """Who holds what, built from the live certificates — and honest about the source.

    Where the books are here, the accounts ledger is authoritative and the certificates
    should agree with it; a disagreement is reported rather than silently reconciled,
    because a register that quietly matches itself to the ledger hides the very error
    worth knowing about.
    """
    stmt = select(SecretarialShareCertificate).where(
        SecretarialShareCertificate.entity_id == entity.id,
        SecretarialShareCertificate.status == "issued",
    )
    if share_class:
        stmt = stmt.where(SecretarialShareCertificate.share_class == share_class)
    certificates = list((await db.execute(stmt.order_by(SecretarialShareCertificate.distinctive_from))).scalars())

    notes: list[str] = []
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    total = Decimal(0)

    for certificate in certificates:
        key = (str(certificate.member_id or certificate.holder_name), certificate.share_class)
        row = rows.setdefault(
            key,
            {
                "member_id": certificate.member_id,
                "holder_name": certificate.holder_name,
                "folio_no": certificate.folio_no,
                "share_class": certificate.share_class,
                "shares": Decimal(0),
                "certificates": 0,
                "distinctive_ranges": [],
            },
        )
        row["shares"] += Decimal(certificate.no_of_shares)
        row["certificates"] += 1
        row["distinctive_ranges"].append(
            f"{certificate.distinctive_from}–{certificate.distinctive_to}"
        )
        total += Decimal(certificate.no_of_shares)

    source = "register" if certificates else "opening"

    if not certificates:
        # Nothing issued yet: fall back to the Phase-1 declared holdings so the screen is
        # not blank for an entity whose register was set up but never certificated.
        members = list(
            (
                await db.execute(
                    select(SecretarialMember).where(
                        SecretarialMember.entity_id == entity.id,
                        SecretarialMember.ceased_on.is_(None),
                    )
                )
            ).scalars()
        )
        for member in members:
            if not member.shares_held:
                continue
            rows[(str(member.id), member.share_class or "Equity")] = {
                "member_id": member.id,
                "holder_name": member.member_name,
                "folio_no": member.folio_no,
                "share_class": member.share_class or "Equity",
                "shares": Decimal(member.shares_held),
                "certificates": 0,
                "distinctive_ranges": [],
            }
            total += Decimal(member.shares_held)
        if rows:
            notes.append(
                "No share certificates have been issued, so these are the declared "
                "opening holdings from the register of members."
            )

    if entity.linked_company_id and certificates:
        source = "ledger"
        notes.append(
            "Holdings are backed by the share ledger in this company's books; the "
            "certificates below are the statutory record of the same shares."
        )

    out_rows = []
    for row in rows.values():
        row["pct"] = (row["shares"] / total * 100).quantize(Decimal("0.01")) if total else None
        out_rows.append(row)
    out_rows.sort(key=lambda r: r["shares"], reverse=True)

    return {
        "entity_id": entity.id,
        "source": source,
        "total_shares": total,
        "rows": out_rows,
        "unissued_from": await peek_distinctive(
            db, entity_id=entity.id, share_class=share_class or "Equity"
        ),
        "notes": notes,
    }
