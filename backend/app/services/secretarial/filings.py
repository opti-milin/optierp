"""Filings — the last link in the evidence chain.

The thread the plan sets out as the real extension of the competitor's strongest
feature: **resolution → document → form → SRN → challan**. Filing itself happens on the
MCA portal with the filer's own credentials; nothing here pretends otherwise. What lives
here is the proof that it happened and what it rested on.

A filing marked `filed` must carry an SRN and a date — enforced in the service and again
by a database check, because a compliance record that says "done" without evidence is
worse than one that says "overdue".
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialComplianceItem,
    SecretarialCircular,
    SecretarialDocument,
    SecretarialFiling,
    SecretarialMeeting,
    SecretarialStatusHistory,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.secretarial.common import get_entity

_DOCTYPE = "Secretarial Filing"

FILING_STATUSES = ("prepared", "filed", "approved", "resubmission", "rejected")


async def get_filing(
    db: AsyncSession, filing_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialFiling:
    row = await db.get(SecretarialFiling, filing_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Filing not found")
    return row


async def record(
    db: AsyncSession,
    user: CurrentUser,
    *,
    entity_id: uuid.UUID,
    form_code: str,
    compliance_item_id: uuid.UUID | None = None,
    fy: str | None = None,
    srn: str | None = None,
    filed_on: date | None = None,
    status: str = "prepared",
    filing_fee: Decimal | None = None,
    additional_fee: Decimal | None = None,
    meeting_id: uuid.UUID | None = None,
    circular_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    challan_file_id: uuid.UUID | None = None,
    filed_by: str | None = None,
    notes: str | None = None,
) -> SecretarialFiling:
    """Record a filing and, if it is done, close the calendar row behind it."""
    assert user.company_id is not None
    await get_entity(db, entity_id, user.company_id)

    if status not in FILING_STATUSES:
        raise ValidationError(f"Unknown filing status '{status}'", field="status")
    if status != "prepared" and not (srn and filed_on):
        raise ValidationError(
            "A filing that has been submitted needs its SRN and the date it was filed — "
            "that is the evidence the record exists for.",
            field="srn",
        )

    # Cross-references must belong to the same entity, or the chain is a fiction.
    await _assert_same_entity(db, entity_id, meeting_id, circular_id, document_id)

    filing = SecretarialFiling(
        company_id=user.company_id,
        entity_id=entity_id,
        compliance_item_id=compliance_item_id,
        form_code=form_code,
        fy=fy,
        srn=srn,
        filed_on=filed_on,
        status=status,
        filing_fee=filing_fee,
        additional_fee=additional_fee,
        meeting_id=meeting_id,
        circular_id=circular_id,
        document_id=document_id,
        challan_file_id=challan_file_id,
        filed_by=filed_by,
        notes=notes,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(filing)
    await db.flush()

    if compliance_item_id and status in ("filed", "approved"):
        await _close_item(db, compliance_item_id, filing, user)

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=filing.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"form": form_code, "srn": srn, "status": status},
    )
    await db.commit()
    return filing


async def _assert_same_entity(
    db: AsyncSession,
    entity_id: uuid.UUID,
    meeting_id: uuid.UUID | None,
    circular_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
) -> None:
    if meeting_id:
        row = await db.get(SecretarialMeeting, meeting_id)
        if row is None or row.entity_id != entity_id:
            raise ValidationError("That meeting belongs to a different company", field="meeting_id")
    if circular_id:
        row = await db.get(SecretarialCircular, circular_id)
        if row is None or row.entity_id != entity_id:
            raise ValidationError("That resolution belongs to a different company", field="circular_id")
    if document_id:
        row = await db.get(SecretarialDocument, document_id)
        if row is None or row.entity_id != entity_id:
            raise ValidationError("That document belongs to a different company", field="document_id")


async def _close_item(
    db: AsyncSession, item_id: uuid.UUID, filing: SecretarialFiling, user: CurrentUser
) -> None:
    """Mark the calendar row filed, and append to its history."""
    item = await db.get(SecretarialComplianceItem, item_id)
    if item is None or item.company_id != filing.company_id:
        return
    previous = item.status
    item.status = "filed"
    item.srn = filing.srn
    item.filed_on = filing.filed_on
    item.completed_on = item.completed_on or filing.filed_on
    item.modified_by = user.id

    db.add(
        SecretarialStatusHistory(
            company_id=filing.company_id,
            item_id=item.id,
            from_status=previous,
            to_status="filed",
            reason=f"{filing.form_code} filed under SRN {filing.srn}",
            changed_by=user.id,
        )
    )
    await db.flush()


async def update_filing(
    db: AsyncSession, filing_id: uuid.UUID, values: dict[str, Any], user: CurrentUser
) -> SecretarialFiling:
    assert user.company_id is not None
    filing = await get_filing(db, filing_id, user.company_id)

    target_status = values.get("status", filing.status)
    srn = values.get("srn", filing.srn)
    filed_on = values.get("filed_on", filing.filed_on)
    if target_status != "prepared" and not (srn and filed_on):
        raise ValidationError(
            "A submitted filing needs its SRN and filing date", field="srn"
        )

    for field, value in values.items():
        if hasattr(filing, field):
            setattr(filing, field, value)
    filing.modified_by = user.id
    await db.flush()

    if filing.compliance_item_id and filing.status in ("filed", "approved"):
        await _close_item(db, filing.compliance_item_id, filing, user)

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=filing.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"status": filing.status, "srn": filing.srn},
    )
    await db.commit()
    return filing


async def list_filings(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    fy: str | None = None,
    form_code: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialFiling], int]:
    stmt = select(SecretarialFiling).where(SecretarialFiling.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialFiling.entity_id == entity_id)
    if fy:
        stmt = stmt.where(SecretarialFiling.fy == fy)
    if form_code:
        stmt = stmt.where(SecretarialFiling.form_code == form_code)
    if status:
        stmt = stmt.where(SecretarialFiling.status == status)
    return await paginate(
        db, stmt.order_by(SecretarialFiling.filed_on.desc().nullslast()), page, page_size
    )


async def evidence_chain(
    db: AsyncSession, filing_id: uuid.UUID, company_id: uuid.UUID
) -> dict[str, Any]:
    """Walk back from the filing to the decision that authorised it.

    This is the answer to "on what authority was this filed?" — and being able to
    produce it in one click is the whole point of keeping the links.
    """
    filing = await get_filing(db, filing_id, company_id)
    chain: dict[str, Any] = {
        "filing": {
            "id": str(filing.id),
            "form_code": filing.form_code,
            "srn": filing.srn,
            "filed_on": filing.filed_on,
            "status": filing.status,
            "has_challan": filing.challan_file_id is not None,
        },
        "resolution": None,
        "document": None,
        "compliance_item": None,
        "gaps": [],
    }

    if filing.meeting_id:
        meeting = await db.get(SecretarialMeeting, filing.meeting_id)
        if meeting:
            chain["resolution"] = {
                "kind": "meeting",
                "id": str(meeting.id),
                "description": (
                    f"{meeting.meeting_type.upper()} on "
                    f"{(meeting.held_at or meeting.scheduled_at):%d %b %Y}"
                ),
                "minutes_signed": meeting.minutes_signed_on,
                "minutes_entry_no": meeting.minutes_entry_no,
            }
            if not meeting.minutes_signed_on:
                chain["gaps"].append("The minutes authorising this filing have not been signed.")
    elif filing.circular_id:
        circular = await db.get(SecretarialCircular, filing.circular_id)
        if circular:
            chain["resolution"] = {
                "kind": "circular",
                "id": str(circular.id),
                "description": f"Resolution by circulation {circular.reference_no}",
                "status": circular.status,
                "decided_at": circular.decided_at,
            }
            if circular.status not in ("passed", "ratified"):
                chain["gaps"].append(
                    f"The resolution behind this filing is {circular.status}, not passed."
                )
            elif circular.status == "passed":
                chain["gaps"].append(
                    "The resolution has not yet been noted at a board meeting (ratification)."
                )
    else:
        chain["gaps"].append("No meeting or resolution is linked to this filing.")

    if filing.document_id:
        doc = await db.get(SecretarialDocument, filing.document_id)
        if doc:
            chain["document"] = {
                "id": str(doc.id),
                "title": doc.title,
                "version": doc.version,
                "status": doc.status,
            }
    else:
        chain["gaps"].append("No generated document is linked to this filing.")

    if filing.compliance_item_id:
        item = await db.get(SecretarialComplianceItem, filing.compliance_item_id)
        if item:
            chain["compliance_item"] = {
                "id": str(item.id),
                "title": item.title,
                "fy": item.fy,
                "due_on": item.due_on,
                "status": item.status,
            }
    if not filing.challan_file_id and filing.status in ("filed", "approved"):
        chain["gaps"].append("No challan has been attached.")

    chain["complete"] = not chain["gaps"]
    return chain


async def item_history(
    db: AsyncSession, item_id: uuid.UUID, company_id: uuid.UUID
) -> list[SecretarialStatusHistory]:
    return list(
        (
            await db.execute(
                select(SecretarialStatusHistory)
                .where(
                    SecretarialStatusHistory.company_id == company_id,
                    SecretarialStatusHistory.item_id == item_id,
                )
                .order_by(SecretarialStatusHistory.creation.desc())
            )
        )
        .scalars()
        .all()
    )
