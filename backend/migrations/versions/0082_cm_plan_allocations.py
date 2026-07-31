"""Add allocations JSONB snapshot on cm_plans for per-plan fixed-cost %."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0082_cm_plan_allocations"
down_revision = "0081_cm_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cm_plans",
        sa.Column("allocations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cm_plans", "allocations")
