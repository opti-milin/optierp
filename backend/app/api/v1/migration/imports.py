"""Migration import session endpoints — Module 12.

The upload is a JSON body carrying the file base64-encoded rather than a
multipart form. That is deliberate on two counts: Tally writes UTF-16 XML as
often as UTF-8, so base64 hands the parser the exact bytes to sniff rather than a
browser's guess; and an .xlsx is a binary ZIP that would not survive being
treated as text at all. It also keeps the API dependency-free.

    curl -X POST /api/v1/migration/imports -H 'Content-Type: application/json' \
      -d "{\"file_name\":\"masters.xml\",\"content_base64\":\"$(base64 -w0 masters.xml)\"}"
"""

import base64
import binascii
import uuid
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.migration import (
    MappingUpdate,
    MigrationAutoMapResponse,
    MigrationEntitySelection,
    MigrationImportEntityResponse,
    MigrationImportListItem,
    MigrationImportOptions,
    MigrationImportResponse,
    MigrationImportStatusResponse,
    MigrationImportSummaryResponse,
    MigrationRollbackResponse,
    MigrationStagingDetailResponse,
    MigrationStagingListResponse,
    MigrationStagingRecordResponse,
    MigrationValidateResponse,
)
from app.services.migration import background, runner, workbooks

router = APIRouter(prefix="/migration/imports", tags=["data migration"])

PERMISSION = "Data Migration"

# 64 MB of base64 ~= 48 MB of XML. A year of vouchers for a mid-size MSME is well
# under that; anything larger should be split by period in Tally's export screen.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


class MigrationUploadRequest(BaseModel):
    """A Tally export, base64-encoded."""

    file_name: str = Field(max_length=255)
    content_base64: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    #: Migration cut-off — opening balances are booked on this date. Defaults to
    #: the export's period start when Tally wrote one.
    opening_date: date | None = None
    options: MigrationImportOptions = Field(default_factory=MigrationImportOptions)


def _decode(payload: MigrationUploadRequest) -> bytes:
    if len(payload.content_base64) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            "That file is too large to import in one go. In Tally, export a "
            "narrower date range (Gateway > Display > Day Book > Alt+E) and "
            "import the periods one at a time.",
            field="content_base64",
        )
    try:
        return base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError(
            "content_base64 is not valid base64", field="content_base64"
        ) from None


async def _response(db: AsyncSession, session) -> MigrationImportResponse:
    await db.refresh(session, ["entities"])
    return MigrationImportResponse.model_validate(session)


@router.get(
    "",
    response_model=list[MigrationImportListItem],
    summary="List Tally import sessions",
    description="Most recent first. One session = one uploaded Tally export.",
)
async def list_imports(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MigrationImportListItem]:
    sessions = await runner.list_imports(db, current_user.company_id, limit=limit)
    return [MigrationImportListItem.model_validate(s) for s in sessions]


