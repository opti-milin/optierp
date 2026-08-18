"""Module 13 — Company Secretarial & Governance schemas.

Naming follows the module: an *entity* is the company or LLP whose statutory record
we keep, which is never the same word as the tenant ``Company``.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import DocumentMeta, ORMModel

EntityKind = Literal["company", "llp"]
EntityClass = Literal["private", "public", "opc", "section8", "nidhi", "producer", "llp"]
EntityStatus = Literal["active", "dormant", "struck_off", "amalgamated", "closed"]
RoleType = Literal["director", "designated_partner", "partner", "kmp", "auditor", "secretary"]
TenantProfile = Literal["practice", "business"]
FinancialAccess = Literal["none", "derived_only", "reports_read", "ledger_read"]
SecretarialAccess = Literal["read", "write"]
EngagementStatus = Literal["pending", "active", "suspended", "ended"]
Relationship = Literal["own", "managed", "delegated"]
OnboardingState = Literal["prospect", "onboarding", "active", "dormant", "exited"]
ItemStatus = Literal[
    "not_applicable",
    "upcoming",
    "due",
    "in_progress",
    "pending_review",
    "filed",
    "completed",
    "overdue",
    "waived",
]


# --- Entities -------------------------------------------------------------------


class EntityBase(BaseModel):
    entity_name: str = Field(min_length=1, max_length=200)
    kind: EntityKind = "company"
    entity_class: EntityClass = "private"
    cin: str | None = Field(default=None, max_length=21)
    llpin: str | None = Field(default=None, max_length=8)
    pan: str | None = Field(default=None, max_length=10)
    tan: str | None = Field(default=None, max_length=10)
    gstin: str | None = Field(default=None, max_length=15)
    is_listed: bool = False
    incorporated_on: date | None = None
    fy_end_mmdd: str = Field(default="0331", pattern=r"^(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
    registered_office: dict[str, Any] | None = None
    email: str | None = Field(default=None, max_length=140)
    phone: str | None = Field(default=None, max_length=40)
    website: str | None = Field(default=None, max_length=200)
    notes: str | None = None

    @field_validator("cin", "llpin", "pan", "tan", "gstin", mode="before")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if isinstance(v, str) and v.strip() else None


class EntityCreate(EntityBase):
    """``linked_company_id`` is set by the service for the tenant's own entity, and
    left NULL for a practice's client — it is not a client-supplied field."""

    status: EntityStatus = "active"


class EntityUpdate(BaseModel):
    entity_name: str | None = Field(default=None, min_length=1, max_length=200)
    entity_class: EntityClass | None = None
    cin: str | None = None
    llpin: str | None = None
    pan: str | None = None
    tan: str | None = None
    gstin: str | None = None
    is_listed: bool | None = None
    incorporated_on: date | None = None
    fy_end_mmdd: str | None = Field(default=None, pattern=r"^(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
    registered_office: dict[str, Any] | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    status: EntityStatus | None = None
    notes: str | None = None


class EntityListItem(ORMModel):
    id: uuid.UUID
    entity_name: str
    kind: str
    entity_class: str
    cin: str | None
    llpin: str | None
    status: str
    is_listed: bool
    incorporated_on: date | None
    linked_company_id: uuid.UUID | None


class EntityResponse(DocumentMeta, EntityBase):
    company_id: uuid.UUID
    status: str
    linked_company_id: uuid.UUID | None = None


# --- Persons & appointments -------------------------------------------------------


class PersonBase(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    din: str | None = Field(default=None, max_length=8)
    pan: str | None = Field(default=None, max_length=10)
    is_body_corporate: bool = False
    fathers_name: str | None = Field(default=None, max_length=200)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=20)
    nationality: str | None = Field(default=None, max_length=60)
    occupation: str | None = Field(default=None, max_length=140)
    qualification: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=140)
    mobile: str | None = Field(default=None, max_length=40)
    address: dict[str, Any] | None = None
    kyc_status: Literal["pending", "submitted", "verified", "expired"] = "pending"
    kyc_verified_on: date | None = None
    kyc: dict[str, Any] | None = None
    is_disqualified: bool = False
    disqualification_note: str | None = None
    notes: str | None = None

    @field_validator("din", "pan", mode="before")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if isinstance(v, str) and v.strip() else None


