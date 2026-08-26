"""Module 12 (Data Migration) — import session, mapping and source-profile schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel


# --- Coverage catalogue --------------------------------------------------------------


class MigrationEntityCoverage(BaseModel):
    """One row of the Tally -> OptiERP coverage matrix."""

    key: str
    label: str
    tally_tag: str
    target: str
    module: str
    stage: int
    support: str  # full | partial | reference | none
    notes: str


class MigrationCatalogueResponse(BaseModel):
    entities: list[MigrationEntityCoverage]
    #: Tally reserved group -> {root_type, account_type, party_type}
    primary_groups: dict[str, dict[str, str | None]]
    #: Tally voucher type -> target DocType
    voucher_types: dict[str, str]
    modules: list[str]


# --- Sessions ------------------------------------------------------------------------


class MigrationImportOptions(BaseModel):
    """Run options stored on the session."""

    submit_vouchers: bool = True  # False leaves everything as drafts
    default_warehouse_id: uuid.UUID | None = None
    order_lead_days: int = Field(default=7, ge=0, le=365)


class MigrationImportCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    opening_date: date | None = None
    options: MigrationImportOptions = Field(default_factory=MigrationImportOptions)


class MigrationImportEntityResponse(DocumentMeta):
    entity_key: str
    label: str
    target_doctype: str
    module: str
    stage: int
    support: str
    selected: bool
    total: int
    created: int
    updated: int
    skipped: int
    failed: int


class MigrationImportListItem(ORMModel):
    id: uuid.UUID
    name: str
    title: str | None
    source_type: str
    #: Which application the file came out of — "Tally", "Zoho Books",
    #: "OptiERP Template", "Custom". Null on sessions created before Module 12
    #: learned about anything but Tally.
    source_app: str | None = None
    file_name: str | None
    source_company_name: str | None
    from_date: date | None
    to_date: date | None
    status: str
    total_records: int
    imported_count: int
    skipped_count: int
    error_count: int
    creation: datetime
    finished_at: datetime | None


class MigrationImportResponse(DocumentMeta):
    name: str
    title: str | None
    company_id: uuid.UUID
    source_type: str
    source_app: str | None = None
    #: Key of the workbook mapping that parsed this session — a built-in profile,
    #: a company-saved one, or "custom" when nothing matched. Null for XML/CSV,
    #: which carry their own structure and need no mapping.
    source_profile: str | None = None
    file_name: str | None
    file_size: int
    source_company_name: str | None
    #: Tally's own company UUID. Two sessions sharing this came from the same
    #: Tally company, whatever the file was called or the company renamed to.
    source_company_guid: str | None
    from_date: date | None
    to_date: date | None
    opening_date: date | None
    status: str
    options: dict[str, Any] | None
    total_records: int
    imported_count: int
    skipped_count: int
    error_count: int
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    entities: list[MigrationImportEntityResponse] = Field(default_factory=list)


class MigrationImportLogResponse(ORMModel):
    id: uuid.UUID
    creation: datetime
    phase: str
    entity_key: str | None
    level: str
    message: str
    context: dict[str, Any] | None


class MigrationImportSummaryResponse(BaseModel):
    session: MigrationImportResponse
    entities: list[MigrationImportEntityResponse]
    logs: list[MigrationImportLogResponse]
    status_counts: dict[str, int]
    unmapped_count: int
    low_confidence_count: int


class MigrationEntitySelection(BaseModel):
    """Turn entities on/off before a run: {"voucher_sales": true, ...}"""

    selections: dict[str, bool]


# --- Staging -------------------------------------------------------------------------


class MigrationStagingMessage(BaseModel):
    level: str
    message: str
    field: str | None = None


class MigrationStagingRecordResponse(ORMModel):
    id: uuid.UUID
    entity_key: str
    sequence: int
    source_guid: str | None
    source_name: str | None
    source_parent: str | None
    source_voucher_type: str | None
    voucher_number: str | None
    posting_date: date | None
    amount: Decimal | None
    target_doctype: str | None
    target_id: uuid.UUID | None
    target_name: str | None
    status: str
    messages: list[dict[str, Any]] | None


class MigrationStagingListResponse(BaseModel):
    items: list[MigrationStagingRecordResponse]
    total: int
    limit: int
    offset: int


class MigrationStagingDetailResponse(MigrationStagingRecordResponse):
    """A single staging row including the raw Tally payload, for debugging."""

    raw: dict[str, Any] | None
    normalised: dict[str, Any] | None


# --- Mappings ------------------------------------------------------------------------


class MigrationMappingResponse(DocumentMeta):
    entity_key: str
    source_name: str
    source_guid: str | None
    source_parent: str | None
    target_doctype: str
    target_id: uuid.UUID | None
    target_name: str | None
    match_method: str
    confidence: int
    is_locked: bool
    attributes: dict[str, Any] | None
    notes: str | None


class MigrationMappingUpdate(BaseModel):
    """A tester's explicit mapping decision (locks the row)."""

    target_doctype: str
    target_id: uuid.UUID | None = None
    notes: str | None = None


