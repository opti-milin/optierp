"""Contribution Margin Plan API — pre-sales planning + scenarios."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.cm_planning import (
    CmActualComparison,
    CmCompareRequest,
    CmCompareResponse,
    CmCostStructureOut,
    CmCostStructureUpdate,
    CmPlanListItem,
    CmPlanResponse,
    CmPlanScenarioCreate,
    CmPlanScenarioUpdate,
    CmPlanUpdate,
    CmPlanningSettings,
    CmPlanningTemplateInfo,
    CmPreviewRequest,
)
from app.schemas.common import ListResponse
from app.services.cm_planning import plan as service
from app.services.cm_planning import settings as planning_settings

router = APIRouter(tags=["selling: contribution margin planning"])


def _company(user: CurrentUser) -> uuid.UUID:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    return user.company_id


@router.get(
    "/cm-planning/templates",
    response_model=list[CmPlanningTemplateInfo],
    summary="List CM planning industry templates",
)
async def list_templates(
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "read"))],
) -> list[CmPlanningTemplateInfo]:
    _ = current_user
    return planning_settings.list_planning_templates()


@router.get(
    "/cm-planning/settings",
    response_model=CmPlanningSettings,
    summary="Get CM planning settings",
)
async def get_settings(
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanningSettings:
    return await planning_settings.get_planning_settings(db, _company(current_user))


@router.put(
    "/cm-planning/settings",
    response_model=CmPlanningSettings,
    summary="Save CM planning settings",
)
async def put_settings(
    payload: CmPlanningSettings,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanningSettings:
    return await planning_settings.save_planning_settings(db, payload, current_user)


@router.get(
    "/cm-planning/cost-structure",
    response_model=CmCostStructureOut,
    summary="Get active CM cost structure (drivers + method/basis)",
)
async def get_cost_structure(
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmCostStructureOut:
    from app.services.cm_planning import structure as structure_service

    data = await structure_service.get_cost_structure_payload(db, _company(current_user))
    return CmCostStructureOut.model_validate(data)


@router.put(
    "/cm-planning/cost-structure",
    response_model=CmCostStructureOut,
    summary="Replace active CM cost structure drivers",
)
async def put_cost_structure(
    payload: CmCostStructureUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmCostStructureOut:
    from app.services.cm_planning import structure as structure_service

    data = await structure_service.replace_structure_drivers(
        db,
        _company(current_user),
        [d.model_dump(mode="json") for d in payload.drivers],
        current_user,
        name=payload.name,
        template_id=payload.template_id,
    )
    await db.commit()
    return CmCostStructureOut.model_validate(data)


@router.post(
    "/cm-planning/apply-template",
    response_model=CmPlanningSettings,
    summary="Apply a planning template into company settings",
)
async def apply_template(
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    template_id: Annotated[str, Query(min_length=1)],
) -> CmPlanningSettings:
    from app.services.cm_planning import structure as structure_service

    await structure_service.apply_template_to_structure(
        db, _company(current_user), template_id, current_user
    )
    await db.commit()
    return await planning_settings.get_planning_settings(db, _company(current_user))


@router.post(
    "/cm-plans/preview",
    summary="Ephemeral CM estimate (no persist)",
)
async def preview(
    payload: CmPreviewRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    return await service.preview_estimate(db, payload, _company(current_user))


@router.post(
    "/cm-plans/from-quotation/{quotation_id}",
    response_model=CmPlanResponse,
    status_code=201,
    summary="Create or recompute draft CM Plan baseline from Quotation",
)
async def from_quotation(
    quotation_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(await service.seed_from_quotation(db, quotation_id, current_user))


@router.post(
    "/cm-plans/from-sales-order/{sales_order_id}",
    response_model=CmPlanResponse,
    status_code=201,
    summary="Create CM Plan baseline from Sales Order",
)
async def from_sales_order(
    sales_order_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(await service.seed_from_sales_order(db, sales_order_id, current_user))


@router.get(
    "/cm-plans",
    response_model=ListResponse[CmPlanListItem],
    summary="List Contribution Margin Plans",
)
async def list_plans(
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[CmPlanListItem]:
    rows, total = await service.list_plans(db, _company(current_user), page, page_size)
    return ListResponse(
        items=[CmPlanListItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/cm-plans/{plan_id}",
    response_model=CmPlanResponse,
    summary="Get a Contribution Margin Plan with scenarios",
)
async def get_plan(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(await service.get_plan(db, plan_id, _company(current_user)))


@router.put(
    "/cm-plans/{plan_id}",
    response_model=CmPlanResponse,
    summary="Update draft plan policy (targets, mins, allocations)",
    description="Edits target/min CM1, submit policy, and allocation % snapshot. "
    "Recomputes all scenarios by default.",
)
async def put_plan(
    plan_id: uuid.UUID,
    payload: CmPlanUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(await service.update_plan(db, plan_id, payload, current_user))


@router.get(
    "/cm-plans/{plan_id}/actual-comparison",
    response_model=CmActualComparison,
    summary="Estimated vs Actual (GL Contribution Margin)",
    description="Compares plan scenario waterfall (default baseline) to company GL CM "
    "report for the date range. Lean Phase 4 — deal estimate vs company actuals.",
)
async def get_actual_comparison(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
    scenario_id: uuid.UUID | None = None,
) -> CmActualComparison:
    return await service.actual_comparison(
        db,
        plan_id,
        _company(current_user),
        from_date=from_date,
        to_date=to_date,
        scenario_id=scenario_id,
    )


@router.post(
    "/cm-plans/{plan_id}/scenarios",
    response_model=CmPlanResponse,
    summary="Clone a scenario with overrides",
)
async def add_scenario(
    plan_id: uuid.UUID,
    payload: CmPlanScenarioCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(await service.create_scenario(db, plan_id, payload, current_user))


@router.put(
    "/cm-plans/{plan_id}/scenarios/{scenario_id}",
    response_model=CmPlanResponse,
    summary="Update scenario overrides and recompute",
)
async def put_scenario(
    plan_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: CmPlanScenarioUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(
        await service.update_scenario(db, plan_id, scenario_id, payload, current_user)
    )


@router.delete(
    "/cm-plans/{plan_id}/scenarios/{scenario_id}",
    response_model=CmPlanResponse,
    summary="Delete a non-baseline scenario",
)
async def remove_scenario(
    plan_id: uuid.UUID,
    scenario_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(
        await service.delete_scenario(db, plan_id, scenario_id, current_user)
    )


@router.post(
    "/cm-plans/{plan_id}/compare",
    response_model=CmCompareResponse,
    summary="Compare scenarios side-by-side",
)
async def compare(
    plan_id: uuid.UUID,
    payload: CmCompareRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmCompareResponse:
    return await service.compare_scenarios(db, plan_id, payload, _company(current_user))


@router.post(
    "/cm-plans/{plan_id}/compare.csv",
    summary="Export scenario compare as CSV",
    response_class=Response,
    description="Same payload as /compare; returns text/csv for download.",
)
async def compare_csv(
    plan_id: uuid.UUID,
    payload: CmCompareRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Response:
    result = await service.compare_scenarios(db, plan_id, payload, _company(current_user))
    body = service.compare_to_csv(result)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="cm-plan-{plan_id}-compare.csv"'},
    )


@router.post(
    "/cm-plans/{plan_id}/scenarios/{scenario_id}/apply-to-quotation",
    response_model=CmPlanResponse,
    summary="Copy scenario rates onto the linked draft Quotation",
)
async def apply_to_quotation(
    plan_id: uuid.UUID,
    scenario_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(
        await service.apply_scenario_to_quotation(db, plan_id, scenario_id, current_user)
    )


@router.post(
    "/cm-plans/{plan_id}/submit",
    response_model=CmPlanResponse,
    summary="Submit a CM Plan",
)
async def submit(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "submit"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(await service.submit_plan(db, plan_id, current_user))


@router.post(
    "/cm-plans/{plan_id}/cancel",
    response_model=CmPlanResponse,
    summary="Cancel a submitted CM Plan",
)
async def cancel(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Contribution Margin Plan", "cancel"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> CmPlanResponse:
    return service.to_response(await service.cancel_plan(db, plan_id, current_user))
