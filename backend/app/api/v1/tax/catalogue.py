"""Read-only statutory catalogue browser — Tier 1 law, not tenant-editable."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    AssesseeClassOut,
    AssessmentYearOut,
    CessRuleOut,
    DepreciationBlockOut,
    DueDateRuleOut,
    FinanceActVersionOut,
    IncomeCharacterOut,
    ItrFormOut,
    ProvisionOut,
    RateScheduleOut,
    RebateRuleOut,
    SurchargeScheduleOut,
    TaxRegimeOut,
)
from app.services.taxation import catalogue as cat

router = APIRouter(prefix="/tax/catalogue", tags=["tax: catalogue"])


@router.get(
    "/assessment-years",
    response_model=list[AssessmentYearOut],
    summary="List assessment years",
    description="Global statutory assessment years from the Income-tax Act catalogue.",
)
async def get_assessment_years(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AssessmentYearOut]:
    _ = current_user
    rows = await cat.list_assessment_years(db)
    return [AssessmentYearOut.model_validate(r) for r in rows]


@router.get(
    "/assessee-classes",
    response_model=list[AssesseeClassOut],
    summary="List assessee classes",
)
async def get_assessee_classes(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AssesseeClassOut]:
    _ = current_user
    rows = await cat.list_assessee_classes(db)
    return [AssesseeClassOut.model_validate(r) for r in rows]


@router.get(
    "/regimes",
    response_model=list[TaxRegimeOut],
    summary="List tax regimes",
)
async def get_regimes(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    assessee_class_code: Annotated[str | None, Query()] = None,
) -> list[TaxRegimeOut]:
    _ = current_user
    rows = await cat.list_regimes(db, assessee_class_code=assessee_class_code)
    return [TaxRegimeOut.model_validate(r) for r in rows]


@router.get(
    "/income-characters",
    response_model=list[IncomeCharacterOut],
    summary="List income characters",
)
async def get_income_characters(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[IncomeCharacterOut]:
    _ = current_user
    rows = await cat.list_income_characters(db)
    return [IncomeCharacterOut.model_validate(r) for r in rows]


@router.get(
    "/finance-act/{ay_code}",
    response_model=FinanceActVersionOut | None,
    summary="Current Finance Act version for an AY",
)
async def get_finance_act(
    ay_code: str,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FinanceActVersionOut | None:
    _ = current_user
    row = await cat.get_current_finance_act(db, ay_code=ay_code)
    return FinanceActVersionOut.model_validate(row) if row else None


@router.get(
    "/rate-schedules",
    response_model=list[RateScheduleOut],
    summary="List rate schedules for an assessment year",
)
async def get_rate_schedules(
    ay_code: Annotated[str, Query(description="Assessment year code, e.g. 2025-26")],
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    assessee_class_code: Annotated[str | None, Query()] = None,
    regime_code: Annotated[str | None, Query()] = None,
) -> list[RateScheduleOut]:
    _ = current_user
    rows = await cat.list_rate_schedules(
        db,
        ay_code=ay_code,
        assessee_class_code=assessee_class_code,
        regime_code=regime_code,
    )
    return [RateScheduleOut.model_validate(r) for r in rows]


@router.get(
    "/surcharge-schedules",
    response_model=list[SurchargeScheduleOut],
    summary="List surcharge schedules for an assessment year",
)
async def get_surcharge_schedules(
    ay_code: Annotated[str, Query()],
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    assessee_class_code: Annotated[str | None, Query()] = None,
) -> list[SurchargeScheduleOut]:
    _ = current_user
    rows = await cat.list_surcharge_schedules(db, ay_code=ay_code, assessee_class_code=assessee_class_code)
    return [SurchargeScheduleOut.model_validate(r) for r in rows]


@router.get(
    "/cess-rules",
    response_model=list[CessRuleOut],
    summary="List cess rules for an assessment year",
)
async def get_cess_rules(
    ay_code: Annotated[str, Query()],
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[CessRuleOut]:
    _ = current_user
    rows = await cat.list_cess_rules(db, ay_code=ay_code)
    return [CessRuleOut.model_validate(r) for r in rows]


@router.get(
    "/rebate-rules",
    response_model=list[RebateRuleOut],
    summary="List rebate (87A) rules for an assessment year",
)
async def get_rebate_rules(
    ay_code: Annotated[str, Query()],
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[RebateRuleOut]:
    _ = current_user
    rows = await cat.list_rebate_rules(db, ay_code=ay_code)
    return [RebateRuleOut.model_validate(r) for r in rows]


@router.get(
    "/provisions",
    response_model=list[ProvisionOut],
    summary="List statutory provisions",
)
async def get_provisions(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ProvisionOut]:
    _ = current_user
    rows = await cat.list_provisions(db)
    return [ProvisionOut.model_validate(r) for r in rows]


@router.get(
    "/due-dates",
    response_model=list[DueDateRuleOut],
    summary="List due-date rules (advance tax / ITR) for an AY",
)
async def get_due_dates(
    ay_code: Annotated[str, Query()],
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[DueDateRuleOut]:
    _ = current_user
    rows = await cat.list_due_date_rules(db, ay_code=ay_code)
    return [DueDateRuleOut.model_validate(r) for r in rows]


@router.get(
    "/depreciation-blocks",
    response_model=list[DepreciationBlockOut],
    summary="List IT Act depreciation blocks",
)
async def get_depreciation_blocks(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[DepreciationBlockOut]:
    _ = current_user
    rows = await cat.list_depreciation_blocks(db)
    return [DepreciationBlockOut.model_validate(r) for r in rows]


@router.get(
    "/itr-forms",
    response_model=list[ItrFormOut],
    summary="List ITR forms and field maps for an AY",
)
async def get_itr_forms(
    ay_code: Annotated[str, Query()],
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    form_code: Annotated[str | None, Query()] = None,
) -> list[ItrFormOut]:
    _ = current_user
    rows = await cat.list_itr_forms(db, ay_code=ay_code, form_code=form_code)
    return [ItrFormOut.model_validate(r) for r in rows]
