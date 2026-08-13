"""Drop legacy Income Tax tenant tables — Phase 10 clean break.

Law lives in schema ``statutory``; worksheets live in ``tax_computations*``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0092_drop_legacy_itr"
down_revision = "0091_tax_filings"
branch_labels = None
depends_on = None

_TABLES = (
    "income_tax_adjustment_lines",
    "income_tax_special_income_lines",
    "income_tax_computations",
    "tax_adjustment_rules",
    "tax_adjustment_rule_packs",
    "tax_adjustment_provisions",
    "tax_depreciation_blocks",
    "tax_policies",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))

    # Orphan SystemSetting blob from Phase 2 migration leftover.
    op.execute(
        sa.text("DELETE FROM system_settings WHERE key = 'income_tax_settings'")
    )


def downgrade() -> None:
    # Clean-break: legacy tables are not recreated. Re-seed from an older
    # revision if a rollback past 0087 is required.
    pass
