"""E-commerce (u/s 52): the operator GSTIN a sales invoice was supplied through.

``sales_invoices.ecommerce_gstin`` — the GSTIN of the e-commerce operator THROUGH which
the supply was made. Reported in GSTR-1 Table 14(a); the operator collects TCS u/s 52 and
files GSTR-8. A pure reporting tag — it does not change the invoice's own GST.

Revision ID: 0062_si_ecommerce_gstin
Revises: 0061_gst_2_0_rates
Create Date: 2026-07-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062_si_ecommerce_gstin"
down_revision: Union[str, None] = "0061_gst_2_0_rates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales_invoices", sa.Column("ecommerce_gstin", sa.String(length=15), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_invoices", "ecommerce_gstin")
