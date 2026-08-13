"""Job Card endpoints — shop-floor time / qty tracking per Work Order operation."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.manufacturing import JobCardCompleteIn, JobCardListItem, JobCardResponse
from app.services import job_card as service

router = APIRouter(prefix="/job-cards", tags=["manufacturing: job card"])


@router.get("", response_model=ListResponse[JobCardListItem], summary="List Job Cards")
async def list_job_cards(
    current_user: Annotated[CurrentUser, Depends(require_permission("Job Card", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    work_order_id: uuid.UUID | None = None,
    status: str | None = None,
) -> ListResponse[JobCardListItem]:
    items, total = await service.list_job_cards(
        db,
        current_user.company_id,
        page,
        page_size,
        work_order_id=work_order_id,
        status=status,
    )
    return ListResponse(
        items=[JobCardListItem.model_validate(j) for j in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{job_card_id}", response_model=JobCardResponse, summary="Get a Job Card")
async def get_job_card(
    job_card_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Job Card", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> JobCardResponse:
    return JobCardResponse.model_validate(
        await service.get_job_card(db, job_card_id, current_user.company_id)
    )


@router.post(
    "/{job_card_id}/start",
    response_model=JobCardResponse,
    summary="Start a Job Card time log",
)
async def start_job_card(
    job_card_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Job Card", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> JobCardResponse:
    return JobCardResponse.model_validate(
        await service.start_job_card(db, job_card_id, current_user)
    )


@router.post(
    "/{job_card_id}/complete",
    response_model=JobCardResponse,
    summary="Complete qty / stop time log on a Job Card",
)
async def complete_job_card(
    job_card_id: uuid.UUID,
    payload: JobCardCompleteIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Job Card", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> JobCardResponse:
    return JobCardResponse.model_validate(
        await service.complete_job_card(db, job_card_id, payload, current_user)
    )


@router.post(
    "/{job_card_id}/cancel",
    response_model=JobCardResponse,
    summary="Cancel a Job Card",
)
async def cancel_job_card(
    job_card_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Job Card", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> JobCardResponse:
    return JobCardResponse.model_validate(
        await service.cancel_job_card(db, job_card_id, current_user)
    )