class PersonCreate(PersonBase):
    pass


class PersonUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    din: str | None = None
    pan: str | None = None
    is_body_corporate: bool | None = None
    fathers_name: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    nationality: str | None = None
    occupation: str | None = None
    qualification: str | None = None
    email: str | None = None
    mobile: str | None = None
    address: dict[str, Any] | None = None
    kyc_status: Literal["pending", "submitted", "verified", "expired"] | None = None
    kyc_verified_on: date | None = None
    kyc: dict[str, Any] | None = None
    is_disqualified: bool | None = None
    disqualification_note: str | None = None
    notes: str | None = None


class PersonListItem(ORMModel):
    id: uuid.UUID
    full_name: str
    din: str | None
    pan: str | None
    email: str | None
    mobile: str | None
    kyc_status: str
    is_disqualified: bool
    is_body_corporate: bool


class PersonResponse(DocumentMeta, PersonBase):
    company_id: uuid.UUID
    contact_id: uuid.UUID | None = None


class PersonEntityLink(BaseModel):
    """One entity a person holds office in — the cross-entity view of a DIN."""

    entity_id: uuid.UUID
    entity_name: str
    role_type: str
    designation: str | None
    appointed_on: date
    ceased_on: date | None


class AppointmentBase(BaseModel):
    entity_id: uuid.UUID
    person_id: uuid.UUID
    role_type: RoleType
    designation: str | None = Field(default=None, max_length=140)
    appointed_on: date
    ceased_on: date | None = None
    cessation_reason: str | None = Field(default=None, max_length=200)
    appointment_mode: str | None = Field(default=None, max_length=30)
    is_signing: bool = False
    is_chairperson: bool = False
    notes: str | None = None


class AppointmentCreate(AppointmentBase):
    pass


class AppointmentUpdate(BaseModel):
    designation: str | None = None
    appointed_on: date | None = None
    ceased_on: date | None = None
    cessation_reason: str | None = None
    appointment_mode: str | None = None
    is_signing: bool | None = None
    is_chairperson: bool | None = None
    notes: str | None = None


class AppointmentResponse(DocumentMeta, AppointmentBase):
    company_id: uuid.UUID
    person_name: str | None = None


class CeaseAppointmentIn(BaseModel):
    ceased_on: date
    cessation_reason: str = Field(min_length=1, max_length=200)


# --- Settings -------------------------------------------------------------------


