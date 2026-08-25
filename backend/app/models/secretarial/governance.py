"""Board and general meetings, circular resolutions, certified true copies.

This is the module's spine: the unbroken thread from *a director consented* through
*the minutes say so* to *here is a certified copy*, with no re-keying of the
resolution text at any step.

Three integrity mechanisms live here and are worth understanding before editing:

* **Minutes-book numbering** is a gap-free serial per (entity, scope). Consecutive
  numbering is what stops a page being inserted after the fact, so the counter is a
  row that gets locked, not a ``MAX()+1``.
* **Consent and circulation events are append-only.** A director's response is
  evidence; it is written once and never updated in place.
* **A CTC is never edited.** A correction is a fresh issuance that references the one
  it supersedes, because a certified copy that quietly changed is worthless.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

MEETING_TYPES = ("board", "agm", "egm", "committee", "partners")

MEETING_STATUSES = (
    "draft",
    "scheduled",
    "circulated",
    "held",
    "minutes_draft",
    "minutes_signed",
    "closed",
    "cancelled",
)

ATTENDANCE_STATUSES = ("present", "absent", "leave_of_absence", "video")

CIRCULAR_STATUSES = ("draft", "circulating", "passed", "failed", "expired", "ratified", "cancelled")

CONSENT_STATUSES = ("pending", "viewed", "consented", "declined", "abstained")

CIRCULATION_STATUSES = ("pending", "viewed", "acknowledged")

CONSENT_RULES = ("majority", "unanimous", "two_thirds")

PASSAGE_MODES = ("board", "circular", "agm", "egm", "committee", "other")

TOKEN_PURPOSES = ("circulation_view", "consent_respond", "client_view")


class SecretarialMeeting(Base, DocumentMixin, CompanyScopedMixin):
    """A board, general or committee meeting.

    The SS-1/SS-2 dates are stored rather than computed on read: a notice period is
    judged against the law as it stood when the meeting was called, and recomputing it
    later from today's rules would quietly rewrite history.
    """

    __tablename__ = "secretarial_meetings"
    __table_args__ = (
        # Per book, not per entity: "the 5th Board Meeting" and "the 5th AGM" are
        # different meetings that both legitimately carry the number 5.
        UniqueConstraint(
            "entity_id", "meeting_type", "serial_no", name="uq_secretarial_meeting_serial"
        ),
        Index("ix_secretarial_meetings_entity", "entity_id", "meeting_type", "scheduled_at"),
        Index("ix_secretarial_meetings_status", "company_id", "status"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    meeting_type: Mapped[str] = mapped_column(String(20), nullable=False)
    committee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_committees.id", ondelete="SET NULL")
    )

    # Human-facing number: "5th Board Meeting". Assigned on scheduling.
    serial_no: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(250))
    fy: Mapped[str] = mapped_column(String(9), nullable=False)

    scheduled_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    held_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    venue: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'physical'"))

    chairperson_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="SET NULL")
    )
    quorum_required: Mapped[int | None] = mapped_column(Integer)
    quorum_met: Mapped[bool | None] = mapped_column(Boolean)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))

    # --- SS-1 / SS-2 date maths, frozen at creation -------------------------------
    notice_days_required: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("7")
    )
    notice_due_on: Mapped[date | None] = mapped_column(Date)
    notice_sent_on: Mapped[date | None] = mapped_column(Date)
    shorter_notice: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    minutes_draft_due_on: Mapped[date | None] = mapped_column(Date)
    minutes_signed_due_on: Mapped[date | None] = mapped_column(Date)
    minutes_draft_on: Mapped[date | None] = mapped_column(Date)
    minutes_signed_on: Mapped[date | None] = mapped_column(Date)

    # Consumed from the minutes-book counter when the minutes are signed, not before —
    # an abandoned draft must never burn a number.
    minutes_entry_no: Mapped[int | None] = mapped_column(Integer)
    minutes_page_from: Mapped[int | None] = mapped_column(Integer)
    minutes_page_to: Mapped[int | None] = mapped_column(Integer)

    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialAgendaItem(Base, DocumentMixin, CompanyScopedMixin):
    """One item of business. The single source for notice, minutes and attendance."""

    __tablename__ = "secretarial_agenda_items"
    __table_args__ = (
        UniqueConstraint("meeting_id", "seq", name="uq_secretarial_agenda_seq"),
        Index("ix_secretarial_agenda_meeting", "meeting_id", "seq"),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    resolution_text: Mapped[str | None] = mapped_column(Text)
    resolution_kind: Mapped[str | None] = mapped_column(String(20))  # ordinary | special | board

    # predefined | library | manual | ratification
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'manual'"))
    pack_code: Mapped[str | None] = mapped_column(String(80))

    # A ratification item carries the circular it confirms.
    circular_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
    )
    is_passed: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialAttendance(Base, DocumentMixin, CompanyScopedMixin):
    """Who attended, who was absent, who had leave of absence recorded."""

    __tablename__ = "secretarial_attendance"
    __table_args__ = (
        UniqueConstraint("meeting_id", "person_id", name="uq_secretarial_attendance"),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'present'"))
    joined_via: Mapped[str | None] = mapped_column(String(40))
    is_chairperson: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    remarks: Mapped[str | None] = mapped_column(Text)


class MinutesBookSequence(Base, DocumentMixin, CompanyScopedMixin):
    """Gap-free entry and page counters per (entity, scope).

    Consecutive numbering is an integrity mechanism, not a convenience: an auditor
    checks it precisely to detect a page inserted after the fact. Allocation happens
    inside the minutes-signing transaction with the row locked ``FOR UPDATE``.
    """

    __tablename__ = "secretarial_minutes_book_seq"
    __table_args__ = (
        UniqueConstraint("entity_id", "scope", name="uq_secretarial_minutes_scope"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    # board | general | committee:<committee_id>
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    next_entry_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    next_page_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class SecretarialCircular(Base, DocumentMixin, CompanyScopedMixin):
    """A resolution by circulation under s.175.

    The Rule 5 eligibility check is stored, not just enforced: when a circular is
    blocked, the reason and the statutory reference become part of the record, which is
    what makes the guardrail auditable rather than merely obstructive.
    """

    __tablename__ = "secretarial_circulars"
    __table_args__ = (
        UniqueConstraint("entity_id", "reference_no", name="uq_secretarial_circular_ref"),
        Index("ix_secretarial_circulars_entity", "entity_id", "status"),
        Index("ix_secretarial_circulars_expiry", "status", "expires_at"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    resolution_text: Mapped[str] = mapped_column(Text, nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(80))
    fy: Mapped[str | None] = mapped_column(String(9))

    consent_rule: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'majority'")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    circulated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    # Outcome of the Rule 5 restricted-matter check, kept whatever the answer was.
    eligibility_checked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    eligibility_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Ratification at a subsequent board meeting (s.175 proviso).
    ratified_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    ratified_on: Mapped[date | None] = mapped_column(Date)
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialConsentResponse(Base, DocumentMixin, CompanyScopedMixin):
    """One director's position on one circular. Append-only in spirit: the status
    moves forward only, and every transition is timestamped."""

    __tablename__ = "secretarial_consent_responses"
    __table_args__ = (
        UniqueConstraint("circular_id", "person_id", name="uq_secretarial_consent"),
        Index("ix_secretarial_consent_circular", "circular_id", "status"),
    )

    circular_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_circulars.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    is_interested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    viewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    responded_ip: Mapped[str | None] = mapped_column(String(64))
    comments: Mapped[str | None] = mapped_column(Text)


class SecretarialCirculation(Base, DocumentMixin, CompanyScopedMixin):
    """One send of papers to a set of recipients — the evidence of service."""

    __tablename__ = "secretarial_circulations"
    __table_args__ = (Index("ix_secretarial_circulations_meeting", "meeting_id"),)

    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="CASCADE")
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    document_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class SecretarialCirculationRecipient(Base, DocumentMixin, CompanyScopedMixin):
    """Per-director delivery state: pending → viewed → acknowledged, with times."""

    __tablename__ = "secretarial_circulation_recipients"
    __table_args__ = (
        UniqueConstraint("circulation_id", "person_id", name="uq_secretarial_circ_recipient"),
    )

    circulation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("secretarial_circulations.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(140))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    viewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    resend_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class SecretarialPortalToken(Base, DocumentMixin, CompanyScopedMixin):
    """A capability handed to someone with no account.

    Directors never log in — that is deliberate (plan §11). The raw token exists only
    in the emailed link; this row stores its SHA-256. Single-purpose, expiring, and
    revoked the moment the directorship ends.
    """

    __tablename__ = "secretarial_portal_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_secretarial_portal_token"),
        Index("ix_secretarial_portal_tokens_subject", "purpose", "person_id"),
    )

    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_persons.id", ondelete="CASCADE")
    )
    # What the token unlocks — a circulation recipient row or a consent response row.
    target_doctype: Mapped[str] = mapped_column(String(60), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(200))
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class SecretarialPortalEvent(Base, DocumentMixin, CompanyScopedMixin):
    """Append-only log of every portal hit — the evidence trail behind a consent."""

    __tablename__ = "secretarial_portal_events"
    __table_args__ = (Index("ix_secretarial_portal_events_token", "token_id", "creation"),)

    token_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("secretarial_portal_tokens.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class SecretarialCtc(Base, DocumentMixin, CompanyScopedMixin):
    """A certified true copy. Append-only: corrections supersede, never overwrite."""

    __tablename__ = "secretarial_ctcs"
    __table_args__ = (
        UniqueConstraint("entity_id", "issuance_no", name="uq_secretarial_ctc_no"),
        Index("ix_secretarial_ctcs_entity", "entity_id", "issued_at"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    issuance_no: Mapped[int] = mapped_column(Integer, nullable=False)
    passage_mode: Mapped[str] = mapped_column(String(20), nullable=False)

    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    circular_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
    )

    resolution_text: Mapped[str] = mapped_column(Text, nullable=False)
    passed_on: Mapped[date | None] = mapped_column(Date)
    certified_on: Mapped[date] = mapped_column(Date, nullable=False)
    place: Mapped[str | None] = mapped_column(String(140))
    issued_to: Mapped[str | None] = mapped_column(String(250))
    purpose: Mapped[str | None] = mapped_column(Text)

    signatories: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_documents.id", ondelete="SET NULL")
    )
    issued_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_ctcs.id", ondelete="SET NULL")
    )
    superseded_reason: Mapped[str | None] = mapped_column(Text)


class SecretarialFinancialFacts(Base, DocumentMixin, CompanyScopedMixin):
    """The numbers that decide which obligations apply (plan §2.8).

    One interface, two sources: derived from the ledger when the entity's books are in
    this account, entered by hand when they are not. The rules engine calls one
    function and never learns which it got — that is what lets threshold-driven
    applicability work for a practice's offline clients too.
    """

    __tablename__ = "secretarial_financial_facts"
    __table_args__ = (
        UniqueConstraint("entity_id", "fy", name="uq_secretarial_facts_period"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    fy: Mapped[str] = mapped_column(String(9), nullable=False)

    turnover: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    net_worth: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    paid_up_capital: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    free_reserves: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    securities_premium: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    borrowings: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    deposits: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))

    source: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'manual'"))
    computed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialFiling(Base, DocumentMixin, CompanyScopedMixin):
    """A form actually filed with the Registrar.

    This is the last link in the chain the plan calls the differentiator:
    resolution → document → form → SRN → challan. Filing happens on the MCA portal;
    what lives here is the evidence that it did.
    """

    __tablename__ = "secretarial_filings"
    __table_args__ = (
        Index("ix_secretarial_filings_entity", "entity_id", "filed_on"),
        Index("ix_secretarial_filings_item", "compliance_item_id"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    compliance_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_compliance_items.id", ondelete="SET NULL")
    )
    form_code: Mapped[str] = mapped_column(String(40), nullable=False)
    fy: Mapped[str | None] = mapped_column(String(9))
    srn: Mapped[str | None] = mapped_column(String(40))
    filed_on: Mapped[date | None] = mapped_column(Date)
    filing_fee: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    additional_fee: Mapped[Decimal | None] = mapped_column(Numeric(21, 2))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'prepared'"))

    # What this filing rests on — the thread back to the board's decision.
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    circular_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_documents.id", ondelete="SET NULL")
    )
    challan_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_files.id", ondelete="SET NULL")
    )
    filed_by: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialStatusHistory(Base, DocumentMixin, CompanyScopedMixin):
    """Append-only status trail for compliance items — who moved it, when, and why."""

    __tablename__ = "secretarial_status_history"
    __table_args__ = (Index("ix_secretarial_status_history_item", "item_id", "creation"),)

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("secretarial_compliance_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
