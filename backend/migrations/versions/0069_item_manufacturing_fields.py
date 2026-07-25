"""Item manufacturing fields: default_bom_id + include_item_in_manufacturing.

Revision ID: 0069_item_manufacturing_fields
Revises: 0068_itr_rate_entity
Create Date: 2026-07-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0069_item_manufacturing_fields"
down_revision: Union[str, None] = "0068_itr_rate_entity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column(
            "default_bom_id",
            UUID(as_uuid=True),
            sa.ForeignKey("boms.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "items",
        sa.Column(
            "include_item_in_manufacturing",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index(
        "ix_items_default_bom",
        "items",
        ["default_bom_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_items_default_bom", table_name="items")
    op.drop_column("items", "include_item_in_manufacturing")
    op.drop_column("items", "default_bom_id")
