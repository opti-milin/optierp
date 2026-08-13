"""Tax adjustment engine: provisions, rule packs, enriched lines, tax dep blocks."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0084_tax_adjustment_engine"
down_revision = "0083_cm_cost_structure"
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
        "tax_adjustment_provisions",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("act_reference", sa.String(80)),
        sa.Column("stage", sa.String(20), nullable=False, server_default=sa.text("'PGBP'")),
        sa.Column("default_effect", sa.String(20), nullable=False, server_default=sa.text("'Add'")),
        sa.Column(
            "applies_to_modes",
            sa.String(40),
            nullable=False,
            server_default=sa.text("'EntityBooks'"),
        ),
        sa.Column("regime_scope", sa.String(20), nullable=False, server_default=sa.text("'Both'")),
        sa.Column("itr_schedule_hint", sa.String(40)),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "section_code", name="uq_tax_adj_provision_section"),
    )
    op.create_index("ix_tax_adjustment_provisions_company_id", "tax_adjustment_provisions", ["company_id"])
    _rls("tax_adjustment_provisions")

    op.create_table(
        "tax_adjustment_rule_packs",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_name", sa.String(140), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("filing_regime", sa.String(20), nullable=False, server_default=sa.text("'Normal'")),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column("remarks", sa.String(255)),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint(
            "company_id",
            "assessment_year",
            "entity_type",
            "filing_regime",
            name="uq_tax_adj_pack_ay_entity_regime",
        ),
    )
    op.create_index(
        "ix_tax_adjustment_rule_packs_company_id", "tax_adjustment_rule_packs", ["company_id"]
    )
    _rls("tax_adjustment_rule_packs")

    op.create_table(
        "tax_adjustment_rules",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pack_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_adjustment_rule_packs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_adjustment_provisions.id"),
            nullable=False,
        ),
        sa.Column("rule_code", sa.String(40), nullable=False),
        sa.Column(
            "evaluation_method",
            sa.String(40),
            nullable=False,
            server_default=sa.text("'Manual'"),
        ),
        sa.Column("parameters", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_type", sa.String(40), nullable=False, server_default=sa.text("'Manual'")),
        sa.Column("source_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("depends_on", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("allow_manual_override", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "include_in_seed_lines", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("pack_id", "rule_code", name="uq_tax_adj_rule_code_per_pack"),
    )
    op.create_index("ix_tax_adjustment_rules_company_id", "tax_adjustment_rules", ["company_id"])
    op.create_index("ix_tax_adjustment_rules_pack_id", "tax_adjustment_rules", ["pack_id"])
    _rls("tax_adjustment_rules")

    op.create_table(
        "tax_depreciation_blocks",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("block_code", sa.String(40), nullable=False),
        sa.Column("block_name", sa.String(140), nullable=False),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("opening_wdv", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("additions", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("deletions", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "depreciation_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("closing_wdv", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("remarks", sa.String(255)),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint(
            "company_id",
            "assessment_year",
            "block_code",
            name="uq_tax_dep_block_ay_code",
        ),
    )
    op.create_index(
        "ix_tax_depreciation_blocks_company_id", "tax_depreciation_blocks", ["company_id"]
    )
    _rls("tax_depreciation_blocks")

    op.add_column(
        "tax_adjustment_categories",
        sa.Column(
            "provision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_adjustment_provisions.id"),
            nullable=True,
        ),
    )

    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column(
            "provision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_adjustment_provisions.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_adjustment_rules.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("section_code", sa.String(40), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("stage", sa.String(20), nullable=False, server_default=sa.text("'PGBP'")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("base_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("computed_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("override_amount", sa.Numeric(21, 6), nullable=True),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("final_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'Manual'")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("explanation", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("inputs", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("source_refs", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "income_tax_adjustment_lines",
        sa.Column("prior_year_line_id", UUID(as_uuid=True), nullable=True),
    )
    # Backfill final_amount / computed from legacy amount
    op.execute(
        "UPDATE income_tax_adjustment_lines "
        "SET final_amount = amount, computed_amount = amount "
        "WHERE final_amount = 0 AND amount <> 0"
    )


def downgrade() -> None:
    for col in (
        "prior_year_line_id",
        "source_refs",
        "inputs",
        "explanation",
        "status",
        "final_amount",
        "override_amount",
        "computed_amount",
        "base_amount",
        "stage",
        "section_code",
        "rule_id",
        "provision_id",
    ):
        op.drop_column("income_tax_adjustment_lines", col)
    op.drop_column("tax_adjustment_categories", "provision_id")
    op.drop_table("tax_depreciation_blocks")
    op.drop_table("tax_adjustment_rules")
    op.drop_table("tax_adjustment_rule_packs")
    op.drop_table("tax_adjustment_provisions")
