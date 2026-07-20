"""Income Tax rate tables keyed by entity + regime; seed default masters.

Revision ID: 0068_itr_rate_entity
Revises: 0067_itr_ay_partial_unique
Create Date: 2026-07-19

- Add entity_type + filing_regime on income_tax_rate_tables
- Replace unique (company, AY) with (company, AY, entity_type, filing_regime)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0068_itr_rate_entity"
down_revision: Union[str, None] = "0067_itr_ay_partial_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "income_tax_rate_tables",
        sa.Column(
            "entity_type",
            sa.String(length=20),
            nullable=False,
            server_default="Company",
        ),
    )
    op.add_column(
        "income_tax_rate_tables",
        sa.Column(
            "filing_regime",
            sa.String(length=20),
            nullable=False,
            server_default="Normal",
        ),
    )
    op.drop_constraint("uq_income_tax_rate_ay", "income_tax_rate_tables", type_="unique")
    op.create_unique_constraint(
        "uq_income_tax_rate_ay_entity_regime",
        "income_tax_rate_tables",
        ["company_id", "assessment_year", "entity_type", "filing_regime"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_income_tax_rate_ay_entity_regime",
        "income_tax_rate_tables",
        type_="unique",
    )
    # May fail if duplicate AYs exist after multi-entity seed — intentional.
    op.create_unique_constraint(
        "uq_income_tax_rate_ay",
        "income_tax_rate_tables",
        ["company_id", "assessment_year"],
    )
    op.drop_column("income_tax_rate_tables", "filing_regime")
    op.drop_column("income_tax_rate_tables", "entity_type")
