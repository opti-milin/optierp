"""Module 12 — heartbeat for background import runs.

The run moved off the request thread, so nothing holds it open any more. If the
process dies mid-import the session would sit in "Importing" forever — and
rollback refuses that status, so the import could never be undone either.

The reaper (``app.jobs.tally_reaper``) uses this column to tell a run that is
still working from one that was abandoned.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0099_tally_import_heartbeat"
down_revision = "0098_tally_imported_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tally_imports",
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Any run already stuck in "Importing" from before this migration has no
    # heartbeat and no task behind it. Give it one so the reaper retires it on
    # its next pass rather than leaving it stuck forever.
    op.execute(
        "UPDATE tally_imports SET heartbeat_at = COALESCE(started_at, modified) "
        "WHERE status = 'Importing'"
    )


def downgrade() -> None:
    op.drop_column("tally_imports", "heartbeat_at")
