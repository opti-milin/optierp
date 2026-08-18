"""Phase 1 — the compliance rule catalogue, its instances, and reminder dispatch.

The shape mirrors the taxation module: a catalogue of "the government says so" rules,
instantiated per entity per financial year, chased by a daily job. Phase 4 upgrades
``applicability`` from a static entity-class filter to a predicate evaluated against
ledger-derived financial facts — the column is already here so that upgrade is a
service change, not a migration.

Rules carry the same provenance and publish gate as content packs (plan §2.12): an
unpublished rule never reaches a client's calendar.
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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

RULE_BASES = ("fy", "event", "recurring")

ITEM_STATUSES = (
    "not_applicable",
    "upcoming",
    "due",
    "in_progress",
    "pending_review",
    "filed",
    "completed",
    "overdue",
    "waived",
)

# Statuses that mean "no longer needs chasing".
CLOSED_STATUSES = ("completed", "filed", "not_applicable", "waived")

DEFAULT_REMINDER_OFFSETS = (30, 14, 7, 1)


class SecretarialComplianceRule(Base, DocumentMixin):
    """One statutory obligation — the recipe for generating calendar rows.

    ``company_id`` NULL = the shipped catalogue; set = a tenant's own addition.
    Due dates are computed from ``due_formula`` rather than hardcoded, so an entity
    with a non-March financial year gets correct dates without a special case:

        {"anchor": "fy_end", "offset_days": 60}
        {"anchor": "fy_end", "month": 10, "day": 30}
        {"anchor": "event",  "event": "agm_held", "offset_days": 30}
    """

    __tablename__ = "secretarial_compliance_rules"
    __table_args__ = (
        UniqueConstraint("code", "version", "company_id", name="uq_secretarial_rule_version"),
        Index("ix_secretarial_rules_lookup", "code", "review_status"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE")
    )

    code: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    act: Mapped[str | None] = mapped_column(String(140))
    section: Mapped[str | None] = mapped_column(String(60))
    form_code: Mapped[str | None] = mapped_column(String(40))  # MGT-7, AOC-4, DIR-3 KYC…
    authority: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'MCA'"))

    basis: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'fy'"))
    due_formula: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Phase 1: {"kinds": [...], "classes": [...], "listed": bool|null}
    # Phase 4: full predicate AST over entity attributes + financial facts.
    applicability: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reminder_offsets: Mapped[list[int] | None] = mapped_column(JSONB)

    penalty_note: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # --- Provenance and review (plan §2.12) --------------------------------------
    source_ref: Mapped[str | None] = mapped_column(String(300))
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'draft'")
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_on: Mapped[date | None] = mapped_column(Date)
    reviewer_name: Mapped[str | None] = mapped_column(String(200))
    reviewer_credential: Mapped[str | None] = mapped_column(String(140))
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    effective_from: Mapped[date | None] = mapped_column(Date)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_compliance_rules.id", ondelete="SET NULL")
    )

    @property
    def is_usable(self) -> bool:
        return (
            self.is_active
            and self.review_status == "published"
            and self.superseded_by_id is None
        )


class SecretarialComplianceItem(Base, DocumentMixin, CompanyScopedMixin):
    """One obligation for one entity for one period — a calendar row.

    Regenerating the calendar must never duplicate or clobber work in progress, hence
    the unique key on (entity, rule, fy) and the rule that generation only ever
    *inserts* — it refreshes due dates on untouched rows and leaves everything else be.
    """

    __tablename__ = "secretarial_compliance_items"
    __table_args__ = (
        UniqueConstraint("entity_id", "rule_code", "fy", name="uq_secretarial_item_period"),
        Index("ix_secretarial_items_due", "company_id", "due_on", "status"),
        Index("ix_secretarial_items_entity", "entity_id", "fy"),
        Index("ix_secretarial_items_assignee", "assigned_to_user_id", "status"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_compliance_rules.id", ondelete="SET NULL")
    )
    # Denormalised so a deleted/superseded rule never orphans the calendar row.
    rule_code: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    form_code: Mapped[str | None] = mapped_column(String(40))
    act_section: Mapped[str | None] = mapped_column(String(200))

    fy: Mapped[str] = mapped_column(String(9), nullable=False)  # "2025-26"
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'upcoming'"))
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    completed_on: Mapped[date | None] = mapped_column(Date)
    # Filing evidence — expanded into its own table with the SRN chain in Phase 4.
    srn: Mapped[str | None] = mapped_column(String(40))
    filed_on: Mapped[date | None] = mapped_column(Date)
    evidence_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_files.id", ondelete="SET NULL")
    )

    waived_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    @property
    def is_open(self) -> bool:
        return self.status not in CLOSED_STATUSES


class SecretarialComplianceReminder(Base, DocumentMixin, CompanyScopedMixin):
    """One sent reminder — the idempotency key for the daily job.

    Without this the job would re-send every morning for the whole lead window. The
    unique constraint is what makes a re-run safe, exactly as ``tax_compliance_reminders``
    does for advance tax.
    """

    __tablename__ = "secretarial_compliance_reminders"
    __table_args__ = (
        UniqueConstraint("item_id", "offset_days", name="uq_secretarial_reminder_offset"),
        Index("ix_secretarial_reminders_item", "item_id"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("secretarial_compliance_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    sent_to: Mapped[list[str] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'sent'"))
    error_message: Mapped[str | None] = mapped_column(Text)
