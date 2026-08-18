"""The compliance calendar, and the review workflow that gates what feeds it."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.secretarial import (
    ComplianceItemListItem,
    ComplianceItemUpdate,
    ComplianceRuleResponse,
    ComplianceRuleReviewIn,
    GenerateCalendarIn,
    GenerateCalendarResult,
)
from app.services.secretarial import compliance as service
from app.services.secretarial import content as content_service

router = APIRouter(prefix="/secretarial/compliance", tags=["secretarial: compliance"])

ITEM_DOCTYPE = "Secretarial Compliance Item"
RULE_DOCTYPE = "Secretarial Compliance Rule"


@router.get(
    "/rules",
    response_model=list[ComplianceRuleResponse],
    summary="The compliance rule catalogue",
    description=(
        "Includes unpublished drafts by default so the legal-content workstream can see "
        "its own backlog. Only `published` rules ever generate calendar rows."
    ),
)
async def list_rules(
    current_user: Annotated[CurrentUser, Depends(require_permission(RULE_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    review_status: str | None = None,
    include_unpublished: bool = True,
) -> list[ComplianceRuleResponse]:
    assert current_user.company_id is not None
    rules = await content_service.list_rules(
        db,
        current_user.company_id,
        review_status=review_status,
        include_unpublished=include_unpublished,
    )
    return [ComplianceRuleResponse.model_validate(r) for r in rules]


@router.get(
    "/rules/review-status",
    summary="How much statutory content is signed off",
    description="Counts by review state. Surfaced so 'the engine works' is never read as "
    "'the module is ready'.",
)
async def review_status(
    current_user: Annotated[CurrentUser, Depends(require_permission(RULE_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, int]:
    assert current_user.company_id is not None
    return await content_service.review_status_counts(db, current_user.company_id)


@router.post(
    "/rules/{rule_id}/review",
    response_model=ComplianceRuleResponse,
    summary="Advance a rule through the review workflow",
    description=(
        "draft → reviewed → approved → published. Publishing requires the reviewer's name "
        "and the date they reviewed it: statutory wording is never published anonymously, "
        "and the database enforces the same rule."
    ),
)
async def review_rule(
    rule_id: uuid.UUID,
    payload: ComplianceRuleReviewIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(RULE_DOCTYPE, "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ComplianceRuleResponse:
    return ComplianceRuleResponse.model_validate(
        await content_service.review_rule(db, rule_id, payload, current_user)
    )


@router.post(
    "/generate",
    response_model=GenerateCalendarResult,
    summary="Generate the calendar for a financial year",
    description=(
        "Safe to re-run. Inserts rows that do not exist and corrects due dates on rows "
        "nobody has started; never overwrites a status, assignee or SRN."
    ),
)
async def generate(
    payload: GenerateCalendarIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> GenerateCalendarResult:
    return await service.generate_calendar(db, payload, current_user)


@router.get(
    "/items",
    response_model=ListResponse[ComplianceItemListItem],
    summary="Calendar rows",
)
async def list_items(
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    fy: str | None = None,
    status: str | None = None,
    open_only: bool = False,
    due_before: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ListResponse[ComplianceItemListItem]:
    assert current_user.company_id is not None
    rows, total = await service.list_items(
        db,
        current_user.company_id,
        entity_id=entity_id,
        fy=fy,
        status=status,
        open_only=open_only,
        due_before=due_before,
        page=page,
        page_size=page_size,
    )
    today = date.today()
    items = []
    for item, entity_name in rows:
        out = ComplianceItemListItem.model_validate(item)
        out.entity_name = entity_name
        out.days_to_due = (item.due_on - today).days
        items.append(out)
    return ListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/summary", summary="Calendar counts by status")
async def summary(
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
) -> dict[str, int]:
    assert current_user.company_id is not None
    return await service.summary(db, current_user.company_id, entity_id=entity_id)


@router.patch(
    "/items/{item_id}",
    response_model=ComplianceItemListItem,
    summary="Update a calendar row",
    description="Waiving an obligation requires a reason, both here and in a DB check "
    "constraint — a waiver with no explanation is worse than an overdue row.",
)
async def update_item(
    item_id: uuid.UUID,
    payload: ComplianceItemUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ComplianceItemListItem:
    item = await service.update_item(db, item_id, payload, current_user)
    out = ComplianceItemListItem.model_validate(item)
    out.days_to_due = (item.due_on - date.today()).days
    return out


@router.post(
    "/refresh-statuses",
    summary="Roll upcoming → due → overdue",
    description="Runs nightly; exposed here so the transition is testable without waiting "
    "for the clock.",
)
async def refresh_statuses(
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, int]:
    assert current_user.company_id is not None
    changed = await service.refresh_statuses(db, current_user.company_id)
    return {"updated": changed}
