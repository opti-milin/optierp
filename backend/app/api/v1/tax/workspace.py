"""Unified Income Tax workspace — aggregate read, live preview and entry helpers.

Thin by contract: every handler resolves the tenant, delegates to
``app.services.taxation`` and serialises. Per-computation routes live under an
explicit ``/computation/{computation_id}`` segment so that a path parameter can
never shadow a static path such as ``/lookups``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    TaxComputationOut,
    TaxCopyPreviousYearIn,
    TaxCopyPreviousYearOut,
    TaxLookupsOut,
    TaxPopulateFromBooksIn,
    TaxPopulateFromBooksOut,
    TaxPreviewOut,
    TaxPreviewRequest,
    TaxStatementOut,
    TaxTemplateApplyIn,
    TaxTemplateOut,
    TaxValidationOut,
    TaxWorkspaceBootstrapOut,
    TaxWorkspaceOut,
)
from app.services.taxation import books as books_service
from app.services.taxation import computations as computations_service
from app.services.taxation import lookups as lookups_service
from app.services.taxation import preview as preview_service
from app.services.taxation import statement as statement_service
from app.services.taxation import templates as templates_service
from app.services.taxation import validation as validation_service
from app.services.taxation import workspace as workspace_service

router = APIRouter(prefix="/tax/workspace", tags=["tax: workspace"])

ReadUser = Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))]
WriteUser = Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))]
Db = Annotated[AsyncSession, Depends(get_tenant_db)]


# ---------------------------------------------------------------------------
# Company-wide entry points.
# ---------------------------------------------------------------------------


@router.get(
    "/bootstrap",
    response_model=TaxWorkspaceBootstrapOut,
    summary="Cold start for the Income Tax landing screen",
    description=(
        "Returns the company's tax registration, one status card per recent assessment "
        "year with its next action and due dates, the available starting templates and "
        "the shared option lists — in a single round trip."
    ),
)
async def get_bootstrap(
    current_user: ReadUser,
    db: Db,
    ay_code: Annotated[
        str | None,
        Query(description="Assessment year to prepare templates and options for."),
    ] = None,
) -> TaxWorkspaceBootstrapOut:
    assert current_user.company_id is not None
    return await workspace_service.bootstrap(
        db, company_id=current_user.company_id, ay_code=ay_code
    )


@router.get(
    "/lookups",
    response_model=TaxLookupsOut,
    summary="Option lists that drive every dropdown in the workspace",
    description=(
        "Assessment years, entity classes, regimes, income characters, statutory "
        "provisions, depreciation blocks, deduction sections, bank branch codes, "
        "deductors and ledger accounts, each already labelled in business terms."
    ),
)
async def get_lookups(
    current_user: ReadUser,
    db: Db,
    ay_code: Annotated[
        str | None, Query(description="Assessment year whose statutory options to return.")
    ] = None,
) -> TaxLookupsOut:
    assert current_user.company_id is not None
    return await lookups_service.build_lookups(
        db, company_id=current_user.company_id, ay_code=ay_code
    )


@router.get(
    "/templates",
    response_model=list[TaxTemplateOut],
    summary="Starting presets for a new computation",
    description=(
        "System-supplied presets such as a private limited company on the normal "
        "provisions or a company electing Section 115BAA. Each preset reports whether "
        "it is available for the assessment year and which incentives it forfeits."
    ),
)
async def list_templates(
    current_user: ReadUser,
    db: Db,
    ay_code: Annotated[
        str | None, Query(description="Assessment year to check preset availability against.")
    ] = None,
) -> list[TaxTemplateOut]:
    assert current_user.company_id is not None
    return await templates_service.list_templates(
        db, company_id=current_user.company_id, ay_code=ay_code
    )


@router.post(
    "/templates/apply",
    response_model=TaxComputationOut,
    summary="Start a computation from a template",
    description=(
        "Creates the computation for the assessment year with the template's entity "
        "class, regime election, income heads and standing adjustments already in place, "
        "optionally carrying the previous year forward and pulling figures from the books."
    ),
)
async def apply_template(
    payload: TaxTemplateApplyIn,
    current_user: WriteUser,
    db: Db,
) -> TaxComputationOut:
    doc = await templates_service.apply_template(db, payload, current_user)
    return computations_service.computation_out(doc)


# ---------------------------------------------------------------------------
# Per-computation paths.
# ---------------------------------------------------------------------------


@router.get(
    "/computation/{computation_id}",
    response_model=TaxWorkspaceOut,
    summary="Everything the Income Tax workspace renders, in one call",
    description=(
        "Aggregates the computation, its context for field visibility, depreciation "
        "register, brought forward losses and set-off, Minimum Alternate Tax credit "
        "ledger, taxes already paid, challans, Form 26AS reconciliations, advance tax "
        "schedule, filings, computation runs, per-section status and the review issues."
    ),
)
async def get_workspace(
    computation_id: UUID,
    current_user: ReadUser,
    db: Db,
) -> TaxWorkspaceOut:
    assert current_user.company_id is not None
    return await workspace_service.get_workspace(
        db, company_id=current_user.company_id, computation_id=computation_id
    )


@router.post(
    "/computation/{computation_id}/preview",
    response_model=TaxPreviewOut,
    summary="Recompute live without saving anything",
    description=(
        "Runs the same pipeline a saved computation run would, optionally over unsaved "
        "editor rows, and persists nothing. Used by the live result rail; the append-only "
        "run history is untouched."
    ),
)
async def preview(
    computation_id: UUID,
    payload: TaxPreviewRequest | None,
    current_user: ReadUser,
    db: Db,
) -> TaxPreviewOut:
    assert current_user.company_id is not None
    return await preview_service.preview_computation(
        db,
        company_id=current_user.company_id,
        computation_id=computation_id,
        overlay=payload,
    )


@router.get(
    "/computation/{computation_id}/validate",
    response_model=TaxValidationOut,
    summary="Blocking errors and advisory warnings for this computation",
    description=(
        "Each issue names the workspace section and field that caused it, so the Review "
        "panel can link straight to it. Only blocking issues prevent submission."
    ),
)
async def validate(
    computation_id: UUID,
    current_user: ReadUser,
    db: Db,
) -> TaxValidationOut:
    assert current_user.company_id is not None
    return await validation_service.validate_computation(
        db, company_id=current_user.company_id, computation_id=computation_id
    )


@router.get(
    "/computation/{computation_id}/statement",
    response_model=TaxStatementOut,
    summary="Statement of Total Income and Tax Computation",
    description=(
        "The reviewer's statement in reading order, with variances against the previous "
        "assessment year and against the books of account."
    ),
)
async def get_statement(
    computation_id: UUID,
    current_user: ReadUser,
    db: Db,
) -> TaxStatementOut:
    assert current_user.company_id is not None
    return await statement_service.build_statement(
        db, company_id=current_user.company_id, computation_id=computation_id
    )


@router.post(
    "/computation/{computation_id}/copy-previous-year",
    response_model=TaxCopyPreviousYearOut,
    summary="Carry last year's computation forward",
    description=(
        "Copies the income structure, standing adjustments and depreciation blocks from "
        "the previous assessment year with all amounts zeroed, carries closing written "
        "down values forward as opening values, and reports what was carried."
    ),
)
async def copy_previous_year(
    computation_id: UUID,
    payload: TaxCopyPreviousYearIn,
    current_user: WriteUser,
    db: Db,
) -> TaxCopyPreviousYearOut:
    return await templates_service.copy_previous_year(
        db, computation_id=computation_id, payload=payload, user=current_user
    )


@router.post(
    "/computation/{computation_id}/populate-from-books",
    response_model=TaxPopulateFromBooksOut,
    summary="Populate the worksheet from the general ledger",
    description=(
        "Reads net profit and depreciation charged in the books for the relevant "
        "financial year, seeds the business income line and adds back accounting "
        "depreciation, leaving existing manual rows untouched unless asked to replace."
    ),
)
async def populate_from_books(
    computation_id: UUID,
    payload: TaxPopulateFromBooksIn,
    current_user: WriteUser,
    db: Db,
) -> TaxPopulateFromBooksOut:
    assert current_user.company_id is not None
    return await books_service.populate_from_books(
        db, computation_id=computation_id, payload=payload, user=current_user
    )
