"""Statutory registers, served through one generic router.

Eight registers with the same shape (entity-scoped, effective-dated, filter → search →
export) would otherwise be eight near-identical routers. A slug in the path selects the
model and schemas; everything else is shared. Adding a ninth register is one entry in
``_REGISTERS``.
"""

import csv
import io
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.models.secretarial import (
    SecretarialAuditor,
    SecretarialBeneficialOwner,
    SecretarialCharge,
    SecretarialCommittee,
    SecretarialDsc,
    SecretarialGroupLink,
    SecretarialMember,
    SecretarialRelatedParty,
)
from app.schemas.common import ListResponse, MessageResponse
from app.schemas.secretarial import (
    AuditorCreate,
    AuditorResponse,
    BeneficialOwnerCreate,
    BeneficialOwnerResponse,
    ChargeCreate,
    ChargeResponse,
    CommitteeCreate,
    CommitteeMemberIn,
    CommitteeMemberOut,
    CommitteeResponse,
    DscCreate,
    DscResponse,
    GroupLinkCreate,
    GroupLinkResponse,
    MemberCreate,
    MemberResponse,
    RelatedPartyCreate,
    RelatedPartyResponse,
    RelatedPartySyncResult,
)
from app.services.secretarial import registers as service

router = APIRouter(prefix="/secretarial/registers", tags=["secretarial: registers"])


class _Register:
    def __init__(self, model: type, create_schema: type, response_schema: type, doctype: str, label: str):
        self.model = model
        self.create_schema = create_schema
        self.response_schema = response_schema
        self.doctype = doctype
        self.label = label


_REGISTERS: dict[str, _Register] = {
    "members": _Register(
        SecretarialMember, MemberCreate, MemberResponse, "Secretarial Member", "Register of Members"
    ),
    "committees": _Register(
        SecretarialCommittee,
        CommitteeCreate,
        CommitteeResponse,
        "Secretarial Committee",
        "Committees",
    ),
    "group-links": _Register(
        SecretarialGroupLink,
        GroupLinkCreate,
        GroupLinkResponse,
        "Secretarial Group Link",
        "Group Structure",
    ),
    "related-parties": _Register(
        SecretarialRelatedParty,
        RelatedPartyCreate,
        RelatedPartyResponse,
        "Secretarial Related Party",
        "Related Parties",
    ),
    "beneficial-owners": _Register(
        SecretarialBeneficialOwner,
        BeneficialOwnerCreate,
        BeneficialOwnerResponse,
        "Secretarial Beneficial Owner",
        "Register of Beneficial Owners",
    ),
    "auditors": _Register(
        SecretarialAuditor, AuditorCreate, AuditorResponse, "Secretarial Auditor", "Auditors"
    ),
    "charges": _Register(
        SecretarialCharge, ChargeCreate, ChargeResponse, "Secretarial Charge", "Register of Charges"
    ),
    "dscs": _Register(SecretarialDsc, DscCreate, DscResponse, "Secretarial DSC", "DSC Register"),
}


def _resolve(slug: str) -> _Register:
    register = _REGISTERS.get(slug)
    if register is None:
        raise NotFoundError(
            f"Unknown register '{slug}'. Available: {', '.join(sorted(_REGISTERS))}"
        )
    return register


async def _authorize(
    db: AsyncSession, user: CurrentUser, register: _Register, action: str
) -> None:
    from app.core.exceptions import PermissionDeniedError
    from app.core.permissions import has_permission

    if not await has_permission(db, user, register.doctype, action):
        raise PermissionDeniedError(
            f"Insufficient permissions: requires '{action}' on {register.doctype}"
        )


@router.get(
    "",
    summary="Which registers exist",
    description="Slug → label map, so the UI can build its register navigation from the "
    "server rather than a hardcoded list.",
)
async def list_registers(
    current_user: Annotated[CurrentUser, Depends(require_permission("Secretarial Entity", "read"))],
) -> list[dict[str, str]]:
    return [{"slug": slug, "label": reg.label} for slug, reg in _REGISTERS.items()]


# --- Register-specific actions ----------------------------------------------------
# These MUST be declared before the generic `/{slug}` routes below: FastAPI matches in
# registration order, and `/related-parties/sync` would otherwise be swallowed by
# `/{slug}/{row_id}` and rejected as a malformed UUID.


