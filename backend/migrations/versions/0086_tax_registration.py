"""Tenant tax configuration — registrations, regime elections, policy overrides.

RLS-scoped; replaces the ``income_tax_settings`` SystemSetting blob.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0086_tax_registration"
down_revision = "0085_statutory_schema"
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
        "tax_registrations",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pan", sa.String(10)),
        sa.Column("tan", sa.String(10)),
        sa.Column("cin", sa.String(30)),
        sa.Column("assessee_class_code", sa.String(40), nullable=False, server_default=sa.text("'Company'")),
        sa.Column("residential_status", sa.String(20), nullable=False, server_default=sa.text("'Resident'")),
        sa.Column("incorporation_date", sa.Date()),
        sa.Column("nature_of_business_codes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("jurisdiction", sa.String(120)),
        sa.Column("default_assessment_year", sa.String(20)),
        sa.Column("itr_efile_provider", sa.String(40)),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint("company_id", name="uq_tax_registrations_company"),
    )
    op.create_index("ix_tax_registrations_company_id", "tax_registrations", ["company_id"])
    _rls("tax_registrations")

    op.create_table(
        "tax_regime_elections",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("regime_code", sa.String(40), nullable=False),
        sa.Column("assessee_class_code", sa.String(40), nullable=False),
        sa.Column("elected_on", sa.Date()),
        sa.Column("form_ack_no", sa.String(80)),
        sa.Column("irrevocable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("remarks", sa.String(255)),
        sa.UniqueConstraint("company_id", "ay_code", name="uq_tax_regime_election_company_ay"),
    )
    op.create_index("ix_tax_regime_elections_company_id", "tax_regime_elections", ["company_id"])
    _rls("tax_regime_elections")

    op.create_table(
        "tax_policy_overrides",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("override_kind", sa.String(40), nullable=False),
        sa.Column("target_code", sa.String(80), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint(
            "company_id",
            "ay_code",
            "override_kind",
            "target_code",
            name="uq_tax_policy_override",
        ),
    )
    op.create_index("ix_tax_policy_overrides_company_id", "tax_policy_overrides", ["company_id"])
    _rls("tax_policy_overrides")

    # Migrate legacy SystemSetting blob → tax_registrations (best-effort).
    op.execute(
        """
        INSERT INTO tax_registrations (
            company_id, assessee_class_code, default_assessment_year, itr_efile_provider, docstatus
        )
        SELECT
            ss.company_id,
            CASE COALESCE(ss.value->>'entity_type', 'Company')
                WHEN 'Proprietor' THEN 'Individual'
                WHEN 'Individual' THEN 'Individual'
                WHEN 'Firm' THEN 'Firm'
                WHEN 'LLP' THEN 'LLP'
                ELSE 'Company'
            END,
            NULLIF(ss.value->>'default_assessment_year', ''),
            NULLIF(ss.value->>'itr_efile_provider', ''),
            0
        FROM system_settings ss
        WHERE ss.key = 'income_tax_settings'
          AND ss.company_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM tax_registrations tr WHERE tr.company_id = ss.company_id
          )
        """
    )
    op.execute(
        """
        INSERT INTO tax_regime_elections (
            company_id, ay_code, regime_code, assessee_class_code, irrevocable, docstatus
        )
        SELECT
            ss.company_id,
            COALESCE(NULLIF(ss.value->>'default_assessment_year', ''), '2025-26'),
            CASE
                WHEN COALESCE(ss.value->>'entity_type', 'Company') IN ('Individual', 'Proprietor')
                     AND COALESCE(ss.value->>'filing_regime', 'Normal') = 'New'
                    THEN '115BAC'
                WHEN COALESCE(ss.value->>'entity_type', 'Company') IN ('Individual', 'Proprietor')
                    THEN 'Old'
                WHEN COALESCE(ss.value->>'filing_regime', 'Normal') = 'New'
                    THEN '115BAA'
                ELSE 'Normal'
            END,
            CASE COALESCE(ss.value->>'entity_type', 'Company')
                WHEN 'Proprietor' THEN 'Individual'
                WHEN 'Individual' THEN 'Individual'
                WHEN 'Firm' THEN 'Firm'
                WHEN 'LLP' THEN 'LLP'
                ELSE 'Company'
            END,
            CASE
                WHEN COALESCE(ss.value->>'filing_regime', 'Normal') = 'New'
                     AND COALESCE(ss.value->>'entity_type', 'Company') = 'Company'
                    THEN true
                ELSE false
            END,
            0
        FROM system_settings ss
        WHERE ss.key = 'income_tax_settings'
          AND ss.company_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM tax_regime_elections te
              WHERE te.company_id = ss.company_id
                AND te.ay_code = COALESCE(NULLIF(ss.value->>'default_assessment_year', ''), '2025-26')
          )
        """
    )


def downgrade() -> None:
    for table in ("tax_policy_overrides", "tax_regime_elections", "tax_registrations"):
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
        op.drop_table(table)
