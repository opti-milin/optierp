"""GST invoice completeness (Phase 1): HSN/SAC, gst_treatment, place of supply, reverse charge.

* ``items``: ``hsn_sac_code`` + ``gst_treatment`` (Taxable/Nil-Rated/Exempt/Non-GST).
* every ``InvoiceItemMixin`` line table: ``hsn_sac_code`` (snapshot from the item).
* ``sales_invoices`` / ``purchase_invoices``: ``place_of_supply`` + ``is_reverse_charge``.

Revision ID: 0059_gst_invoice_fields
Revises: 0058_disposal_sales_invoice
Create Date: 2026-06-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_gst_invoice_fields"
down_revision: Union[str, None] = "0058_disposal_sales_invoice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# hsn_sac_code lives on the shared ``InvoiceItemMixin``, so the ORM emits it for
# every table below — the migrated schema MUST add it to all six or reads/writes
# of those lines hit UndefinedColumn (mirrors 0024/0034's _LINE_TABLES rule).
_HSN_LINE_TABLES = (
    "quotation_items",
    "supplier_quotation_items",
    "purchase_order_items",
    "sales_order_items",
    "sales_invoice_items",
    "purchase_invoice_items",
)

# longest GST state label ("26-Dadra and Nagar Haveli and Daman and Diu") is 43 chars
_POS_LEN = 64


def upgrade() -> None:
    op.add_column("items", sa.Column("hsn_sac_code", sa.String(length=8), nullable=True))
    op.add_column(
        "items",
        sa.Column("gst_treatment", sa.String(length=20), server_default=sa.text("'Taxable'"), nullable=False),
    )
    for tbl in _HSN_LINE_TABLES:
        op.add_column(tbl, sa.Column("hsn_sac_code", sa.String(length=8), nullable=True))
    for tbl in ("sales_invoices", "purchase_invoices"):
        op.add_column(tbl, sa.Column("place_of_supply", sa.String(length=_POS_LEN), nullable=True))
        op.add_column(
            tbl,
            sa.Column("is_reverse_charge", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )


def downgrade() -> None:
    for tbl in ("sales_invoices", "purchase_invoices"):
        op.drop_column(tbl, "is_reverse_charge")
        op.drop_column(tbl, "place_of_supply")
    for tbl in _HSN_LINE_TABLES:
        op.drop_column(tbl, "hsn_sac_code")
    op.drop_column("items", "gst_treatment")
    op.drop_column("items", "hsn_sac_code")
