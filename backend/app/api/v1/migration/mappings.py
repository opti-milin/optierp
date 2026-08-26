"""Tally name-mapping endpoints — Module 12.

These back the wizard's "Mappings" tab: review what auto-map proposed, sort by
confidence, and correct anything it guessed wrong. A correction is locked, so
re-running auto-map (or importing a second file from the same Tally company)
keeps the tester's decision.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.migration import (
    MigrationMappingCreate,
    MigrationMappingResponse,
    MigrationMappingUpdate,
)
from app.services.migration import mapping as mapping_service

router = APIRouter(prefix="/migration/mappings", tags=["data migration"])

PERMISSION = "Data Migration"


@router.get(
    "",
    response_model=list[MigrationMappingResponse],
    summary="List Tally name mappings",
    description=(
        "Every Tally name this company has seen and what it points at. Sorted "
        "least-confident first so the rows worth checking are at the top."
    ),
)
async def list_mappings(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_key: str | None = None,
    unresolved_only: bool = False,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[MigrationMappingResponse]:
    rows = await mapping_service.list_mappings(
        db, current_user.company_id,
        entity_key=entity_key, unresolved_only=unresolved_only, search=search,
    )
    return [MigrationMappingResponse.model_validate(m) for m in rows[:limit]]


@router.get(
    "/targets",
    summary="Candidate records for a mapping target",
    description=(
        "Feeds the mapping editor's picker. `target_doctype` is one of Account, "
        "Customer, Supplier, Item, Item Group, Warehouse, Cost Center, UOM, "
        "Currency, Price List."
    ),
)
async def list_targets(
    target_doctype: str,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, str]]:
    candidates = await mapping_service.load_candidates(
        db, current_user.company_id, target_doctype
    )
    if search:
        needle = search.casefold()
        candidates = [c for c in candidates if needle in c.name.casefold()]
    seen: set[uuid.UUID] = set()
    unique = []
    for candidate in sorted(candidates, key=lambda c: c.name):
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        unique.append({"id": str(candidate.id), "name": candidate.name, **{
            k: str(v) for k, v in candidate.extra.items() if v is not None
        }})
        if len(unique) >= limit:
            break
    return unique


@router.post(
    "",
    response_model=MigrationMappingResponse,
    status_code=201,
    summary="Create a mapping by hand",
    description="Use this to pre-map a name before uploading, or to add one auto-map missed.",
)
async def create_mapping(
    payload: MigrationMappingCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationMappingResponse:
    row = await mapping_service.upsert_mapping(
        db, current_user.company_id, current_user,
        entity_key=payload.entity_key,
        source_name=payload.source_name,
        target_doctype=payload.target_doctype,
        target_id=payload.target_id,
        match_method="manual",
        lock=True,
    )
    if payload.notes is not None:
        row.notes = payload.notes
    await db.commit()
    return MigrationMappingResponse.model_validate(row)


@router.put(
    "/{mapping_id}",
    response_model=MigrationMappingResponse,
    summary="Correct a mapping",
    description=(
        "Locks the row: later auto-map runs, and later imports from the same "
        "Tally company, will keep this decision."
    ),
)
async def update_mapping(
    mapping_id: uuid.UUID,
    payload: MigrationMappingUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MigrationMappingResponse:
    row = await mapping_service.set_mapping(
        db, current_user.company_id, current_user, mapping_id,
        target_doctype=payload.target_doctype,
        target_id=payload.target_id,
        notes=payload.notes,
    )
    await db.commit()
    return MigrationMappingResponse.model_validate(row)


@router.delete(
    "/{mapping_id}",
    status_code=204,
    summary="Forget a mapping",
    description="The next auto-map run will propose it again from scratch.",
)
async def delete_mapping(
    mapping_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "delete"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    await mapping_service.delete_mapping(db, current_user.company_id, mapping_id)
    await db.commit()