class MigrationMappingCreate(MigrationMappingUpdate):
    entity_key: str
    source_name: str = Field(min_length=1, max_length=255)


class MigrationMappingBulkUpdate(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1)


# --- Phase results -------------------------------------------------------------------


class MigrationAutoMapResponse(BaseModel):
    summary: dict[str, dict[str, int]]
    session: MigrationImportResponse


class MigrationValidateResponse(BaseModel):
    planned: dict[str, int]
    total_planned: int
    unresolved: dict[str, list[str]]
    unresolved_count: int
    blockers: list[str]
    #: Records in this file that this company already imported. Not blockers —
    #: the run skips them — but the tester needs to see them before committing,
    #: because they are the difference between "900 new" and "588 new".
    duplicates: list[dict[str, Any]] = []
    duplicate_count: int = 0
    #: Records this company imported that Tally has since edited (same voucher,
    #: higher ALTERID). Reported apart from duplicates because they need a
    #: decision: the two copies genuinely disagree.
    amendments: list[dict[str, Any]] = []
    amendment_count: int = 0
    #: Cancelled or "optional" vouchers in the file. Staged and reported, never
    #: posted — so they are excluded from `planned` rather than promised.
    not_posting_count: int = 0
    session: MigrationImportResponse


class MigrationSyncCompany(BaseModel):
    """How far this OptiERP company has synced one Tally company."""

    source_company_guid: str | None
    source_company_name: str | None
    #: High-water ALTERID. Export "changes above this" from Tally next time.
    last_alter_id: int | None
    documents_imported: int
    last_imported_at: datetime | None
    earliest_voucher_date: date | None
    latest_voucher_date: date | None


class MigrationSyncStateResponse(BaseModel):
    companies: list[MigrationSyncCompany]
    total_documents_imported: int


class MigrationImportStatusResponse(BaseModel):
    """Progress of a run in flight — safe to poll every second or two."""

    id: uuid.UUID
    status: str
    is_running: bool
    total_records: int
    processed: int
    imported_count: int
    skipped_count: int
    error_count: int
    percent: int
    started_at: datetime | None
    #: Last sign of life. If this stops advancing while `is_running` is true, the
    #: reaper will retire the run; it is not stuck forever.
    heartbeat_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    current_entity: str | None


class MigrationRollbackResponse(BaseModel):
    cancelled: int
    failed: list[str]
    session: MigrationImportResponse


# --- Spreadsheet source profiles -----------------------------------------------------


class SourceProfileSummary(BaseModel):
    """A workbook shape the importer knows, built-in or saved by this company."""

    key: str
    label: str
    app: str
    notes: str = ""
    saved: bool = False
    id: uuid.UUID | None = None
    use_count: int = 0


class SourceProfileCreate(BaseModel):
    """Save the mapping currently on an import as a reusable named profile."""

    label: str = Field(min_length=1, max_length=140)
    source_app: str | None = Field(default=None, max_length=60)
    notes: str | None = None
    #: Serialised SourceProfile. Omitted when saving straight off a session, in
    #: which case that session's own mapping is stored.
    definition: dict[str, Any] | None = None
    migration_import_id: uuid.UUID | None = None


class SourceProfileResponse(DocumentMeta):
    key: str
    label: str
    source_app: str | None
    notes: str | None
    use_count: int
    definition: dict[str, Any]


class MappingUpdate(BaseModel):
    """Change how a spreadsheet import reads its workbook, then re-parse.

    Either a whole edited ``definition`` from the wizard, or ``profile`` to adopt
    a different built-in or saved mapping wholesale.
    """

    definition: dict[str, Any] | None = None
    profile: str | None = Field(default=None, max_length=80)
