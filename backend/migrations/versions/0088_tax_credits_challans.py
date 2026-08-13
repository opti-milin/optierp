"""Tax challans + append-only credit entries + persisted 26AS recon (Phase 5)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0088_tax_credits_challans"
down_revision = "0087_tax_computation"
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
        "tax_challans",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("challan_type", sa.String(40), nullable=False),
        sa.Column("bsr_code", sa.String(20), nullable=False),
        sa.Column("challan_serial", sa.String(40), nullable=False),
        sa.Column("cin", sa.String(40)),
        sa.Column("deposit_date", sa.Date(), nullable=False),
        sa.Column("major_head", sa.String(20), nullable=False, server_default=sa.text("'0021'")),
        sa.Column("minor_head", sa.String(20), nullable=False, server_default=sa.text("'100'")),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False),
        sa.Column(
            "bank_account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "tax_payable_account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computations.id"),
            nullable=True,
        ),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint("company_id", "name", name="uq_tax_challans_company_name"),
        sa.UniqueConstraint(
            "company_id",
            "bsr_code",
            "challan_serial",
            "deposit_date",
            name="uq_tax_challans_bsr_serial_date",
        ),
    )
    op.create_index("ix_tax_challans_company_id", "tax_challans", ["company_id"])
    op.create_index("ix_tax_challans_ay", "tax_challans", ["company_id", "ay_code"])
    _rls("tax_challans")

    op.create_table(
        "tax_credit_entries",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column(
            "computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computations.id"),
            nullable=True,
        ),
        sa.Column("credit_kind", sa.String(40), nullable=False),
        sa.Column("deductor_tan", sa.String(20)),
        sa.Column("deductor_name", sa.String(200)),
        sa.Column("section_code", sa.String(40)),
        sa.Column("amount_credited", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("amount_claimed", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "challan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_challans.id"),
            nullable=True,
        ),
        sa.Column(
            "reconciliation_status",
            sa.String(40),
            nullable=False,
            server_default=sa.text("'Unmatched'"),
        ),
        sa.Column("portal_amount", sa.Numeric(21, 6)),
        sa.Column("source_refs", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("remarks", sa.String(255)),
    )
    op.create_index("ix_tax_credit_entries_company_id", "tax_credit_entries", ["company_id"])
    op.create_index(
        "ix_tax_credit_entries_ay",
        "tax_credit_entries",
        ["company_id", "ay_code"],
    )
    op.create_index(
        "ix_tax_credit_entries_comp",
        "tax_credit_entries",
        ["computation_id"],
    )
    _rls("tax_credit_entries")

    op.create_table(
        "tax_26as_recon_runs",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computations.id"),
            nullable=True,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("books_total", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("portal_total", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("difference", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("summary", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("remarks", sa.String(255)),
    )
    op.create_index("ix_tax_26as_recon_company", "tax_26as_recon_runs", ["company_id"])
    _rls("tax_26as_recon_runs")

    # Pin provision GL accounts + posted flag on computation header
    op.add_column(
        "tax_computations",
        sa.Column("provision_expense_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id")),
    )
    op.add_column(
        "tax_computations",
        sa.Column("provision_liability_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id")),
    )
    op.add_column(
        "tax_computations",
        sa.Column(
            "provision_gl_posted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tax_computations", "provision_gl_posted")
    op.drop_column("tax_computations", "provision_liability_account_id")
    op.drop_column("tax_computations", "provision_expense_account_id")
    for table in ("tax_26as_recon_runs", "tax_credit_entries", "tax_challans"):
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
        op.drop_table(table)
