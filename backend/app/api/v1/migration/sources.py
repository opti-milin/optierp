"""Spreadsheet source profiles and the OptiERP import template — Module 12.

A Tally XML export needs none of this: the format is the mapping. A spreadsheet
does, because no file can tell you which of its sheets is the invoice list. These
endpoints are the two halves of that answer —

* ``/migration/sources`` — the shapes we already know (built in), plus the ones
  this company has taught us (saved), so an unrecognised export only ever has to
  be mapped once.
* ``/migration/template.xlsx`` — the shape to aim at when nobody has profiled
  your application at all. Generated from the same catalogue the importer reads,
  so it cannot ask for a column the importer ignores.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.migration import (
    SourceProfileCreate,
    SourceProfileResponse,
    SourceProfileSummary,
)
from app.services.migration import runner, workbooks
from app.services.migration.sources import templates

router = APIRouter(prefix="/migration/sources", tags=["data migration"])

PERMISSION = "Data Migration"


@router.get(
    "",
    summary="Workbook shapes this company can import",
    description=(
        "Built-in profiles (Tally workbook, Tally flat sheet, Zoho Books backup, "
        "the OptiERP template) plus any mapping this company has saved. Also "
        "returns the vocabulary of shapes and fields the mapping wizard offers."
    ),
)
async def list_sources(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    catalogue = workbooks.catalogue()
    saved = await workbooks.list_saved(db, current_user.company_id)
    return {
        **catalogue,
        "saved": [
            SourceProfileSummary(
                key=row.key,
                label=row.label,
                app=row.source_app or "Custom",
                notes=row.notes or "",
                saved=True,
                id=row.id,
                use_count=row.use_count,
            ).model_dump()
            for row in saved
        ],
        "template_version": templates.TEMPLATE_VERSION,
    }


@router.post(
    "",
    response_model=SourceProfileResponse,
    status_code=201,
    summary="Save a workbook mapping for reuse",
    description=(
        "Stores the sheet-and-column mapping under a name, scoped to this "
        "company. The next upload of the same shape is recognised automatically, "
        "so an unprofiled application only has to be mapped once."
    ),
)
async def create_source(
    payload: SourceProfileCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SourceProfileResponse:
    definition = payload.definition
    if definition is None:
        if payload.migration_import_id is None:
            raise ValidationError(
                "Send either a mapping definition or the import to take one from",
                field="definition",
            )
        session = await runner.get_import(
            db, payload.migration_import_id, current_user.company_id
        )
        if not session.sheet_map:
            raise ValidationError(
                "That import has no sheet mapping to save — it was not a spreadsheet.",
                field="migration_import_id",
            )
        definition = session.sheet_map

    row = await workbooks.save_profile(
        db,
        current_user,
        label=payload.label,
        definition=definition,
        source_app=payload.source_app,
        notes=payload.notes,
    )
    return SourceProfileResponse.model_validate(row)


@router.delete(
    "/{profile_id}",
    status_code=204,
    summary="Delete a saved workbook mapping",
)
async def delete_source(
    profile_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "delete"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Response:
    await workbooks.delete_profile(db, current_user.company_id, profile_id)
    return Response(status_code=204)


# The template lives under this router because it is the same question — "what
# shape should my file be?" — answered for an application nobody has profiled.
template_router = APIRouter(prefix="/migration", tags=["data migration"])


@template_router.get(
    "/template.xlsx",
    summary="Download the OptiERP import template",
    description=(
        "An .xlsx with one sheet per importable entity, the exact column headings "
        "the importer reads, a note on every column, and a Fields sheet with a "
        "worked example for each. Fill it in from any system and upload it back — "
        "a filled template needs no mapping step. Pass `entities` to get only the "
        "sheets you need."
    ),
    responses={200: {"content": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
    }}},
)
async def download_template(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    entities: Annotated[
        str | None,
        Query(description="Comma-separated catalogue entity keys, e.g. `ledger,stock_item`"),
    ] = None,
) -> Response:
    wanted = tuple(e for e in (entities or "").split(",") if e.strip())
    content = templates.build(wanted)
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{templates.FILENAME}"'},
    )
