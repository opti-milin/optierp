"""The director portal — unauthenticated, token-scoped.

Every other router in this application requires a JWT. These endpoints deliberately do
not, because the people using them have no account and should never be made to get one
(plan §11). What stands in for a login is a single-purpose capability token in the URL,
resolved by ``app.core.portal`` before any tenant context is set.

Rules that hold across every endpoint here:

* Purpose is checked, not just validity — a link that views papers cannot cast a consent.
* Responses contain only what that one person is entitled to see: their own record and
  the papers sent to them. Never a list, never another director's response.
* Every hit is written to an append-only event log. "Opened at 14:12, consented at 14:15"
  is the evidence the whole feature exists to produce.
* Errors are deliberately vague. An attacker probing tokens learns nothing about whether
  one existed, expired or was revoked.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.core.portal import PortalSession, portal_session
from app.models.secretarial import (
    SecretarialCirculation,
    SecretarialCirculationRecipient,
    SecretarialCircular,
    SecretarialConsentResponse,
    SecretarialDocument,
    SecretarialEntity,
    SecretarialPerson,
)
from app.schemas.secretarial_governance import (
    PortalCirculationOut,
    PortalConsentOut,
    PortalDocumentOut,
    PortalRespondIn,
)
from app.services.secretarial import circulation as circulation_service
from app.services.secretarial import circular as circular_service
from app.services.secretarial import documents as document_service

# No auth dependency anywhere in this router — that is the point.
router = APIRouter(prefix="/portal", tags=["portal (no login)"])


@router.get(
    "/c/{token}",
    response_model=PortalCirculationOut,
    summary="View board papers sent to you",
    description="Opening the link records that it was viewed, which is half the evidence "
    "the sender needs.",
)
async def view_circulation(
    session: Annotated[PortalSession, Depends(portal_session("circulation_view"))],
) -> PortalCirculationOut:
    db, token = session.db, session.token

    recipient = await db.get(SecretarialCirculationRecipient, token.target_id)
    if recipient is None:
        # Same vague error as an invalid token: never confirm what exists.
        from app.core.portal import PortalTokenInvalid

        raise PortalTokenInvalid("This link is not valid.")

    await circulation_service.mark_viewed(db, recipient.id)
    from app.core.portal import record_event

    await record_event(db, token, "circulation_viewed", session.request)

    circulation = await db.get(SecretarialCirculation, recipient.circulation_id)
    entity = await db.get(SecretarialEntity, token.entity_id)
    person = await db.get(SecretarialPerson, recipient.person_id)

    doc_ids = [uuid.UUID(d) for d in (circulation.document_ids or [])] if circulation else []
    documents = (
        list(
            (
                await db.execute(
                    select(SecretarialDocument).where(SecretarialDocument.id.in_(doc_ids))
                )
            )
            .scalars()
            .all()
        )
        if doc_ids
        else []
    )

    await db.commit()
    return PortalCirculationOut(
        entity_name=entity.entity_name if entity else "",
        subject=circulation.subject if circulation else "",
        message=circulation.message if circulation else None,
        recipient_name=person.full_name if person else "",
        status=recipient.status,
        sent_at=recipient.sent_at,
        acknowledged_at=recipient.acknowledged_at,
        documents=[
            PortalDocumentOut(id=d.id, title=d.title, document_type=d.document_type)
            for d in documents
        ],
    )


@router.get(
    "/c/{token}/documents/{document_id}",
    summary="Download one of the papers sent to you",
    description="Restricted to documents actually attached to this circulation — a valid "
    "token does not open the whole library.",
)
async def download_paper(
    document_id: uuid.UUID,
    session: Annotated[PortalSession, Depends(portal_session("circulation_view"))],
) -> Response:
    db, token = session.db, session.token
    from app.core.portal import PortalTokenInvalid, record_event

    recipient = await db.get(SecretarialCirculationRecipient, token.target_id)
    if recipient is None:
        raise PortalTokenInvalid("This link is not valid.")
    circulation = await db.get(SecretarialCirculation, recipient.circulation_id)
    allowed = {str(d) for d in (circulation.document_ids or [])} if circulation else set()
    if str(document_id) not in allowed:
        raise PortalTokenInvalid("That document is not part of this circulation.")

    content, filename, media_type = await document_service.render(
        db, document_id, token.company_id, "pdf"
    )
    await record_event(db, token, "document_downloaded", session.request, {"document_id": str(document_id)})
    await db.commit()

    body = content if isinstance(content, bytes) else content.encode("utf-8")
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/c/{token}/acknowledge",
    response_model=PortalCirculationOut,
    summary="Acknowledge receipt of the papers",
)
async def acknowledge(
    session: Annotated[PortalSession, Depends(portal_session("circulation_view"))],
) -> PortalCirculationOut:
    db, token = session.db, session.token
    from app.core.portal import record_event

    recipient = await circulation_service.acknowledge(db, token.target_id)
    await record_event(db, token, "circulation_acknowledged", session.request)

    circulation = await db.get(SecretarialCirculation, recipient.circulation_id)
    entity = await db.get(SecretarialEntity, token.entity_id)
    person = await db.get(SecretarialPerson, recipient.person_id)
    await db.commit()

    return PortalCirculationOut(
        entity_name=entity.entity_name if entity else "",
        subject=circulation.subject if circulation else "",
        message=circulation.message if circulation else None,
        recipient_name=person.full_name if person else "",
        status=recipient.status,
        sent_at=recipient.sent_at,
        acknowledged_at=recipient.acknowledged_at,
        documents=[],
    )


@router.get(
    "/r/{token}",
    response_model=PortalConsentOut,
    summary="View a resolution circulated for your consent",
)
async def view_consent(
    session: Annotated[PortalSession, Depends(portal_session("consent_respond"))],
) -> PortalConsentOut:
    db, token = session.db, session.token
    from app.core.portal import PortalTokenInvalid, record_event

    response = await db.get(SecretarialConsentResponse, token.target_id)
    if response is None:
        raise PortalTokenInvalid("This link is not valid.")

    await circular_service.mark_viewed(db, response.id)
    await record_event(db, token, "consent_viewed", session.request)

    circular = await db.get(SecretarialCircular, response.circular_id)
    entity = await db.get(SecretarialEntity, token.entity_id)
    person = await db.get(SecretarialPerson, response.person_id)
    await db.commit()

    return PortalConsentOut(
        entity_name=entity.entity_name if entity else "",
        title=circular.title if circular else "",
        reference_no=circular.reference_no if circular else None,
        resolution_text=circular.resolution_text if circular else "",
        description=circular.description if circular else None,
        consent_rule=circular.consent_rule if circular else "majority",
        expires_at=circular.expires_at if circular else None,
        recipient_name=person.full_name if person else "",
        status=response.status,
        responded_at=response.responded_at,
        circular_status=circular.status if circular else "",
    )


@router.post(
    "/r/{token}/respond",
    response_model=PortalConsentOut,
    summary="Record your consent, dissent or abstention",
    description=(
        "Recorded once. Changing a cast vote would make the audit trail a fiction, so a "
        "second attempt is refused and the director is told to contact the company."
    ),
)
async def respond(
    payload: PortalRespondIn,
    request: Request,
    session: Annotated[PortalSession, Depends(portal_session("consent_respond"))],
) -> PortalConsentOut:
    db, token = session.db, session.token
    from app.core.portal import PortalTokenInvalid, record_event

    response = await db.get(SecretarialConsentResponse, token.target_id)
    if response is None:
        raise PortalTokenInvalid("This link is not valid.")

    saved, circular = await circular_service.record_response(
        db,
        circular_id=response.circular_id,
        person_id=response.person_id,
        status=payload.decision,
        comments=payload.comments,
        ip_address=request.client.host if request.client else None,
    )
    await record_event(
        db, token, f"consent_{payload.decision}", request, {"circular_id": str(circular.id)}
    )

    entity = await db.get(SecretarialEntity, token.entity_id)
    person = await db.get(SecretarialPerson, saved.person_id)
    await db.commit()

    return PortalConsentOut(
        entity_name=entity.entity_name if entity else "",
        title=circular.title,
        reference_no=circular.reference_no,
        resolution_text=circular.resolution_text,
        description=circular.description,
        consent_rule=circular.consent_rule,
        expires_at=circular.expires_at,
        recipient_name=person.full_name if person else "",
        status=saved.status,
        responded_at=saved.responded_at,
        circular_status=circular.status,
    )
