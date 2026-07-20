"""Income Tax Computation endpoints — entity ITR worksheet (Phase 1+) + export / calendar / 26AS."""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.compliance import (
    AdvanceTaxCalendar,
    AdvanceTaxInstalment,
    Form26asReconReport,
    Form26asReconRequest,
    IncomeTaxComputationCreate,
    IncomeTaxComputationListItem,
    IncomeTaxComputationResponse,
    IncomeTaxComputationUpdate,
    Itr6ExportPack,
    ItrEfileResult,
)
from app.services import form26as_recon, income_tax_computation as service, itr_efile, itr_export

router = APIRouter(prefix="/income-tax-computations", tags=["compliance: income tax"])


@router.post(
    "",
    response_model=IncomeTaxComputationResponse,
    status_code=201,
    summary="Create an income-tax computation (draft)",
    description="Seeds book profit from P&L and TDS credit from the purchase register "
    "(unless seed_from_books is false), applies adjustments, and computes tax.",
)
async def create_computation(
    payload: IncomeTaxComputationCreate,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "create"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> IncomeTaxComputationResponse:
    return IncomeTaxComputationResponse.model_validate(
        await service.create_computation(db, payload, current_user)
    )


@router.get(
    "",
    response_model=ListResponse[IncomeTaxComputationListItem],
    summary="List income-tax computations",
)
async def list_computations(
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "read"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    status: str | None = None,
) -> ListResponse[IncomeTaxComputationListItem]:
    rows, total = await service.list_computations(
        db, current_user.company_id, page, page_size, status=status
    )
    return ListResponse(
        items=[IncomeTaxComputationListItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/advance-tax-calendar",
    response_model=AdvanceTaxCalendar,
    summary="Advance-tax instalment calendar for an assessment year",
    description="Statutory due dates (15 Jun / 15 Sep / 15 Dec / 15 Mar). Optional "
    "total_tax fills suggested cumulative amounts.",
)
async def get_advance_tax_calendar(
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "read"))
    ],
    assessment_year: Annotated[str, Query(min_length=4, max_length=20)],
    total_tax: Annotated[Decimal | None, Query()] = None,
) -> AdvanceTaxCalendar:
    _ = current_user
    rows = itr_export.advance_tax_instalments(assessment_year)
    instalments: list[AdvanceTaxInstalment] = []
    for r in rows:
        suggested = None
        if total_tax is not None:
            suggested = (total_tax * Decimal(r["cumulative_percent"]) / Decimal("100")).quantize(
                Decimal("0.01")
            )
        instalments.append(
            AdvanceTaxInstalment(
                instalment=r["instalment"],
                due_date=r["due_date"],
                cumulative_percent=r["cumulative_percent"],
                suggested_amount=suggested,
            )
        )
    return AdvanceTaxCalendar(assessment_year=assessment_year, instalments=instalments)


@router.post(
    "/26as/reconcile",
    response_model=Form26asReconReport,
    summary="Reconcile computation TDS credit vs uploaded 26AS JSON",
)
async def reconcile_26as(
    payload: Form26asReconRequest,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "report"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Form26asReconReport:
    assert current_user.company_id is not None
    return await form26as_recon.reconcile_26as(
        db,
        company_id=current_user.company_id,
        computation_id=payload.computation_id,
        form26as=payload.form26as,
    )


@router.get(
    "/{doc_id}",
    response_model=IncomeTaxComputationResponse,
    summary="Get an income-tax computation",
)
async def get_computation(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "read"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> IncomeTaxComputationResponse:
    return IncomeTaxComputationResponse.model_validate(
        await service.get_computation(db, doc_id, current_user.company_id)
    )


@router.put(
    "/{doc_id}",
    response_model=IncomeTaxComputationResponse,
    summary="Update a draft income-tax computation",
)
async def update_computation(
    doc_id: uuid.UUID,
    payload: IncomeTaxComputationUpdate,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "write"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> IncomeTaxComputationResponse:
    return IncomeTaxComputationResponse.model_validate(
        await service.update_computation(db, doc_id, payload, current_user)
    )


@router.post(
    "/{doc_id}/submit",
    response_model=IncomeTaxComputationResponse,
    summary="Submit an income-tax computation",
)
async def submit_computation(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "submit"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> IncomeTaxComputationResponse:
    return IncomeTaxComputationResponse.model_validate(
        await service.submit_computation(db, doc_id, current_user)
    )


@router.post(
    "/{doc_id}/cancel",
    response_model=IncomeTaxComputationResponse,
    summary="Cancel a submitted income-tax computation",
)
async def cancel_computation(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "cancel"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> IncomeTaxComputationResponse:
    return IncomeTaxComputationResponse.model_validate(
        await service.cancel_computation(db, doc_id, current_user)
    )


@router.get(
    "/{doc_id}/itr",
    response_model=Itr6ExportPack,
    summary="Export entity ITR handoff pack (JSON)",
    description="Form follows Income Tax Settings entity_type (ITR-6 / ITR-3 / ITR-5).",
)
async def export_itr(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "report"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Itr6ExportPack:
    assert current_user.company_id is not None
    return await itr_export.build_itr_pack(db, doc_id, current_user.company_id)


@router.get(
    "/{doc_id}/itr.csv",
    summary="Export entity ITR summary as CSV",
    response_class=Response,
)
async def export_itr_csv(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "report"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Response:
    assert current_user.company_id is not None
    pack = await itr_export.build_itr_pack(db, doc_id, current_user.company_id)
    body = itr_export.itr6_csv(pack)
    slug = pack.form.lower().replace("-", "")
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}-{pack.assessment_year}.csv"'
        },
    )


@router.post(
    "/{doc_id}/itr/efile",
    response_model=ItrEfileResult,
    summary="E-file entity ITR pack (or return JSON if no provider)",
    description="Mirrors GSP push: with itr_efile_provider=none returns generated JSON; "
    "with sandbox returns a stub acknowledgement. No portal HTTPS / DSC yet.",
)
async def efile_itr(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "submit"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ItrEfileResult:
    assert current_user.company_id is not None
    return await itr_efile.efile_computation(
        db, company_id=current_user.company_id, doc_id=doc_id
    )


@router.get(
    "/{doc_id}/itr-6",
    response_model=Itr6ExportPack,
    summary="Export ITR-6 handoff pack (JSON)",
    description="Schedule-oriented pack for CA / offline utility. Requires a submitted "
    "computation and entity_type=Company. Prefer GET …/itr for entity-matched packs.",
)
async def export_itr6(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "report"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Itr6ExportPack:
    assert current_user.company_id is not None
    return await itr_export.build_itr6_pack(db, doc_id, current_user.company_id)


@router.get(
    "/{doc_id}/itr-6.csv",
    summary="Export ITR-6 summary as CSV",
    response_class=Response,
)
async def export_itr6_csv(
    doc_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Income Tax Computation", "report"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> Response:
    assert current_user.company_id is not None
    pack = await itr_export.build_itr6_pack(db, doc_id, current_user.company_id)
    body = itr_export.itr6_csv(pack)
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="itr6-{pack.assessment_year}.csv"'
        },
    )
