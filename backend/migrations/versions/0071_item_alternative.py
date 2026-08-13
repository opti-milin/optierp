"""Item Alternative master — allowed substitutes for BOM/WO Finish.

Revision ID: 0071_item_alternative
Revises: 0070_bom_depth
Create Date: 2026-07-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0071_item_alternative"
down_revision: Union[str, None] = "0070_bom_depth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "item_alternatives",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", pg.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "company_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(280), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column(
            "alternative_item_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("items.id"),
            nullable=False,
        ),
        sa.Column("two_way", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint(
            "company_id", "item_id", "alternative_item_id", name="uq_item_alternative_pair"
        ),
    )
    op.create_index(
        "ix_item_alternatives_company_item",
        "item_alternatives",
        ["company_id", "item_id"],
    )
    op.execute("ALTER TABLE item_alternatives ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY company_isolation ON item_alternatives "
        "USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON item_alternatives")
    op.drop_index("ix_item_alternatives_company_item", table_name="item_alternatives")
    op.drop_table("item_alternatives")
