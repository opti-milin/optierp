"""Delegation: granting a CS practice access to one entity, and taking it back.

Read the module docstring of ``services.secretarial.engagement`` before changing
anything here — the authorization rules are load-bearing.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.secretarial import (
    AccessSummary,
    EngagementCreate,
    EngagementEndIn,
    EngagementResponse,
    EngagementUpdate,
    PracticeClientRow,
    PracticeClientUpdate,
)
from app.services.secretarial import engagement as service
from app.services.secretarial import roster as roster_service

router = APIRouter(prefix="/secretarial", tags=["secretarial: delegation"])

DOCTYPE = "Secretarial Engagement"


@router.post(
    "/engagements",
    response_model=EngagementResponse,
    status_code=201,
    summary="Grant a CS firm access to one of your entities",
    description=(
        "Issued by the **client**. Creates the grant in `pending` state — no access is "
        "given until it is activated. Scoped to a single entity; engaging a firm for one "
        "subsidiary never exposes the rest of the group."
    ),
)
async def create_engagement(
    payload: EngagementCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EngagementResponse:
    return EngagementResponse.model_validate(
        await service.create_engagement(db, payload, current_user)
    )


@router.get(
    "/engagements",
    response_model=ListResponse[EngagementResponse],
    summary="Engagements you granted or hold",
    description="`direction=granted` — engagements over your own entities. "
    "`direction=held` — clients who have engaged you. Default returns both.",
)
async def list_engagements(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    direction: Annotated[str, Query(pattern="^(all|granted|held)$")] = "all",
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[EngagementResponse]:
    assert current_user.company_id is not None
    items, total = await service.list_engagements(
        db,
        current_user.company_id,
        direction=direction,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ListResponse(
        items=[EngagementResponse.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/engagements/{engagement_id}",
    response_model=EngagementResponse,
    summary="Fetch one engagement",
)
async def get_engagement(
    engagement_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EngagementResponse:
    assert current_user.company_id is not None
    return EngagementResponse.model_validate(
        await service.get_engagement(db, engagement_id, current_user.company_id)
    )


@router.get(
    "/engagements/{engagement_id}/access-summary",
    response_model=AccessSummary,
    summary="What this grant actually permits, in plain words",
    description=(
        "Shown to the client before they accept. `ledger_read` is the default, which "
        "means a client could otherwise hand over their whole book of accounts by "
        "clicking Accept on a screen full of enum names."
    ),
)
async def access_summary(
    engagement_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AccessSummary:
    assert current_user.company_id is not None
    engagement = await service.get_engagement(db, engagement_id, current_user.company_id)
    return service.access_summary(engagement)


@router.post(
    "/engagements/{engagement_id}/activate",
    response_model=EngagementResponse,
    summary="Activate a grant",
    description="Projects company-scoped roles onto the named users at the firm. They can "
    "then switch into this company; until now they could not.",
)
async def activate_engagement(
    engagement_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EngagementResponse:
    return EngagementResponse.model_validate(
        await service.activate_engagement(db, engagement_id, current_user)
    )


@router.patch(
    "/engagements/{engagement_id}",
    response_model=EngagementResponse,
    summary="Change the scope of a grant",
    description="If the grant is live, roles are re-projected immediately — narrowing "
    "access takes effect now, not at next login.",
)
async def update_engagement(
    engagement_id: uuid.UUID,
    payload: EngagementUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EngagementResponse:
    return EngagementResponse.model_validate(
        await service.update_engagement(db, engagement_id, payload, current_user)
    )


@router.post(
    "/engagements/{engagement_id}/end",
    response_model=EngagementResponse,
    summary="Revoke a grant",
    description="One-sided and immediate: only the client can end it, roles are deleted in "
    "the same transaction, and the record of the engagement stays for the audit trail.",
)
async def end_engagement(
    engagement_id: uuid.UUID,
    payload: EngagementEndIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EngagementResponse:
    return EngagementResponse.model_validate(
        await service.end_engagement(db, engagement_id, payload, current_user)
    )


# --- Practice roster --------------------------------------------------------------


@router.get(
    "/practice/clients",
    response_model=ListResponse[PracticeClientRow],
    summary="The practice client roster",
    description=(
        "Own, managed and delegated clients in one list. Refreshed on read. Counts for "
        "delegated clients are filled by the nightly job, since they live in another "
        "tenant — until it runs they show zero rather than a wrong number."
    ),
)
async def list_clients(
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    search: str | None = None,
    onboarding_state: str | None = None,
    relationship: str | None = None,
    refresh: bool = True,
) -> ListResponse[PracticeClientRow]:
    assert current_user.company_id is not None
    if refresh:
        await roster_service.refresh(db, current_user.company_id)
    items, total = await roster_service.list_clients(
        db,
        current_user.company_id,
        page=page,
        page_size=page_size,
        search=search,
        onboarding_state=onboarding_state,
        relationship=relationship,
    )
    return ListResponse(
        items=[PracticeClientRow.model_validate(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/practice/clients/{client_id}",
    response_model=PracticeClientRow,
    summary="Update a roster row",
    description="Lifecycle and assignment only — identity and counts are derived and get "
    "overwritten on the next refresh.",
)
async def update_client(
    client_id: uuid.UUID,
    payload: PracticeClientUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PracticeClientRow:
    return PracticeClientRow.model_validate(
        await roster_service.update_client(db, client_id, payload, current_user)
    )
