"""Let a portal token be found before any tenant is known.

A director clicking a link has no session and therefore no ``app.company_id``. The
standard isolation policy compares ``company_id`` to that setting, so with no context it
matches nothing and the lookup fails — the token could never be resolved.

The fix is a policy that also matches when no tenant context is set at all. What makes
that safe is what the table stores: the raw token exists only in the emailed link, and
the column here is its SHA-256. Being able to read the row does not confer the ability
to use it, and finding the row requires already knowing the secret.

Authenticated sessions always carry a context, so they keep full isolation — this only
opens the case that has no tenant yet, which is precisely the portal.
"""

from __future__ import annotations

from alembic import op

revision = "0097_portal_token_lookup"
down_revision = "0096_secretarial_governance"
branch_labels = None
depends_on = None

_UNSCOPED = "NULLIF(current_setting('app.company_id', true), '') IS NULL"
_SCOPED = "company_id = NULLIF(current_setting('app.company_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON secretarial_portal_tokens")
    op.execute(
        "CREATE POLICY portal_token_lookup ON secretarial_portal_tokens "
        f"USING ({_SCOPED} OR {_UNSCOPED})"
    )

    # The event log is written during the same unscoped moment (recording that the link
    # was opened happens before anything else), so it needs the same treatment.
    op.execute("DROP POLICY IF EXISTS company_isolation ON secretarial_portal_events")
    op.execute(
        "CREATE POLICY portal_event_write ON secretarial_portal_events "
        f"USING ({_SCOPED} OR {_UNSCOPED}) WITH CHECK ({_SCOPED} OR {_UNSCOPED})"
    )


def downgrade() -> None:
    for table, policy in (
        ("secretarial_portal_tokens", "portal_token_lookup"),
        ("secretarial_portal_events", "portal_event_write"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY company_isolation ON {table} "
            f"USING ({_SCOPED})"
        )
