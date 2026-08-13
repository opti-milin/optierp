"""Tax computation documents — CRUD + append-only runs."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.taxation import (
    TaxComputationCreate,
    TaxComputationOut,
    TaxComputationRunCreate,
    TaxComputationRunOut,
    TaxComputationSubmit,
    TaxComputationUpdate,
)
from app.services.taxation import computations as service
from app.services.taxation import runs as runs_service

router = APIRouter(prefix="/tax/computations", tags=["tax: computations"])


@router.get(
    "",
    response_model=list[TaxComputationOut],
    summary="List tax computations for this company",
)
async def list_computations(
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[TaxComputationOut]:
    assert current_user.company_id is not None
    rows = await service.list_computations(db, current_user.company_id)
    # Lightweight list — reload with lines would be heavy; return headers only.
    return [
        TaxComputationOut(
            id=r.id,
            name=r.name,
            ay_code=r.ay_code,
            assessee_class_code=r.assessee_class_code,
            regime_election_id=r.regime_election_id,
            regime_code=r.regime_code,
            filing_type=r.filing_type,
            revises_computation_id=r.revises_computation_id,
            from_date=r.from_date,
            to_date=r.to_date,
            finance_act_version_id=r.finance_act_version_id,
            status=r.status,
            remarks=r.remarks,
            current_run_id=r.current_run_id,
            docstatus=r.docstatus,
        )
        for r in rows
    ]


@router.post(
    "",
    response_model=TaxComputationOut,
    summary="Create a tax computation (pins Finance Act version)",
)
async def create_computation(
    payload: TaxComputationCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxComputationOut:
    doc = await service.create_computation(db, payload, current_user)
    return service.computation_out(doc)


@router.get(
    "/{computation_id}",
    response_model=TaxComputationOut,
    summary="Get a tax computation with income and adjustment lines",
)
async def get_computation(
    computation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxComputationOut:
    assert current_user.company_id is not None
    doc = await service.get_computation(db, current_user.company_id, computation_id)
    return service.computation_out(doc)


@router.put(
    "/{computation_id}",
    response_model=TaxComputationOut,
    summary="Update draft tax computation worksheet lines",
)
async def update_computation(
    computation_id: UUID,
    payload: TaxComputationUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxComputationOut:
    doc = await service.update_computation(db, computation_id, payload, current_user)
    return service.computation_out(doc)


@router.post(
    "/{computation_id}/submit",
    response_model=TaxComputationOut,
    summary="Submit a tax computation (posts current-tax provision JE by default)",
)
async def submit_computation(
    computation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    payload: TaxComputationSubmit | None = None,
) -> TaxComputationOut:
    body = payload or TaxComputationSubmit()
    doc = await service.submit_computation(
        db,
        computation_id,
        current_user,
        provision_expense_account_id=body.provision_expense_account_id,
        provision_liability_account_id=body.provision_liability_account_id,
        post_provision=body.post_provision,
    )
    return service.computation_out(doc)


@router.post(
    "/{computation_id}/cancel",
    response_model=TaxComputationOut,
    summary="Cancel a tax computation",
)
async def cancel_computation(
    computation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxComputationOut:
    doc = await service.cancel_computation(db, computation_id, current_user)
    return service.computation_out(doc)


@router.post(
    "/{computation_id}/runs",
    response_model=TaxComputationRunOut,
    summary="Append a computation run (recompute — never deletes prior runs)",
)
async def post_run(
    computation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    payload: TaxComputationRunCreate | None = None,
) -> TaxComputationRunOut:
    trigger = (payload.trigger if payload else "manual") or "manual"
    run = await runs_service.create_run(
        db, computation_id=computation_id, user=current_user, trigger=trigger
    )
    # Reload with result
    assert current_user.company_id is not None
    run = await runs_service.get_run(db, current_user.company_id, computation_id, run.id)
    return service.run_out(run)


@router.get(
    "/{computation_id}/runs",
    response_model=list[TaxComputationRunOut],
    summary="List append-only run history for a computation",
)
async def get_runs(
    computation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[TaxComputationRunOut]:
    assert current_user.company_id is not None
    rows = await runs_service.list_runs(db, current_user.company_id, computation_id)
    return [service.run_out(r) for r in rows]


@router.get(
    "/{computation_id}/runs/{run_id}",
    response_model=TaxComputationRunOut,
    summary="Get one run with its result",
)
async def get_run(
    computation_id: UUID,
    run_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TaxComputationRunOut:
    assert current_user.company_id is not None
    run = await runs_service.get_run(db, current_user.company_id, computation_id, run_id)
    return service.run_out(run)


@router.get(
    "/{computation_id}/runs/{run_id}/explain",
    response_model=dict[str, Any],
    summary="Explain a run (hashes, breakdown, pipeline notes)",
)
async def explain_run(
    computation_id: UUID,
    run_id: UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Income Tax Computation", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    assert current_user.company_id is not None
    run = await runs_service.get_run(db, current_user.company_id, computation_id, run_id)
    result = run.result
    return {
        "run_id": str(run.id),
        "run_no": run.run_no,
        "engine_version": run.engine_version,
        "ruleset_hash": run.ruleset_hash,
        "input_hash": run.input_hash,
        "finance_act_version_id": str(run.finance_act_version_id),
        "superseded_at": run.superseded_at.isoformat() if run.superseded_at else None,
        "breakdown": result.breakdown if result else {},
        "net_payable": format(result.net_payable, "f") if result else None,
    }
