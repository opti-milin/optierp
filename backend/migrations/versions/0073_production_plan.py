"""Production Plan (Phase 3 MRP-I): aggregate SO demand → proposed WOs + MRs.

Revision ID: 0073_production_plan
Revises: 0072_shop_floor
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0073_production_plan"
down_revision: Union[str, None] = "0072_shop_floor"
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
        "production_plans",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column(
            "get_items_from",
            sa.String(30),
            nullable=False,
            server_default=sa.text("'Sales Order'"),
        ),
        sa.Column(
            "fg_warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=True,
        ),
        sa.Column(
            "source_warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column("work_orders_created", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "material_requests_created",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_production_plan_name"),
    )
    op.create_index(
        "ix_production_plans_company_docstatus",
        "production_plans",
        ["company_id", "docstatus"],
    )

    op.create_table(
        "production_plan_items",
        *_meta_columns(),
        sa.Column(
            "production_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("production_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("bom_id", UUID(as_uuid=True), sa.ForeignKey("boms.id"), nullable=True),
        sa.Column(
            "sales_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sales_orders.id"),
            nullable=True,
        ),
        sa.Column(
            "sales_order_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sales_order_items.id"),
            nullable=True,
        ),
        sa.Column("planned_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("pending_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("ordered_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=True,
        ),
        sa.Column("planned_start_date", sa.Date(), nullable=True),
        sa.Column(
            "work_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("work_orders.id"),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_production_plan_items_plan", "production_plan_items", ["production_plan_id"])
    op.create_index("ix_production_plan_items_item", "production_plan_items", ["item_id"])

    op.create_table(
        "production_plan_material_requests",
        *_meta_columns(),
        sa.Column(
            "production_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("production_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column(
            "warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=True,
        ),
        sa.Column("required_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("available_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("shortfall_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "material_request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_requests.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_production_plan_mrs_plan",
        "production_plan_material_requests",
        ["production_plan_id"],
    )

    op.add_column(
        "work_orders",
        sa.Column(
            "production_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("production_plans.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_work_orders_production_plan",
        "work_orders",
        ["production_plan_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_orders_production_plan", table_name="work_orders")
    op.drop_column("work_orders", "production_plan_id")

    op.drop_index("ix_production_plan_mrs_plan", table_name="production_plan_material_requests")
    op.drop_table("production_plan_material_requests")

    op.drop_index("ix_production_plan_items_item", table_name="production_plan_items")
    op.drop_index("ix_production_plan_items_plan", table_name="production_plan_items")
    op.drop_table("production_plan_items")

    op.drop_index("ix_production_plans_company_docstatus", table_name="production_plans")
    op.drop_table("production_plans")
