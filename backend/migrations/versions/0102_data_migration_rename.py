"""Module 12 — generalise "Tally import" into "Data Migration".

The pipeline below the parser was never Tally-specific: staging, the durable name
book, GL/stock posting through the real services, GUID dedupe and
rollback-by-cancellation all work on any source. Only the *parser* knew about
Tally. Adding spreadsheet import (Tally workbooks, Zoho Books backups, and
whatever an unprofiled application exports) makes the Tally-shaped names actively
misleading — a Zoho invoice does not have a "tally_guid".

So this renames the six tables and the columns that claimed to be Tally's, adds
the four columns a spreadsheet session needs, and introduces
``migration_source_profiles`` for a saved workbook shape.

Renames rather than a new set of tables, because existing sessions, their staging
rows and — critically — ``migration_imported_documents`` must survive: that table
is what stops a re-import posting a voucher twice, and dropping it would silently
re-arm every duplicate a tester has already avoided.

``ALTER TABLE ... RENAME`` carries indexes, constraints, FKs and RLS policies with
it, so those need renaming only for legibility, not correctness. The policies are
left in place untouched — they are named ``company_isolation`` on every table in
this codebase and follow their table across a rename.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0102_data_migration_rename"
down_revision = "0101_meeting_serial_per_type"
branch_labels = None
depends_on = None


TABLES: tuple[tuple[str, str], ...] = (
    ("tally_imports", "migration_imports"),
    ("tally_import_entities", "migration_import_entities"),
    ("tally_staging_records", "migration_staging_records"),
    ("tally_mappings", "migration_mappings"),
    ("tally_imported_documents", "migration_imported_documents"),
    ("tally_import_logs", "migration_import_logs"),
)

#: (table after rename, old column, new column)
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("migration_imports", "tally_company_name", "source_company_name"),
    ("migration_imports", "tally_guid", "source_company_guid"),
    ("migration_import_entities", "tally_import_id", "migration_import_id"),
    ("migration_staging_records", "tally_import_id", "migration_import_id"),
    ("migration_staging_records", "tally_guid", "source_guid"),
    ("migration_staging_records", "tally_name", "source_name"),
    ("migration_staging_records", "tally_parent", "source_parent"),
    ("migration_staging_records", "tally_voucher_type", "source_voucher_type"),
    ("migration_mappings", "tally_name", "source_name"),
    ("migration_mappings", "tally_guid", "source_guid"),
    ("migration_mappings", "tally_parent", "source_parent"),
    ("migration_imported_documents", "tally_import_id", "migration_import_id"),
    ("migration_imported_documents", "tally_guid", "source_guid"),
    ("migration_import_logs", "tally_import_id", "migration_import_id"),
)

INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_tally_imports_company_id", "ix_migration_imports_company_id"),
    ("ix_tally_imports_file_hash", "ix_migration_imports_file_hash"),
    ("ix_tally_imports_company_status", "ix_migration_imports_company_status"),
    ("ix_tally_import_entities_import", "ix_migration_import_entities_import"),
    ("ix_tally_staging_records_import", "ix_migration_staging_records_import"),
    ("ix_tally_staging_records_guid", "ix_migration_staging_records_guid"),
    ("ix_tally_staging_import_entity", "ix_migration_staging_import_entity"),
    ("ix_tally_staging_status", "ix_migration_staging_status"),
    ("ix_tally_staging_target", "ix_migration_staging_target"),
    ("ix_tally_mappings_company_id", "ix_migration_mappings_company_id"),
    ("ix_tally_mappings_company_entity", "ix_migration_mappings_company_entity"),
    ("ix_tally_mappings_guid", "ix_migration_mappings_guid"),
    ("ix_tally_imported_docs_company", "ix_migration_imported_docs_company"),
    ("ix_tally_imported_docs_import", "ix_migration_imported_docs_import"),
    ("ix_tally_import_logs_import", "ix_migration_import_logs_import"),
)

#: Unique constraints are renamed via ALTER TABLE ... RENAME CONSTRAINT, which
#: also renames the index backing them.
CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("migration_imports", "uq_tally_import_name", "uq_migration_import_name"),
    ("migration_import_entities", "uq_tally_import_entity", "uq_migration_import_entity"),
    ("migration_mappings", "uq_tally_mapping", "uq_migration_mapping"),
    (
        "migration_imported_documents",
        "uq_tally_imported_document",
        "uq_migration_imported_document",
    ),
)


def _rename(pairs, statement: str) -> None:
    for args in pairs:
        op.execute(statement.format(*args))


def upgrade() -> None:
    _rename(TABLES, "ALTER TABLE {0} RENAME TO {1}")
    _rename(COLUMNS, "ALTER TABLE {0} RENAME COLUMN {1} TO {2}")
    _rename(CONSTRAINTS, "ALTER TABLE {0} RENAME CONSTRAINT {1} TO {2}")
    _rename(INDEXES, "ALTER INDEX IF EXISTS {0} RENAME TO {1}")

    # --- What a spreadsheet session needs beyond an XML one -------------------------
    op.add_column("migration_imports", sa.Column("source_app", sa.String(60)))
    op.add_column("migration_imports", sa.Column("source_profile", sa.String(80)))
    op.add_column("migration_imports", sa.Column("sheet_map", JSONB()))
    op.add_column(
        "migration_imports",
        sa.Column(
            "payload_encoding", sa.String(10), nullable=False, server_default=sa.text("'text'")
        ),
    )
    # Every session that exists today came from a Tally XML or CSV upload, and
    # `payload` holds its decoded text. Say so, rather than leaving the column
    # to imply a source nobody chose.
    op.execute("UPDATE migration_imports SET source_app = 'Tally' WHERE source_app IS NULL")

    # --- Saved workbook shapes ------------------------------------------------------
    op.create_table(
        "migration_source_profiles",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(140), nullable=False),
        sa.Column("source_app", sa.String(60)),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("company_id", "key", name="uq_migration_source_profile"),
    )
    op.create_index(
        "ix_migration_source_profiles_company", "migration_source_profiles", ["company_id"]
    )
    op.execute("ALTER TABLE migration_source_profiles ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY company_isolation ON migration_source_profiles "
        "USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON migration_source_profiles TO erp_app")

    # --- Permission + naming series -------------------------------------------------
    # The permission doctype is stored as a string on role_permissions, so a
    # rename here is a data update, not DDL. Roles keep exactly the rights they
    # had; only the name of the thing they have rights over changes.
    op.execute(
        "UPDATE role_permissions SET doctype = 'Data Migration' WHERE doctype = 'Tally Import'"
    )
    # `naming_series` is deliberately untouched. Its rows are per-expanded-prefix
    # counters, so the new pattern (MIGRATION-.YYYY.-) simply opens its own
    # counter on first use, and existing session names (TALLY-IMP-2026-00001)
    # keep working — they are the user-visible identity of imports people have
    # already run and may be quoted in a support thread. Rewriting them would
    # break every link and reference to an import that already happened.


def downgrade() -> None:
    op.execute(
        "UPDATE role_permissions SET doctype = 'Tally Import' WHERE doctype = 'Data Migration'"
    )
    op.execute("DROP POLICY IF EXISTS company_isolation ON migration_source_profiles")
    op.drop_table("migration_source_profiles")
    op.drop_column("migration_imports", "payload_encoding")
    op.drop_column("migration_imports", "sheet_map")
    op.drop_column("migration_imports", "source_profile")
    op.drop_column("migration_imports", "source_app")

    _rename([(b, a) for a, b in INDEXES], "ALTER INDEX IF EXISTS {0} RENAME TO {1}")
    _rename([(t, b, a) for t, a, b in CONSTRAINTS], "ALTER TABLE {0} RENAME CONSTRAINT {1} TO {2}")
    _rename([(t, b, a) for t, a, b in COLUMNS], "ALTER TABLE {0} RENAME COLUMN {1} TO {2}")
    _rename([(b, a) for a, b in TABLES], "ALTER TABLE {0} RENAME TO {1}")
