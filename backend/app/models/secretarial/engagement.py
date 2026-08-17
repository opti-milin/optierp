"""Cross-tenant delegation: a client grants a CS practice access to one entity.

Plan §2.2. There is deliberately **no new auth path** here. ``user_roles.company_id``
already lets one user hold roles in several companies, and ``POST /auth/switch-company``
already refuses to issue a token for a company the user holds no role in. An engagement
is the governed record of *why* those role rows exist: activating one projects them into
the client's tenant, ending one deletes them.

Two tenancy notes worth reading before changing anything here:

* ``secretarial_engagements`` is **bilateral** — both the granting client and the
  grantee firm must see it — so it does not use ``CompanyScopedMixin``. Its RLS policy
  matches either side (see migration ``0094``).
* ``practice_client_index`` is owned by the **firm**, and denormalises the display
  fields on purpose: the entity itself may live in another tenant where RLS would hide
  it, so the roster must never need a join to render.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

# The read-only ladder of §2.2.1. Every rung is cumulative and none grants write.
FINANCIAL_ACCESS_LEVELS = ("none", "derived_only", "reports_read", "ledger_read")

SECRETARIAL_ACCESS_LEVELS = ("read", "write")

ENGAGEMENT_STATUSES = ("pending", "active", "suspended", "ended")

RELATIONSHIPS = ("own", "managed", "delegated")

ONBOARDING_STATES = ("prospect", "onboarding", "active", "dormant", "exited")


class SecretarialEngagement(Base, DocumentMixin):
    """A client tenant's grant of access over one entity to a practice tenant.

    Scoped to a single entity, never to a whole tenant: engaging a CS firm for one
    subsidiary must not expose the rest of the group.
    """

    __tablename__ = "secretarial_engagements"
    __table_args__ = (
        # One live engagement per (entity, firm). Ended ones stay for the record.
        Index(
            "uq_secretarial_engagement_active",
            "entity_id",
            "firm_company_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'active', 'suspended')"),
        ),
        Index("ix_secretarial_engagements_firm", "firm_company_id", "status"),
        Index("ix_secretarial_engagements_client", "client_company_id", "status"),
    )

    # The owning tenant — the grantor. Only this side may revoke.
    client_company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # The practice — the grantee.
    firm_company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("secretarial_entities.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Snapshot of the entity's identity, written by the client when the grant is
    # created. The firm cannot SELECT the entity row itself — RLS hides another
    # tenant's data — so without this the roster would have nothing to display.
    # Identity crosses the boundary; the record does not.
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_kind: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'company'")
    )
    entity_registration_no: Mapped[str | None] = mapped_column(String(21))
    client_name: Mapped[str | None] = mapped_column(String(200))
    firm_name: Mapped[str | None] = mapped_column(String(200))

    secretarial_access: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'write'")
    )
    financial_access: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ledger_read'")
    )
    # Bank statements, payment-instrument detail and payroll sit outside the ladder
    # (§2.2.1) — a CS engagement does not imply consent to them.
    include_banking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    accepted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    ended_reason: Mapped[str | None] = mapped_column(Text)

    # Users in the firm this grant was projected onto, so revoke removes exactly
    # what activation added — no guessing, no orphaned role rows.
    granted_user_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    # Role names written into the client's tenant on activation.
    projected_roles: Mapped[list[str] | None] = mapped_column(JSONB)

    notes: Mapped[str | None] = mapped_column(Text)


class PracticeClientIndex(Base, DocumentMixin, CompanyScopedMixin):
    """The practice's operational spine — one row per client the firm works on.

    Plan §2.3. This is not a cache that can be rebuilt from nothing: ``onboarding_state``,
    ``active_from`` and ``active_to`` are *history*, and history cannot be reconstructed
    after the fact. That is why the lifecycle columns ship in Phase 0 rather than
    whenever metering gets built.

    ``company_id`` is the **firm's** tenant. ``owner_company_id`` is whoever owns the
    entity — equal to ``company_id`` for own/managed clients, different for delegated.
    """

    __tablename__ = "secretarial_practice_clients"
    __table_args__ = (
        Index("uq_practice_client_entity", "company_id", "entity_id", unique=True),
        Index("ix_practice_clients_state", "company_id", "onboarding_state"),
        Index("ix_practice_clients_due", "company_id", "next_due_on"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner_company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    engagement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_engagements.id", ondelete="SET NULL")
    )
    relationship_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'managed'")
    )

    # Denormalised so the roster renders without touching another tenant's rows.
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'company'"))
    registration_no: Mapped[str | None] = mapped_column(String(21))

    onboarding_state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )
    billable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    active_from: Mapped[date | None] = mapped_column(Date)
    active_to: Mapped[date | None] = mapped_column(Date)
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    # Refreshed by the nightly compliance job and on write (Phase 1).
    next_due_on: Mapped[date | None] = mapped_column(Date)
    overdue_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    open_item_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_activity_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    refreshed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
