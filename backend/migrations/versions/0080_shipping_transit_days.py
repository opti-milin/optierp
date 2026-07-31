"""Add transit_days to shipping_rules for outbound delivery lead time.

Revision ID: 0080_shipping_transit_days
Revises: 0079_contribution_margin
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0080_shipping_transit_days"
down_revision = "0079_contribution_margin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shipping_rules",
        sa.Column(
            "transit_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("shipping_rules", "transit_days")
