"""GST on advances received: Payment Entry tax columns + AdvanceGstAdjustment ledger.

Regular dealers book output GST on a SERVICE advance at receipt (inclusive; Dr 'GST on
Advances' control / Cr Output GST) and reverse it proportionally as the advance is adjusted
to invoices. ``payment_entries`` gains the advance-GST state; ``advance_gst_adjustments``
records each adjustment (GSTR-1 Table 11B + the GSTR-3B 3.1(a) netting source).

Revision ID: 0063_advance_gst
Revises: 0062_sales_invoice_ecommerce_gstin
Create Date: 2026-07-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0063_advance_gst"
down_revision: Union[str, None] = "0062_sales_invoice_ecommerce_gstin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_entries", sa.Column("advance_supply_type", sa.String(length=20), nullable=True))
    op.add_column("payment_entries", sa.Column("advance_gst_rate", sa.Numeric(9, 4), nullable=True))
    op.add_column(
        "payment_entries",
        sa.Column("advance_gst_amount", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "payment_entries",
        sa.Column("advance_is_inter_state", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "payment_entries",
        sa.Column("advance_gst_outstanding", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
    )

    op.create_table(
        "advance_gst_adjustments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("creation", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("modified", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("owner", UUID(as_uuid=True), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), nullable=True),
        sa.Column("docstatus", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "payment_entry_id", UUID(as_uuid=True),
            sa.ForeignKey("payment_entries.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("invoice_id", UUID(as_uuid=True), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("place_of_supply", sa.String(length=64), nullable=True),
        sa.Column("rate", sa.Numeric(9, 4), server_default=sa.text("0"), nullable=False),
        sa.Column("base", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("cgst", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("sgst", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("igst", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("cess", sa.Numeric(21, 6), server_default=sa.text("0"), nullable=False),
    )
    op.create_index(
        "ix_advance_gst_adjustments_company_date", "advance_gst_adjustments",
        ["company_id", "posting_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_advance_gst_adjustments_company_date", table_name="advance_gst_adjustments")
    op.drop_table("advance_gst_adjustments")
    op.drop_column("payment_entries", "advance_gst_outstanding")
    op.drop_column("payment_entries", "advance_is_inter_state")
    op.drop_column("payment_entries", "advance_gst_amount")
    op.drop_column("payment_entries", "advance_gst_rate")
    op.drop_column("payment_entries", "advance_supply_type")
