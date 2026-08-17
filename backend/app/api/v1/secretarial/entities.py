"""Secretarial entities, settings and the module bootstrap."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse, MessageResponse
from app.schemas.secretarial import (
    EntityCreate,
    EntityListItem,
    EntityResponse,
    EntityUpdate,
    SettingsResponse,
    SettingsUpdate,
)
from app.services.secretarial import entity as service

router = APIRouter(prefix="/secretarial", tags=["secretarial: entities"])

DOCTYPE = "Secretarial Entity"


@router.post(
    "/bootstrap",
    response_model=SettingsResponse,
    summary="Prepare the module for this tenant",
    description=(
        "Idempotent. Creates the settings row, and — for a business-profile tenant with "
        "no entity yet — an entity mirroring the tenant's own company, so an MSME never "
        "has to understand what a 'secretarial entity' is."
    ),
)
async def bootstrap(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SettingsResponse:
    assert current_user.company_id is not None
    settings = await service.ensure_bootstrap(db, current_user.company_id, current_user)
    return SettingsResponse.model_validate(settings)


@router.get("/settings", response_model=SettingsResponse, summary="Module settings")
async def read_settings(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SettingsResponse:
    assert current_user.company_id is not None
    settings = await service.get_settings(db, current_user.company_id)
    await db.commit()
    return SettingsResponse.model_validate(settings)


@router.patch(
    "/settings",
    response_model=SettingsResponse,
    summary="Update module settings",
    description="Switching `profile` between practice and business changes which shell "
    "the module opens in; it moves no data.",
)
async def update_settings(
    payload: SettingsUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SettingsResponse:
    assert current_user.company_id is not None
    settings = await service.update_settings(db, current_user.company_id, payload, current_user)
    return SettingsResponse.model_validate(settings)


@router.post("/entities", response_model=EntityResponse, status_code=201, summary="Add an entity")
async def create_entity(
    payload: EntityCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EntityResponse:
    return EntityResponse.model_validate(await service.create_entity(db, payload, current_user))


@router.get("/entities", response_model=ListResponse[EntityListItem], summary="List entities")
async def list_entities(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    search: str | None = None,
    kind: str | None = None,
    status: str | None = None,
) -> ListResponse[EntityListItem]:
    assert current_user.company_id is not None
    items, total = await service.list_entities(
        db, current_user.company_id, page, page_size, search=search, kind=kind, status=status
    )
    return ListResponse(
        items=[EntityListItem.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/entities/{entity_id}", response_model=EntityResponse, summary="Fetch one entity")
async def get_entity(
    entity_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EntityResponse:
    assert current_user.company_id is not None
    return EntityResponse.model_validate(
        await service.fetch_entity(db, entity_id, current_user.company_id)
    )


@router.patch("/entities/{entity_id}", response_model=EntityResponse, summary="Update an entity")
async def update_entity(
    entity_id: uuid.UUID,
    payload: EntityUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EntityResponse:
    return EntityResponse.model_validate(
        await service.update_entity(db, entity_id, payload, current_user)
    )


@router.delete(
    "/entities/{entity_id}",
    response_model=MessageResponse,
    summary="Delete an entity",
    description="For correcting a mistaken entry only. An entity with statutory history "
    "should be closed by setting its status instead.",
)
async def delete_entity(
    entity_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "delete"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MessageResponse:
    await service.delete_entity(db, entity_id, current_user)
    return MessageResponse(message="Entity deleted")