class SettingsResponse(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    profile: str
    practice_name: str | None
    practice_registration_no: str | None
    default_financial_access: str
    reminder_offsets: list[int] | None


class SettingsUpdate(BaseModel):
    profile: TenantProfile | None = None
    practice_name: str | None = Field(default=None, max_length=200)
    practice_registration_no: str | None = Field(default=None, max_length=40)
    default_financial_access: FinancialAccess | None = None
    reminder_offsets: list[int] | None = None


# --- Engagements (delegation) ------------------------------------------------------


class EngagementCreate(BaseModel):
    """Issued by the **client** tenant: 'this firm may work on this entity'."""

    entity_id: uuid.UUID
    firm_company_id: uuid.UUID
    secretarial_access: SecretarialAccess = "write"
    financial_access: FinancialAccess = "ledger_read"
    include_banking: bool = False
    starts_on: date | None = None
    ends_on: date | None = None
    grant_to_user_ids: list[uuid.UUID] = Field(default_factory=list)
    notes: str | None = None


class EngagementUpdate(BaseModel):
    secretarial_access: SecretarialAccess | None = None
    financial_access: FinancialAccess | None = None
    include_banking: bool | None = None
    ends_on: date | None = None
    grant_to_user_ids: list[uuid.UUID] | None = None
    notes: str | None = None


class EngagementEndIn(BaseModel):
    ended_reason: str = Field(min_length=1)


class EngagementResponse(DocumentMeta):
    client_company_id: uuid.UUID
    firm_company_id: uuid.UUID
    entity_id: uuid.UUID
    secretarial_access: str
    financial_access: str
    include_banking: bool
    status: str
    starts_on: date | None
    ends_on: date | None
    accepted_at: datetime | None
    ended_at: datetime | None
    ended_reason: str | None
    granted_user_ids: list[str] | None
    projected_roles: list[str] | None
    notes: str | None
    # Denormalised for display; filled by the service.
    entity_name: str | None = None
    firm_name: str | None = None
    client_name: str | None = None


class AccessSummary(BaseModel):
    """Plain-language rendering of what a grant permits — shown to the client
    *before* they accept, per plan §2.2.1."""

    secretarial: str
    financial: str
    banking: str
    warnings: list[str] = Field(default_factory=list)


# --- Practice roster --------------------------------------------------------------


class PracticeClientRow(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    owner_company_id: uuid.UUID
    engagement_id: uuid.UUID | None
    relationship_type: str
    entity_name: str
    entity_kind: str
    registration_no: str | None
    onboarding_state: str
    billable: bool
    active_from: date | None
    active_to: date | None
    assigned_to_user_id: uuid.UUID | None
    next_due_on: date | None
    overdue_count: int
    open_item_count: int
    last_activity_at: datetime | None
    refreshed_at: datetime | None


class PracticeClientUpdate(BaseModel):
    onboarding_state: OnboardingState | None = None
    billable: bool | None = None
    assigned_to_user_id: uuid.UUID | None = None
    active_from: date | None = None
    active_to: date | None = None


# --- Registers ------------------------------------------------------------------


class MemberBase(BaseModel):
    entity_id: uuid.UUID
    member_name: str = Field(min_length=1, max_length=200)
    folio_no: str = Field(min_length=1, max_length=40)
    member_type: Literal[
        "individual", "body_corporate", "trust", "huf", "nominee", "government"
    ] = "individual"
    person_id: uuid.UUID | None = None
    pan: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=140)
    nationality: str | None = Field(default=None, max_length=60)
    address: dict[str, Any] | None = None
    share_class: str | None = Field(default=None, max_length=60)
    shares_held: Decimal = Field(default=Decimal("0"), ge=0)
    nominal_value: Decimal | None = Field(default=None, ge=0)
    holding_as_on: date | None = None
    joined_on: date | None = None
    ceased_on: date | None = None
    is_beneficial_owner: bool = False
    notes: str | None = None


class MemberCreate(MemberBase):
    pass


class MemberResponse(DocumentMeta, MemberBase):
    company_id: uuid.UUID
    shareholder_id: uuid.UUID | None = None


class CommitteeBase(BaseModel):
    entity_id: uuid.UUID
    committee_name: str = Field(min_length=1, max_length=140)
    committee_type: str | None = Field(default=None, max_length=60)
    constituted_on: date | None = None
    dissolved_on: date | None = None
    terms_of_reference: str | None = None
    quorum: int | None = Field(default=None, ge=1)


class CommitteeMemberIn(BaseModel):
    person_id: uuid.UUID
    is_chair: bool = False
    valid_from: date
    valid_to: date | None = None


class CommitteeCreate(CommitteeBase):
    members: list[CommitteeMemberIn] = Field(default_factory=list)


class CommitteeMemberOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID
    person_name: str | None = None
    is_chair: bool
    valid_from: date
    valid_to: date | None


class CommitteeResponse(DocumentMeta, CommitteeBase):
    company_id: uuid.UUID
    members: list[CommitteeMemberOut] = Field(default_factory=list)


class GroupLinkBase(BaseModel):
    entity_id: uuid.UUID
    related_entity_id: uuid.UUID | None = None
    related_entity_name: str = Field(min_length=1, max_length=200)
    related_cin: str | None = Field(default=None, max_length=21)
    relation: Literal["holding", "subsidiary", "associate", "joint_venture", "fellow_subsidiary"]
    shareholding_pct: Decimal | None = Field(default=None, ge=0, le=100)
    valid_from: date
    valid_to: date | None = None
    notes: str | None = None


class GroupLinkCreate(GroupLinkBase):
    pass


class GroupLinkResponse(DocumentMeta, GroupLinkBase):
    company_id: uuid.UUID


class RelatedPartyBase(BaseModel):
    entity_id: uuid.UUID
    party_name: str = Field(min_length=1, max_length=200)
    basis: Literal["director", "kmp", "member", "group", "relative", "manual"]
    relationship_note: str | None = Field(default=None, max_length=300)
    person_id: uuid.UUID | None = None
    related_entity_id: uuid.UUID | None = None
    valid_from: date | None = None
    valid_to: date | None = None


class RelatedPartyCreate(RelatedPartyBase):
    pass


class RelatedPartyResponse(DocumentMeta, RelatedPartyBase):
    company_id: uuid.UUID
    is_manual: bool


class RelatedPartySyncResult(BaseModel):
    added: int
    updated: int
    removed: int
    kept_manual: int
    total: int


class BeneficialOwnerBase(BaseModel):
    entity_id: uuid.UUID
    person_id: uuid.UUID | None = None
    person_name: str = Field(min_length=1, max_length=200)
    classification: Literal["bo", "sbo", "ubo"]
    holding_pct: Decimal | None = Field(default=None, ge=0, le=100)
    nature_of_interest: str | None = Field(default=None, max_length=300)
    declaration_ref: str | None = Field(default=None, max_length=80)
    declared_on: date | None = None
    valid_from: date
    valid_to: date | None = None


class BeneficialOwnerCreate(BeneficialOwnerBase):
    pass


class BeneficialOwnerResponse(DocumentMeta, BeneficialOwnerBase):
    company_id: uuid.UUID


class AuditorBase(BaseModel):
    entity_id: uuid.UUID
    firm_name: str = Field(min_length=1, max_length=200)
    registration_no: str | None = Field(default=None, max_length=40)
    auditor_type: Literal["statutory", "internal", "secretarial", "cost", "tax"] = "statutory"
    appointed_on: date | None = None
    appointment_mode: str | None = Field(default=None, max_length=40)
    term_from_fy: str | None = Field(default=None, max_length=9)
    term_to_fy: str | None = Field(default=None, max_length=9)
    ceased_on: date | None = None
    cessation_reason: str | None = Field(default=None, max_length=200)
    adt1_filed_on: date | None = None
    email: str | None = Field(default=None, max_length=140)
    notes: str | None = None


class AuditorCreate(AuditorBase):
    pass


class AuditorResponse(DocumentMeta, AuditorBase):
    company_id: uuid.UUID


class ChargeBase(BaseModel):
    entity_id: uuid.UUID
    charge_id_no: str | None = Field(default=None, max_length=40)
    holder_name: str = Field(min_length=1, max_length=200)
    charge_type: str | None = Field(default=None, max_length=60)
    amount_secured: Decimal | None = Field(default=None, ge=0)
    property_description: str | None = None
    created_on: date | None = None
    modified_on: date | None = None
    satisfied_on: date | None = None
    status: Literal["open", "satisfied", "modified"] = "open"
    srn: str | None = Field(default=None, max_length=40)
    notes: str | None = None


class ChargeCreate(ChargeBase):
    pass


class ChargeResponse(DocumentMeta, ChargeBase):
    company_id: uuid.UUID


class DscBase(BaseModel):
    entity_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None
    holder_name: str = Field(min_length=1, max_length=200)
    serial_no: str | None = Field(default=None, max_length=80)
    issuing_authority: str | None = Field(default=None, max_length=140)
    issued_on: date | None = None
    expires_on: date | None = None
    status: Literal["active", "expired", "revoked"] = "active"
    custodian: str | None = Field(default=None, max_length=200)
    notes: str | None = None


class DscCreate(DscBase):
    pass


class DscResponse(DocumentMeta, DscBase):
    company_id: uuid.UUID
    days_to_expiry: int | None = None


# --- Files ----------------------------------------------------------------------


class FileUploadIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_base64: str
    content_type: str = Field(default="application/octet-stream", max_length=120)
    entity_id: uuid.UUID | None = None
    reference_doctype: str | None = Field(default=None, max_length=100)
    reference_id: uuid.UUID | None = None
    category: str | None = Field(default=None, max_length=60)
    description: str | None = None


class FileResponse(ORMModel):
    id: uuid.UUID
    file_name: str
    content_type: str
    file_size: int
    sha256: str | None
    entity_id: uuid.UUID | None
    reference_doctype: str | None
    reference_id: uuid.UUID | None
    category: str | None
    description: str | None
    creation: datetime


# --- Compliance -----------------------------------------------------------------


class ComplianceRuleResponse(ORMModel):
    id: uuid.UUID
    code: str
    version: int
    title: str
    description: str | None
    act: str | None
    section: str | None
    form_code: str | None
    authority: str
    basis: str
    due_formula: dict[str, Any] | None
    applicability: dict[str, Any] | None
    reminder_offsets: list[int] | None
    penalty_note: str | None
    source_ref: str | None
    review_status: str
    reviewer_name: str | None
    reviewed_on: date | None
    is_active: bool
    is_system: bool


class ComplianceRuleReviewIn(BaseModel):
    """Advance a rule or pack along draft → reviewed → approved → published.

    Publishing requires the reviewer to name themselves — the DB enforces it too.
    """

    review_status: Literal["draft", "reviewed", "approved", "published", "retired"]
    reviewer_name: str | None = Field(default=None, max_length=200)
    reviewer_credential: str | None = Field(default=None, max_length=140)
    reviewed_on: date | None = None
    effective_from: date | None = None


class ComplianceItemListItem(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    rule_code: str
    title: str
    form_code: str | None
    act_section: str | None
    fy: str
    due_on: date
    status: str
    assigned_to_user_id: uuid.UUID | None
    completed_on: date | None
    srn: str | None
    filed_on: date | None
    entity_name: str | None = None
    days_to_due: int | None = None


class ComplianceItemUpdate(BaseModel):
    status: ItemStatus | None = None
    assigned_to_user_id: uuid.UUID | None = None
    due_on: date | None = None
    completed_on: date | None = None
    srn: str | None = Field(default=None, max_length=40)
    filed_on: date | None = None
    evidence_file_id: uuid.UUID | None = None
    waived_reason: str | None = None
    notes: str | None = None


class GenerateCalendarIn(BaseModel):
    entity_id: uuid.UUID | None = None  # all entities when omitted
    fy: str = Field(pattern=r"^\d{4}-\d{2}$")


class GenerateCalendarResult(BaseModel):
    fy: str
    entities_processed: int
    rules_evaluated: int
    items_created: int
    items_refreshed: int
    items_skipped_not_applicable: int
    unpublished_rules_ignored: int
    applicability_unknown: int = 0


# --- Workspace ------------------------------------------------------------------


class WorkspaceCard(BaseModel):
    label: str
    value: float | int
    format: str = "int"


class WorkspaceTrendPoint(BaseModel):
    label: str
    value: float


class SecretarialWorkspace(BaseModel):
    profile: str
    entity_count: int
    cards: list[WorkspaceCard]
    chart_title: str
    trend_format: str
    trend: list[WorkspaceTrendPoint]
    currency: str = "INR"
