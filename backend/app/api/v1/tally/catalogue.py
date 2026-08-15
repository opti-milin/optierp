"""Coverage-matrix + workspace endpoints — Module 12.

`/tally/catalogue` is the machine-readable answer to "what can I bring over from
Tally?". The UI renders it as a table and the docs are generated from the same
data, so the three can never drift apart.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.tally import TallyCatalogueResponse, TallyEntityCoverage
from app.services import module_workspace
from app.services.tally.catalogue import ENTITIES, PRIMARY_GROUPS, VOUCHER_TYPES, coverage_matrix

router = APIRouter(prefix="/tally", tags=["data migration: tally"])

PERMISSION = "Tally Import"


@router.get(
    "/catalogue",
    response_model=TallyCatalogueResponse,
    summary="Tally coverage matrix",
    description=(
        "Every Tally entity the importer understands, the OptiERP DocType it "
        "becomes, and how completely it maps. Also returns the ledger-group and "
        "voucher-type classification tables the importer uses."
    ),
)
async def get_catalogue(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
) -> TallyCatalogueResponse:
    return TallyCatalogueResponse(
        entities=[TallyEntityCoverage(**row) for row in coverage_matrix()],
        primary_groups={
            name: {
                "root_type": spec.root_type,
                "account_type": spec.account_type,
                "party_type": spec.party_type,
            }
            for name, spec in PRIMARY_GROUPS.items()
        },
        voucher_types={name: spec.doctype for name, spec in VOUCHER_TYPES.items()},
        modules=sorted({e.module for e in ENTITIES}),
    )


@router.get(
    "/workspace",
    summary="Data Migration workspace stats",
    description="Number cards (imports, documents imported, unmapped names) plus a "
    "12-month trend, in the shape every module workspace page renders.",
)
async def get_workspace(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return await module_workspace.get_migration_workspace(db, current_user.company_id)
