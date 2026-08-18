"""Evidence upload and download."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse, MessageResponse
from app.schemas.secretarial import FileResponse, FileUploadIn
from app.services.secretarial import files as service

router = APIRouter(prefix="/secretarial/files", tags=["secretarial: files"])

DOCTYPE = "Secretarial File"


@router.post(
    "",
    response_model=FileResponse,
    status_code=201,
    summary="Upload evidence",
    description="Base64 in a JSON body, 20 MB cap — the same shape as the Tally import "
    "endpoint. Executable formats are refused rather than stored.",
)
async def upload(
    payload: FileUploadIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FileResponse:
    return FileResponse.model_validate(await service.upload(db, payload, current_user))


@router.get("", response_model=ListResponse[FileResponse], summary="List uploaded files")
async def list_files(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    reference_doctype: str | None = None,
    reference_id: uuid.UUID | None = None,
    category: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[FileResponse]:
    assert current_user.company_id is not None
    items, total = await service.list_files(
        db,
        current_user.company_id,
        entity_id=entity_id,
        reference_doctype=reference_doctype,
        reference_id=reference_id,
        category=category,
        page=page,
        page_size=page_size,
    )
    return ListResponse(
        items=[FileResponse.model_validate(f) for f in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{file_id}/download",
    summary="Download a file",
    description="Served as an attachment with the stored content type. Content is never "
    "rendered inline — an uploaded HTML or SVG would otherwise execute in the app's origin.",
)
async def download(
    file_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Response:
    assert current_user.company_id is not None
    row = await service.get_file(db, file_id, current_user.company_id)
    if row.content is None:
        raise NotFoundError("File content is not stored in the database")
    return Response(
        content=row.content,
        media_type=row.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{row.file_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{file_id}", response_model=MessageResponse, summary="Delete a file")
async def delete_file(
    file_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "delete"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MessageResponse:
    await service.delete_file(db, file_id, current_user)
    return MessageResponse(message="File deleted")
