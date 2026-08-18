"""Delegation — a client tenant granting a CS practice access to one entity.

Plan §2.2. The important thing to understand before editing: **this module creates no
new way to authenticate.** It writes ordinary ``user_roles`` rows into the client's
tenant, and the existing ``POST /auth/switch-company`` then does what it always did —
refuses to issue a token for a company the user holds no role in. The engagement is the
governed record of *why* those rows exist, so revoking is exact rather than approximate.

Three invariants the code enforces:

1. Only the **client** side may create, amend or end a grant. A firm cannot grant itself
   access, and a firm cannot un-revoke.
2. Projected roles are always **company-scoped**. A global role row (``company_id IS
   NULL``) would leak into every tenant the user touches — the one mistake in this file
   that would be a security bug rather than an inconvenience.
3. No rung of the ladder grants write on financial data (plan §2.2.1). That is enforced
   by which roles exist at all: the seeded financial roles carry ``can_read`` and nothing
   else, so there is no code path that could grant more.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.core.security import CurrentUser
from app.models.core import Company, UserRole
from app.models.secretarial import SecretarialEngagement
from app.schemas.secretarial import (
    AccessSummary,
    EngagementCreate,
    EngagementEndIn,
    EngagementUpdate,
)
from app.services.audit import log_audit, serialize_document
from app.services.pagination import paginate
from app.services.secretarial.common import get_entity

_DOCTYPE = "Secretarial Engagement"

ROLE_SECRETARIAL_WRITE = "Delegated CS Secretarial"
ROLE_SECRETARIAL_READ = "Delegated CS Observer"
ROLE_FACTS = "Delegated CS Facts"
ROLE_REPORTS = "Delegated CS Reports"
ROLE_LEDGER = "Delegated CS Ledger"
ROLE_BANKING = "Delegated CS Banking"

# Each rung includes the rungs below it — a firm that can read the ledger can
# obviously read the reports it was derived from.
_FINANCIAL_ROLE_LADDER: dict[str, list[str]] = {
    "none": [],
    "derived_only": [ROLE_FACTS],
    "reports_read": [ROLE_FACTS, ROLE_REPORTS],
    "ledger_read": [ROLE_FACTS, ROLE_REPORTS, ROLE_LEDGER],
}

ALL_DELEGATED_ROLES = (
    ROLE_SECRETARIAL_WRITE,
    ROLE_SECRETARIAL_READ,
    ROLE_FACTS,
    ROLE_REPORTS,
    ROLE_LEDGER,
    ROLE_BANKING,
)


def roles_for(engagement: SecretarialEngagement) -> list[str]:
    """The exact role set a grant projects — the single source of truth for both
    activation and revocation, so the two can never drift."""
    roles = [
        ROLE_SECRETARIAL_WRITE
        if engagement.secretarial_access == "write"
        else ROLE_SECRETARIAL_READ
    ]
    roles.extend(_FINANCIAL_ROLE_LADDER.get(engagement.financial_access, []))
    if engagement.include_banking:
        roles.append(ROLE_BANKING)
    return roles


_FINANCIAL_WORDING = {
    "none": "No access to your accounts.",
    "derived_only": (
        "Only the summary figures used to decide which filings apply "
        "(turnover, net profit, net worth). No transactions."
    ),
    "reports_read": (
        "Read-only access to your financial statements — profit & loss, "
        "balance sheet, trial balance. No transactions, no edits."
    ),
    "ledger_read": (
        "Read-only access to your books: financial statements plus the general "
        "ledger, journal entries and invoices behind them. They can look at "
        "anything, but change nothing."
    ),
}


def access_summary(engagement: SecretarialEngagement) -> AccessSummary:
    """Plain-language rendering shown to the client *before* they accept.

    Required by plan §2.2.1: ``ledger_read`` being the default means a client could
    otherwise hand over their whole book of accounts by clicking Accept on a screen
    full of enum names.
    """
    warnings: list[str] = []
    if engagement.financial_access == "ledger_read":
        warnings.append(
            "This firm will be able to read every transaction in your accounts."
        )
    if engagement.include_banking:
        warnings.append(
            "This grant also includes bank statements and payment details, which are "
            "excluded by default."
        )

    return AccessSummary(
        secretarial=(
            "Full access to statutory records — registers, meetings, filings — for this "
            "entity only."
            if engagement.secretarial_access == "write"
            else "Can view statutory records for this entity, but not change them."
        ),
        financial=_FINANCIAL_WORDING.get(engagement.financial_access, "Unknown"),
        banking=(
            "Bank statements and payment details are included."
            if engagement.include_banking
            else "Bank statements, payment details and payroll are excluded."
        ),
        warnings=warnings,
    )


async def get_engagement(
    db: AsyncSession, engagement_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialEngagement:
    """Visible to either party; RLS enforces the same rule at the row level."""
    engagement = await db.get(SecretarialEngagement, engagement_id)
    if engagement is None or company_id not in (
        engagement.client_company_id,
        engagement.firm_company_id,
    ):
        raise NotFoundError("Engagement not found")
    return engagement


def _require_client_side(engagement: SecretarialEngagement, company_id: uuid.UUID) -> None:
    if engagement.client_company_id != company_id:
        raise PermissionDeniedError(
            "Only the client who granted this engagement can change or end it"
        )


async def _firm_user_ids(
    db: AsyncSession, firm_company_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Keep only users who actually work at the firm.

    A user belongs to a firm if they hold any role scoped to it. Without this check a
    client could be tricked into granting access to an arbitrary account.
    """
    if not user_ids:
        return []
    stmt = select(UserRole.user_id).where(
        UserRole.user_id.in_(user_ids), UserRole.company_id == firm_company_id
    )
    found = set((await db.execute(stmt)).scalars().all())
    missing = [u for u in user_ids if u not in found]
    if missing:
        raise ValidationError(
            "One or more users do not belong to that firm and cannot be granted access",
            field="grant_to_user_ids",
        )
    return list(found)


