"""Quality Inspection + Item.inspection_required (Phase 5).

Revision ID: 0075_quality_inspection
Revises: 0074_subcontracting
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0075_quality_inspection"
down_revision: Union[str, None] = "0074_subcontracting"
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
    op.add_column(
        "items",
        sa.Column(
            "inspection_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "quality_inspections",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("reference_type", sa.String(40), nullable=False),
        sa.Column("reference_id", UUID(as_uuid=True), nullable=False),
        sa.Column("reference_name", sa.String(140), nullable=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("qty", sa.Numeric(21, 6), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column("inspection_date", sa.Date(), nullable=False),
        sa.Column("inspected_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_quality_inspection_name"),
    )
    op.create_index(
        "ix_quality_inspections_company_docstatus",
        "quality_inspections",
        ["company_id", "docstatus"],
    )
    op.create_index(
        "ix_quality_inspections_reference",
        "quality_inspections",
        ["company_id", "reference_type", "reference_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_quality_inspections_reference", table_name="quality_inspections")
    op.drop_index("ix_quality_inspections_company_docstatus", table_name="quality_inspections")
    op.drop_table("quality_inspections")
    op.drop_column("items", "inspection_required")
