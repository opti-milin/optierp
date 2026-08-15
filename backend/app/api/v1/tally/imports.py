"""Tally import session endpoints — Module 12.

The upload is a JSON body carrying the file base64-encoded rather than a
multipart form. That is deliberate: Tally writes UTF-16 XML as often as UTF-8,
and base64 hands us the exact bytes so the parser can sniff the encoding itself
instead of trusting a browser's guess. It also keeps the API dependency-free.

    curl -X POST /api/v1/tally/imports -H 'Content-Type: application/json' \
      -d "{\"file_name\":\"masters.xml\",\"content_base64\":\"$(base64 -w0 masters.xml)\"}"
"""

import base64
import binascii
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.tally import (
    TallyAutoMapResponse,
    TallyEntitySelection,
    TallyImportEntityResponse,
    TallyImportListItem,
    TallyImportOptions,
    TallyImportResponse,
    TallyImportSummaryResponse,
    TallyRollbackResponse,
    TallyStagingDetailResponse,
    TallyStagingListResponse,
    TallyStagingRecordResponse,
    TallyValidateResponse,
)
from app.services.tally import runner

router = APIRouter(prefix="/tally/imports", tags=["data migration: tally"])

PERMISSION = "Tally Import"

# 64 MB of base64 ~= 48 MB of XML. A year of vouchers for a mid-size MSME is well
# under that; anything larger should be split by period in Tally's export screen.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


class TallyUploadRequest(BaseModel):
    """A Tally export, base64-encoded."""

    file_name: str = Field(max_length=255)
    content_base64: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    #: Migration cut-off — opening balances are booked on this date. Defaults to
    #: the export's period start when Tally wrote one.
    opening_date: date | None = None
    options: TallyImportOptions = Field(default_factory=TallyImportOptions)


def _decode(payload: TallyUploadRequest) -> bytes:
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


async def _response(db: AsyncSession, session) -> TallyImportResponse:
    await db.refresh(session, ["entities"])
    return TallyImportResponse.model_validate(session)


@router.get(
    "",
    response_model=list[TallyImportListItem],
    summary="List Tally import sessions",
    description="Most recent first. One session = one uploaded Tally export.",
)
async def list_imports(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TallyImportListItem]:
    sessions = await runner.list_imports(db, current_user.company_id, limit=limit)
    return [TallyImportListItem.model_validate(s) for s in sessions]


@router.post(
    "",
    response_model=TallyImportResponse,
    status_code=201,
    summary="Upload a Tally export and parse it",
    description=(
        "Accepts a Tally XML export (Gateway of Tally > Export > Format: XML) or "
        "a Day Book CSV. The file is parsed immediately into staging rows; "
        "nothing is written to the ledgers until you call `/run`."
    ),
)
async def create_import(
    payload: TallyUploadRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyImportResponse:
    session = await runner.create_import(
        db,
        current_user,
        raw=_decode(payload),
        file_name=payload.file_name,
        title=payload.title,
        opening_date=payload.opening_date,
        options=payload.options.model_dump(mode="json", exclude_none=True),
    )
    return await _response(db, session)


@router.get(
    "/{import_id}",
    response_model=TallyImportResponse,
    summary="Get one import session",
)
async def get_import(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyImportResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    return await _response(db, session)


@router.get(
    "/{import_id}/summary",
    response_model=TallyImportSummaryResponse,
    summary="Import session summary",
    description="Per-entity progress, activity log and mapping health in one call.",
)
async def get_summary(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyImportSummaryResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    summary = await runner.import_summary(db, session)
    return TallyImportSummaryResponse(
        session=await _response(db, session),
        entities=[TallyImportEntityResponse.model_validate(e) for e in summary["entities"]],
        logs=summary["logs"],
        status_counts=summary["status_counts"],
        unmapped_count=summary["unmapped_count"],
        low_confidence_count=summary["low_confidence_count"],
    )


@router.get(
    "/{import_id}/records",
    response_model=TallyStagingListResponse,
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
) -> TallyStagingListResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    records, total = await runner.staging_records(
        db, session, entity_key=entity_key, status=status, search=search,
        limit=limit, offset=offset,
    )
    return TallyStagingListResponse(
        items=[TallyStagingRecordResponse.model_validate(r) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{import_id}/records/{record_id}",
    response_model=TallyStagingDetailResponse,
    summary="One staged record, including its raw Tally payload",
)
async def get_record(
    import_id: uuid.UUID,
    record_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyStagingDetailResponse:
    from app.core.exceptions import NotFoundError
    from app.models.tally import TallyStagingRecord

    await runner.get_import(db, import_id, current_user.company_id)
    record = await db.get(TallyStagingRecord, record_id)
    if record is None or record.tally_import_id != import_id:
        raise NotFoundError("Staged record not found")
    return TallyStagingDetailResponse.model_validate(record)


@router.post(
    "/{import_id}/entities",
    response_model=list[TallyImportEntityResponse],
    summary="Choose which entities to import",
    description="Example: `{'selections': {'voucher_sales': true, 'budget': false}}`",
)
async def set_entities(
    import_id: uuid.UUID,
    payload: TallyEntitySelection,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[TallyImportEntityResponse]:
    session = await runner.get_import(db, import_id, current_user.company_id)
    entities = await runner.set_entity_selection(db, session, current_user, payload.selections)
    return [TallyImportEntityResponse.model_validate(e) for e in entities]


@router.post(
    "/{import_id}/parse",
    response_model=TallyImportResponse,
    summary="Re-parse the stored file",
    description="Replaces the staging set from the file kept on the session.",
)
async def reparse(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyImportResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    await runner.parse_import(db, session, current_user)
    await db.commit()
    return await _response(db, session)


@router.post(
    "/{import_id}/automap",
    response_model=TallyAutoMapResponse,
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
) -> TallyAutoMapResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    summary = await runner.automap_import(db, session, current_user)
    return TallyAutoMapResponse(summary=summary, session=await _response(db, session))


@router.post(
    "/{import_id}/validate",
    response_model=TallyValidateResponse,
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
) -> TallyValidateResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    summary = await runner.validate_import(db, session, current_user)
    return TallyValidateResponse(**summary, session=await _response(db, session))


@router.post(
    "/{import_id}/run",
    response_model=TallyImportResponse,
    summary="Run the import",
    description=(
        "Creates the documents, posting GL and stock through the same services "
        "the UI uses. Each record is its own transaction, so a failure part-way "
        "keeps everything already imported."
    ),
)
async def run(
    import_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyImportResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    session = await runner.run_import(db, session, current_user)
    return await _response(db, session)


@router.post(
    "/{import_id}/rollback",
    response_model=TallyRollbackResponse,
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
) -> TallyRollbackResponse:
    session = await runner.get_import(db, import_id, current_user.company_id)
    result = await runner.rollback_import(db, session, current_user)
    return TallyRollbackResponse(**result, session=await _response(db, session))


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
