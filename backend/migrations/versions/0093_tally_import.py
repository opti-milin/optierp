"""Module 12 (Data Migration) — Tally import sessions, staging + name mappings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0093_tally_import"
down_revision = "0092_drop_legacy_itr"
branch_labels = None
depends_on = None

# Company-scoped tables get the standard tenant-isolation policy; the child
# tables below are reached only through their RLS-protected parent.
RLS_TABLES = ("tally_imports", "tally_mappings")


def _meta_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    ]


def _company_col() -> sa.Column:
    return sa.Column(
        "company_id",
        UUID(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )


def _parent_col() -> sa.Column:
    return sa.Column(
        "tally_import_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tally_imports.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    # --- Sessions -----------------------------------------------------------------
    op.create_table(
        "tally_imports",
        *_meta_columns(),
        _company_col(),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("source_type", sa.String(20), nullable=False, server_default=sa.text("'XML'")),
        sa.Column("file_name", sa.String(255)),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("file_hash", sa.String(64)),
        sa.Column("payload", sa.Text()),
        sa.Column("tally_company_name", sa.String(200)),
        sa.Column("tally_guid", sa.String(80)),
        sa.Column("from_date", sa.Date()),
        sa.Column("to_date", sa.Date()),
        sa.Column("opening_date", sa.Date()),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column("options", JSONB()),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.UniqueConstraint("company_id", "name", name="uq_tally_import_name"),
    )
    op.create_index("ix_tally_imports_company_id", "tally_imports", ["company_id"])
    op.create_index("ix_tally_imports_file_hash", "tally_imports", ["file_hash"])
    op.create_index("ix_tally_imports_company_status", "tally_imports", ["company_id", "status"])

    # --- Per-entity progress ------------------------------------------------------
    op.create_table(
        "tally_import_entities",
        *_meta_columns(),
        _parent_col(),
        sa.Column("entity_key", sa.String(60), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("target_doctype", sa.String(80), nullable=False),
        sa.Column("module", sa.String(40), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("support", sa.String(20), nullable=False, server_default=sa.text("'full'")),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("tally_import_id", "entity_key", name="uq_tally_import_entity"),
    )
    op.create_index("ix_tally_import_entities_import", "tally_import_entities", ["tally_import_id"])

    # --- Staging ------------------------------------------------------------------
    op.create_table(
        "tally_staging_records",
        *_meta_columns(),
        _parent_col(),
        sa.Column("entity_key", sa.String(60), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tally_guid", sa.String(120)),
        sa.Column("tally_name", sa.String(255)),
        sa.Column("tally_parent", sa.String(255)),
        sa.Column("tally_voucher_type", sa.String(120)),
        sa.Column("voucher_number", sa.String(120)),
        sa.Column("posting_date", sa.Date()),
        sa.Column("amount", sa.Numeric(21, 6)),
        sa.Column("raw", JSONB()),
        sa.Column("normalised", JSONB()),
        sa.Column("target_doctype", sa.String(80)),
        sa.Column("target_id", UUID(as_uuid=True)),
        sa.Column("target_name", sa.String(140)),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'Pending'")),
        sa.Column("messages", JSONB()),
    )
    op.create_index("ix_tally_staging_records_import", "tally_staging_records", ["tally_import_id"])
    op.create_index("ix_tally_staging_records_guid", "tally_staging_records", ["tally_guid"])
    op.create_index(
        "ix_tally_staging_import_entity", "tally_staging_records", ["tally_import_id", "entity_key"]
    )
    op.create_index("ix_tally_staging_status", "tally_staging_records", ["tally_import_id", "status"])
    op.create_index("ix_tally_staging_target", "tally_staging_records", ["target_doctype", "target_id"])

    # --- Durable name mappings ----------------------------------------------------
    op.create_table(
        "tally_mappings",
        *_meta_columns(),
        _company_col(),
        sa.Column("entity_key", sa.String(60), nullable=False),
        sa.Column("tally_name", sa.String(255), nullable=False),
        sa.Column("tally_guid", sa.String(120)),
        sa.Column("tally_parent", sa.String(255)),
        sa.Column("target_doctype", sa.String(40), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True)),
        sa.Column("target_name", sa.String(255)),
        sa.Column("match_method", sa.String(20), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("attributes", JSONB()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("company_id", "entity_key", "tally_name", name="uq_tally_mapping"),
    )
    op.create_index("ix_tally_mappings_company_id", "tally_mappings", ["company_id"])
    op.create_index("ix_tally_mappings_company_entity", "tally_mappings", ["company_id", "entity_key"])
    op.create_index("ix_tally_mappings_guid", "tally_mappings", ["company_id", "tally_guid"])

    # --- Logs ---------------------------------------------------------------------
    op.create_table(
        "tally_import_logs",
        *_meta_columns(),
        _parent_col(),
        sa.Column("phase", sa.String(30), nullable=False),
        sa.Column("entity_key", sa.String(60)),
        sa.Column("level", sa.String(10), nullable=False, server_default=sa.text("'info'")),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context", JSONB()),
    )
    op.create_index("ix_tally_import_logs_import", "tally_import_logs", ["tally_import_id", "creation"])

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY company_isolation ON {table} "
            f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
        )
    for table in (*RLS_TABLES, "tally_import_entities", "tally_staging_records", "tally_import_logs"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO erp_app")


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
    op.drop_table("tally_import_logs")
    op.drop_table("tally_mappings")
    op.drop_table("tally_staging_records")
    op.drop_table("tally_import_entities")
    op.drop_table("tally_imports")
