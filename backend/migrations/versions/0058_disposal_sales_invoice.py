"""Disposal via Sales Invoice: link the asset to the sale invoice.

Adds ``assets.disposal_sales_invoice_id`` so a Sell disposal raised as a GST tax invoice
records the Sales Invoice it was sold on (alongside the asset-removal Journal Entry).

Revision ID: 0058_disposal_sales_invoice
Revises: 0057_maintenance_sched
Create Date: 2026-06-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0058_disposal_sales_invoice"
down_revision: Union[str, None] = "0057_maintenance_sched"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "disposal_sales_invoice_id", UUID(as_uuid=True),
            sa.ForeignKey("sales_invoices.id"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("assets", "disposal_sales_invoice_id")
