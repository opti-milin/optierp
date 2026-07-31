"""Statutory catalogue schema — global Income-tax Act law, read-only to tenants.

Tables live in schema ``statutory`` with NO company_id and NO RLS.
``erp_app`` receives SELECT (and USAGE on the schema) only; ``erp_owner`` is the
sole writer (migrations + load_statutory CLI).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0085_statutory_schema"
down_revision = "0084_tax_adjustment_engine"
branch_labels = None
depends_on = None

SCHEMA = "statutory"


def _id_ts() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO erp_app")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT SELECT ON TABLES TO erp_app")

    op.create_table(
        "assessment_year",
        *_id_ts(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("ay_start", sa.Date(), nullable=False),
        sa.Column("ay_end", sa.Date(), nullable=False),
        sa.Column("fy_start", sa.Date(), nullable=False),
        sa.Column("fy_end", sa.Date(), nullable=False),
        sa.Column("prev_ay_code", sa.String(20)),
        sa.UniqueConstraint("code", name="uq_statutory_assessment_year_code"),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_statutory_ay_prev",
        "assessment_year",
        "assessment_year",
        ["prev_ay_code"],
        ["code"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    op.create_table(
        "finance_act_version",
        *_id_ts(),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("enacted_on", sa.Date()),
        sa.Column("source_ref", sa.String(255)),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["ay_code"], [f"{SCHEMA}.assessment_year.code"]),
        sa.UniqueConstraint("ay_code", "version", name="uq_statutory_fav_ay_version"),
        schema=SCHEMA,
    )

    op.create_table(
        "assessee_class",
        *_id_ts(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("default_itr_form", sa.String(20)),
        sa.Column("pan_4th_chars", sa.String(20), nullable=False, server_default=sa.text("''")),
        sa.UniqueConstraint("code", name="uq_statutory_assessee_class_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "tax_regime",
        *_id_ts(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("assessee_class_code", sa.String(40), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("election_irrevocable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("election_form", sa.String(40)),
        sa.ForeignKeyConstraint(["assessee_class_code"], [f"{SCHEMA}.assessee_class.code"]),
        sa.UniqueConstraint("code", "assessee_class_code", name="uq_statutory_tax_regime_class"),
        schema=SCHEMA,
    )

    op.create_table(
        "income_character",
        *_id_ts(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("surcharge_cap_percent", sa.Numeric(8, 4)),
        sa.Column("rebate_eligible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("setoff_group", sa.String(40)),
        sa.Column("loss_carry_years", sa.SmallInteger()),
        sa.UniqueConstraint("code", name="uq_statutory_income_character_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "rate_schedule",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("assessee_class_code", sa.String(40), nullable=False),
        sa.Column("regime_code", sa.String(40), nullable=False),
        sa.Column("income_character_code", sa.String(40)),
        sa.Column("age_category", sa.String(20), nullable=False, server_default=sa.text("'General'")),
        sa.Column("condition_expr", sa.String(255)),
        sa.Column("schedule_kind", sa.String(20), nullable=False, server_default=sa.text("'Slab'")),
        sa.Column("remarks", sa.String(255)),
        sa.ForeignKeyConstraint(["assessee_class_code"], [f"{SCHEMA}.assessee_class.code"]),
        sa.ForeignKeyConstraint(["income_character_code"], [f"{SCHEMA}.income_character.code"]),
        sa.UniqueConstraint(
            "finance_act_version_id",
            "code",
            name="uq_statutory_rate_schedule_fav_code",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_statutory_rate_schedule_lookup",
        "rate_schedule",
        ["finance_act_version_id", "assessee_class_code", "regime_code", "income_character_code"],
        schema=SCHEMA,
    )

    op.create_table(
        "rate_band",
        *_id_ts(),
        sa.Column(
            "rate_schedule_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.rate_schedule.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("lower", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("upper", sa.Numeric(21, 6)),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("fixed_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("rate_schedule_id", "seq", name="uq_statutory_rate_band_seq"),
        schema=SCHEMA,
    )

    op.create_table(
        "surcharge_schedule",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("assessee_class_code", sa.String(40), nullable=False),
        sa.Column("regime_code", sa.String(40)),
        sa.Column("marginal_relief_method", sa.String(40), nullable=False, server_default=sa.text("'RerunAtThreshold'")),
        sa.Column("capped_characters", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("remarks", sa.String(255)),
        sa.ForeignKeyConstraint(["assessee_class_code"], [f"{SCHEMA}.assessee_class.code"]),
        sa.UniqueConstraint("finance_act_version_id", "code", name="uq_statutory_surcharge_sched_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "surcharge_band",
        *_id_ts(),
        sa.Column(
            "surcharge_schedule_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.surcharge_schedule.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("lower", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("upper", sa.Numeric(21, 6)),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False),
        sa.UniqueConstraint("surcharge_schedule_id", "seq", name="uq_statutory_surcharge_band_seq"),
        schema=SCHEMA,
    )

    op.create_table(
        "cess_rule",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("base", sa.String(40), nullable=False, server_default=sa.text("'TaxPlusSurcharge'")),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint("finance_act_version_id", "code", name="uq_statutory_cess_rule_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "rebate_rule",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("section_code", sa.String(20), nullable=False, server_default=sa.text("'87A'")),
        sa.Column("assessee_class_code", sa.String(40), nullable=False),
        sa.Column("regime_code", sa.String(40), nullable=False),
        sa.Column("max_taxable_income", sa.Numeric(21, 6), nullable=False),
        sa.Column("max_rebate_amount", sa.Numeric(21, 6), nullable=False),
        sa.Column("marginal_relief_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("excluded_characters", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("remarks", sa.String(255)),
        sa.ForeignKeyConstraint(["assessee_class_code"], [f"{SCHEMA}.assessee_class.code"]),
        sa.UniqueConstraint("finance_act_version_id", "code", name="uq_statutory_rebate_rule_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "provision",
        *_id_ts(),
        sa.Column("section_code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("act_reference", sa.String(80)),
        sa.Column("stage", sa.String(20), nullable=False, server_default=sa.text("'PGBP'")),
        sa.Column("default_effect", sa.String(20), nullable=False, server_default=sa.text("'Add'")),
        sa.Column("itr_schedule", sa.String(40)),
        sa.Column("itr_field_path", sa.String(120)),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("section_code", name="uq_statutory_provision_section"),
        schema=SCHEMA,
    )

    op.create_table(
        "rule_pack",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("assessee_class_code", sa.String(40), nullable=False),
        sa.Column("regime_code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("remarks", sa.String(255)),
        sa.ForeignKeyConstraint(["assessee_class_code"], [f"{SCHEMA}.assessee_class.code"]),
        sa.UniqueConstraint("finance_act_version_id", "code", name="uq_statutory_rule_pack_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "rule",
        *_id_ts(),
        sa.Column(
            "rule_pack_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.rule_pack.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provision_section_code",
            sa.String(40),
            sa.ForeignKey(f"{SCHEMA}.provision.section_code"),
            nullable=False,
        ),
        sa.Column("rule_code", sa.String(40), nullable=False),
        sa.Column("evaluation_method", sa.String(40), nullable=False, server_default=sa.text("'Manual'")),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("depends_on", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("seq", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("rule_pack_id", "rule_code", name="uq_statutory_rule_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "deduction_section",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("cap_amount", sa.Numeric(21, 6)),
        sa.Column("cap_expr", sa.String(255)),
        sa.Column("qualifying_limit_percent", sa.Numeric(8, 4)),
        sa.Column("regime_allowed", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("age_dependent_caps", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint("finance_act_version_id", "section_code", name="uq_statutory_deduction_section"),
        schema=SCHEMA,
    )

    op.create_table(
        "depreciation_block",
        *_id_ts(),
        sa.Column("block_code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("rate_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("additional_depreciation_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint("block_code", name="uq_statutory_depreciation_block"),
        schema=SCHEMA,
    )

    op.create_table(
        "due_date_rule",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("rule_kind", sa.String(40), nullable=False),
        sa.Column("assessee_class_code", sa.String(40)),
        sa.Column("seq", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("due_month", sa.SmallInteger()),
        sa.Column("due_day", sa.SmallInteger()),
        sa.Column("percent_of_tax", sa.Numeric(8, 4)),
        sa.Column("label", sa.String(120)),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("finance_act_version_id", "code", name="uq_statutory_due_date_rule"),
        schema=SCHEMA,
    )

    op.create_table(
        "interest_rule",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("section_code", sa.String(20), nullable=False),
        sa.Column("rate_percent_per_month", sa.Numeric(8, 4), nullable=False),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint("finance_act_version_id", "code", name="uq_statutory_interest_rule"),
        schema=SCHEMA,
    )

    op.create_table(
        "itr_form",
        *_id_ts(),
        sa.Column(
            "finance_act_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.finance_act_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("form_code", sa.String(20), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("assessee_class_codes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.UniqueConstraint(
            "finance_act_version_id",
            "form_code",
            "schema_version",
            name="uq_statutory_itr_form",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "itr_field_map",
        *_id_ts(),
        sa.Column(
            "itr_form_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.itr_form.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_field", sa.String(120), nullable=False),
        sa.Column("cbdt_json_path", sa.String(255), nullable=False),
        sa.Column("transform", sa.String(80)),
        sa.UniqueConstraint("itr_form_id", "canonical_field", name="uq_statutory_itr_field_map"),
        schema=SCHEMA,
    )

    # SELECT-only for the app role — never INSERT/UPDATE/DELETE on statutory law.
    for table in (
        "assessment_year",
        "finance_act_version",
        "assessee_class",
        "tax_regime",
        "income_character",
        "rate_schedule",
        "rate_band",
        "surcharge_schedule",
        "surcharge_band",
        "cess_rule",
        "rebate_rule",
        "provision",
        "rule_pack",
        "rule",
        "deduction_section",
        "depreciation_block",
        "due_date_rule",
        "interest_rule",
        "itr_form",
        "itr_field_map",
    ):
        op.execute(f"GRANT SELECT ON {SCHEMA}.{table} TO erp_app")


def downgrade() -> None:
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
