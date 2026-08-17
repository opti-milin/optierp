"""People and the offices they hold."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.secretarial import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentUpdate,
    CeaseAppointmentIn,
    PersonCreate,
    PersonEntityLink,
    PersonListItem,
    PersonResponse,
    PersonUpdate,
)
from app.services.secretarial import persons as service

router = APIRouter(prefix="/secretarial", tags=["secretarial: people"])

PERSON_DOCTYPE = "Secretarial Person"


@router.post("/persons", response_model=PersonResponse, status_code=201, summary="Add a person")
async def create_person(
    payload: PersonCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PersonResponse:
    return PersonResponse.model_validate(await service.create_person(db, payload, current_user))


@router.get(
    "/persons",
    response_model=ListResponse[PersonListItem],
    summary="List people",
    description="Tenant-wide, not entity-scoped — one director record is shared across "
    "every entity they sit on, which is what makes cross-entity batches possible. "
    "Filter by `entity_id` to narrow to one board.",
)
async def list_persons(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    search: str | None = None,
    entity_id: uuid.UUID | None = None,
    role_type: str | None = None,
    active_only: bool = False,
) -> ListResponse[PersonListItem]:
    assert current_user.company_id is not None
    items, total = await service.list_persons(
        db,
        current_user.company_id,
        page,
        page_size,
        search=search,
        entity_id=entity_id,
        role_type=role_type,
        active_only=active_only,
    )
    return ListResponse(
        items=[PersonListItem.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/persons/{person_id}", response_model=PersonResponse, summary="Fetch one person")
async def get_person(
    person_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PersonResponse:
    assert current_user.company_id is not None
    return PersonResponse.model_validate(
        await service.get_person(db, person_id, current_user.company_id)
    )


@router.patch("/persons/{person_id}", response_model=PersonResponse, summary="Update a person")
async def update_person(
    person_id: uuid.UUID,
    payload: PersonUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PersonResponse:
    return PersonResponse.model_validate(
        await service.update_person(db, person_id, payload, current_user)
    )


@router.get(
    "/persons/{person_id}/entities",
    response_model=list[PersonEntityLink],
    summary="Every entity this person holds office in",
    description="The DIN-centric view. This is what a batch MBP-1 or DIR-8 run iterates.",
)
async def person_entities(
    person_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[PersonEntityLink]:
    assert current_user.company_id is not None
    return await service.person_entities(db, person_id, current_user.company_id)


# --- Appointments ----------------------------------------------------------------


@router.post(
    "/appointments",
    response_model=AppointmentResponse,
    status_code=201,
    summary="Appoint someone to office",
    description="Validates the role against the entity kind (a company has directors, an "
    "LLP has designated partners), requires a DIN where the Act does, and refuses to "
    "double-appoint someone whose existing office is still open.",
)
async def create_appointment(
    payload: AppointmentCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AppointmentResponse:
    return AppointmentResponse.model_validate(
        await service.create_appointment(db, payload, current_user)
    )


@router.get(
    "/entities/{entity_id}/appointments",
    response_model=ListResponse[AppointmentResponse],
    summary="Register of directors / partners / KMP for an entity",
)
async def list_appointments(
    entity_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    role_type: str | None = None,
    include_ceased: bool = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ListResponse[AppointmentResponse]:
    assert current_user.company_id is not None
    rows, total = await service.list_appointments(
        db,
        current_user.company_id,
        entity_id,
        role_type=role_type,
        include_ceased=include_ceased,
        page=page,
        page_size=page_size,
    )
    items = []
    for appointment, person_name in rows:
        item = AppointmentResponse.model_validate(appointment)
        item.person_name = person_name
        items.append(item)
    return ListResponse(items=items, total=total, page=page, page_size=page_size)


@router.patch(
    "/appointments/{appointment_id}",
    response_model=AppointmentResponse,
    summary="Update an appointment",
)
async def update_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AppointmentResponse:
    return AppointmentResponse.model_validate(
        await service.update_appointment(db, appointment_id, payload, current_user)
    )


@router.post(
    "/appointments/{appointment_id}/cease",
    response_model=AppointmentResponse,
    summary="Close an appointment",
    description="Resignation, retirement or removal. The row stays on the register with "
    "its dates — it is never deleted, because past minutes refer to it.",
)
async def cease_appointment(
    appointment_id: uuid.UUID,
    payload: CeaseAppointmentIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(PERSON_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AppointmentResponse:
    return AppointmentResponse.model_validate(
        await service.cease_appointment(db, appointment_id, payload, current_user)
    )
