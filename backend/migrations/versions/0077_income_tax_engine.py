"""Income Tax rule engine — separate masters + slim rate table.

Revision ID: 0077_income_tax_engine
Revises: 0076_individual_itr
Create Date: 2026-07-29

- New masters: slab sets, surcharge, cess, rebate, special rates, tax policy
- Slim income_tax_rate_tables (drop surcharge_rate / cess_rate)
- Migrate legacy rate-table slab children → slab sets
- Computation: policy snapshot, rebate_amount, marginal relief, tcs, breakdown,
  special-income child lines
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0077_income_tax_engine"
down_revision: Union[str, None] = "0076_individual_itr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def _drop_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")


def upgrade() -> None:
    # --- New parent masters -------------------------------------------------
    op.create_table(
        "income_tax_slab_sets",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("set_name", sa.String(140), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("filing_regime", sa.String(20), nullable=False, server_default=sa.text("'Normal'")),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(255), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "set_name", name="uq_income_tax_slab_set_name"),
    )
    op.create_index("ix_income_tax_slab_sets_company_id", "income_tax_slab_sets", ["company_id"])

    op.create_table(
        "surcharge_rule_sets",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("set_name", sa.String(140), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False, server_default=sa.text("'Company'")),
        sa.Column("filing_regime", sa.String(20), nullable=False, server_default=sa.text("'Normal'")),
        sa.Column("marginal_relief_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(255), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "set_name", name="uq_surcharge_rule_set_name"),
    )
    op.create_index("ix_surcharge_rule_sets_company_id", "surcharge_rule_sets", ["company_id"])

    op.create_table(
        "surcharge_brackets",
        *_meta_columns(),
        sa.Column(
            "rule_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("surcharge_rule_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("income_from", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("income_to", sa.Numeric(21, 6), nullable=True),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_surcharge_brackets_rule_set_id", "surcharge_brackets", ["rule_set_id"])

    op.create_table(
        "health_education_cess_rules",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_name", sa.String(140), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=True),
        sa.Column("filing_regime", sa.String(20), nullable=True),
        sa.Column("cess_rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("4")),
        sa.Column(
            "cess_base",
            sa.String(40),
            nullable=False,
            server_default=sa.text("'TaxPlusSurcharge'"),
        ),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(255), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "rule_name", name="uq_hec_cess_rule_name"),
    )
    op.create_index(
        "ix_health_education_cess_rules_company_id",
        "health_education_cess_rules",
        ["company_id"],
    )

    op.create_table(
        "rebate_rules",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_code", sa.String(40), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("filing_regime", sa.String(20), nullable=False, server_default=sa.text("'Normal'")),
        sa.Column("max_taxable_income", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("max_rebate_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(255), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint(
            "company_id",
            "assessment_year",
            "section_code",
            "filing_regime",
            name="uq_rebate_rule_ay_section_regime",
        ),
    )
    op.create_index("ix_rebate_rules_company_id", "rebate_rules", ["company_id"])

    op.create_table(
        "special_income_tax_rates",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("income_category_code", sa.String(40), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("filing_regime", sa.String(20), nullable=False, server_default=sa.text("'Normal'")),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint(
            "company_id",
            "assessment_year",
            "income_category_code",
            "filing_regime",
            name="uq_special_rate_ay_cat_regime",
        ),
    )
    op.create_index(
        "ix_special_income_tax_rates_company_id",
        "special_income_tax_rates",
        ["company_id"],
    )

    # --- Migrate legacy slab lines off rate_table ---------------------------
    # 1) Rename old child table
    op.rename_table("income_tax_slab_lines", "income_tax_slab_lines_legacy")

    # 2) Create new slab_lines pointing at slab_sets
    op.create_table(
        "income_tax_slab_lines",
        *_meta_columns(),
        sa.Column(
            "slab_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("income_tax_slab_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("from_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("to_amount", sa.Numeric(21, 6), nullable=True),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_income_tax_slab_lines_slab_set_id", "income_tax_slab_lines", ["slab_set_id"])

    # 3) For each rate table that had slabs, create a slab set and copy lines
    op.execute(
        """
        INSERT INTO income_tax_slab_sets (
            id, creation, modified, docstatus, owner, modified_by,
            company_id, set_name, assessment_year, entity_type, filing_regime,
            remarks, disabled
        )
        SELECT
            gen_random_uuid(),
            now(), now(), 0, rt.owner, rt.modified_by,
            rt.company_id,
            rt.entity_type || ' ' || rt.filing_regime || ' ' || rt.assessment_year,
            rt.assessment_year,
            rt.entity_type,
            rt.filing_regime,
            COALESCE(rt.remarks, 'Migrated from rate-table slabs'),
            rt.disabled
        FROM income_tax_rate_tables rt
        WHERE EXISTS (
            SELECT 1 FROM income_tax_slab_lines_legacy l WHERE l.rate_table_id = rt.id
        )
        """
    )
    op.execute(
        """
        INSERT INTO income_tax_slab_lines (
            id, creation, modified, docstatus, owner, modified_by,
            slab_set_id, idx, from_amount, to_amount, rate_percent
        )
        SELECT
            gen_random_uuid(),
            now(), now(), 0, l.owner, l.modified_by,
            ss.id,
            l.idx,
            l.from_amount,
            l.to_amount,
            l.rate
        FROM income_tax_slab_lines_legacy l
        JOIN income_tax_rate_tables rt ON rt.id = l.rate_table_id
        JOIN income_tax_slab_sets ss ON
            ss.company_id = rt.company_id
            AND ss.assessment_year = rt.assessment_year
            AND ss.entity_type = rt.entity_type
            AND ss.filing_regime = rt.filing_regime
        """
    )
    op.drop_table("income_tax_slab_lines_legacy")

    # --- Migrate flat surcharge/cess into dedicated masters -----------------
    op.execute(
        """
        INSERT INTO surcharge_rule_sets (
            id, creation, modified, docstatus, owner, modified_by,
            company_id, set_name, assessment_year, entity_type, filing_regime,
            marginal_relief_enabled, remarks, disabled
        )
        SELECT
            gen_random_uuid(), now(), now(), 0, owner, modified_by,
            company_id,
            'SUR-' || entity_type || '-' || filing_regime || '-' || assessment_year,
            assessment_year, entity_type, filing_regime,
            false,
            'Migrated from rate-table surcharge_rate',
            disabled
        FROM income_tax_rate_tables
        WHERE surcharge_rate IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO surcharge_brackets (
            id, creation, modified, docstatus, owner, modified_by,
            rule_set_id, idx, income_from, income_to, rate_percent
        )
        SELECT
            gen_random_uuid(), now(), now(), 0, rt.owner, rt.modified_by,
            ss.id, 0, 0, NULL, rt.surcharge_rate
        FROM income_tax_rate_tables rt
        JOIN surcharge_rule_sets ss ON
            ss.company_id = rt.company_id
            AND ss.assessment_year = rt.assessment_year
            AND ss.entity_type = rt.entity_type
            AND ss.filing_regime = rt.filing_regime
        """
    )
    op.execute(
        """
        INSERT INTO health_education_cess_rules (
            id, creation, modified, docstatus, owner, modified_by,
            company_id, rule_name, assessment_year, entity_type, filing_regime,
            cess_rate, cess_base, remarks, disabled
        )
        SELECT
            gen_random_uuid(), now(), now(), 0, owner, modified_by,
            company_id,
            'CESS-' || entity_type || '-' || filing_regime || '-' || assessment_year,
            assessment_year, entity_type, filing_regime,
            COALESCE(cess_rate, 4),
            'TaxPlusSurcharge',
            'Migrated from rate-table cess_rate',
            disabled
        FROM income_tax_rate_tables
        """
    )

    # --- Tax policy (links packs) -------------------------------------------
    op.create_table(
        "tax_policies",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("filing_regime", sa.String(20), nullable=False, server_default=sa.text("'Normal'")),
        sa.Column("computation_method", sa.String(20), nullable=False, server_default=sa.text("'FlatRate'")),
        sa.Column("ordinary_method", sa.String(20), nullable=False, server_default=sa.text("'FlatRate'")),
        sa.Column("rate_table_id", UUID(as_uuid=True), sa.ForeignKey("income_tax_rate_tables.id"), nullable=True),
        sa.Column("slab_set_id", UUID(as_uuid=True), sa.ForeignKey("income_tax_slab_sets.id"), nullable=True),
        sa.Column("surcharge_set_id", UUID(as_uuid=True), sa.ForeignKey("surcharge_rule_sets.id"), nullable=True),
        sa.Column("cess_rule_id", UUID(as_uuid=True), sa.ForeignKey("health_education_cess_rules.id"), nullable=True),
        sa.Column("rebate_rule_id", UUID(as_uuid=True), sa.ForeignKey("rebate_rules.id"), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(255), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint(
            "company_id",
            "assessment_year",
            "entity_type",
            "filing_regime",
            name="uq_tax_policy_ay_entity_regime",
        ),
    )
    op.create_index("ix_tax_policies_company_id", "tax_policies", ["company_id"])

    op.execute(
        """
        INSERT INTO tax_policies (
            id, creation, modified, docstatus, owner, modified_by,
            company_id, assessment_year, entity_type, filing_regime,
            computation_method, ordinary_method,
            rate_table_id, slab_set_id, surcharge_set_id, cess_rule_id,
            remarks, disabled
        )
        SELECT
            gen_random_uuid(), now(), now(), 0, rt.owner, rt.modified_by,
            rt.company_id, rt.assessment_year, rt.entity_type, rt.filing_regime,
            CASE WHEN ss.id IS NOT NULL THEN 'SlabBased' ELSE 'FlatRate' END,
            CASE WHEN ss.id IS NOT NULL THEN 'SlabBased' ELSE 'FlatRate' END,
            rt.id,
            ss.id,
            sur.id,
            cess.id,
            'Backfilled from rate table',
            rt.disabled
        FROM income_tax_rate_tables rt
        LEFT JOIN income_tax_slab_sets ss ON
            ss.company_id = rt.company_id
            AND ss.assessment_year = rt.assessment_year
            AND ss.entity_type = rt.entity_type
            AND ss.filing_regime = rt.filing_regime
        LEFT JOIN surcharge_rule_sets sur ON
            sur.company_id = rt.company_id
            AND sur.assessment_year = rt.assessment_year
            AND sur.entity_type = rt.entity_type
            AND sur.filing_regime = rt.filing_regime
        LEFT JOIN health_education_cess_rules cess ON
            cess.company_id = rt.company_id
            AND cess.assessment_year = rt.assessment_year
            AND cess.entity_type = rt.entity_type
            AND cess.filing_regime = rt.filing_regime
        """
    )

    # --- Slim rate table + effective dates ----------------------------------
    op.add_column("income_tax_rate_tables", sa.Column("effective_from", sa.Date(), nullable=True))
    op.add_column("income_tax_rate_tables", sa.Column("effective_to", sa.Date(), nullable=True))
    op.drop_column("income_tax_rate_tables", "surcharge_rate")
    op.drop_column("income_tax_rate_tables", "cess_rate")

    # --- Computation extensions ---------------------------------------------
    op.add_column(
        "income_tax_computations",
        sa.Column("policy_id", UUID(as_uuid=True), sa.ForeignKey("tax_policies.id"), nullable=True),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("computation_method", sa.String(20), nullable=True),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("rebate_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "marginal_relief_amount",
            sa.Numeric(21, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("tcs_credit", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "tax_breakdown",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute("UPDATE income_tax_computations SET rebate_amount = rebate_87a")

    op.create_table(
        "income_tax_special_income_lines",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "computation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("income_tax_computations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "special_rate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("special_income_tax_rates.id"),
            nullable=True,
        ),
        sa.Column("income_category_code", sa.String(40), nullable=False, server_default=sa.text("''")),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_income_tax_special_income_lines_company_id",
        "income_tax_special_income_lines",
        ["company_id"],
    )
    op.create_index(
        "ix_income_tax_special_income_lines_computation_id",
        "income_tax_special_income_lines",
        ["computation_id"],
    )

    for table in (
        "income_tax_slab_sets",
        "surcharge_rule_sets",
        "health_education_cess_rules",
        "rebate_rules",
        "special_income_tax_rates",
        "tax_policies",
        "income_tax_special_income_lines",
    ):
        _rls(table)


def downgrade() -> None:
    for table in (
        "income_tax_special_income_lines",
        "tax_policies",
        "special_income_tax_rates",
        "rebate_rules",
        "health_education_cess_rules",
        "surcharge_rule_sets",
        "income_tax_slab_sets",
    ):
        _drop_rls(table)

    op.drop_table("income_tax_special_income_lines")
    op.drop_column("income_tax_computations", "tax_breakdown")
    op.drop_column("income_tax_computations", "tcs_credit")
    op.drop_column("income_tax_computations", "marginal_relief_amount")
    op.drop_column("income_tax_computations", "rebate_amount")
    op.drop_column("income_tax_computations", "computation_method")
    op.drop_column("income_tax_computations", "policy_id")

    op.add_column(
        "income_tax_rate_tables",
        sa.Column("cess_rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_rate_tables",
        sa.Column("surcharge_rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
    )
    op.drop_column("income_tax_rate_tables", "effective_to")
    op.drop_column("income_tax_rate_tables", "effective_from")

    op.drop_table("tax_policies")
    op.drop_table("special_income_tax_rates")
    op.drop_table("rebate_rules")
    op.drop_table("health_education_cess_rules")
    op.drop_table("surcharge_brackets")
    op.drop_table("surcharge_rule_sets")

    # Restore legacy slab lines on rate table (empty — data loss on downgrade)
    op.drop_table("income_tax_slab_lines")
    op.drop_table("income_tax_slab_sets")
    op.create_table(
        "income_tax_slab_lines",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "rate_table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("income_tax_rate_tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("from_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("to_amount", sa.Numeric(21, 6), nullable=True),
        sa.Column("rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
    )
