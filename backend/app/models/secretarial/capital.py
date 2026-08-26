"""Phase 5 — share capital, certificates and the s.186 register.

The design decision worth reading before editing anything here: **this module does not
keep a cap table.** ``app/models/accounts/share.py`` already holds an append-only
``share_transfers`` ledger from which every holder's balance is derived, and a second
table of who-owns-what would eventually disagree with it. What lives here is the legal
overlay the Companies Act asks for and a cap table has no reason to carry — the SH-4
instrument, the board approval behind it, the stamp duty, the distinctive numbers, and
the certificate that has to be cancelled and reissued when shares move.

So ``SecretarialShareTransferDetail`` is a *wrapper* keyed to a ``share_transfers`` row.
For a managed client whose books are kept elsewhere there is no row to wrap, and
``share_transfer_id`` is null — the wrapper then stands alone and the UI says so, the
same honesty the financial-facts panel already applies to ledger-derived vs typed figures.

**Distinctive numbers** are a contiguous range per share class, and the live ranges must
tile the issued capital with no gap and no overlap. That is the minutes-book integrity
problem again, so it gets the minutes-book answer: a locked counter row, not ``MAX()+1``.
A cancelled certificate's range is never returned to the pool — the range identifies the
shares, not the paper, so the replacement certificate carries the same numbers.
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

TRANSFER_STATUSES = ("draft", "board_approved", "issued_posted", "reverted")

CERTIFICATE_ISSUE_TYPES = ("original", "duplicate", "renewed", "split", "consolidation")

CERTIFICATE_STATUSES = ("issued", "cancelled", "surrendered")

CAPITAL_EVENT_TYPES = (
    "right_issue",
    "private_placement",
    "preferential_allotment",
    "esop_grant",
    "bonus_issue",
    "buyback",
    "dividend",
)

CAPITAL_EVENT_STATUSES = ("draft", "approved", "allotted", "cancelled")

S186_ENTRY_TYPES = ("loan", "guarantee", "security", "investment")

S186_ENTRY_STATUSES = ("outstanding", "repaid", "invoked", "written_off")

FACT_SOURCES = ("ledger", "manual")


class SecretarialShareTransferDetail(Base, DocumentMixin, CompanyScopedMixin):
    """The SH-4 instrument and its board approval, wrapped around a cap-table movement.

    ``share_transfer_id`` points at the accounts-side ledger row that actually moves the
    shares. It is nullable on purpose: a managed client has no books in this tenant, and
    a legal register that refused to exist without them would be useless to a practice.
    """

    __tablename__ = "secretarial_share_transfer_details"
    __table_args__ = (
        UniqueConstraint("entity_id", "instrument_no", name="uq_secretarial_sh4_no"),
        Index("ix_secretarial_sh4_entity", "entity_id", "executed_on"),
        Index("ix_secretarial_sh4_status", "company_id", "status"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    # Gap-free per entity: "SH-4/2025-26/003". The integer behind it is what is
    # serialised; the label is cosmetic.
    instrument_no: Mapped[int] = mapped_column(Integer, nullable=False)

    share_transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("share_transfers.id", ondelete="SET NULL")
    )

    transferor_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_members.id", ondelete="SET NULL")
    )
    transferee_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_members.id", ondelete="SET NULL")
    )
    # Denormalised so a superseded or deleted member still reads correctly on an old
    # instrument — the register has to survive its subjects.
    transferor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    transferee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    transferor_folio: Mapped[str | None] = mapped_column(String(40))
    transferee_folio: Mapped[str | None] = mapped_column(String(40))

    share_class: Mapped[str] = mapped_column(String(60), nullable=False, server_default=text("'Equity'"))
    no_of_shares: Mapped[Decimal] = mapped_column(Numeric(21, 4), nullable=False)
    face_value: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    consideration: Mapped[Decimal] = mapped_column(
        Numeric(21, 4), nullable=False, server_default=text("0")
    )
    # 0.25% of consideration under Art. 62(a) of the Stamp Act — recorded, not computed,
    # because the rate is state-dependent and the client has the franked instrument.
    stamp_duty: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))

    executed_on: Mapped[date] = mapped_column(Date, nullable=False)  # date on the SH-4
    lodged_on: Mapped[date | None] = mapped_column(Date)  # delivered to the company

    distinctive_from: Mapped[int | None] = mapped_column(Integer)
    distinctive_to: Mapped[int | None] = mapped_column(Integer)

    board_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    board_agenda_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_agenda_items.id", ondelete="SET NULL")
    )
    circular_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
    )
    approved_on: Mapped[date | None] = mapped_column(Date)

    surrendered_certificate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_share_certificates.id", ondelete="SET NULL")
    )
    issued_certificate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_share_certificates.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_documents.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    posted_on: Mapped[date | None] = mapped_column(Date)
    reverted_on: Mapped[date | None] = mapped_column(Date)
    # Mandatory when reverted, and checked in the database: a reversal without a stated
    # reason is exactly the entry an inspection asks about.
    reverted_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialShareCertificate(Base, DocumentMixin, CompanyScopedMixin):
    """SH-1 share certificate. Issued once; a replacement supersedes it.

    ``deferred`` covers the real-world gap between an allotment being approved and the
    certificate physically being issued — s.56(4) allows two months. A deferred row is
    already numbered and already holds its distinctive range, so the register is complete
    the moment the board approves, and the outstanding paper is visible as a list rather
    than as somebody's memory.
    """

    __tablename__ = "secretarial_share_certificates"
    __table_args__ = (
        UniqueConstraint("entity_id", "certificate_no", name="uq_secretarial_certificate_no"),
        Index("ix_secretarial_certificates_entity", "entity_id", "status"),
        Index("ix_secretarial_certificates_member", "member_id"),
        Index(
            "ix_secretarial_certificates_range",
            "entity_id",
            "share_class",
            "distinctive_from",
        ),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    certificate_no: Mapped[int] = mapped_column(Integer, nullable=False)

    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_members.id", ondelete="SET NULL")
    )
    holder_name: Mapped[str] = mapped_column(String(200), nullable=False)
    folio_no: Mapped[str | None] = mapped_column(String(40))

    share_class: Mapped[str] = mapped_column(String(60), nullable=False, server_default=text("'Equity'"))
    no_of_shares: Mapped[Decimal] = mapped_column(Numeric(21, 4), nullable=False)
    face_value: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    amount_paid_up: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))

    distinctive_from: Mapped[int] = mapped_column(Integer, nullable=False)
    distinctive_to: Mapped[int] = mapped_column(Integer, nullable=False)

    issue_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'original'")
    )
    issued_on: Mapped[date | None] = mapped_column(Date)
    deferred: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'issued'"))
    cancelled_on: Mapped[date | None] = mapped_column(Date)
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    # The certificate this one replaces — a transfer, a split, a lost original.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_share_certificates.id", ondelete="SET NULL")
    )
    capital_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_capital_events.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_documents.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialDistinctiveSequence(Base, DocumentMixin, CompanyScopedMixin):
    """Next unallocated distinctive number per (entity, share class).

    Locked with ``SELECT … FOR UPDATE`` when a range is handed out, for the same reason
    the minutes book is (see ``ss_dates.allocate_minutes_number``): two simultaneous
    allotments reading the same maximum would issue overlapping ranges, and overlapping
    distinctive numbers mean two certificates claim the same shares.
    """

    __tablename__ = "secretarial_distinctive_seq"
    __table_args__ = (
        UniqueConstraint("entity_id", "share_class", name="uq_secretarial_distinctive_scope"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    share_class: Mapped[str] = mapped_column(String(60), nullable=False)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class SecretarialCapitalEvent(Base, DocumentMixin, CompanyScopedMixin):
    """A right issue, private placement, ESOP grant, bonus, buyback or dividend.

    One table rather than six because the shape is identical — an authorising resolution,
    an offer, an allotment, a form — and the differences that are real (which form, which
    section) live in the content packs and the rule catalogue, as §2.13 of the plan
    argues for appointments.
    """

    __tablename__ = "secretarial_capital_events"
    __table_args__ = (
        Index("ix_secretarial_capital_events_entity", "entity_id", "event_type", "status"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    fy: Mapped[str | None] = mapped_column(String(9))

    board_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    general_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    circular_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
    )

    share_class: Mapped[str | None] = mapped_column(String(60))
    shares_offered: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    shares_allotted: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    face_value: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    price_per_share: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    premium_per_share: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))

    # For a dividend: the per-share rate and the total outgo checked against s.123.
    dividend_per_share: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))

    offer_on: Mapped[date | None] = mapped_column(Date)
    record_on: Mapped[date | None] = mapped_column(Date)
    closes_on: Mapped[date | None] = mapped_column(Date)
    allotted_on: Mapped[date | None] = mapped_column(Date)

    paid_up_before: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    paid_up_after: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    authorised_capital: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))

    # Allottee list for a placement/ESOP — name, shares, amount. Kept as JSONB because it
    # is evidence of the offer as made, not a queryable ledger; the shares themselves
    # arrive through certificates.
    allottees: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # The s.123 verdict frozen at approval, so a later change of figures does not
    # retroactively make a declared dividend look legal or illegal.
    solvency_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    filing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_filings.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_documents.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialS186Limit(Base, DocumentMixin, CompanyScopedMixin):
    """The s.186 enabling limit for one entity in one financial year.

    Stored rather than recomputed on read for the same reason the SS-1 notice dates are:
    the limit that governs a loan made in October is the one that existed in October.
    A refresh writes a new set of figures; it does not silently change what an old entry
    was judged against.
    """

    __tablename__ = "secretarial_s186_limits"
    __table_args__ = (
        UniqueConstraint("entity_id", "fy", name="uq_secretarial_s186_limit_fy"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    fy: Mapped[str] = mapped_column(String(9), nullable=False)

    paid_up_capital: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    free_reserves: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    securities_premium: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))

    # s.186(2): the higher of 60% of (paid-up + free reserves + securities premium) and
    # 100% of (free reserves + securities premium).
    limit_sixty_pct: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    limit_hundred_pct: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))
    effective_limit: Mapped[Decimal | None] = mapped_column(Numeric(21, 4))

    # A special resolution under s.186(3) lifts the cap entirely.
    special_resolution_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    special_resolution_on: Mapped[date | None] = mapped_column(Date)

    source: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'manual'"))
    computed_on: Mapped[date | None] = mapped_column(Date)
    # Which figures were missing, if any — so the UI can say "cannot judge" instead of
    # "within limits".
    gaps: Mapped[list[str] | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)


class SecretarialS186Entry(Base, DocumentMixin, CompanyScopedMixin):
    """The s.186 register: every loan, guarantee, security and investment.

    s.186(9) requires the register in Form MBP-2 with these particulars, kept at the
    registered office and open to member inspection. The exposure that matters for the
    limit is the *outstanding* total, so repayment is a state change here rather than a
    deletion.
    """

    __tablename__ = "secretarial_s186_entries"
    __table_args__ = (
        Index("ix_secretarial_s186_entries_entity", "entity_id", "status", "made_on"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    fy: Mapped[str | None] = mapped_column(String(9))

    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    party_cin: Mapped[str | None] = mapped_column(String(30))
    # "wholly-owned subsidiary", "joint venture", "unrelated" — s.186(11) exempts some.
    party_relation: Mapped[str | None] = mapped_column(String(60))

    amount: Mapped[Decimal] = mapped_column(Numeric(21, 4), nullable=False)
    rate_of_interest: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    purpose: Mapped[str | None] = mapped_column(Text)
    security_details: Mapped[str | None] = mapped_column(Text)

    made_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date | None] = mapped_column(Date)
    repaid_on: Mapped[date | None] = mapped_column(Date)

    board_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    special_resolution_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )
    # The limit verdict as it stood when the entry was made — see the limit docstring.
    limit_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'outstanding'")
    )
    notes: Mapped[str | None] = mapped_column(Text)
