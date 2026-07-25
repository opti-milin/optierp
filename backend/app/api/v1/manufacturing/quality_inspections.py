"""Quality Inspection endpoints (Phase 5 lean manufacturing gate)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.quality import (
    QualityInspectionCreate,
    QualityInspectionListItem,
    QualityInspectionResponse,
)
from app.services import quality_inspection as service

router = APIRouter(prefix="/quality-inspections", tags=["quality"])


@router.post(
    "",
    response_model=QualityInspectionResponse,
    status_code=201,
    summary="Create a Quality Inspection",
)
async def create_quality_inspection(
    payload: QualityInspectionCreate,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Quality Inspection", "create"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QualityInspectionResponse:
    return QualityInspectionResponse.model_validate(
        await service.create_quality_inspection(db, payload, current_user)
    )


@router.get(
    "",
    response_model=ListResponse[QualityInspectionListItem],
    summary="List Quality Inspections",
)
async def list_quality_inspections(
    current_user: Annotated[CurrentUser, Depends(require_permission("Quality Inspection", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    status: str | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
) -> ListResponse[QualityInspectionListItem]:
    items, total = await service.list_quality_inspections(
        db,
        current_user.company_id,
        page,
        page_size,
        status=status,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    return ListResponse(
        items=[QualityInspectionListItem.model_validate(q) for q in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{qi_id}",
    response_model=QualityInspectionResponse,
    summary="Get a Quality Inspection",
)
async def get_quality_inspection(
    qi_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Quality Inspection", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QualityInspectionResponse:
    return QualityInspectionResponse.model_validate(
        await service.get_quality_inspection(db, qi_id, current_user.company_id)
    )


@router.post(
    "/{qi_id}/accept",
    response_model=QualityInspectionResponse,
    summary="Accept a Quality Inspection",
)
async def accept_quality_inspection(
    qi_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Quality Inspection", "submit"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QualityInspectionResponse:
    return QualityInspectionResponse.model_validate(
        await service.submit_quality_inspection(db, qi_id, current_user, accept=True)
    )


@router.post(
    "/{qi_id}/reject",
    response_model=QualityInspectionResponse,
    summary="Reject a Quality Inspection",
)
async def reject_quality_inspection(
    qi_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Quality Inspection", "submit"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QualityInspectionResponse:
    return QualityInspectionResponse.model_validate(
        await service.submit_quality_inspection(db, qi_id, current_user, accept=False)
    )


@router.post(
    "/{qi_id}/cancel",
    response_model=QualityInspectionResponse,
    summary="Cancel a Quality Inspection",
)
async def cancel_quality_inspection(
    qi_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Quality Inspection", "cancel"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> QualityInspectionResponse:
    return QualityInspectionResponse.model_validate(
        await service.cancel_quality_inspection(db, qi_id, current_user)
    )
