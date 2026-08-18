"""Financial facts, applicability, and the filing evidence chain (Phase 4)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.secretarial_governance import (
    ApplicabilityCheckOut,
    FilingCreate,
    FilingOut,
    FilingUpdate,
    FinancialFactsIn,
    FinancialFactsOut,
    StatusHistoryOut,
)
from app.services.secretarial import applicability as applicability_engine
from app.services.secretarial import content as content_service
from app.services.secretarial import filings as service
from app.services.secretarial import financial_facts as facts_service
from app.services.secretarial.common import get_entity

router = APIRouter(prefix="/secretarial", tags=["secretarial: filings"])

FILING_DOCTYPE = "Secretarial Filing"
ITEM_DOCTYPE = "Secretarial Compliance Item"


@router.get(
    "/entities/{entity_id}/facts",
    response_model=FinancialFactsOut,
    summary="The figures that decide which obligations apply",
    description=(
        "Derived from the general ledger when this company's books are in this account, "
        "read from the manually entered row when they are not. `source` says which, so "
        "the UI can be honest about how much to trust a number."
    ),
)
async def get_facts(
    entity_id: uuid.UUID,
    fy: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    refresh: bool = False,
) -> FinancialFactsOut:
    assert current_user.company_id is not None
    entity = await get_entity(db, entity_id, current_user.company_id)
    facts = await facts_service.get_facts(db, entity, fy, refresh=refresh)
    await db.commit()
    return FinancialFactsOut.model_validate(facts)


@router.put(
    "/entities/{entity_id}/facts",
    response_model=FinancialFactsOut,
    summary="Enter the figures by hand",
    description="The path for a client whose books are kept elsewhere. Marks the row "
    "`manual`, which stops the ledger refresh from overwriting it.",
)
async def set_facts(
    entity_id: uuid.UUID,
    payload: FinancialFactsIn,
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FinancialFactsOut:
    assert current_user.company_id is not None
    await facts_service.set_manual_facts(
        db, entity_id, payload.fy, payload.model_dump(exclude={"fy"}), current_user
    )
    entity = await get_entity(db, entity_id, current_user.company_id)
    facts = await facts_service.get_facts(db, entity, payload.fy)
    await db.commit()
    return FinancialFactsOut.model_validate(facts)


@router.get(
    "/entities/{entity_id}/applicability",
    response_model=list[ApplicabilityCheckOut],
    summary="Why each obligation does or does not apply",
    description=(
        "Runs every published rule against this company and its figures, and explains "
        "each verdict in words. A rule whose threshold cannot be evaluated comes back "
        "`unknown` rather than `not_applicable` — never tell someone they are exempt "
        "because a number is missing."
    ),
)
async def applicability(
    entity_id: uuid.UUID,
    fy: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ApplicabilityCheckOut]:
    assert current_user.company_id is not None
    entity = await get_entity(db, entity_id, current_user.company_id)
    facts = await facts_service.get_facts(db, entity, fy)
    context = applicability_engine.build_context(entity, facts)
    rules = await content_service.published_rules(db, current_user.company_id)
    await db.commit()

    out = []
    for rule in rules:
        verdict, reasons = applicability_engine.evaluate(rule.applicability, context)
        out.append(
            ApplicabilityCheckOut(
                rule_code=rule.code, title=rule.title, verdict=verdict, reasons=reasons
            )
        )
    return out


# --- Filings ----------------------------------------------------------------------


@router.post(
    "/filings",
    response_model=FilingOut,
    status_code=201,
    summary="Record a filing",
    description="Filing happens on the MCA portal with the filer's own credentials; this "
    "records the evidence that it did. A submitted filing must carry its SRN and date.",
)
async def create_filing(
    payload: FilingCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission(FILING_DOCTYPE, "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FilingOut:
    return FilingOut.model_validate(
        await service.record(db, current_user, **payload.model_dump())
    )


@router.get("/filings", response_model=ListResponse[FilingOut], summary="List filings")
async def list_filings(
    current_user: Annotated[CurrentUser, Depends(require_permission(FILING_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    fy: str | None = None,
    form_code: str | None = None,
    status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListResponse[FilingOut]:
    assert current_user.company_id is not None
    items, total = await service.list_filings(
        db,
        current_user.company_id,
        entity_id=entity_id,
        fy=fy,
        form_code=form_code,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ListResponse(
        items=[FilingOut.model_validate(f) for f in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/filings/{filing_id}", response_model=FilingOut, summary="Update a filing")
async def update_filing(
    filing_id: uuid.UUID,
    payload: FilingUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission(FILING_DOCTYPE, "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FilingOut:
    return FilingOut.model_validate(
        await service.update_filing(
            db, filing_id, payload.model_dump(exclude_unset=True), current_user
        )
    )


@router.get(
    "/filings/{filing_id}/chain",
    summary="On what authority was this filed?",
    description=(
        "Walks back from the filing to the resolution that authorised it: "
        "resolution → document → form → SRN → challan. Reports gaps — unsigned minutes, "
        "an unratified circular, a missing challan — rather than presenting an "
        "incomplete chain as sound."
    ),
)
async def evidence_chain(
    filing_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(FILING_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict:
    assert current_user.company_id is not None
    return await service.evidence_chain(db, filing_id, current_user.company_id)


@router.get(
    "/compliance/items/{item_id}/history",
    response_model=list[StatusHistoryOut],
    summary="Append-only status trail for a calendar row",
)
async def item_history(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission(ITEM_DOCTYPE, "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[StatusHistoryOut]:
    assert current_user.company_id is not None
    rows = await service.item_history(db, item_id, current_user.company_id)
    return [StatusHistoryOut.model_validate(r) for r in rows]
