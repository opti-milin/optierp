"""Tax computation documents — append-only runs; drop tenant statutory masters.

Creates ``tax_computations`` / income lines / adjustment lines / runs / results.
Drops the eight tenant-owned rate/slab/surcharge/cess/rebate/special tables and
``tax_adjustment_categories`` (clean break — law lives in ``statutory``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0087_tax_computation"
down_revision = "0086_tax_registration"
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
    # --- New Tier 3 documents ---
    op.create_table(
        "tax_computations",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("assessee_class_code", sa.String(40), nullable=False),
        sa.Column(
            "regime_election_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_regime_elections.id"),
            nullable=True,
        ),
        sa.Column("regime_code", sa.String(40), nullable=False),
        sa.Column("filing_type", sa.String(40), nullable=False, server_default=sa.text("'Original'")),
        sa.Column(
            "revises_computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computations.id"),
            nullable=True,
        ),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("finance_act_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column("remarks", sa.String(255)),
        sa.Column(
            "current_run_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.UniqueConstraint("company_id", "name", name="uq_tax_computations_company_name"),
    )
    op.create_index("ix_tax_computations_company_id", "tax_computations", ["company_id"])
    op.create_index("ix_tax_computations_ay", "tax_computations", ["company_id", "ay_code"])
    _rls("tax_computations")

    op.create_table(
        "tax_computation_income_lines",
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
            sa.ForeignKey("tax_computations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("head", sa.String(40), nullable=False, server_default=sa.text("'PGBP'")),
        sa.Column("income_character_code", sa.String(40), nullable=False, server_default=sa.text("'ORDINARY'")),
        sa.Column("sub_ref", sa.String(80)),
        sa.Column("gross", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("deductions", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("net", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("source_doc_type", sa.String(80)),
        sa.Column("source_doc_id", UUID(as_uuid=True)),
    )
    op.create_index(
        "ix_tax_computation_income_lines_comp",
        "tax_computation_income_lines",
        ["computation_id"],
    )
    _rls("tax_computation_income_lines")

    op.create_table(
        "tax_computation_adjustment_lines",
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
            sa.ForeignKey("tax_computations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("provision_section_code", sa.String(40)),
        sa.Column("rule_code", sa.String(40)),
        sa.Column("section_code", sa.String(40), nullable=False, server_default=sa.text("''")),
        sa.Column("stage", sa.String(20), nullable=False, server_default=sa.text("'PGBP'")),
        sa.Column("description", sa.String(255)),
        sa.Column("direction", sa.String(20), nullable=False, server_default=sa.text("'Add'")),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("override_amount", sa.Numeric(21, 6)),
        sa.Column("final_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'Manual'")),
        sa.Column("explanation", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "prior_year_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computation_adjustment_lines.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tax_computation_adj_lines_comp",
        "tax_computation_adjustment_lines",
        ["computation_id"],
    )
    _rls("tax_computation_adjustment_lines")

    op.create_table(
        "tax_computation_runs",
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
            sa.ForeignKey("tax_computations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("engine_version", sa.String(40), nullable=False),
        sa.Column("finance_act_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ruleset_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("superseded_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("computation_id", "run_no", name="uq_tax_computation_run_no"),
    )
    op.create_index("ix_tax_computation_runs_comp", "tax_computation_runs", ["computation_id"])
    _rls("tax_computation_runs")

    op.create_table(
        "tax_computation_results",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_computation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("taxable_income", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_normal", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_mat", sa.Numeric(21, 6)),
        sa.Column("tax_applied_basis", sa.String(20), nullable=False, server_default=sa.text("'Normal'")),
        sa.Column("tax_before_rebate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("rebate_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("surcharge_before_relief", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("marginal_relief_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("surcharge_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("cess_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("interest_234a", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("interest_234b", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("interest_234c", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("credits_total", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tax", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("net_payable", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("breakdown", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("run_id", name="uq_tax_computation_result_run"),
    )
    op.create_index("ix_tax_computation_results_run", "tax_computation_results", ["run_id"])
    _rls("tax_computation_results")

    # FK current_run_id now that runs exist
    op.create_foreign_key(
        "fk_tax_computations_current_run",
        "tax_computations",
        "tax_computation_runs",
        ["current_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_tax_adj_lines_run",
        "tax_computation_adjustment_lines",
        "tax_computation_runs",
        ["run_id"],
        ["id"],
    )

    # --- Soften old computation FKs to masters we are about to drop ---
    for table, cols in (
        ("income_tax_computations", ("rate_table_id", "policy_id")),
        ("tax_policies", ("rate_table_id", "slab_set_id", "surcharge_set_id", "cess_rule_id", "rebate_rule_id")),
        ("income_tax_adjustment_lines", ("category_id",)),
        ("income_tax_special_income_lines", ("special_rate_id",)),
    ):
        for col in cols:
            # Drop any FK that references the column (name unknown / varies).
            op.execute(
                f"""
                DO $$
                DECLARE r record;
                BEGIN
                  FOR r IN
                    SELECT con.conname
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
                    WHERE con.contype = 'f'
                      AND rel.relname = '{table}'
                      AND att.attname = '{col}'
                  LOOP
                    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', r.conname);
                  END LOOP;
                END $$;
                """
            )

    # Drop child lines first, then sets / masters.
    op.execute("DROP TABLE IF EXISTS income_tax_slab_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS surcharge_brackets CASCADE")
    op.execute("DROP TABLE IF EXISTS income_tax_slab_sets CASCADE")
    op.execute("DROP TABLE IF EXISTS surcharge_rule_sets CASCADE")
    op.execute("DROP TABLE IF EXISTS health_education_cess_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS rebate_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS special_income_tax_rates CASCADE")
    op.execute("DROP TABLE IF EXISTS income_tax_rate_tables CASCADE")
    op.execute("DROP TABLE IF EXISTS tax_adjustment_categories CASCADE")


def downgrade() -> None:
    # Clean-break: do not recreate dropped tenant masters.
    for table in (
        "tax_computation_results",
        "tax_computation_runs",
        "tax_computation_adjustment_lines",
        "tax_computation_income_lines",
        "tax_computations",
    ):
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
        op.drop_table(table)
