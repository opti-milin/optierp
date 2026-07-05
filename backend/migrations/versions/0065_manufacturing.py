"""Manufacturing module: BOM → Work Order → Manufacture.

``boms`` + ``bom_items`` (the recipe, no GL) and ``work_orders`` + ``work_order_items``
(make N of a finished good). Adds a ``work_order_id`` link + ``operating_cost`` /
``operating_cost_account_id`` to ``stock_entries`` for the new "Manufacture" purpose, which
reuses the existing Stock Ledger / valuation / perpetual GL — no new posting engine.
Company-scoped tables filter by company_id explicitly (no RLS, like the accounting / assets
tables).

Revision ID: 0065_manufacturing
Revises: 0064_sez_export
Create Date: 2026-07-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0065_manufacturing"
down_revision: Union[str, None] = "0064_sez_export"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _meta_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("docstatus", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    ]


def _company_column() -> sa.Column:
    return sa.Column(
        "company_id", UUID(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
    )


def upgrade() -> None:
    # --- BOM (bespoke document) ---
    op.create_table(
        "boms",
        *_meta_columns(),
        _company_column(),
        sa.Column("name", sa.String(length=140), nullable=False),
        sa.Column("production_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("uom", sa.String(length=140), nullable=True),
        sa.Column("quantity", sa.Numeric(21, 6), server_default=sa.text("1"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("operating_cost", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("raw_material_cost", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("total_cost", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_bom_name"),
    )
    op.create_index("ix_boms_company_id", "boms", ["company_id"])
    op.create_index("ix_boms_company_item", "boms", ["company_id", "production_item_id"])
    op.create_index("ix_boms_company_docstatus", "boms", ["company_id", "docstatus"])

    # --- BOM Item (child of BOM) ---
    op.create_table(
        "bom_items",
        *_meta_columns(),
        sa.Column("bom_id", UUID(as_uuid=True), sa.ForeignKey("boms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idx", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("qty", sa.Numeric(21, 6), nullable=False),
        sa.Column("uom", sa.String(length=140), nullable=True),
        sa.Column("conversion_factor", sa.Numeric(21, 9), server_default=sa.text("1"), nullable=False),
        sa.Column("stock_qty", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("rate", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("amount", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("source_warehouse_id", UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=True),
    )
    op.create_index("ix_bom_items_bom", "bom_items", ["bom_id"])
    op.create_index("ix_bom_items_item", "bom_items", ["item_id"])

    # --- Work Order (bespoke document) ---
    op.create_table(
        "work_orders",
        *_meta_columns(),
        _company_column(),
        sa.Column("name", sa.String(length=140), nullable=False),
        sa.Column("production_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("bom_id", UUID(as_uuid=True), sa.ForeignKey("boms.id"), nullable=False),
        sa.Column("qty", sa.Numeric(21, 6), nullable=False),
        sa.Column("produced_qty", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("material_transferred_qty", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("source_warehouse_id", UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("wip_warehouse_id", UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("fg_warehouse_id", UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("skip_transfer", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("operating_cost", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("operating_cost_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'Draft'"), nullable=False),
        sa.Column("planned_start_date", sa.Date(), nullable=True),
        sa.Column("planned_end_date", sa.Date(), nullable=True),
        sa.Column("actual_start_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.Column("sales_order_id", UUID(as_uuid=True), sa.ForeignKey("sales_orders.id"), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_work_order_name"),
    )
    op.create_index("ix_work_orders_company_id", "work_orders", ["company_id"])
    op.create_index("ix_work_orders_company_docstatus", "work_orders", ["company_id", "docstatus"])
    op.create_index("ix_work_orders_company_status", "work_orders", ["company_id", "status"])

    # --- Work Order Item (child of Work Order) ---
    op.create_table(
        "work_order_items",
        *_meta_columns(),
        sa.Column(
            "work_order_id", UUID(as_uuid=True),
            sa.ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("idx", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("required_qty", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("transferred_qty", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("consumed_qty", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("source_warehouse_id", UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("rate", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("amount", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
    )
    op.create_index("ix_work_order_items_wo", "work_order_items", ["work_order_id"])
    op.create_index("ix_work_order_items_item", "work_order_items", ["item_id"])

    # --- Stock Entry: Manufacture purpose links + operating cost ---
    op.add_column(
        "stock_entries",
        sa.Column("work_order_id", UUID(as_uuid=True), sa.ForeignKey("work_orders.id"), nullable=True),
    )
    op.add_column(
        "stock_entries",
        sa.Column("operating_cost", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "stock_entries",
        sa.Column(
            "operating_cost_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("stock_entries", "operating_cost_account_id")
    op.drop_column("stock_entries", "operating_cost")
    op.drop_column("stock_entries", "work_order_id")

    op.drop_index("ix_work_order_items_item", table_name="work_order_items")
    op.drop_index("ix_work_order_items_wo", table_name="work_order_items")
    op.drop_table("work_order_items")

    op.drop_index("ix_work_orders_company_status", table_name="work_orders")
    op.drop_index("ix_work_orders_company_docstatus", table_name="work_orders")
    op.drop_index("ix_work_orders_company_id", table_name="work_orders")
    op.drop_table("work_orders")

    op.drop_index("ix_bom_items_item", table_name="bom_items")
    op.drop_index("ix_bom_items_bom", table_name="bom_items")
    op.drop_table("bom_items")

    op.drop_index("ix_boms_company_docstatus", table_name="boms")
    op.drop_index("ix_boms_company_item", table_name="boms")
    op.drop_index("ix_boms_company_id", table_name="boms")
    op.drop_table("boms")
