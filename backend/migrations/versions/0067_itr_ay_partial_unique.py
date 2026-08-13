"""Allow a new Income Tax Computation after cancel for the same AY.

Revision ID: 0067_itr_ay_partial_unique
Revises: 0066_income_tax
Create Date: 2026-07-19

Replace full unique (company_id, assessment_year) with a partial unique index
that excludes cancelled rows (docstatus = 2), matching the service-layer check.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067_itr_ay_partial_unique"
down_revision: Union[str, None] = "0066_income_tax"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_income_tax_computation_ay", "income_tax_computations", type_="unique"
    )
    op.create_index(
        "uq_income_tax_computation_ay_active",
        "income_tax_computations",
        ["company_id", "assessment_year"],
        unique=True,
        postgresql_where=sa.text("docstatus <> 2"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_income_tax_computation_ay_active",
        table_name="income_tax_computations",
    )
    op.create_unique_constraint(
        "uq_income_tax_computation_ay",
        "income_tax_computations",
        ["company_id", "assessment_year"],
    )
