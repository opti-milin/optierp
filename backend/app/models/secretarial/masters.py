"""Phase 1 — statutory masters and registers.

Everything here is entity-scoped and effective-dated. The pattern repeats: a row is
never deleted once it has legal effect, it is *closed* with a ``ceased_on`` /
``valid_to`` date, because a register has to answer "who was a member in FY 2024-25",
not just "who is a member today".

Simplification recorded deliberately: FY-overlap filters use plain btree indexes on
``(entity_id, valid_from, valid_to)`` rather than the GiST/daterange combination the
plan sketches. At register volumes (hundreds of rows per entity) the planner picks the
btree path anyway, and GiST over a uuid+range composite needs ``btree_gist`` installed.
Revisit if a tenant ever holds six figures of register rows.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

MEMBER_TYPES = ("individual", "body_corporate", "trust", "huf", "nominee", "government")
GROUP_RELATIONS = ("holding", "subsidiary", "associate", "joint_venture", "fellow_subsidiary")
RELATED_PARTY_BASES = ("director", "kmp", "member", "group", "relative", "manual")
BO_CLASSIFICATIONS = ("bo", "sbo", "ubo")
CHARGE_STATUSES = ("open", "satisfied", "modified")
DSC_STATUSES = ("active", "expired", "revoked")


class _EntityScoped:
    """Mixin: every register row belongs to exactly one entity."""

    @property
    def _entity_fk(self) -> str:  # documentation only
        return "secretarial_entities.id"


class SecretarialMember(Base, DocumentMixin, CompanyScopedMixin):
    """Register of members (s.88) — *identity* of each member and their folio.

    Holdings: the declared columns below are a Phase-1 snapshot so the register is
    usable now. Phase 5 adds the transfer ledger, after which holdings for entities
    that have one become **derived** and these columns hold only the opening position.
    That ordering is deliberate — see plan §2.7 on never keeping two cap tables that
    can disagree.
    """

    __tablename__ = "secretarial_members"
    __table_args__ = (
        UniqueConstraint("entity_id", "folio_no", name="uq_secretarial_member_folio"),
        Index("ix_secretarial_members_entity", "entity_id", "ceased_on"),
        Index("ix_secretarial_members_name", "entity_id", "member_name"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    member_name: Mapped[str] = mapped_column(String(200), nullable=False)
    folio_no: Mapped[str] = mapped_column(String(40), nullable=False)
    member_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'individual'")
    )

    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="SET NULL")
    )
    # Set when this entity's books are in OptiReach and the member is also a
    # Shareholder in the accounts cap table.
    shareholder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shareholders.id", ondelete="SET NULL")
    )

    pan: Mapped[str | None] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(140))
    nationality: Mapped[str | None] = mapped_column(String(60))
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Declared position — see class docstring.
    share_class: Mapped[str | None] = mapped_column(String(60))
    shares_held: Mapped[Decimal] = mapped_column(
        Numeric(21, 4), nullable=False, server_default=text("0")
    )
    nominal_value: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    holding_as_on: Mapped[date | None] = mapped_column(Date)

    joined_on: Mapped[date | None] = mapped_column(Date)
    ceased_on: Mapped[date | None] = mapped_column(Date)
    is_beneficial_owner: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialCommittee(Base, DocumentMixin, CompanyScopedMixin):
    """Audit / NRC / CSR / Stakeholders committee constitution.

    Phase 3 reads this to scope meeting participants automatically, which is how
    committee papers structurally cannot go to the wrong directors.
    """

    __tablename__ = "secretarial_committees"
    __table_args__ = (
        UniqueConstraint("entity_id", "committee_name", name="uq_secretarial_committee_name"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    committee_name: Mapped[str] = mapped_column(String(140), nullable=False)
    committee_type: Mapped[str | None] = mapped_column(String(60))
    constituted_on: Mapped[date | None] = mapped_column(Date)
    dissolved_on: Mapped[date | None] = mapped_column(Date)
    terms_of_reference: Mapped[str | None] = mapped_column(Text)
    quorum: Mapped[int | None] = mapped_column(Integer)


class SecretarialCommitteeMember(Base, DocumentMixin, CompanyScopedMixin):
    """Effective-dated membership of a committee."""

    __tablename__ = "secretarial_committee_members"
    __table_args__ = (Index("ix_secretarial_committee_members", "committee_id", "valid_to"),)

    committee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_committees.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="RESTRICT"), nullable=False
    )
    is_chair: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)


class SecretarialGroupLink(Base, DocumentMixin, CompanyScopedMixin):
    """Holding / subsidiary / associate relationships, effective-dated.

    The counterparty may be another entity in this tenant (``related_entity_id``) or a
    company we only know by name — most group members are not on our books.
    """

    __tablename__ = "secretarial_group_links"
    __table_args__ = (Index("ix_secretarial_group_links_entity", "entity_id", "valid_to"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="SET NULL")
    )
    related_entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    related_cin: Mapped[str | None] = mapped_column(String(21))
    relation: Mapped[str] = mapped_column(String(30), nullable=False)
    shareholding_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialRelatedParty(Base, DocumentMixin, CompanyScopedMixin):
    """s.188 / Ind AS 24 working list — derived from masters, editable by hand.

    ``basis`` records *why* a party is on the list, and ``is_manual`` protects rows a
    user added from being wiped by the next sync. Sync rewrites only rows it owns.
    """

    __tablename__ = "secretarial_related_parties"
    __table_args__ = (
        Index("ix_secretarial_related_parties_entity", "entity_id", "basis"),
        UniqueConstraint(
            "entity_id", "party_name", "basis", name="uq_secretarial_related_party"
        ),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    basis: Mapped[str] = mapped_column(String(20), nullable=False)
    relationship_note: Mapped[str | None] = mapped_column(String(300))
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="SET NULL")
    )
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="SET NULL")
    )
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class SecretarialBeneficialOwner(Base, DocumentMixin, CompanyScopedMixin):
    """s.90 register of significant beneficial owners, effective-dated."""

    __tablename__ = "secretarial_beneficial_owners"
    __table_args__ = (Index("ix_secretarial_bo_entity", "entity_id", "valid_from", "valid_to"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="SET NULL")
    )
    person_name: Mapped[str] = mapped_column(String(200), nullable=False)
    classification: Mapped[str] = mapped_column(String(10), nullable=False)
    holding_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    nature_of_interest: Mapped[str | None] = mapped_column(String(300))
    declaration_ref: Mapped[str | None] = mapped_column(String(80))
    declared_on: Mapped[date | None] = mapped_column(Date)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)


class SecretarialAuditor(Base, DocumentMixin, CompanyScopedMixin):
    """Statutory / internal / secretarial / cost auditor appointments (s.139)."""

    __tablename__ = "secretarial_auditors"
    __table_args__ = (Index("ix_secretarial_auditors_entity", "entity_id", "ceased_on"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    firm_name: Mapped[str] = mapped_column(String(200), nullable=False)
    registration_no: Mapped[str | None] = mapped_column(String(40))  # FRN / membership no.
    auditor_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'statutory'")
    )
    appointed_on: Mapped[date | None] = mapped_column(Date)
    appointment_mode: Mapped[str | None] = mapped_column(String(40))
    term_from_fy: Mapped[str | None] = mapped_column(String(9))  # "2024-25"
    term_to_fy: Mapped[str | None] = mapped_column(String(9))
    ceased_on: Mapped[date | None] = mapped_column(Date)
    cessation_reason: Mapped[str | None] = mapped_column(String(200))
    adt1_filed_on: Mapped[date | None] = mapped_column(Date)
    email: Mapped[str | None] = mapped_column(String(140))
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialCharge(Base, DocumentMixin, CompanyScopedMixin):
    """Register of charges (s.85) — CHG-1 / CHG-4 supporting record."""

    __tablename__ = "secretarial_charges"
    __table_args__ = (Index("ix_secretarial_charges_entity", "entity_id", "status"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    charge_id_no: Mapped[str | None] = mapped_column(String(40))  # MCA charge id
    holder_name: Mapped[str] = mapped_column(String(200), nullable=False)
    charge_type: Mapped[str | None] = mapped_column(String(60))
    amount_secured: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    property_description: Mapped[str | None] = mapped_column(Text)
    created_on: Mapped[date | None] = mapped_column(Date)
    modified_on: Mapped[date | None] = mapped_column(Date)
    satisfied_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'open'"))
    srn: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialDsc(Base, DocumentMixin, CompanyScopedMixin):
    """Digital signature certificates held for e-filing, with expiry tracking."""

    __tablename__ = "secretarial_dscs"
    __table_args__ = (Index("ix_secretarial_dscs_expiry", "company_id", "expires_on"),)

    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE")
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="SET NULL")
    )
    holder_name: Mapped[str] = mapped_column(String(200), nullable=False)
    serial_no: Mapped[str | None] = mapped_column(String(80))
    issuing_authority: Mapped[str | None] = mapped_column(String(140))
    issued_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    custodian: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialFile(Base, DocumentMixin, CompanyScopedMixin):
    """Uploaded evidence — signed minutes scans, challans, MCA exports.

    First real file-upload surface in the codebase, so it follows the Tally pattern:
    base64 in a JSON body with a hard size cap, bytes in Postgres. ``storage_backend``
    is the seam for moving to object storage later without changing any caller —
    ``content`` simply becomes NULL and ``storage_ref`` carries the key.
    """

    __tablename__ = "secretarial_files"
    __table_args__ = (
        Index("ix_secretarial_files_ref", "reference_doctype", "reference_id"),
        Index("ix_secretarial_files_entity", "entity_id"),
    )

    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE")
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=text("'application/octet-stream'")
    )
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_backend: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'db'")
    )
    storage_ref: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[bytes | None] = mapped_column(LargeBinary)

    reference_doctype: Mapped[str | None] = mapped_column(String(100))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    category: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(Text)
