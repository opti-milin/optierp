"""The person graph — DIN-keyed people and their appointments.

Two decisions live here.

**Persons are tenant-scoped, not entity-scoped** (plan §2.4). The same director sits
on several boards, and batch declarations (MBP-1, DIR-8) across every entity in the
tenant are only possible if one person record is shared. ``contacts`` is deliberately
not reused: a Contact is a customer/supplier contact with no DIN, no KYC and no
effective dating.

**One appointments table for every role** (plan §2.13). A director of a company and a
designated partner of an LLP go through byte-identical meeting, consent and register
machinery — only labels and forms differ, and those live in content packs. Separate
tables would fork every governance query for no gain.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

ROLE_TYPES = (
    "director",
    "designated_partner",
    "partner",
    "kmp",
    "auditor",
    "secretary",
)

KYC_STATUSES = ("pending", "submitted", "verified", "expired")


class SecretarialPerson(Base, DocumentMixin, CompanyScopedMixin):
    """A natural or corporate person who holds office somewhere in this tenant.

    ``din`` covers both DIN (companies) and DPIN (LLPs) — MCA merged the two
    numbering series, so one column is correct rather than convenient.
    """

    __tablename__ = "secretarial_persons"
    __table_args__ = (
        # Partial unique: many persons legitimately have no DIN (auditors, corporate
        # members), and NULLs must not collide with each other.
        Index(
            "uq_secretarial_person_din",
            "company_id",
            "din",
            unique=True,
            postgresql_where=text("din IS NOT NULL"),
        ),
        Index("ix_secretarial_persons_company_name", "company_id", "full_name"),
        UniqueConstraint("company_id", "full_name", "pan", name="uq_secretarial_person_identity"),
    )

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    din: Mapped[str | None] = mapped_column(String(8))
    pan: Mapped[str | None] = mapped_column(String(10))
    is_body_corporate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    fathers_name: Mapped[str | None] = mapped_column(String(200))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    nationality: Mapped[str | None] = mapped_column(String(60))
    occupation: Mapped[str | None] = mapped_column(String(140))
    qualification: Mapped[str | None] = mapped_column(String(200))

    email: Mapped[str | None] = mapped_column(String(140))
    mobile: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # DIR-3 KYC state; the compliance calendar reads this in Phase 1.
    kyc_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    kyc_verified_on: Mapped[date | None] = mapped_column(Date)
    kyc: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # s.164 disqualification — blocks appointment and drives DIR-8 wording.
    is_disqualified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    disqualification_note: Mapped[str | None] = mapped_column(Text)

    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialAppointment(Base, DocumentMixin, CompanyScopedMixin):
    """Effective-dated office held by a person in an entity.

    Open-ended while ``ceased_on`` is NULL. Cessation is recorded, never deleted:
    a director who resigned in 2024 must still appear in the 2024 minutes and the
    register of directors.
    """

    __tablename__ = "secretarial_appointments"
    __table_args__ = (
        Index("ix_secretarial_appointments_entity", "entity_id", "role_type"),
        Index("ix_secretarial_appointments_person", "person_id"),
        Index(
            "ix_secretarial_appointments_active",
            "entity_id",
            postgresql_where=text("ceased_on IS NULL"),
        ),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("secretarial_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("secretarial_persons.id", ondelete="RESTRICT"),
        nullable=False,
    )

    role_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Free text within the role: "Managing Director", "Independent Director", "CFO".
    designation: Mapped[str | None] = mapped_column(String(140))

    appointed_on: Mapped[date] = mapped_column(Date, nullable=False)
    ceased_on: Mapped[date | None] = mapped_column(Date)
    cessation_reason: Mapped[str | None] = mapped_column(String(200))

    # How the office was conferred — quoted in the resolution and the DIR-12 pack.
    appointment_mode: Mapped[str | None] = mapped_column(String(30))

    # Signs board papers / CTCs; drives the signatory picker in Phase 3.
    is_signing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_chairperson: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    notes: Mapped[str | None] = mapped_column(Text)

    @property
    def is_active(self) -> bool:
        return self.ceased_on is None
