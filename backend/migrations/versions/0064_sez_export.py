"""SEZ / Export (zero-rated) GST fields on the sales invoice.

``sales_invoices``: ``gst_category`` (Regular | SEZ | Export | Deemed Export),
``export_with_payment`` (SEZ/Export with IGST vs LUT), and the export shipping-bill
details (``shipping_bill_no`` / ``shipping_bill_date`` / ``port_code``) for GSTR-1 Table 6A.

Revision ID: 0064_sez_export
Revises: 0063_advance_gst
Create Date: 2026-07-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064_sez_export"
down_revision: Union[str, None] = "0063_advance_gst"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales_invoices",
        sa.Column("gst_category", sa.String(length=20), server_default=sa.text("'Regular'"), nullable=False),
    )
    op.add_column(
        "sales_invoices",
        sa.Column("export_with_payment", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("sales_invoices", sa.Column("shipping_bill_no", sa.String(length=20), nullable=True))
    op.add_column("sales_invoices", sa.Column("shipping_bill_date", sa.Date(), nullable=True))
    op.add_column("sales_invoices", sa.Column("port_code", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_invoices", "port_code")
    op.drop_column("sales_invoices", "shipping_bill_date")
    op.drop_column("sales_invoices", "shipping_bill_no")
    op.drop_column("sales_invoices", "export_with_payment")
    op.drop_column("sales_invoices", "gst_category")
