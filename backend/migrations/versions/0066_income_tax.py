"""Income Tax (entity ITR) — Phase 0–1.

Revision ID: 0066_income_tax
Revises: 0065_manufacturing
Create Date: 2026-07-19

- Company.pan / Company.tan
- Engine masters: income_tax_rate_tables, tax_adjustment_categories
- Bespoke worksheet: income_tax_computations + income_tax_adjustment_lines
- RLS company_isolation on all new tenant tables
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0066_income_tax"
down_revision: Union[str, None] = "0065_manufacturing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY company_isolation ON {table} "
        f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
    )


def _drop_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")


def upgrade() -> None:
    op.add_column("companies", sa.Column("pan", sa.String(10), nullable=True))
    op.add_column("companies", sa.Column("tan", sa.String(10), nullable=True))

    op.create_table(
        "income_tax_rate_tables",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "company_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("tax_rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("surcharge_rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("cess_rate", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("remarks", sa.String(255), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "assessment_year", name="uq_income_tax_rate_ay"),
    )
    op.create_index("ix_income_tax_rate_tables_company_id", "income_tax_rate_tables", ["company_id"])

    op.create_table(
        "tax_adjustment_categories",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "company_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("category_code", sa.String(40), nullable=False),
        sa.Column("category_name", sa.String(140), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False, server_default=sa.text("'Add'")),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "category_code", name="uq_tax_adj_category_code"),
    )
    op.create_index("ix_tax_adjustment_categories_company_id", "tax_adjustment_categories", ["company_id"])

    op.create_table(
        "income_tax_computations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "company_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("assessment_year", sa.String(20), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("rate_table_id", pg.UUID(as_uuid=True), sa.ForeignKey("income_tax_rate_tables.id")),
        sa.Column("book_profit", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("net_adjustments", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("taxable_income", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("surcharge_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("cess_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tax", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("tds_credit", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("advance_tax_paid", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_payable", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "assessment_year", name="uq_income_tax_computation_ay"),
        sa.UniqueConstraint("company_id", "name", name="uq_income_tax_computation_name"),
    )
    op.create_index("ix_income_tax_computations_company_id", "income_tax_computations", ["company_id"])

    op.create_table(
        "income_tax_adjustment_lines",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "company_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "computation_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("income_tax_computations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("category_id", pg.UUID(as_uuid=True), sa.ForeignKey("tax_adjustment_categories.id")),
        sa.Column("description", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("direction", sa.String(10), nullable=False, server_default=sa.text("'Add'")),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "ix_income_tax_adjustment_lines_company_id", "income_tax_adjustment_lines", ["company_id"]
    )
    op.create_index(
        "ix_income_tax_adjustment_lines_computation_id",
        "income_tax_adjustment_lines",
        ["computation_id"],
    )

    for table in (
        "income_tax_rate_tables",
        "tax_adjustment_categories",
        "income_tax_computations",
        "income_tax_adjustment_lines",
    ):
        _rls(table)


def downgrade() -> None:
    for table in (
        "income_tax_adjustment_lines",
        "income_tax_computations",
        "tax_adjustment_categories",
        "income_tax_rate_tables",
    ):
        _drop_rls(table)
    op.drop_table("income_tax_adjustment_lines")
    op.drop_table("income_tax_computations")
    op.drop_table("tax_adjustment_categories")
    op.drop_table("income_tax_rate_tables")
    op.drop_column("companies", "tan")
    op.drop_column("companies", "pan")
