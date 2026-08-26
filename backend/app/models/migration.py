"""Module 12 (Data Migration) — import session models.

Seven tables carry a migration from "customer emailed me their books" to "the
data is live in OptiERP", without ever writing straight into the ledgers. The
source may be a Tally XML export, a Tally Day Book CSV, or a spreadsheet from
Tally, Zoho Books or any other application — the tables below are the same
either way, because everything past the parser is source-agnostic:

``MigrationImport``
    One upload = one session. Holds the file, the parse summary and the run
    status. Everything else hangs off it.
``MigrationImportEntity``
    Per-entity progress inside a session (120 ledgers: 118 created, 2 failed).
    This is what the wizard's progress table renders.
``MigrationStagingRecord``
    One row per record found in the file, with its raw payload, the
    normalised payload, its resolved target and its per-row messages. Nothing
    is imported until its staging row is clean, and every imported document
    points back at the row that produced it.
``MigrationMapping``
    The durable name book: "the source's ledger 'ABC Traders' is our Customer
    <uuid>". Company-scoped and reusable, so a second import from the same
    source reuses every decision the tester already made.
``MigrationImportedDocument``
    The company-scoped record of "this source record already became this
    document here", so an overlapping second export cannot post it twice.
``MigrationSourceProfile``
    A saved, named answer to "which sheet is which entity, and which column is
    which field" for one application's spreadsheet export. The same durability
    argument as ``MigrationMapping``, one level up: the *shape* of the file is
    worth remembering, not just the names inside it.
``MigrationImportLog``
    Append-only audit of what the importer did, per entity, per phase.

The staging table is what makes the feature safe to hand to testers: they can
import, compare against the old system, roll back, fix a mapping, and import
again.
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

# What a source name was mapped onto. Kept in step with
# ``services.migration.mapping.ENTITY_TARGETS`` — an entity whose target is not
# listed here cannot be auto-mapped, which is a validation error the tester sees
# as a failed automap rather than as a missing feature.
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
    "Payment Terms Template",
    "Bank Account",
    "Tax Template",
    "Address",
    "Contact",
    "Asset",
    "Budget",
    "Bank Transaction",
    "Skip",  # explicitly ignore this source name
)


class MigrationImport(Base, DocumentMixin, CompanyScopedMixin):
    """One Tally import session (upload -> parse -> map -> validate -> run)."""

    __tablename__ = "migration_imports"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_migration_import_name"),
        Index("ix_migration_imports_company_status", "company_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="XML", server_default=text("'XML'")
    )  # XML | CSV | XLSX | Gateway
    #: Which application the file came out of — "Tally", "Zoho Books",
    #: "OptiERP Template", "Custom". Shown in the wizard and used to pick the
    #: right guidance text; it never changes how a record is imported.
    source_app: Mapped[str | None] = mapped_column(String(60))
    #: Key of the profile that parsed a spreadsheet (built-in or company-saved).
    source_profile: Mapped[str | None] = mapped_column(String(80))
    #: The resolved sheet -> entity and column -> field map actually used, after
    #: any edit the tester made in the wizard. Kept so a re-parse reproduces the
    #: run rather than re-detecting and possibly deciding differently.
    sheet_map: Mapped[dict | None] = mapped_column(JSONB)
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    # sha256 of the upload — lets us warn "you already imported this file".
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # The raw upload, kept so a session can be re-parsed after a parser fix, or
    # after a mapping edit, without asking the tester for the file again.
    payload: Mapped[str | None] = mapped_column(Text)
    #: How ``payload`` holds the bytes. XML and CSV decode to text and are stored
    #: as-is; a workbook is binary, so it is stored base64. Without this a
    #: re-parse of a spreadsheet would hand the parser mojibake.
    payload_encoding: Mapped[str] = mapped_column(
        String(10), nullable=False, default="text", server_default=text("'text'")
    )  # text | base64

    source_company_name: Mapped[str | None] = mapped_column(String(200))
    source_company_guid: Mapped[str | None] = mapped_column(String(80))
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
    # Bumped as the run works. A run executes in a background task, so if the
    # process dies mid-import nothing is left to move the status off
    # "Importing" — and rollback refuses that status, which would leave the
    # import permanently stuck. The reaper uses this to spot an abandoned run.
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    entities = relationship(
        "MigrationImportEntity",
        back_populates="migration_import",
        cascade="all, delete-orphan",
        order_by="MigrationImportEntity.stage",
    )


class MigrationImportEntity(Base, DocumentMixin):
    """Per-entity counters for one session — the wizard's progress table."""

    __tablename__ = "migration_import_entities"
    __table_args__ = (
        UniqueConstraint("migration_import_id", "entity_key", name="uq_migration_import_entity"),
    )

    migration_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("migration_imports.id", ondelete="CASCADE"), nullable=False, index=True
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

    migration_import = relationship("MigrationImport", back_populates="entities")


