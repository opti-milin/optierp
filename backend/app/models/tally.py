"""Module 12 (Data Migration) — Tally import models.

Five tables carry a migration from "customer emailed me a Tally export" to
"the data is live in OptiERP", without ever writing straight into the ledgers:

``TallyImport``
    One upload = one session. Holds the file, the parse summary and the run
    status. Everything else hangs off it.
``TallyImportEntity``
    Per-entity progress inside a session (120 ledgers: 118 created, 2 failed).
    This is what the wizard's progress table renders.
``TallyStagingRecord``
    One row per record found in the Tally file, with its raw payload, the
    normalised payload, its resolved target and its per-row messages. Nothing
    is imported until its staging row is clean, and every imported document
    points back at the row that produced it.
``TallyMapping``
    The durable name book: "Tally ledger 'ABC Traders' is our Customer <uuid>".
    Company-scoped and reusable, so a second import of the same Tally company
    reuses every decision the tester already made.
``TallyImportLog``
    Append-only audit of what the importer did, per entity, per phase.

The staging table is what makes the feature safe to hand to testers: they can
import, compare against Tally, roll back, fix a mapping, and import again.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

# Session lifecycle. A session only ever moves forward; re-running after a
# rollback creates messages on the same session rather than a new one.
IMPORT_STATUSES = (
    "Draft",  # created, file not parsed yet
    "Parsed",  # records staged, nothing mapped
    "Mapped",  # auto-map done (may still have unresolved rows)
    "Validated",  # dry run passed
    "Importing",  # run in progress
    "Imported",  # everything committed
    "Partially Imported",  # committed with failures
    "Failed",  # aborted
    "Rolled Back",  # documents cancelled/deleted, staging kept
)

# Per-staging-row status.
RECORD_STATUSES = (
    "Pending",  # parsed, not yet resolved
    "Ready",  # resolved and validated — safe to import
    "Warning",  # importable, but something was assumed
    "Error",  # blocked; will not be imported
    "Imported",
    "Skipped",  # deliberately not imported (unsupported entity or user choice)
    "Rolled Back",
)

# What a Tally name was mapped onto.
MAPPING_TARGETS = (
    "Account",
    "Customer",
    "Supplier",
    "Item",
    "Item Group",
    "Warehouse",
    "Cost Center",
    "UOM",
    "Currency",
    "Price List",
    "Voucher Type",
    "Skip",  # explicitly ignore this Tally name
)


class TallyImport(Base, DocumentMixin, CompanyScopedMixin):
    """One Tally import session (upload -> parse -> map -> validate -> run)."""

    __tablename__ = "tally_imports"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_tally_import_name"),
        Index("ix_tally_imports_company_status", "company_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="XML", server_default=text("'XML'")
    )  # XML | CSV | Gateway
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    # sha256 of the upload — lets us warn "you already imported this file".
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # The raw upload, kept so a session can be re-parsed after a parser fix
    # without asking the tester for the file again.
    payload: Mapped[str | None] = mapped_column(Text)

    tally_company_name: Mapped[str | None] = mapped_column(String(200))
    tally_guid: Mapped[str | None] = mapped_column(String(80))
    from_date: Mapped[date | None] = mapped_column(Date)
    to_date: Mapped[date | None] = mapped_column(Date)
    # Migration cut-off: opening balances are booked on this date.
    opening_date: Mapped[date | None] = mapped_column(Date)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    # Run options: which entities to include, submit-vs-draft, dedupe policy...
    options: Mapped[dict | None] = mapped_column(JSONB)

    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    entities = relationship(
        "TallyImportEntity",
        back_populates="tally_import",
        cascade="all, delete-orphan",
        order_by="TallyImportEntity.stage",
    )


class TallyImportEntity(Base, DocumentMixin):
    """Per-entity counters for one session — the wizard's progress table."""

    __tablename__ = "tally_import_entities"
    __table_args__ = (
        UniqueConstraint("tally_import_id", "entity_key", name="uq_tally_import_entity"),
    )

    tally_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tally_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_key: Mapped[str] = mapped_column(String(60), nullable=False)  # catalogue EntitySpec.key
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    target_doctype: Mapped[str] = mapped_column(String(80), nullable=False)
    module: Mapped[str] = mapped_column(String(40), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    support: Mapped[str] = mapped_column(
        String(20), nullable=False, default="full", server_default=text("'full'")
    )
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    tally_import = relationship("TallyImport", back_populates="entities")


class TallyStagingRecord(Base, DocumentMixin):
    """One record parsed out of the Tally file, before/after it becomes a document."""

    __tablename__ = "tally_staging_records"
    __table_args__ = (
        Index("ix_tally_staging_import_entity", "tally_import_id", "entity_key"),
        Index("ix_tally_staging_status", "tally_import_id", "status"),
        Index("ix_tally_staging_target", "target_doctype", "target_id"),
    )

    tally_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tally_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_key: Mapped[str] = mapped_column(String(60), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    # Tally identity. GUID is stable across exports; name is what users recognise.
    tally_guid: Mapped[str | None] = mapped_column(String(120), index=True)
    tally_name: Mapped[str | None] = mapped_column(String(255))
    tally_parent: Mapped[str | None] = mapped_column(String(255))
    tally_voucher_type: Mapped[str | None] = mapped_column(String(120))
    voucher_number: Mapped[str | None] = mapped_column(String(120))
    posting_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))

    raw: Mapped[dict | None] = mapped_column(JSONB)  # parsed Tally record, as-is
    normalised: Mapped[dict | None] = mapped_column(JSONB)  # after mapping resolution

    target_doctype: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_name: Mapped[str | None] = mapped_column(String(140))

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Pending", server_default=text("'Pending'")
    )
    # [{level: error|warning|info, message: str, field: str|None}]
    messages: Mapped[list | None] = mapped_column(JSONB)


class TallyMapping(Base, DocumentMixin, CompanyScopedMixin):
    """A durable Tally-name -> OptiERP-record decision, reused across sessions."""

    __tablename__ = "tally_mappings"
    __table_args__ = (
        UniqueConstraint("company_id", "entity_key", "tally_name", name="uq_tally_mapping"),
        Index("ix_tally_mappings_company_entity", "company_id", "entity_key"),
        Index("ix_tally_mappings_guid", "company_id", "tally_guid"),
    )

    entity_key: Mapped[str] = mapped_column(String(60), nullable=False)
    tally_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tally_guid: Mapped[str | None] = mapped_column(String(120))
    tally_parent: Mapped[str | None] = mapped_column(String(255))

    target_doctype: Mapped[str] = mapped_column(String(40), nullable=False)  # MAPPING_TARGETS
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_name: Mapped[str | None] = mapped_column(String(255))

    # How the mapping was decided — shown in the UI so a tester can tell an
    # exact GUID match from a fuzzy name guess.
    match_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto", server_default=text("'auto'")
    )  # guid | exact | fuzzy | created | manual
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    # A tester's explicit choice; never overwritten by a later auto-map.
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Extra classification carried alongside (root_type for accounts, GSTIN, ...).
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)


class TallyImportLog(Base, DocumentMixin):
    """Append-only trace of importer activity (per phase, per entity)."""

    __tablename__ = "tally_import_logs"
    __table_args__ = (Index("ix_tally_import_logs_import", "tally_import_id", "creation"),)

    tally_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tally_imports.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(30), nullable=False)  # parse|automap|validate|run|rollback
    entity_key: Mapped[str | None] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(
        String(10), nullable=False, default="info", server_default=text("'info'")
    )  # info | warning | error
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)
