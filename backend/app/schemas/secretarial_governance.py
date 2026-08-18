"""Schemas for Phases 2-4: documents, meetings, circulars, CTCs, portal, filings.

Split from ``schemas.secretarial`` (Phases 0-1) purely for size — one 1,200-line schema
module is harder to navigate than two that each cover a coherent slice.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

# --- Documents (Phase 2) ----------------------------------------------------------


class DocumentGenerateIn(BaseModel):
    entity_id: uuid.UUID
    pack_code: str = Field(min_length=1, max_length=80)
    fragment: str = Field(default="resolution", max_length=40)
    form_data: dict[str, Any] = Field(default_factory=dict)
    title: str | None = Field(default=None, max_length=250)
    document_date: date | None = None


class DocumentRegenerateIn(BaseModel):
    form_data: dict[str, Any] | None = None
    change_summary: str | None = None


class DocumentListItem(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    title: str
    document_type: str
    fragment: str
    pack_code: str | None
    pack_version: int | None
    status: str
    document_date: date | None
    version: int
    is_current: bool
    change_summary: str | None
    root_id: uuid.UUID | None
    creation: datetime


class DocumentResponse(DocumentListItem):
    resolved_blocks: list[dict[str, Any]] | None = None
    generation_inputs: dict[str, Any] | None = None
    compliance_meta: dict[str, Any] | None = None
    source_doctype: str | None = None
    source_id: uuid.UUID | None = None


class ContentPackOut(ORMModel):
    id: uuid.UUID
    code: str
    version: int
    title: str
    event_type: str
    applies_to_kinds: list[str] | None
    variables: list[dict[str, Any]] | None
    compliance_meta: dict[str, Any] | None
    source_ref: str | None
    review_status: str
    reviewer_name: str | None
    reviewed_on: date | None
    is_system: bool
    fragments: dict[str, Any] | None = None


# --- Meetings (Phase 3) -----------------------------------------------------------

MeetingType = Literal["board", "agm", "egm", "committee", "partners"]
MeetingStatus = Literal[
    "draft",
    "scheduled",
    "circulated",
    "held",
    "minutes_draft",
    "minutes_signed",
    "closed",
    "cancelled",
]


class MeetingCreate(BaseModel):
    entity_id: uuid.UUID
    meeting_type: MeetingType
    scheduled_at: datetime
    venue: str | None = None
    committee_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=250)
    mode: Literal["physical", "video", "hybrid"] = "physical"
    chairperson_id: uuid.UUID | None = None
    fy: str | None = None


class MeetingUpdate(BaseModel):
    title: str | None = None
    scheduled_at: datetime | None = None
    venue: str | None = None
    mode: Literal["physical", "video", "hybrid"] | None = None
    chairperson_id: uuid.UUID | None = None
    quorum_required: int | None = None
    shorter_notice: bool | None = None
    notice_sent_on: date | None = None
    notes: str | None = None


class MeetingListItem(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    meeting_type: str
    serial_no: int | None
    title: str | None
    fy: str
    scheduled_at: datetime
    held_at: datetime | None
    venue: str | None
    status: str
    notice_due_on: date | None
    notice_sent_on: date | None
    minutes_signed_on: date | None
    minutes_entry_no: int | None
    minutes_page_from: int | None
    minutes_page_to: int | None
    quorum_met: bool | None
    entity_name: str | None = None


class MeetingTransitionIn(BaseModel):
    target: MeetingStatus
    on_date: date | None = None
    pages: int = Field(default=1, ge=1, le=200)


class AgendaItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    body: str | None = None
    resolution_text: str | None = None
    resolution_kind: Literal["ordinary", "special", "board"] | None = None
    source: Literal["predefined", "library", "manual", "ratification"] = "manual"
    pack_code: str | None = None


class AgendaItemOut(ORMModel):
    id: uuid.UUID
    meeting_id: uuid.UUID
    seq: int
    title: str
    body: str | None
    resolution_text: str | None
    resolution_kind: str | None
    source: str
    circular_id: uuid.UUID | None
    is_passed: bool | None


class AttendanceIn(BaseModel):
    person_id: uuid.UUID
    status: Literal["present", "absent", "leave_of_absence", "video"] = "present"
    joined_via: str | None = None
    is_chairperson: bool = False
    remarks: str | None = None


class AttendanceOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID
    person_name: str | None = None
    status: str
    joined_via: str | None
    is_chairperson: bool
    remarks: str | None


class GeneratePackIn(BaseModel):
    fragments: list[str] | None = None


# --- Circular resolutions ---------------------------------------------------------


class CircularCreate(BaseModel):
    entity_id: uuid.UUID
    title: str = Field(min_length=1, max_length=400)
    resolution_text: str = Field(min_length=1)
    description: str | None = None
    consent_rule: Literal["majority", "unanimous", "two_thirds"] = "majority"
    reference_no: str | None = Field(default=None, max_length=80)


class CircularCirculateIn(BaseModel):
    person_ids: list[uuid.UUID] | None = None
    interested_person_ids: list[uuid.UUID] = Field(default_factory=list)
    expires_at: datetime | None = None
    override_rule5: bool = False
    override_reason: str | None = None
    send_email: bool = True


class CircularListItem(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    title: str
    reference_no: str | None
    consent_rule: str
    status: str
    fy: str | None
    circulated_at: datetime | None
    expires_at: datetime | None
    decided_at: datetime | None
    ratified_meeting_id: uuid.UUID | None


class CircularResponse(CircularListItem):
    description: str | None = None
    resolution_text: str
    eligibility_result: dict[str, Any] | None = None


class ConsentResponseOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID
    person_name: str | None = None
    status: str
    is_interested: bool
    sent_at: datetime | None
    viewed_at: datetime | None
    responded_at: datetime | None
    comments: str | None


class TallyOut(BaseModel):
    rule: str
    entitled: int
    interested_excluded: int
    consented: int
    declined: int
    abstained: int
    pending: int
    needed: int
    reached: bool
    impossible: bool
    expires_at: datetime | None
    status: str


class EligibilityOut(BaseModel):
    eligible: bool
    blocked_matters: list[dict[str, str]] = Field(default_factory=list)
    message: str
    overridden: bool = False
    override_reason: str | None = None


class RatifyIn(BaseModel):
    meeting_id: uuid.UUID | None = None


# --- Circulation ------------------------------------------------------------------


class CirculateIn(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    person_ids: list[uuid.UUID] | None = None
    subject: str | None = Field(default=None, max_length=300)
    message: str | None = None
    send_email: bool = True


class CirculationRecipientOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID
    person_name: str | None = None
    email: str | None
    status: str
    sent_at: datetime | None
    viewed_at: datetime | None
    acknowledged_at: datetime | None
    resend_count: int


class CirculationOut(ORMModel):
    id: uuid.UUID
    meeting_id: uuid.UUID | None
    entity_id: uuid.UUID
    subject: str
    message: str | None
    document_ids: list[str] | None
    sent_at: datetime | None


# --- Certified true copies --------------------------------------------------------

PassageMode = Literal["board", "circular", "agm", "egm", "committee", "other"]


class CtcSignatory(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    designation: str | None = Field(default=None, max_length=140)
    din: str | None = Field(default=None, max_length=8)


class CtcIssueIn(BaseModel):
    entity_id: uuid.UUID
    passage_mode: PassageMode
    resolution_text: str = Field(min_length=1)
    signatories: list[CtcSignatory] = Field(min_length=1)
    certified_on: date | None = None
    passed_on: date | None = None
    meeting_id: uuid.UUID | None = None
    circular_id: uuid.UUID | None = None
    place: str | None = Field(default=None, max_length=140)
    issued_to: str | None = Field(default=None, max_length=250)
    purpose: str | None = None
    supersedes_id: uuid.UUID | None = None
    superseded_reason: str | None = None


class CtcOut(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    issuance_no: int
    passage_mode: str
    resolution_text: str
    passed_on: date | None
    certified_on: date
    place: str | None
    issued_to: str | None
    purpose: str | None
    signatories: list[dict[str, Any]] | None
    document_id: uuid.UUID | None
    issued_at: datetime
    supersedes_id: uuid.UUID | None
    superseded_reason: str | None


class CtcEmailIn(BaseModel):
    to: list[str] = Field(min_length=1)
    message: str | None = None


# --- Portal (no login) ------------------------------------------------------------


class PortalDocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    document_type: str


class PortalCirculationOut(BaseModel):
    entity_name: str
    subject: str
    message: str | None
    recipient_name: str
    status: str
    sent_at: datetime | None
    acknowledged_at: datetime | None
    documents: list[PortalDocumentOut]


class PortalConsentOut(BaseModel):
    entity_name: str
    title: str
    reference_no: str | None
    resolution_text: str
    description: str | None
    consent_rule: str
    expires_at: datetime | None
    recipient_name: str
    status: str
    responded_at: datetime | None
    circular_status: str


class PortalRespondIn(BaseModel):
    decision: Literal["consented", "declined", "abstained"]
    comments: str | None = None


# --- Financial facts & filings (Phase 4) ------------------------------------------


class FinancialFactsOut(BaseModel):
    fy: str
    source: str
    computed_at: datetime | None = None
    turnover: float | None = None
    net_profit: float | None = None
    net_worth: float | None = None
    paid_up_capital: float | None = None
    free_reserves: float | None = None
    securities_premium: float | None = None
    borrowings: float | None = None
    deposits: float | None = None


class FinancialFactsIn(BaseModel):
    fy: str = Field(pattern=r"^\d{4}-\d{2}$")
    turnover: Decimal | None = None
    net_profit: Decimal | None = None
    net_worth: Decimal | None = None
    paid_up_capital: Decimal | None = None
    free_reserves: Decimal | None = None
    securities_premium: Decimal | None = None
    borrowings: Decimal | None = None
    deposits: Decimal | None = None
    notes: str | None = None


class ApplicabilityCheckOut(BaseModel):
    rule_code: str
    title: str
    verdict: Literal["applies", "not_applicable", "unknown"]
    reasons: list[str]


class FilingCreate(BaseModel):
    entity_id: uuid.UUID
    form_code: str = Field(min_length=1, max_length=40)
    compliance_item_id: uuid.UUID | None = None
    fy: str | None = None
    srn: str | None = Field(default=None, max_length=40)
    filed_on: date | None = None
    status: Literal["prepared", "filed", "approved", "resubmission", "rejected"] = "prepared"
    filing_fee: Decimal | None = None
    additional_fee: Decimal | None = None
    meeting_id: uuid.UUID | None = None
    circular_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    challan_file_id: uuid.UUID | None = None
    filed_by: str | None = Field(default=None, max_length=200)
    notes: str | None = None


class FilingUpdate(BaseModel):
    srn: str | None = None
    filed_on: date | None = None
    status: Literal["prepared", "filed", "approved", "resubmission", "rejected"] | None = None
    filing_fee: Decimal | None = None
    additional_fee: Decimal | None = None
    challan_file_id: uuid.UUID | None = None
    filed_by: str | None = None
    notes: str | None = None


class FilingOut(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    compliance_item_id: uuid.UUID | None
    form_code: str
    fy: str | None
    srn: str | None
    filed_on: date | None
    status: str
    filing_fee: Decimal | None
    additional_fee: Decimal | None
    meeting_id: uuid.UUID | None
    circular_id: uuid.UUID | None
    document_id: uuid.UUID | None
    challan_file_id: uuid.UUID | None
    filed_by: str | None
    notes: str | None


class StatusHistoryOut(ORMModel):
    id: uuid.UUID
    from_status: str | None
    to_status: str
    reason: str | None
    changed_by: uuid.UUID | None
    creation: datetime
