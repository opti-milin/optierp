"""Add account cm_class for Contribution Margin reporting.

Revision ID: 0079_contribution_margin
Revises: 0078_relax_itr_ay_unique
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0079_contribution_margin"
down_revision = "0078_relax_itr_ay_unique"
branch_labels = None
depends_on = None

CM_CLASS_VALUES = (
    "revenue",
    "variable_cost",
    "product_channel_fixed",
    "segment_bu_fixed",
    "corporate_overhead",
)


def upgrade() -> None:
    cm_class = postgresql.ENUM(*CM_CLASS_VALUES, name="cm_class", create_type=False)
    cm_class.create(op.get_bind(), checkfirst=True)
    op.add_column("accounts", sa.Column("cm_class", cm_class, nullable=True))
    op.create_index(
        "ix_accounts_company_cm_class",
        "accounts",
        ["company_id", "cm_class"],
    )


def downgrade() -> None:
    op.drop_index("ix_accounts_company_cm_class", table_name="accounts")
    op.drop_column("accounts", "cm_class")
    postgresql.ENUM(name="cm_class").drop(op.get_bind(), checkfirst=True)