@router.get(
    "/committees/{committee_id}/members",
    response_model=list[CommitteeMemberOut],
    summary="Committee constitution",
)
async def committee_members(
    committee_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Secretarial Committee", "read"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[CommitteeMemberOut]:
    assert current_user.company_id is not None
    rows = await service.committee_members(db, committee_id, current_user.company_id)
    out = []
    for member, person_name in rows:
        item = CommitteeMemberOut.model_validate(member)
        item.person_name = person_name
        out.append(item)
    return out


@router.put(
    "/committees/{committee_id}/members",
    response_model=list[CommitteeMemberOut],
    summary="Replace a committee's constitution",
)
async def set_committee_members(
    committee_id: uuid.UUID,
    members: list[CommitteeMemberIn],
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Secretarial Committee", "write"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[CommitteeMemberOut]:
    rows = await service.set_committee_members(db, committee_id, members, current_user)
    return [CommitteeMemberOut.model_validate(r) for r in rows]


@router.post(
    "/related-parties/sync",
    response_model=RelatedPartySyncResult,
    summary="Rebuild the related-party list from master data",
    description=(
        "Derives parties from live appointments, group links and members holding 20%+. "
        "Rows a user added by hand are left alone — the sync owns only what it created."
    ),
)
async def sync_related_parties(
    entity_id: uuid.UUID,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Secretarial Related Party", "write"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> RelatedPartySyncResult:
    return await service.sync_related_parties(db, entity_id, current_user)


# --- Generic register CRUD --------------------------------------------------------


@router.get(
    "/{slug}",
    response_model=ListResponse[dict],
    summary="Read a register",
    description="`fy` filters to rows whose validity window overlaps that financial year — "
    "the question a register is actually asked ('who was a member during 2024-25'), not "
    "'who is a member today'.",
)
async def list_rows(
    slug: str,
    current_user: Annotated[CurrentUser, Depends(require_permission("Secretarial Entity", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    search: str | None = None,
    fy: str | None = None,
    active_only: bool = False,
) -> ListResponse[dict]:
    assert current_user.company_id is not None
    register = _resolve(slug)
    await _authorize(db, current_user, register, "read")
    items, total = await service.list_rows(
        db,
        register.model,
        current_user.company_id,
        entity_id,
        page=page,
        page_size=page_size,
        search=search,
        fy=fy,
        active_only=active_only,
    )
    return ListResponse(
        items=[register.response_schema.model_validate(row).model_dump(mode="json") for row in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{slug}/export",
    summary="Export a register as CSV",
    description="Every register is exportable — an inspector or an auditor asks for the "
    "register, not for a screenshot of it.",
)
async def export_rows(
    slug: str,
    current_user: Annotated[CurrentUser, Depends(require_permission("Secretarial Entity", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    entity_id: uuid.UUID | None = None,
    fy: str | None = None,
    active_only: bool = False,
) -> Response:
    assert current_user.company_id is not None
    register = _resolve(slug)
    await _authorize(db, current_user, register, "read")
    items, _ = await service.list_rows(
        db,
        register.model,
        current_user.company_id,
        entity_id,
        page=1,
        page_size=10_000,
        fy=fy,
        active_only=active_only,
    )
    rows = [register.response_schema.model_validate(row).model_dump(mode="json") for row in items]

    buffer = io.StringIO()
    if rows:
        # Drop plumbing columns nobody wants in an exported register.
        drop = {"company_id", "owner", "modified_by", "docstatus"}
        fields = [f for f in rows[0] if f not in drop]
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    filename = f"{slug}-{fy or date.today().isoformat()}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{slug}", status_code=201, summary="Add a register row")
async def create_row(
    slug: str,
    payload: dict[str, Any],
    current_user: Annotated[CurrentUser, Depends(require_permission("Secretarial Entity", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    register = _resolve(slug)
    await _authorize(db, current_user, register, "create")
    parsed = register.create_schema.model_validate(payload)

    if register.model is SecretarialCommittee:
        row = await service.create_committee(db, parsed, current_user)
    else:
        row = await service.create_row(db, register.model, parsed, current_user)
    return register.response_schema.model_validate(row).model_dump(mode="json")


@router.patch("/{slug}/{row_id}", summary="Update a register row")
async def update_row(
    slug: str,
    row_id: uuid.UUID,
    payload: dict[str, Any],
    current_user: Annotated[CurrentUser, Depends(require_permission("Secretarial Entity", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    register = _resolve(slug)
    await _authorize(db, current_user, register, "write")
    row = await service.update_row(db, register.model, row_id, payload, current_user)
    return register.response_schema.model_validate(row).model_dump(mode="json")


@router.delete("/{slug}/{row_id}", response_model=MessageResponse, summary="Delete a register row")
async def delete_row(
    slug: str,
    row_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Secretarial Entity", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MessageResponse:
    register = _resolve(slug)
    await _authorize(db, current_user, register, "delete")
    await service.delete_row(db, register.model, row_id, current_user)
    return MessageResponse(message="Row deleted")

