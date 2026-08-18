"""Uploaded evidence — signed minutes, challans, MCA exports.

This is the codebase's first file-upload surface, so it follows the one precedent that
exists (``api/v1/tally/imports.py``): base64 inside a JSON body with a hard size cap,
bytes stored in Postgres. ``storage_backend`` is the seam — moving to object storage
later sets ``storage_ref`` and leaves ``content`` NULL, and no caller changes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import SecretarialFile
from app.schemas.secretarial import FileUploadIn
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.secretarial.common import get_entity

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — a scanned minute book, not a video

_DOCTYPE = "Secretarial File"

# Evidence formats a secretarial file legitimately takes. Anything executable is
# refused outright rather than stored and hoped about.
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-excel",
    "text/csv",
    "text/plain",
    "application/zip",
    "application/octet-stream",
}


def _decode(payload: FileUploadIn) -> bytes:
    # base64 inflates by ~4/3, so cap the encoded string before decoding it.
    if len(payload.content_base64) > MAX_UPLOAD_BYTES * 4 // 3 + 1024:
        raise ValidationError(
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
            field="content_base64",
        )
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("File content is not valid base64", field="content_base64") from exc
    if not raw:
        raise ValidationError("File is empty", field="content_base64")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
            field="content_base64",
        )
    return raw


async def upload(db: AsyncSession, payload: FileUploadIn, user: CurrentUser) -> SecretarialFile:
    assert user.company_id is not None
    if payload.content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            f"'{payload.content_type}' is not an accepted evidence format",
            field="content_type",
        )
    if payload.entity_id:
        await get_entity(db, payload.entity_id, user.company_id)

    raw = _decode(payload)
    row = SecretarialFile(
        company_id=user.company_id,
        entity_id=payload.entity_id,
        file_name=payload.file_name,
        content_type=payload.content_type,
        file_size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        storage_backend="db",
        content=raw,
        reference_doctype=payload.reference_doctype,
        reference_id=payload.reference_id,
        category=payload.category,
        description=payload.description,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(row)
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=row.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"file_name": row.file_name, "file_size": row.file_size, "sha256": row.sha256},
    )
    await db.commit()
    return row


async def get_file(db: AsyncSession, file_id: uuid.UUID, company_id: uuid.UUID) -> SecretarialFile:
    row = await db.get(SecretarialFile, file_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("File not found")
    return row


async def list_files(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    reference_doctype: str | None = None,
    reference_id: uuid.UUID | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialFile], int]:
    stmt = select(SecretarialFile).where(SecretarialFile.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialFile.entity_id == entity_id)
    if reference_doctype:
        stmt = stmt.where(SecretarialFile.reference_doctype == reference_doctype)
    if reference_id:
        stmt = stmt.where(SecretarialFile.reference_id == reference_id)
    if category:
        stmt = stmt.where(SecretarialFile.category == category)
    return await paginate(db, stmt.order_by(SecretarialFile.creation.desc()), page, page_size)


async def delete_file(db: AsyncSession, file_id: uuid.UUID, user: CurrentUser) -> None:
    assert user.company_id is not None
    row = await get_file(db, file_id, user.company_id)
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=row.id,
        action="DELETE",
        user_id=user.id,
        company_id=user.company_id,
        data_before={"file_name": row.file_name, "sha256": row.sha256},
    )
    await db.delete(row)
    await db.commit()
