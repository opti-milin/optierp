"""BOM depth (Phase 1): phantom flag, scrap items, alternate-item flag.

Revision ID: 0070_bom_depth
Revises: 0069_item_manufacturing_fields
Create Date: 2026-07-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0070_bom_depth"
down_revision: Union[str, None] = "0069_item_manufacturing_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "boms",
        sa.Column(
            "is_phantom",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "boms",
        sa.Column(
            "scrap_cost",
            sa.Numeric(21, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "bom_items",
        sa.Column(
            "allow_alternative_item",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "work_order_items",
        sa.Column(
            "allow_alternative_item",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "bom_scrap_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id", use_alter=True), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id", use_alter=True), nullable=True),
        sa.Column(
            "bom_id",
            UUID(as_uuid=True),
            sa.ForeignKey("boms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("qty", sa.Numeric(21, 6), nullable=False),
        sa.Column("uom", sa.String(140), nullable=True),
        sa.Column("conversion_factor", sa.Numeric(21, 9), nullable=False, server_default=sa.text("1")),
        sa.Column("stock_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("stock_warehouse_id", UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=True),
    )
    op.create_index("ix_bom_scrap_items_bom", "bom_scrap_items", ["bom_id"])
    op.create_index("ix_bom_scrap_items_item", "bom_scrap_items", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_bom_scrap_items_item", table_name="bom_scrap_items")
    op.drop_index("ix_bom_scrap_items_bom", table_name="bom_scrap_items")
    op.drop_table("bom_scrap_items")
    op.drop_column("work_order_items", "allow_alternative_item")
    op.drop_column("bom_items", "allow_alternative_item")
    op.drop_column("boms", "scrap_cost")
    op.drop_column("boms", "is_phantom")
