"""Subcontract Job endpoints — send materials / receive FG (Phase 4)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.manufacturing import (
    SubcontractJobActionResult,
    SubcontractJobCreate,
    SubcontractJobListItem,
    SubcontractJobReceiveIn,
    SubcontractJobResponse,
    SubcontractJobSendIn,
)
from app.services import subcontract_job as service

router = APIRouter(prefix="/subcontract-jobs", tags=["manufacturing: subcontract"])


@router.post(
    "",
    response_model=SubcontractJobResponse,
    status_code=201,
    summary="Create a Subcontract Job",
)
async def create_subcontract_job(
    payload: SubcontractJobCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Subcontract Job", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SubcontractJobResponse:
    return SubcontractJobResponse.model_validate(
        await service.create_subcontract_job(db, payload, current_user)
    )


@router.get(
    "",
    response_model=ListResponse[SubcontractJobListItem],
    summary="List Subcontract Jobs",
)
async def list_subcontract_jobs(
    current_user: Annotated[CurrentUser, Depends(require_permission("Subcontract Job", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    status: str | None = None,
) -> ListResponse[SubcontractJobListItem]:
    items, total = await service.list_subcontract_jobs(
        db, current_user.company_id, page, page_size, status=status
    )
    return ListResponse(
        items=[SubcontractJobListItem.model_validate(j) for j in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{job_id}",
    response_model=SubcontractJobResponse,
    summary="Get a Subcontract Job",
)
async def get_subcontract_job(
    job_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Subcontract Job", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SubcontractJobResponse:
    return SubcontractJobResponse.model_validate(
        await service.get_subcontract_job(db, job_id, current_user.company_id)
    )


@router.post(
    "/{job_id}/submit",
    response_model=SubcontractJobResponse,
    summary="Submit a Subcontract Job",
)
async def submit_subcontract_job(
    job_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Subcontract Job", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SubcontractJobResponse:
    return SubcontractJobResponse.model_validate(
        await service.submit_subcontract_job(db, job_id, current_user)
    )


@router.post(
    "/{job_id}/cancel",
    response_model=SubcontractJobResponse,
    summary="Cancel a Subcontract Job",
)
async def cancel_subcontract_job(
    job_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Subcontract Job", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SubcontractJobResponse:
    return SubcontractJobResponse.model_validate(
        await service.cancel_subcontract_job(db, job_id, current_user)
    )


@router.post(
    "/{job_id}/send",
    response_model=SubcontractJobActionResult,
    summary="Send materials to subcontractor",
    description="Posts a Send-to-Subcontractor Stock Entry (source → supplier warehouse).",
)
async def send_to_subcontractor(
    job_id: uuid.UUID,
    payload: SubcontractJobSendIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Subcontract Job", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SubcontractJobActionResult:
    job, entry = await service.send_to_subcontractor(db, job_id, payload, current_user)
    return SubcontractJobActionResult(
        job=SubcontractJobResponse.model_validate(job),
        stock_entry_id=entry.id,
        stock_entry_name=entry.name,
    )


@router.post(
    "/{job_id}/receive",
    response_model=SubcontractJobActionResult,
    summary="Receive finished goods from subcontractor",
    description="Posts a Subcontract Receipt Stock Entry (consume at supplier WH + FG in).",
)
async def receive_from_subcontractor(
    job_id: uuid.UUID,
    payload: SubcontractJobReceiveIn,
    current_user: Annotated[CurrentUser, Depends(require_permission("Subcontract Job", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> SubcontractJobActionResult:
    job, entry = await service.receive_from_subcontractor(db, job_id, payload, current_user)
    return SubcontractJobActionResult(
        job=SubcontractJobResponse.model_validate(job),
        stock_entry_id=entry.id,
        stock_entry_name=entry.name,
    )