class MigrationStagingRecord(Base, DocumentMixin):
    """One record parsed out of the Tally file, before/after it becomes a document."""

    __tablename__ = "migration_staging_records"
    __table_args__ = (
        Index("ix_migration_staging_import_entity", "migration_import_id", "entity_key"),
        Index("ix_migration_staging_status", "migration_import_id", "status"),
        Index("ix_migration_staging_target", "target_doctype", "target_id"),
    )

    migration_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("migration_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_key: Mapped[str] = mapped_column(String(60), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    # Tally identity. GUID is stable across exports; name is what users recognise.
    source_guid: Mapped[str | None] = mapped_column(String(120), index=True)
    source_name: Mapped[str | None] = mapped_column(String(255))
    source_parent: Mapped[str | None] = mapped_column(String(255))
    source_voucher_type: Mapped[str | None] = mapped_column(String(120))
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


class MigrationMapping(Base, DocumentMixin, CompanyScopedMixin):
    """A durable Tally-name -> OptiERP-record decision, reused across sessions."""

    __tablename__ = "migration_mappings"
    __table_args__ = (
        UniqueConstraint("company_id", "entity_key", "source_name", name="uq_migration_mapping"),
        Index("ix_migration_mappings_company_entity", "company_id", "entity_key"),
        Index("ix_migration_mappings_guid", "company_id", "source_guid"),
    )

    entity_key: Mapped[str] = mapped_column(String(60), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_guid: Mapped[str | None] = mapped_column(String(120))
    source_parent: Mapped[str | None] = mapped_column(String(255))

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


class MigrationImportedDocument(Base, DocumentMixin, CompanyScopedMixin):
    """"Tally record X already became document Y here" — across all sessions.

    Deduplication used to be whole-file only: a sha256 of the upload warned "you
    already imported this file". That misses the case customers actually hit —
    export Apr-Jun, import it, then export Apr-Sep and import that. Different
    bytes, different hash, no warning, and April to June posts a second time.

    This table is the company-scoped answer: one row per Tally record that became
    a real document, consulted by the dry run (so overlap is reported *before*
    anything posts) and enforced by the run (so it cannot post twice). Rollback
    removes the rows it cancelled, which is what makes the
    import -> compare -> roll back -> fix -> import again loop keep working.

    Only transactional records are tracked. Masters are already idempotent: the
    mapping book resolves an existing ledger or item and the importer reuses it.
    """

    __tablename__ = "migration_imported_documents"
    __table_args__ = (
        # One Tally record maps to one document per company. This is the
        # constraint that actually prevents the double-post; the checks in the
        # runner are the friendly path to the same guarantee.
        UniqueConstraint("company_id", "source_guid", name="uq_migration_imported_document"),
        Index("ix_migration_imported_docs_company", "company_id", "entity_key"),
        Index("ix_migration_imported_docs_import", "migration_import_id"),
    )

    #: Nulled rather than cascaded when a session is deleted — the document it
    #: created still exists, so the identity must outlive its session.
    migration_import_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("migration_imports.id", ondelete="SET NULL")
    )
    source_guid: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(60), nullable=False)

    #: Tally's change counter at the moment we imported. A later export showing a
    #: higher ALTERID for the same record means it was edited in Tally since.
    alter_id: Mapped[int | None] = mapped_column(Integer)
    #: Durable voucher identity — survives renumbering, unlike voucher_number.
    vch_key: Mapped[str | None] = mapped_column(String(160))

    voucher_number: Mapped[str | None] = mapped_column(String(120))
    posting_date: Mapped[date | None] = mapped_column(Date)

    target_doctype: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_name: Mapped[str | None] = mapped_column(String(140))


class MigrationImportLog(Base, DocumentMixin):
    """Append-only trace of importer activity (per phase, per entity)."""

    __tablename__ = "migration_import_logs"
    __table_args__ = (Index("ix_migration_import_logs_import", "migration_import_id", "creation"),)

    migration_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("migration_imports.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(30), nullable=False)  # parse|automap|validate|run|rollback
    entity_key: Mapped[str | None] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(
        String(10), nullable=False, default="info", server_default=text("'info'")
    )  # info | warning | error
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)


class MigrationSourceProfile(Base, DocumentMixin, CompanyScopedMixin):
    """A saved answer to "what shape is this application's spreadsheet?".

    ``MigrationMapping`` remembers the *names* inside a file — "their ledger ABC
    Traders is our Customer X". This remembers the *file* — "in a Busy export the
    ``VchMaster`` sheet is the voucher header, its ``VchCode`` column joins to
    ``VchDetail``, and ``AmtDr`` is the debit". The two are independent: a company
    that changes accountants keeps its shape and loses none of its names.

    Built-in profiles (Tally workbook, Tally flat, Zoho Books, the OptiERP
    template) live in code and are not rows here. A row appears only when a
    tester maps an unrecognised workbook and presses "save as profile", which is
    exactly the case the built-ins cannot cover: an application nobody has seen.
    """

    __tablename__ = "migration_source_profiles"
    __table_args__ = (
        UniqueConstraint("company_id", "key", name="uq_migration_source_profile"),
        Index("ix_migration_source_profiles_company", "company_id"),
    )

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(140), nullable=False)
    #: The application this shape came out of, free text — nobody can enumerate
    #: every accounting package an Indian MSME might be leaving behind.
    source_app: Mapped[str | None] = mapped_column(String(60))
    #: Serialised SourceProfile: {"sheets": [{sheet, entity, kind, key, columns,
    #: children, constants}], "account_types": {...}}. Validated against the
    #: dataclasses in ``services/migration/sources/profiles.py`` on load, so a
    #: profile saved by an older build fails loudly instead of importing wrong.
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    #: Bumped on each successful import, so the wizard can offer the shape this
    #: company actually uses ahead of one somebody tried once and abandoned.
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
