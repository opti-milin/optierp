"""HSN/SAC → GST-rate reference master (India compliance).

Global (not company-scoped) lookup table seeded from the official HSN rate
schedule. Powers "type an item name → auto-fetch HSN + GST rate" on the Item
form. A GIN full-text index over the commodity description gives ranked search;
a plain index on ``hsn_code`` serves code-prefix lookups.

Data is loaded by ``scripts/seed.py`` (``data/seeds/hsn_codes.json``), not here.

Revision ID: 0060_hsn_codes
Revises: 0059_gst_invoice_fields
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0060_hsn_codes"
down_revision: Union[str, None] = "0059_gst_invoice_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hsn_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("docstatus", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("hsn_code", sa.String(length=8), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("gst_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("gst_treatment", sa.String(length=20), server_default=sa.text("'Taxable'"), nullable=False),
        sa.Column("chapter", sa.Integer(), nullable=True),
        sa.Column("schedule", sa.String(length=6), nullable=True),
        sa.UniqueConstraint("hsn_code", "description", name="uq_hsn_code_description"),
    )
    op.create_index("ix_hsn_codes_hsn_code", "hsn_codes", ["hsn_code"])
    # GIN full-text index for ranked "search by product name" lookups. The
    # two-arg to_tsvector(regconfig, text) form is IMMUTABLE, so it is indexable.
    op.execute(
        "CREATE INDEX ix_hsn_codes_fts ON hsn_codes "
        "USING GIN (to_tsvector('english', description))"
    )


def downgrade() -> None:
    op.drop_index("ix_hsn_codes_fts", table_name="hsn_codes")
    op.drop_index("ix_hsn_codes_hsn_code", table_name="hsn_codes")
    op.drop_table("hsn_codes")
