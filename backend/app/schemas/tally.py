"""Module 12 (Data Migration) — Tally import schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel


# --- Coverage catalogue --------------------------------------------------------------


class TallyEntityCoverage(BaseModel):
    """One row of the Tally -> OptiERP coverage matrix."""

    key: str
    label: str
    tally_tag: str
    target: str
    module: str
    stage: int
    support: str  # full | partial | reference | none
    notes: str


class TallyCatalogueResponse(BaseModel):
    entities: list[TallyEntityCoverage]
    #: Tally reserved group -> {root_type, account_type, party_type}
    primary_groups: dict[str, dict[str, str | None]]
    #: Tally voucher type -> target DocType
    voucher_types: dict[str, str]
    modules: list[str]


# --- Sessions ------------------------------------------------------------------------


class TallyImportOptions(BaseModel):
    """Run options stored on the session."""

    submit_vouchers: bool = True  # False leaves everything as drafts
    default_warehouse_id: uuid.UUID | None = None
    order_lead_days: int = Field(default=7, ge=0, le=365)


class TallyImportCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    opening_date: date | None = None
    options: TallyImportOptions = Field(default_factory=TallyImportOptions)


class TallyImportEntityResponse(DocumentMeta):
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


class TallyImportListItem(ORMModel):
    id: uuid.UUID
    name: str
    title: str | None
    source_type: str
    file_name: str | None
    tally_company_name: str | None
    from_date: date | None
    to_date: date | None
    status: str
    total_records: int
    imported_count: int
    skipped_count: int
    error_count: int
    creation: datetime
    finished_at: datetime | None


class TallyImportResponse(DocumentMeta):
    name: str
    title: str | None
    company_id: uuid.UUID
    source_type: str
    file_name: str | None
    file_size: int
    tally_company_name: str | None
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
    entities: list[TallyImportEntityResponse] = Field(default_factory=list)


class TallyImportLogResponse(ORMModel):
    id: uuid.UUID
    creation: datetime
    phase: str
    entity_key: str | None
    level: str
    message: str
    context: dict[str, Any] | None


class TallyImportSummaryResponse(BaseModel):
    session: TallyImportResponse
    entities: list[TallyImportEntityResponse]
    logs: list[TallyImportLogResponse]
    status_counts: dict[str, int]
    unmapped_count: int
    low_confidence_count: int


class TallyEntitySelection(BaseModel):
    """Turn entities on/off before a run: {"voucher_sales": true, ...}"""

    selections: dict[str, bool]


# --- Staging -------------------------------------------------------------------------


class TallyStagingMessage(BaseModel):
    level: str
    message: str
    field: str | None = None


class TallyStagingRecordResponse(ORMModel):
    id: uuid.UUID
    entity_key: str
    sequence: int
    tally_guid: str | None
    tally_name: str | None
    tally_parent: str | None
    tally_voucher_type: str | None
    voucher_number: str | None
    posting_date: date | None
    amount: Decimal | None
    target_doctype: str | None
    target_id: uuid.UUID | None
    target_name: str | None
    status: str
    messages: list[dict[str, Any]] | None


class TallyStagingListResponse(BaseModel):
    items: list[TallyStagingRecordResponse]
    total: int
    limit: int
    offset: int


class TallyStagingDetailResponse(TallyStagingRecordResponse):
    """A single staging row including the raw Tally payload, for debugging."""

    raw: dict[str, Any] | None
    normalised: dict[str, Any] | None


# --- Mappings ------------------------------------------------------------------------


class TallyMappingResponse(DocumentMeta):
    entity_key: str
    tally_name: str
    tally_guid: str | None
    tally_parent: str | None
    target_doctype: str
    target_id: uuid.UUID | None
    target_name: str | None
    match_method: str
    confidence: int
    is_locked: bool
    attributes: dict[str, Any] | None
    notes: str | None


class TallyMappingUpdate(BaseModel):
    """A tester's explicit mapping decision (locks the row)."""

    target_doctype: str
    target_id: uuid.UUID | None = None
    notes: str | None = None


class TallyMappingCreate(TallyMappingUpdate):
    entity_key: str
    tally_name: str = Field(min_length=1, max_length=255)


class TallyMappingBulkUpdate(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1)


# --- Phase results -------------------------------------------------------------------


class TallyAutoMapResponse(BaseModel):
    summary: dict[str, dict[str, int]]
    session: TallyImportResponse


class TallyValidateResponse(BaseModel):
    planned: dict[str, int]
    total_planned: int
    unresolved: dict[str, list[str]]
    unresolved_count: int
    blockers: list[str]
    session: TallyImportResponse


class TallyRollbackResponse(BaseModel):
    cancelled: int
    failed: list[str]
    session: TallyImportResponse


class TallyWorkspaceStats(BaseModel):
    """Numbers for the Data Migration workspace card."""

    total_imports: int
    completed_imports: int
    failed_imports: int
    documents_imported: int
    unmapped_names: int
    last_import_at: datetime | None
    last_import_status: str | None
    supported_entities: int
