"""Scheduled job: retire abandoned Tally import runs.

An import runs in a background task. If the process dies mid-run there is
nothing left to move the session off "Importing" — and ``rollback_import``
refuses that status, so the import would be stuck: not finishable, not
undoable. This sweeps those up on a heartbeat timeout.

Idempotent: a run that is still beating is never touched, and one already
retired is no longer "Importing" so it is not seen again.
"""

from app.core.logging import get_logger
from app.services.migration.background import reap_stale_runs

logger = get_logger(__name__)


async def run_reaper() -> int:
    """Fail any Tally import whose heartbeat has stopped."""
    retired = await reap_stale_runs()
    if retired:
        logger.info("migration_reaper_retired", count=retired)
    return retired
