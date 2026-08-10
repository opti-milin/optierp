"""Tax filings — ITR-6 JSON + acknowledgement + return chaining (Phase 8)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0091_tax_filings"
down_revision = "0090_tax_interest"
branch_labels = None
depends_on = None


def _meta_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    ]


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY company_isolation ON {table} "
        f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO erp_app")


def upgrade() -> None:
    op.create_table(
        "tax_filings",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column(
            "computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("form_code", sa.String(20), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column(
            "filing_type",
            sa.String(40),
            nullable=False,
            server_default=sa.text("'Original'"),
        ),
        sa.Column(
            "revises_filing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_filings.id"),
            nullable=True,
        ),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("canonical_snapshot", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(40), nullable=False, server_default=sa.text("'Generated'")),
        sa.Column("ack_no", sa.String(80), nullable=True),
        sa.Column("filed_on", sa.Date(), nullable=True),
        sa.Column("verification_mode", sa.String(20), nullable=True),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("provider_response", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("remarks", sa.String(255), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_tax_filings_company_name"),
    )
    op.create_index(
        "ix_tax_filings_company_ay",
        "tax_filings",
        ["company_id", "ay_code"],
    )
    op.create_index(
        "ix_tax_filings_computation",
        "tax_filings",
        ["company_id", "computation_id"],
    )
    _rls("tax_filings")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON tax_filings")
    op.drop_index("ix_tax_filings_computation", table_name="tax_filings")
    op.drop_index("ix_tax_filings_company_ay", table_name="tax_filings")
    op.drop_table("tax_filings")
