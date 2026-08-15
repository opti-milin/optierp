"""Coverage-matrix + workspace endpoints — Module 12.

`/tally/catalogue` is the machine-readable answer to "what can I bring over from
Tally?". The UI renders it as a table and the docs are generated from the same
data, so the three can never drift apart.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.models.tally import TallyImport, TallyMapping
from app.schemas.tally import (
    TallyCatalogueResponse,
    TallyEntityCoverage,
    TallyWorkspaceStats,
)
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
    response_model=TallyWorkspaceStats,
    summary="Data Migration workspace stats",
)
async def get_workspace(
    current_user: Annotated[CurrentUser, Depends(require_permission(PERMISSION, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> TallyWorkspaceStats:
    company_id = current_user.company_id
    rows = list(
        (
            await db.execute(
                select(TallyImport).where(TallyImport.company_id == company_id)
            )
        ).scalars().all()
    )
    latest = max(rows, key=lambda r: r.creation, default=None)
    unmapped = int(
        await db.scalar(
            select(func.count())
            .select_from(TallyMapping)
            .where(TallyMapping.company_id == company_id, TallyMapping.target_id.is_(None))
        )
        or 0
    )
    return TallyWorkspaceStats(
        total_imports=len(rows),
        completed_imports=sum(
            1 for r in rows if r.status in ("Imported", "Partially Imported")
        ),
        failed_imports=sum(1 for r in rows if r.status == "Failed"),
        documents_imported=sum(r.imported_count for r in rows),
        unmapped_names=unmapped,
        last_import_at=latest.creation if latest else None,
        last_import_status=latest.status if latest else None,
        supported_entities=len(ENTITIES),
    )