async def create_engagement(
    db: AsyncSession, payload: EngagementCreate, user: CurrentUser
) -> SecretarialEngagement:
    """Issued by the client tenant. Starts ``pending`` until explicitly activated."""
    assert user.company_id is not None
    if payload.firm_company_id == user.company_id:
        raise ValidationError(
            "A tenant cannot engage itself; pick the CS firm's company",
            field="firm_company_id",
        )

    entity = await get_entity(db, payload.entity_id, user.company_id)

    firm = await db.get(Company, payload.firm_company_id)
    if firm is None:
        raise NotFoundError("Firm company not found")
    client = await db.get(Company, user.company_id)

    existing = await db.scalar(
        select(SecretarialEngagement.id).where(
            SecretarialEngagement.entity_id == payload.entity_id,
            SecretarialEngagement.firm_company_id == payload.firm_company_id,
            SecretarialEngagement.status.in_(("pending", "active", "suspended")),
        )
    )
    if existing:
        raise ValidationError("This firm already has a live engagement for this entity")

    granted = await _firm_user_ids(db, payload.firm_company_id, payload.grant_to_user_ids)

    engagement = SecretarialEngagement(
        client_company_id=user.company_id,
        firm_company_id=payload.firm_company_id,
        entity_id=payload.entity_id,
        entity_name=entity.entity_name,
        entity_kind=entity.kind,
        entity_registration_no=entity.registration_no,
        client_name=client.company_name if client else None,
        firm_name=firm.company_name,
        secretarial_access=payload.secretarial_access,
        financial_access=payload.financial_access,
        include_banking=payload.include_banking,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        granted_user_ids=[str(u) for u in granted],
        notes=payload.notes,
        status="pending",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(engagement)
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=engagement.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after=serialize_document(engagement),
    )
    await db.commit()
    return engagement


async def _project_roles(
    db: AsyncSession, engagement: SecretarialEngagement
) -> list[str]:
    """Write the role rows into the *client's* tenant. Always company-scoped."""
    roles = roles_for(engagement)
    user_ids = [uuid.UUID(u) for u in (engagement.granted_user_ids or [])]

    await _clear_roles(db, engagement)
    for user_id in user_ids:
        for role in roles:
            db.add(
                UserRole(
                    user_id=user_id,
                    role=role,
                    # Scoped to the client company — never NULL. A global row here
                    # would grant the firm access to every tenant this user touches.
                    company_id=engagement.client_company_id,
                )
            )
    await db.flush()
    return roles


