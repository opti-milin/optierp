"""Relax income-tax computation per-AY uniqueness (testing).

Allows multiple Draft/Submitted worksheets for the same assessment year × mode
so engine scenarios can be compared side-by-side. Re-tighten later if product
wants one active pack per AY again.

Revision ID: 0078_relax_itr_ay_unique
Revises: 0077_income_tax_engine
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0078_relax_itr_ay_unique"
down_revision = "0077_income_tax_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_itr_individual_unassigned_ay_active", table_name="income_tax_computations")
    op.drop_index("uq_itr_individual_employee_ay_active", table_name="income_tax_computations")
    op.drop_index("uq_itr_entity_books_ay_active", table_name="income_tax_computations")


def downgrade() -> None:
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