@router.post(
    "",
    response_model=MigrationImportResponse,
    status_code=201,
    summary="Upload an export and parse it",
    description=(
        "Accepts a Tally XML export (Gateway of Tally > Export > Format: XML), a "
        "Day Book CSV, or an .xlsx workbook exported from Tally, Zoho Books, the "
        "OptiERP template, or any other system. A workbook's shape is detected "
        "and reported; review it at `/mapping` before running. The file is parsed "
        "immediately into staging rows; nothing is written to the ledgers until "
        "you call `/run`."
    ),
)
async def create_import(
    payload: MigrationUploadRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationImportResponse:
    session = await runner.create_import(
        db,
        current_user,
        raw=_decode(payload),
        file_name=payload.file_name,
        title=payload.title,
        opening_date=payload.opening_date,
        options=payload.options.model_dump(mode="json", exclude_none=True),
        saved_profiles=await workbooks.saved_profiles(db, current_user.company_id),
    )
    return await _response(db, session)


@router.get(
    "/{import_id}",
    response_model=MigrationImportResponse,
    summary="Get one import session",
)
async def get_import(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationImportResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    return await _response(db, session)


@router.get(
    "/{import_id}/summary",
    response_model=MigrationImportSummaryResponse,
    summary="Import session summary",
    description="Per-entity progress, activity log and mapping health in one call.",
)
async def get_summary(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationImportSummaryResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    summary = await runner.import_summary(db, session)
    return MigrationImportSummaryResponse(
        session=await _response(db, session),
        entities=[MigrationImportEntityResponse.model_validate(e) for e in summary["entities"]],
        logs=summary["logs"],
        status_counts=summary["status_counts"],
        unmapped_count=summary["unmapped_count"],
        low_confidence_count=summary["low_confidence_count"],
    )


@router.get(
    "/{import_id}/records",
    response_model=MigrationStagingListResponse,
    summary="Browse staged records",
    description="Filter by entity and status to review exactly what will be imported.",
)
async def list_records(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_key: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MigrationStagingListResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    records, total = await runner.staging_records(
        db, session, entity_key=entity_key, status=status, search=search,
        limit=limit, offset=offset,
    )
    return MigrationStagingListResponse(
        items=[MigrationStagingRecordResponse.model_validate(r) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{import_id}/records/{record_id}",
    response_model=MigrationStagingDetailResponse,
    summary="One staged record, including its raw Tally payload",
)
async def get_record(
    import_id: uuid.UUID,
    record_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationStagingDetailResponse:
    from app.core.exceptions import NotFoundError
    from app.models.migration import MigrationStagingRecord

    await runner.get_import(db, import_id, current_user.company_id)
    record = await db.get(MigrationStagingRecord, record_id)
    if record is None or record.migration_import_id != import_id:
        raise NotFoundError("Staged record not found")
    return MigrationStagingDetailResponse.model_validate(record)


@router.post(
    "/{import_id}/entities",
    response_model=list[MigrationImportEntityResponse],
    summary="Choose which entities to import",
    description="Example: `{'selections': {'voucher_sales': true, 'budget': false}}`",
)
async def set_entities(
    import_id: uuid.UUID,
    payload: MigrationEntitySelection,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[MigrationImportEntityResponse]:
    session = await runner.get_import(db, import_id, current_user.company_id)
    entities = await runner.set_entity_selection(db, session, current_user, payload.selections)
    return [MigrationImportEntityResponse.model_validate(e) for e in entities]


@router.post(
    "/{import_id}/parse",
    response_model=MigrationImportResponse,
    summary="Re-parse the stored file",
    description=(
        "Replaces the staging set from the file kept on the session. A spreadsheet "
        "is re-read through the mapping the session already has, so re-parsing "
        "never quietly re-detects a different shape than the one you reviewed."
    ),
)
async def reparse(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationImportResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    await runner.parse_import(
        db,
        session,
        current_user,
        profile=await workbooks.session_profile(db, session),
        saved_profiles=await workbooks.saved_profiles(db, current_user.company_id),
    )
    await db.commit()
    return await _response(db, session)


@router.get(
    "/{import_id}/mapping",
    summary="How this workbook's sheets and columns are being read",
    description=(
        "Every sheet with its real headings and a few sample values, what the "
        "current mapping claims each one is, which required columns are still "
        "missing, and the other profiles that might fit. Spreadsheet imports "
        "only — Tally XML and CSV carry their own structure."
    ),
)
async def get_mapping(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    session = await runner.get_import(db, import_id, current_user.company_id)
    return workbooks.mapping_view(
        session,
        await workbooks.session_profile(db, session),
        await workbooks.saved_profiles(db, current_user.company_id),
    )


@router.put(
    "/{import_id}/mapping",
    response_model=MigrationImportResponse,
    summary="Change the mapping and re-parse",
    description=(
        "Send an edited mapping definition, or the key of a different profile. "
        "The stored workbook is read again through it, replacing the staging set. "
        "Refused once the import has run — roll it back first, because the "
        "documents already created would no longer match their staging rows."
    ),
)
async def put_mapping(
    import_id: uuid.UUID,
    payload: MappingUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationImportResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    await workbooks.apply_mapping(
        db,
        session,
        current_user,
        definition=payload.definition,
        profile_key=payload.profile,
    )
    return await _response(db, session)


@router.post(
    "/{import_id}/automap",
    response_model=MigrationAutoMapResponse,
    summary="Auto-map every Tally name",
    description=(
        "Proposes an OptiERP record for each Tally ledger, item, godown and unit "
        "— by remembered GUID, exact name, normalised name, then fuzzy match. "
        "Mappings you locked by hand are never overwritten."
    ),
)
async def automap(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationAutoMapResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    summary = await runner.automap_import(db, session, current_user)
    return MigrationAutoMapResponse(summary=summary, session=await _response(db, session))


@router.post(
    "/{import_id}/validate",
    response_model=MigrationValidateResponse,
    summary="Dry run",
    description=(
        "Reports how many documents each entity would create and which Tally "
        "names are still unresolved. Writes nothing."
    ),
)
async def validate(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationValidateResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    summary = await runner.validate_import(db, session, current_user)
    return MigrationValidateResponse(**summary, session=await _response(db, session))


@router.post(
    "/{import_id}/run",
    response_model=MigrationImportResponse,
    status_code=202,
    summary="Start the import",
    description=(
        "Starts the run in the background and returns immediately with status "
        "`Importing` — a full year of vouchers takes far longer than any request "
        "should. Poll `GET /{import_id}/status` for progress.\n\n"
        "Documents are created through the same services the UI uses. Each record "
        "is its own transaction, so a failure part-way keeps everything already "
        "imported."
    ),
)
async def run(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationImportResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    # Refuse here, in the request, so the caller gets a real error rather than a
    # 202 followed by a failure they have to go looking for.
    runner.assert_runnable(session)
    session.status = "Importing"
    session.started_at = datetime.now(timezone.utc)
    session.heartbeat_at = session.started_at
    session.error_message = None
    session.modified_by = current_user.id
    await db.commit()

    background.start_run(session.id, current_user.company_id, current_user)
    return await _response(db, session)


@router.get(
    "/{import_id}/status",
    response_model=MigrationImportStatusResponse,
    summary="Progress of a running import",
    description=(
        "Cheap enough to poll. Reads the counters the run writes as it goes, so "
        "it answers correctly even from a different process than the one running "
        "the import."
    ),
)
async def get_status(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationImportStatusResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    return MigrationImportStatusResponse(**await runner.import_progress(db, session))


@router.post(
    "/{import_id}/rollback",
    response_model=MigrationRollbackResponse,
    summary="Roll back an import",
    description=(
        "Cancels every document this import created (newest first, so payments "
        "unwind before the invoices they settled). Master records are left in "
        "place. Cancellation writes reversing entries — nothing is deleted."
    ),
)
async def rollback(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationRollbackResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    result = await runner.rollback_import(db, session, current_user)
    return MigrationRollbackResponse(**result, session=await _response(db, session))


@router.delete(
    "/{import_id}",
    status_code=204,
    summary="Delete an import session",
    description="Refused while its documents are still live — roll back first.",
)
async def delete_import(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "delete"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    session = await runner.get_import(db, import_id, current_user.company_id)
    await runner.delete_import(db, session, current_user)
