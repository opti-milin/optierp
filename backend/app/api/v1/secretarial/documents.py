"""Document generation, versioning and download."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.models.secretarial import SecretarialContentPack
from app.schemas.common import ListResponse
from app.schemas.secretarial import ComplianceRuleReviewIn
from app.schemas.secretarial_governance import (
    ContentPackOut,
    DocumentGenerateIn,
    DocumentListItem,
    DocumentRegenerateIn,
    DocumentResponse,
)
from app.services.secretarial import content as content_service
from app.services.secretarial import documents as service

router = APIRouter(prefix="/secretarial/documents", tags=["secretarial: documents"])

DOCTYPE = "Secretarial Document"


@router.get(
    "/packs",
    response_model=list[ContentPackOut],
    summary="Available document templates",
    description="Drafts are listed so an author can see the backlog, but only published "
    "packs can produce a document.",
)
async def list_packs(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_kind: str | None = None,
    published_only: bool = False,
) -> list[ContentPackOut]:
    assert current_user.company_id is not None
    stmt = select(SecretarialContentPack).where(
        or_(
            SecretarialContentPack.company_id.is_(None),
            SecretarialContentPack.company_id == current_user.company_id,
        )
    )
    if published_only:
        stmt = stmt.where(SecretarialContentPack.review_status == "published")
    rows = list((await db.execute(stmt.order_by(SecretarialContentPack.title))).scalars().all())
    if entity_kind:
        rows = [p for p in rows if not p.applies_to_kinds or entity_kind in p.applies_to_kinds]
    return [ContentPackOut.model_validate(p) for p in rows]


@router.post(
    "/packs/{pack_id}/review",
    response_model=ContentPackOut,
    summary="Advance a template through review",
    description="draft → reviewed → approved → published. Publishing needs a named "
    "reviewer; the database enforces the same rule.",
)
async def review_pack(
    pack_id: uuid.UUID,
    payload: ComplianceRuleReviewIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ContentPackOut:
    return ContentPackOut.model_validate(
        await content_service.review_pack(db, pack_id, payload, current_user)
    )


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=201,
    summary="Generate a document from a template",
)
async def generate(
    payload: DocumentGenerateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentResponse:
    doc = await service.generate(
        db,
        current_user,
        entity_id=payload.entity_id,
        pack_code=payload.pack_code,
        fragment=payload.fragment,
        form_data=payload.form_data,
        title=payload.title,
        document_date=payload.document_date,
    )
    return DocumentResponse.model_validate(doc)


@router.get("", response_model=ListResponse[DocumentListItem], summary="The document library")
async def list_documents(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    document_type: str | None = None,
    status: str | None = None,
    source_doctype: str | None = None,
    source_id: uuid.UUID | None = None,
    current_only: bool = True,
    search: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[DocumentListItem]:
    assert current_user.company_id is not None
    items, total = await service.list_documents(
        db,
        current_user.company_id,
        entity_id=entity_id,
        document_type=document_type,
        status=status,
        source_doctype=source_doctype,
        source_id=source_id,
        current_only=current_only,
        search=search,
        page=page,
        page_size=page_size,
    )
    return ListResponse(
        items=[DocumentListItem.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentResponse, summary="Fetch one document")
async def get_document(
    document_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentResponse:
    assert current_user.company_id is not None
    return DocumentResponse.model_validate(
        await service.get_document(db, document_id, current_user.company_id)
    )


@router.get(
    "/{document_id}/versions",
    response_model=list[DocumentListItem],
    summary="Every version of this document",
    description="Regeneration never overwrites, so this is the full history with what "
    "changed at each step.",
)
async def versions(
    document_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[DocumentListItem]:
    assert current_user.company_id is not None
    rows = await service.version_history(db, document_id, current_user.company_id)
    return [DocumentListItem.model_validate(r) for r in rows]


@router.post(
    "/{document_id}/regenerate",
    response_model=DocumentResponse,
    summary="Re-render as a new version",
    description="Picks up corrected master data, a newer published template, or amended "
    "form values. The previous version is kept exactly as it was.",
)
async def regenerate(
    document_id: uuid.UUID,
    payload: DocumentRegenerateIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentResponse:
    return DocumentResponse.model_validate(
        await service.regenerate(
            db,
            document_id,
            current_user,
            form_data=payload.form_data,
            change_summary=payload.change_summary,
        )
    )


@router.post(
    "/{document_id}/finalise",
    response_model=DocumentResponse,
    summary="Mark final, or issue",
    description="Issuing is the point of no return: an issued document can only be "
    "superseded by a new version, never edited.",
)
async def finalise(
    document_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    issue: bool = False,
) -> DocumentResponse:
    return DocumentResponse.model_validate(
        await service.finalise(db, document_id, current_user, issue=issue)
    )


@router.get(
    "/{document_id}/download",
    summary="Render and download",
    description="Re-rendered from stored inputs against the pinned template version, so "
    "the same document renders identically years later. Anything not yet issued is "
    "watermarked.",
)
async def download(
    document_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "print"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    fmt: Annotated[str, Query(pattern="^(pdf|html)$")] = "pdf",
) -> Response:
    assert current_user.company_id is not None
    content, filename, media_type = await service.render(
        db, document_id, current_user.company_id, fmt
    )
    body = content if isinstance(content, bytes) else content.encode("utf-8")
    disposition = "inline" if fmt == "html" else "attachment"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
