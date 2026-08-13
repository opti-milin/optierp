"""Subcontract Job + Send/Receipt Stock Entry purposes (Phase 4).

Revision ID: 0074_subcontracting
Revises: 0073_production_plan
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0074_subcontracting"
down_revision: Union[str, None] = "0073_production_plan"
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
        "subcontract_jobs",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("supplier_id", UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column(
            "production_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("items.id"),
            nullable=False,
        ),
        sa.Column("bom_id", UUID(as_uuid=True), sa.ForeignKey("boms.id"), nullable=False),
        sa.Column("qty", sa.Numeric(21, 6), nullable=False),
        sa.Column("sent_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("received_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "source_warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=False,
        ),
        sa.Column(
            "supplier_warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=False,
        ),
        sa.Column(
            "fg_warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=False,
        ),
        sa.Column("service_cost", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "service_cost_account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_subcontract_job_name"),
    )
    op.create_index(
        "ix_subcontract_jobs_company_docstatus",
        "subcontract_jobs",
        ["company_id", "docstatus"],
    )
    op.create_index(
        "ix_subcontract_jobs_company_status",
        "subcontract_jobs",
        ["company_id", "status"],
    )

    op.create_table(
        "subcontract_job_items",
        *_meta_columns(),
        sa.Column(
            "subcontract_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subcontract_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("required_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("sent_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("consumed_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "source_warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id"),
            nullable=True,
        ),
        sa.Column("rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_subcontract_job_items_item", "subcontract_job_items", ["item_id"])

    op.add_column(
        "stock_entries",
        sa.Column(
            "subcontract_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subcontract_jobs.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("stock_entries", "subcontract_job_id")
    op.drop_table("subcontract_job_items")
    op.drop_index("ix_subcontract_jobs_company_status", table_name="subcontract_jobs")
    op.drop_index("ix_subcontract_jobs_company_docstatus", table_name="subcontract_jobs")
    op.drop_table("subcontract_jobs")
