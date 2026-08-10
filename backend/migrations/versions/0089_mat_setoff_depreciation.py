"""Corporate tax depth — depreciation register, loss CF, MAT credit (Phase 6)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0089_mat_setoff_depreciation"
down_revision = "0088_tax_credits_challans"
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
    op.add_column(
        "asset_categories",
        sa.Column("tax_block_code", sa.String(40), nullable=True),
    )

    op.create_table(
        "tax_depreciation_registers",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("block_code", sa.String(40), nullable=False),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("opening_wdv", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("additions_full", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("additions_half", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("deletions", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("depreciation_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "additional_depreciation_amount",
            sa.Numeric(21, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("closing_wdv", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint(
            "company_id", "ay_code", "block_code", name="uq_tax_dep_reg_ay_block"
        ),
    )
    op.create_index(
        "ix_tax_dep_registers_company_ay",
        "tax_depreciation_registers",
        ["company_id", "ay_code"],
    )
    _rls("tax_depreciation_registers")

    op.create_table(
        "tax_depreciation_movements",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "register_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_depreciation_registers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assets.id"),
            nullable=True,
        ),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("put_to_use_date", sa.Date()),
        sa.Column("half_rate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("remarks", sa.String(255)),
    )
    op.create_index(
        "ix_tax_dep_movements_register",
        "tax_depreciation_movements",
        ["register_id"],
    )
    _rls("tax_depreciation_movements")

    op.create_table(
        "tax_loss_carry_forward_ledger",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("origin_ay_code", sa.String(20), nullable=False),
        sa.Column("expires_after_ay", sa.String(20)),
        sa.Column("setoff_group", sa.String(40), nullable=False),
        sa.Column("loss_kind", sa.String(40), nullable=False),
        sa.Column("entry_kind", sa.String(20), nullable=False, server_default=sa.text("'Created'")),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False),
        sa.Column("amount_remaining", sa.Numeric(21, 6), nullable=False),
        sa.Column(
            "computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computations.id"),
            nullable=True,
        ),
        sa.Column("remarks", sa.String(255)),
    )
    op.create_index(
        "ix_tax_loss_cf_company",
        "tax_loss_carry_forward_ledger",
        ["company_id", "origin_ay_code"],
    )
    _rls("tax_loss_carry_forward_ledger")

    op.create_table(
        "tax_loss_setoff_entries",
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
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computation_runs.id"),
            nullable=True,
        ),
        sa.Column(
            "ledger_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_loss_carry_forward_ledger.id"),
            nullable=False,
        ),
        sa.Column("against_character", sa.String(40), nullable=False),
        sa.Column("amount_set_off", sa.Numeric(21, 6), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("explanation", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "ix_tax_loss_setoff_ay",
        "tax_loss_setoff_entries",
        ["company_id", "ay_code"],
    )
    _rls("tax_loss_setoff_entries")

    op.create_table(
        "mat_credit_ledger",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("entry_kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False),
        sa.Column("tax_mat", sa.Numeric(21, 6)),
        sa.Column("tax_normal", sa.Numeric(21, 6)),
        sa.Column("expires_after_ay", sa.String(20)),
        sa.Column(
            "computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computations.id"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computation_runs.id"),
            nullable=True,
        ),
        sa.Column("remarks", sa.String(255)),
    )
    op.create_index("ix_mat_credit_company_ay", "mat_credit_ledger", ["company_id", "ay_code"])
    _rls("mat_credit_ledger")

    # Book profit input on computation for 115JB
    op.add_column(
        "tax_computations",
        sa.Column("book_profit_115jb", sa.Numeric(21, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tax_computations", "book_profit_115jb")
    for table in (
        "mat_credit_ledger",
        "tax_loss_setoff_entries",
        "tax_loss_carry_forward_ledger",
        "tax_depreciation_movements",
        "tax_depreciation_registers",
    ):
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
        op.drop_table(table)
    op.drop_column("asset_categories", "tax_block_code")