async def _clear_roles(db: AsyncSession, engagement: SecretarialEngagement) -> None:
    """Remove exactly what a previous activation added — no more, no less."""
    user_ids = [uuid.UUID(u) for u in (engagement.granted_user_ids or [])]
    if not user_ids:
        return
    await db.execute(
        delete(UserRole).where(
            UserRole.user_id.in_(user_ids),
            UserRole.company_id == engagement.client_company_id,
            UserRole.role.in_(ALL_DELEGATED_ROLES),
        )
    )


async def activate_engagement(
    db: AsyncSession, engagement_id: uuid.UUID, user: CurrentUser
) -> SecretarialEngagement:
    assert user.company_id is not None
    engagement = await get_engagement(db, engagement_id, user.company_id)
    _require_client_side(engagement, user.company_id)
    if engagement.status == "ended":
        raise ValidationError("An ended engagement cannot be reactivated; create a new one")
    if not engagement.granted_user_ids:
        raise ValidationError(
            "Name at least one user at the firm before activating", field="grant_to_user_ids"
        )

    before = serialize_document(engagement)
    engagement.projected_roles = await _project_roles(db, engagement)
    engagement.status = "active"
    engagement.accepted_at = datetime.now(UTC)
    engagement.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=engagement.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(engagement),
    )
    await db.commit()
    return engagement


async def update_engagement(
    db: AsyncSession, engagement_id: uuid.UUID, payload: EngagementUpdate, user: CurrentUser
) -> SecretarialEngagement:
    """Change scope. If the grant is live, roles are re-projected immediately —
    narrowing access must take effect now, not on next login."""
    assert user.company_id is not None
    engagement = await get_engagement(db, engagement_id, user.company_id)
    _require_client_side(engagement, user.company_id)
    if engagement.status == "ended":
        raise ValidationError("An ended engagement cannot be amended")

    before = serialize_document(engagement)
    data = payload.model_dump(exclude_unset=True)
    if "grant_to_user_ids" in data:
        granted = await _firm_user_ids(
            db, engagement.firm_company_id, data.pop("grant_to_user_ids") or []
        )
        await _clear_roles(db, engagement)
        engagement.granted_user_ids = [str(u) for u in granted]
    for field, value in data.items():
        setattr(engagement, field, value)
    engagement.modified_by = user.id

    if engagement.status == "active":
        engagement.projected_roles = await _project_roles(db, engagement)

    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=engagement.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(engagement),
    )
    await db.commit()
    return engagement


async def end_engagement(
    db: AsyncSession, engagement_id: uuid.UUID, payload: EngagementEndIn, user: CurrentUser
) -> SecretarialEngagement:
    """Revoke. Roles vanish in the same transaction; the record of the grant stays."""
    assert user.company_id is not None
    engagement = await get_engagement(db, engagement_id, user.company_id)
    _require_client_side(engagement, user.company_id)
    if engagement.status == "ended":
        return engagement

    before = serialize_document(engagement)
    await _clear_roles(db, engagement)
    engagement.status = "ended"
    engagement.ended_at = datetime.now(UTC)
    engagement.ended_reason = payload.ended_reason
    engagement.projected_roles = []
    engagement.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=engagement.id,
        action="CANCEL",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(engagement),
    )
    await db.commit()
    return engagement


async def list_engagements(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    direction: str = "all",
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialEngagement], int]:
    """``direction``: ``granted`` (we are the client) · ``held`` (we are the firm) · ``all``."""
    stmt = select(SecretarialEngagement)
    if direction == "granted":
        stmt = stmt.where(SecretarialEngagement.client_company_id == company_id)
    elif direction == "held":
        stmt = stmt.where(SecretarialEngagement.firm_company_id == company_id)
    else:
        stmt = stmt.where(
            or_(
                SecretarialEngagement.client_company_id == company_id,
                SecretarialEngagement.firm_company_id == company_id,
            )
        )
    if status:
        stmt = stmt.where(SecretarialEngagement.status == status)
    return await paginate(db, stmt.order_by(SecretarialEngagement.creation.desc()), page, page_size)
