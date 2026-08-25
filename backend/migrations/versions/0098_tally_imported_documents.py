"""Module 12 — record-level import idempotency.

Dedup was whole-file only (a sha256 of the upload). That misses the case
customers actually hit: export Apr-Jun, import it, then export Apr-Sep and
import that. Different bytes, different hash, no warning — and April to June
posts a second time.

This table records "Tally record X already became document Y in this company",
so the dry run can report overlap before anything posts and the run can refuse
to post it twice. The unique constraint is the real guarantee; the checks in the
runner are the friendly path to it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0098_tally_imported_documents"
down_revision = "0097_portal_token_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tally_imported_documents",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "creation", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "modified", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "company_id", UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        # SET NULL, not CASCADE: deleting a session must not silently un-protect
        # the documents it created — those still exist in the ledgers.
        sa.Column(
            "tally_import_id", UUID(as_uuid=True),
            sa.ForeignKey("tally_imports.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("tally_guid", sa.String(120), nullable=False),
        sa.Column("entity_key", sa.String(60), nullable=False),
        sa.Column("alter_id", sa.Integer()),
        sa.Column("vch_key", sa.String(160)),
        sa.Column("voucher_number", sa.String(120)),
        sa.Column("posting_date", sa.Date()),
        sa.Column("target_doctype", sa.String(80), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_name", sa.String(140)),
        sa.UniqueConstraint("company_id", "tally_guid", name="uq_tally_imported_document"),
    )
    op.create_index(
        "ix_tally_imported_docs_company", "tally_imported_documents",
        ["company_id", "entity_key"],
    )
    op.create_index(
        "ix_tally_imported_docs_import", "tally_imported_documents", ["tally_import_id"]
    )

    op.execute("ALTER TABLE tally_imported_documents ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY company_isolation ON tally_imported_documents "
        "USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON tally_imported_documents TO erp_app"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON tally_imported_documents")
    op.drop_table("tally_imported_documents")
