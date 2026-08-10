"""Tax interest / advance-tax calendar API — Phase 7."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    AdvanceTaxCalendarOut,
    Interest234PreviewOut,
    Interest234PreviewRequest,
    TaxComplianceReminderOut,
)
from app.services.taxation import calendar as service

router = APIRouter(prefix="/tax/calendar", tags=["tax: calendar"])


@router.get(
    "/advance-tax",
    response_model=AdvanceTaxCalendarOut,
    summary="Advance-tax instalment calendar with shortfall projection",
    description=(
        "Uses statutory due_date_rules for the AY and submitted AdvanceTax challans. "
        "Pass estimated_tax or computation_id to size the instalments."
    ),
)
async def advance_tax_calendar(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str, Query()],
    estimated_tax: Annotated[Decimal | None, Query()] = None,
    computation_id: Annotated[UUID | None, Query()] = None,
) -> AdvanceTaxCalendarOut:
    assert current_user.company_id is not None
    return await service.get_advance_tax_calendar(
        db,
        current_user.company_id,
        ay_code=ay_code,
        estimated_tax=estimated_tax,
        computation_id=computation_id,
    )


@router.post(
    "/interest-preview",
    response_model=Interest234PreviewOut,
    summary="Preview 234A/B/C interest from challan dates",
)
async def interest_preview(
    payload: Interest234PreviewRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Interest234PreviewOut:
    assert current_user.company_id is not None
    return await service.preview_interest(db, current_user.company_id, payload)


@router.get(
    "/reminders",
    response_model=list[TaxComplianceReminderOut],
    summary="List compliance reminders already sent",
)
async def list_reminders(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ay_code: Annotated[str | None, Query()] = None,
) -> list[TaxComplianceReminderOut]:
    assert current_user.company_id is not None
    rows = await service.list_reminders(db, current_user.company_id, ay_code=ay_code)
    return [service.reminder_out(r) for r in rows]


@router.post(
    "/reminders/dispatch",
    summary="Dispatch due advance-tax reminders (idempotent)",
    description="Sends reminders for instalments due within 7 days with a shortfall.",
)
async def dispatch_reminders(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, int]:
    assert current_user.company_id is not None
    n = await service.send_due_reminders(db, company_id=current_user.company_id)
    return {"sent": n}
