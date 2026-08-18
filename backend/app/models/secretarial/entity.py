"""The aggregate root of the Secretarial module, plus per-tenant settings.

Design note (plan §2.1): OptiReach's ``Company`` is already the RLS tenant, so
secretarial data cannot hang off it directly without making the CS-practice case
impossible. ``SecretarialEntity`` is the subject of the statutory record; its
``company_id`` is the tenant that *owns* that record.

* An MSME keeping its own books here → one entity, ``linked_company_id`` set.
* A CS firm's client who is not an OptiReach customer → entity owned by the firm.
* A CS firm's client who *is* an OptiReach customer → entity owned by the client,
  reached through an engagement (see ``engagement.py``).
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

ENTITY_KINDS = ("company", "llp")

# Statutory labels that drive which obligations apply (plan §7).
ENTITY_CLASSES = (
    "private",
    "public",
    "opc",
    "section8",
    "nidhi",
    "producer",
    "llp",
)

ENTITY_STATUSES = ("active", "dormant", "struck_off", "amalgamated", "closed")

TENANT_PROFILES = ("practice", "business")


class SecretarialEntity(Base, DocumentMixin, CompanyScopedMixin):
    """A company or LLP whose statutory record we keep.

    ``company_id`` is the **owning** tenant. Re-homing an entity later means
    rewriting ``company_id`` across every child table, so ownership is decided at
    creation and only ever changed by the audited transfer operation (Phase 6).

    CIN/LLPIN are unique per owning tenant, not globally: two different practices
    may legitimately both hold a record for the same company (one of them will be
    stale, but that is a business problem, not a constraint violation).
    """

    __tablename__ = "secretarial_entities"
    __table_args__ = (
        UniqueConstraint("company_id", "cin", name="uq_secretarial_entity_cin"),
        UniqueConstraint("company_id", "llpin", name="uq_secretarial_entity_llpin"),
        Index("ix_secretarial_entities_linked", "linked_company_id"),
        Index("ix_secretarial_entities_company_status", "company_id", "status"),
    )

    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'company'"))
    entity_class: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'private'")
    )
    cin: Mapped[str | None] = mapped_column(String(21))
    llpin: Mapped[str | None] = mapped_column(String(8))
    pan: Mapped[str | None] = mapped_column(String(10))
    tan: Mapped[str | None] = mapped_column(String(10))
    gstin: Mapped[str | None] = mapped_column(String(15))

    is_listed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    incorporated_on: Mapped[date | None] = mapped_column(Date)
    # MMDD of the financial-year end — '0331' for the Indian default. Stored as
    # text rather than a date because it recurs every year.
    fy_end_mmdd: Mapped[str] = mapped_column(String(4), nullable=False, server_default=text("'0331'"))

    registered_office: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    email: Mapped[str | None] = mapped_column(String(140))
    phone: Mapped[str | None] = mapped_column(String(40))
    website: Mapped[str | None] = mapped_column(String(200))

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    notes: Mapped[str | None] = mapped_column(Text)

    # Set when this entity's books live in OptiReach — the switch that decides
    # whether financial facts are derived from the GL or entered by hand (§2.8).
    linked_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
    )

    @property
    def registration_no(self) -> str | None:
        """CIN for a company, LLPIN for an LLP — whichever identifies this entity."""
        return self.llpin if self.kind == "llp" else self.cin


class SecretarialSettings(Base, DocumentMixin, CompanyScopedMixin):
    """One row per tenant — decides which shell the module opens in (plan §2.3).

    ``practice`` lands on the client roster; ``business`` lands on the single
    entity. Same routes and components either way; only the entry point differs.
    """

    __tablename__ = "secretarial_settings"
    __table_args__ = (UniqueConstraint("company_id", name="uq_secretarial_settings_company"),)

    profile: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'business'"))
    practice_name: Mapped[str | None] = mapped_column(String(200))
    # Membership no. of the practising CS / firm registration no., printed on packs.
    practice_registration_no: Mapped[str | None] = mapped_column(String(40))
    # Default offered when this tenant (as a firm) is granted access to a client.
    default_financial_access: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ledger_read'")
    )
    # Reminder lead times in days before a compliance due date (Phase 1).
    reminder_offsets: Mapped[list[int] | None] = mapped_column(JSONB)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
