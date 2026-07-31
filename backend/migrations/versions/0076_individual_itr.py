"""Individual ITR slabs + income heads + payroll bridge columns.

Revision ID: 0076_individual_itr
Revises: 0075_quality_inspection
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0076_individual_itr"
down_revision: Union[str, None] = "0075_quality_inspection"
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


def upgrade() -> None:
    op.create_table(
        "income_tax_slab_lines",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "rate_table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("income_tax_rate_tables.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("from_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("to_amount", sa.Numeric(21, 6), nullable=True),
        sa.Column("rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "ix_income_tax_slab_lines_company_id",
        "income_tax_slab_lines",
        ["company_id"],
    )

    # Computation: assessee mode, heads, Form 16, payroll bridge
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "assessee_mode",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'EntityBooks'"),
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("salary_income", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "house_property_income", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "other_sources_income", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "capital_gains_income", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "chapter_via_deduction", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "standard_deduction", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("salary_tds", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("rebate_87a", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    # Form 16 lean fields
    op.add_column("income_tax_computations", sa.Column("employer_name", sa.String(255), nullable=True))
    op.add_column("income_tax_computations", sa.Column("employer_tan", sa.String(20), nullable=True))
    op.add_column(
        "income_tax_computations", sa.Column("employer_address", sa.String(500), nullable=True)
    )
    op.add_column("income_tax_computations", sa.Column("employee_name", sa.String(255), nullable=True))
    op.add_column("income_tax_computations", sa.Column("employee_pan", sa.String(20), nullable=True))
    op.add_column(
        "income_tax_computations",
        sa.Column("gross_salary", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("exemptions_total", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("taxable_salary", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("tax_deducted", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    # Payroll bridge (no FK until Employee / Payroll Entry exist)
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "seed_source",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'Manual'"),
        ),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("employee_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column("payroll_entry_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "income_tax_computations",
        sa.Column(
            "salary_slip_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # Replace company×AY uniqueness with mode-aware partial indexes
    op.drop_index("uq_income_tax_computation_ay_active", table_name="income_tax_computations")
    op.create_index(
        "uq_itr_entity_books_ay_active",
        "income_tax_computations",
        ["company_id", "assessment_year"],
        unique=True,
        postgresql_where=sa.text("assessee_mode = 'EntityBooks' AND docstatus <> 2"),
    )
    op.create_index(
        "uq_itr_individual_employee_ay_active",
        "income_tax_computations",
        ["company_id", "assessment_year", "employee_id"],
        unique=True,
        postgresql_where=sa.text(
            "assessee_mode = 'IndividualHeads' AND employee_id IS NOT NULL AND docstatus <> 2"
        ),
    )
    op.create_index(
        "uq_itr_individual_unassigned_ay_active",
        "income_tax_computations",
        ["company_id", "assessment_year"],
        unique=True,
        postgresql_where=sa.text(
            "assessee_mode = 'IndividualHeads' AND employee_id IS NULL AND docstatus <> 2"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_itr_individual_unassigned_ay_active", table_name="income_tax_computations")
    op.drop_index("uq_itr_individual_employee_ay_active", table_name="income_tax_computations")
    op.drop_index("uq_itr_entity_books_ay_active", table_name="income_tax_computations")
    op.create_index(
        "uq_income_tax_computation_ay_active",
        "income_tax_computations",
        ["company_id", "assessment_year"],
        unique=True,
        postgresql_where=sa.text("docstatus <> 2"),
    )

    for col in (
        "salary_slip_ids",
        "payroll_entry_id",
        "employee_id",
        "seed_source",
        "tax_deducted",
        "taxable_salary",
        "exemptions_total",
        "gross_salary",
        "employee_pan",
        "employee_name",
        "employer_address",
        "employer_tan",
        "employer_name",
        "rebate_87a",
        "salary_tds",
        "standard_deduction",
        "chapter_via_deduction",
        "capital_gains_income",
        "other_sources_income",
        "house_property_income",
        "salary_income",
        "assessee_mode",
    ):
        op.drop_column("income_tax_computations", col)

    op.drop_index("ix_income_tax_slab_lines_company_id", table_name="income_tax_slab_lines")
    op.drop_table("income_tax_slab_lines")
