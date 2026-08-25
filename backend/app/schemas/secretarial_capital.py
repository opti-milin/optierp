"""Schemas for Phase 5: share transfers, certificates, capital events, s.186.

Split out from ``schemas.secretarial_governance`` for the same reason that one was split
from ``schemas.secretarial`` — one coherent slice per module.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel

TransferStatus = Literal["draft", "board_approved", "issued_posted", "reverted"]
CertificateIssueType = Literal["original", "duplicate", "renewed", "split", "consolidation"]
CertificateStatus = Literal["issued", "cancelled", "surrendered"]
CapitalEventType = Literal[
    "right_issue",
    "private_placement",
    "preferential_allotment",
    "esop_grant",
    "bonus_issue",
    "buyback",
    "dividend",
]
CapitalEventStatus = Literal["draft", "approved", "allotted", "cancelled"]
S186EntryType = Literal["loan", "guarantee", "security", "investment"]
S186EntryStatus = Literal["outstanding", "repaid", "invoked", "written_off"]


# --- Share certificates -----------------------------------------------------------


class CertificateIssueIn(BaseModel):
    """Issue a certificate.

    The distinctive range is deliberately *not* an input. It is allocated by the server
    from a locked counter, because a client that could choose its own numbers could
    choose ones already issued.
    """

    entity_id: uuid.UUID
    member_id: uuid.UUID | None = None
    holder_name: str | None = Field(default=None, max_length=200)
    folio_no: str | None = Field(default=None, max_length=40)
    share_class: str = Field(default="Equity", max_length=60)
    no_of_shares: Decimal = Field(gt=0)
    face_value: Decimal | None = None
    amount_paid_up: Decimal | None = None
    issue_type: CertificateIssueType = "original"
    issued_on: date | None = None
    deferred: bool = False
    capital_event_id: uuid.UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _needs_a_holder(self) -> "CertificateIssueIn":
        if not self.member_id and not self.holder_name:
            raise ValueError("A certificate needs a member or a holder name")
        return self


class CertificateCancelIn(BaseModel):
    reason: str = Field(min_length=3)
    cancelled_on: date | None = None
    # Issue the replacement over the same distinctive range in one step — the normal
    # case for a lost or defaced certificate. Off for a surrender, where the shares are
    # gone and no replacement paper is due.
    reissue: bool = True
    reissue_to_member_id: uuid.UUID | None = None
    reissue_issue_type: CertificateIssueType = "duplicate"


class CertificateOut(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    certificate_no: int
    member_id: uuid.UUID | None
    holder_name: str
    folio_no: str | None
    share_class: str
    no_of_shares: Decimal
    face_value: Decimal | None
    amount_paid_up: Decimal | None
    distinctive_from: int
    distinctive_to: int
    issue_type: str
    issued_on: date | None
    deferred: bool
    status: str
    cancelled_on: date | None
    cancelled_reason: str | None
    supersedes_id: uuid.UUID | None
    capital_event_id: uuid.UUID | None
    document_id: uuid.UUID | None
    notes: str | None
    creation: datetime


# --- SH-4 share transfers ---------------------------------------------------------


class TransferCreateIn(BaseModel):
    entity_id: uuid.UUID
    transferor_member_id: uuid.UUID | None = None
    transferee_member_id: uuid.UUID | None = None
    transferor_name: str | None = Field(default=None, max_length=200)
    transferee_name: str | None = Field(default=None, max_length=200)
    share_class: str = Field(default="Equity", max_length=60)
    no_of_shares: Decimal = Field(gt=0)
    face_value: Decimal | None = None
    consideration: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal | None = None
    executed_on: date
    lodged_on: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _needs_both_sides(self) -> "TransferCreateIn":
        if not self.transferor_member_id and not self.transferor_name:
            raise ValueError("Who is transferring the shares?")
        if not self.transferee_member_id and not self.transferee_name:
            raise ValueError("Who is receiving the shares?")
        return self


class TransferUpdateIn(BaseModel):
    no_of_shares: Decimal | None = Field(default=None, gt=0)
    consideration: Decimal | None = Field(default=None, ge=0)
    stamp_duty: Decimal | None = None
    executed_on: date | None = None
    lodged_on: date | None = None
    face_value: Decimal | None = None
    notes: str | None = None


class TransferApproveIn(BaseModel):
    """The board decision that authorises the transfer under s.56."""

    board_meeting_id: uuid.UUID | None = None
    board_agenda_item_id: uuid.UUID | None = None
    circular_id: uuid.UUID | None = None
    approved_on: date | None = None

    @model_validator(mode="after")
    def _needs_authority(self) -> "TransferApproveIn":
        if not self.board_meeting_id and not self.circular_id:
            raise ValueError("A transfer is approved by a board meeting or a circular resolution")
        return self


class TransferPostIn(BaseModel):
    posted_on: date | None = None
    # Which certificate the transferor is giving up. Optional: a transferor whose
    # holding predates the certificate register may not have one to surrender.
    surrender_certificate_id: uuid.UUID | None = None
    issue_certificate: bool = True


class TransferRevertIn(BaseModel):
    # Not optional, and the database says so too. A reversal without a stated reason is
    # the entry an inspection asks about.
    reason: str = Field(min_length=3)
    reverted_on: date | None = None


class TransferOut(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    instrument_no: int
    share_transfer_id: uuid.UUID | None
    transferor_member_id: uuid.UUID | None
    transferee_member_id: uuid.UUID | None
    transferor_name: str
    transferee_name: str
    transferor_folio: str | None
    transferee_folio: str | None
    share_class: str
    no_of_shares: Decimal
    face_value: Decimal | None
    consideration: Decimal
    stamp_duty: Decimal | None
    executed_on: date
    lodged_on: date | None
    distinctive_from: int | None
    distinctive_to: int | None
    board_meeting_id: uuid.UUID | None
    circular_id: uuid.UUID | None
    approved_on: date | None
    surrendered_certificate_id: uuid.UUID | None
    issued_certificate_id: uuid.UUID | None
    document_id: uuid.UUID | None
    status: str
    posted_on: date | None
    reverted_on: date | None
    reverted_reason: str | None
    notes: str | None
    creation: datetime


# --- Capital events ---------------------------------------------------------------


class CapitalEventIn(BaseModel):
    entity_id: uuid.UUID
    event_type: CapitalEventType
    title: str = Field(min_length=1, max_length=250)
    fy: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    board_meeting_id: uuid.UUID | None = None
    general_meeting_id: uuid.UUID | None = None
    circular_id: uuid.UUID | None = None
    share_class: str | None = Field(default=None, max_length=60)
    shares_offered: Decimal | None = None
    face_value: Decimal | None = None
    price_per_share: Decimal | None = None
    premium_per_share: Decimal | None = None
    total_amount: Decimal | None = None
    dividend_per_share: Decimal | None = None
    offer_on: date | None = None
    record_on: date | None = None
    closes_on: date | None = None
    authorised_capital: Decimal | None = None
    allottees: list[dict[str, Any]] | None = None
    notes: str | None = None


class CapitalEventUpdateIn(BaseModel):
    title: str | None = Field(default=None, max_length=250)
    shares_offered: Decimal | None = None
    price_per_share: Decimal | None = None
    premium_per_share: Decimal | None = None
    total_amount: Decimal | None = None
    dividend_per_share: Decimal | None = None
    offer_on: date | None = None
    record_on: date | None = None
    closes_on: date | None = None
    authorised_capital: Decimal | None = None
    allottees: list[dict[str, Any]] | None = None
    board_meeting_id: uuid.UUID | None = None
    general_meeting_id: uuid.UUID | None = None
    filing_id: uuid.UUID | None = None
    notes: str | None = None


class CapitalEventAllotIn(BaseModel):
    allotted_on: date
    shares_allotted: Decimal | None = Field(default=None, gt=0)
    # Issue a certificate per allottee in the same transaction. Off for a dividend,
    # which allots nothing.
    issue_certificates: bool = True


class CapitalEventOut(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    event_type: str
    title: str
    fy: str | None
    board_meeting_id: uuid.UUID | None
    general_meeting_id: uuid.UUID | None
    circular_id: uuid.UUID | None
    share_class: str | None
    shares_offered: Decimal | None
    shares_allotted: Decimal | None
    face_value: Decimal | None
    price_per_share: Decimal | None
    premium_per_share: Decimal | None
    total_amount: Decimal | None
    dividend_per_share: Decimal | None
    offer_on: date | None
    record_on: date | None
    closes_on: date | None
    allotted_on: date | None
    paid_up_before: Decimal | None
    paid_up_after: Decimal | None
    authorised_capital: Decimal | None
    allottees: list[dict[str, Any]] | None
    solvency_check: dict[str, Any] | None
    filing_id: uuid.UUID | None
    document_id: uuid.UUID | None
    status: str
    notes: str | None
    creation: datetime


# --- Cap table --------------------------------------------------------------------


class CapTableRow(BaseModel):
    member_id: uuid.UUID | None
    holder_name: str
    folio_no: str | None
    share_class: str
    shares: Decimal
    pct: Decimal | None
    certificates: int
    distinctive_ranges: list[str]


class CapTableOut(BaseModel):
    """Who holds what, and where the answer came from.

    ``source`` is the honest bit: ``ledger`` when the accounts cap table backs these
    numbers, ``register`` when the certificates are all there is (a managed client whose
    books live elsewhere), ``opening`` when neither exists and the Phase-1 declared
    holdings are being shown.
    """

    entity_id: uuid.UUID
    source: Literal["ledger", "register", "opening"]
    total_shares: Decimal
    rows: list[CapTableRow]
    unissued_from: int | None = None
    notes: list[str] = Field(default_factory=list)


# --- s.186 ------------------------------------------------------------------------


class S186LimitOut(ORMModel):
    id: uuid.UUID | None = None
    entity_id: uuid.UUID
    fy: str
    paid_up_capital: Decimal | None
    free_reserves: Decimal | None
    securities_premium: Decimal | None
    limit_sixty_pct: Decimal | None
    limit_hundred_pct: Decimal | None
    effective_limit: Decimal | None
    special_resolution_meeting_id: uuid.UUID | None
    special_resolution_on: date | None
    source: str
    computed_on: date | None
    gaps: list[str] | None
    notes: str | None
    # Live figures, not stored: what the register currently adds up to against the limit.
    exposure: Decimal | None = None
    headroom: Decimal | None = None
    verdict: Literal["within", "exceeded", "lifted", "unknown"] = "unknown"


class S186LimitIn(BaseModel):
    fy: str = Field(pattern=r"^\d{4}-\d{2}$")
    paid_up_capital: Decimal | None = None
    free_reserves: Decimal | None = None
    securities_premium: Decimal | None = None
    special_resolution_meeting_id: uuid.UUID | None = None
    special_resolution_on: date | None = None
    notes: str | None = None


class S186EntryIn(BaseModel):
    entity_id: uuid.UUID
    entry_type: S186EntryType
    fy: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    party_name: str = Field(min_length=1, max_length=200)
    party_cin: str | None = Field(default=None, max_length=30)
    party_relation: str | None = Field(default=None, max_length=60)
    amount: Decimal = Field(gt=0)
    rate_of_interest: Decimal | None = None
    purpose: str | None = None
    security_details: str | None = None
    made_on: date
    due_on: date | None = None
    board_meeting_id: uuid.UUID | None = None
    special_resolution_meeting_id: uuid.UUID | None = None
    notes: str | None = None


class S186EntryUpdateIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)
    rate_of_interest: Decimal | None = None
    purpose: str | None = None
    security_details: str | None = None
    due_on: date | None = None
    repaid_on: date | None = None
    status: S186EntryStatus | None = None
    board_meeting_id: uuid.UUID | None = None
    special_resolution_meeting_id: uuid.UUID | None = None
    notes: str | None = None


class S186EntryOut(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    entry_type: str
    fy: str | None
    party_name: str
    party_cin: str | None
    party_relation: str | None
    amount: Decimal
    rate_of_interest: Decimal | None
    purpose: str | None
    security_details: str | None
    made_on: date
    due_on: date | None
    repaid_on: date | None
    board_meeting_id: uuid.UUID | None
    special_resolution_meeting_id: uuid.UUID | None
    limit_check: dict[str, Any] | None
    status: str
    notes: str | None
    creation: datetime


# --- Dividend ---------------------------------------------------------------------


class DividendCheckOut(BaseModel):
    """s.123 — is there enough distributable profit to declare this dividend?

    ``verdict`` is ``unknown``, never ``ok``, when a figure is missing. Telling someone
    they are clear because a number is absent is the failure mode the applicability
    engine already refuses, and it matters more here: an unlawful dividend is recoverable
    from the directors personally.
    """

    entity_id: uuid.UUID
    fy: str
    current_profit: Decimal | None
    accumulated_profit: Decimal | None
    accumulated_losses: Decimal | None
    depreciation_provided: bool | None
    distributable: Decimal | None
    proposed: Decimal | None
    verdict: Literal["ok", "exceeded", "unknown"]
    reasons: list[str]
    source: str
