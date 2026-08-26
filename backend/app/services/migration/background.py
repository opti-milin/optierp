"""Running an import off the request thread.

A real migration is tens of thousands of vouchers, each created through the full
service layer. That cannot happen inside an HTTP request: the client would hold
a connection open past any sane proxy timeout, and the only progress signal
would be a spinner.

So ``POST /run`` starts the work here and returns immediately. Three things make
that safe rather than merely asynchronous:

**The task owns its session.** A request-scoped session dies with the response.
The task opens its own from ``async_session_factory`` and arms the tenant GUC
itself, exactly as the scheduled jobs in ``app/jobs`` do — RLS is not something
a background task gets to skip.

**Progress lives in the database, not in memory.** The importer already writes
per-entity counters and an append-only log as it goes, so any process can answer
"how far along is it?" — including a different worker from the one running it.
Nothing about the status endpoint depends on this task being local.

**An abandoned run is recoverable.** If the process dies mid-import there is
nothing left to move the status off "Importing", and ``rollback_import`` refuses
that status — the import would be stuck forever, neither finishable nor
undoable. :func:`reap_stale_runs` retires those on a heartbeat timeout.

There is deliberately no broker here. What makes this portable to Celery or arq
later is the shape — a coroutine that takes ids, opens its own session and
reports through the database — not the transport. Swapping the transport is then
a change to this file alone.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.database import async_session_factory, set_company_context
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.models.migration import MigrationImport

logger = get_logger(__name__)

#: How long a run may go without a heartbeat before it is presumed dead. The
#: importer beats between entities and every batch of vouchers, so a healthy run
#: is never quiet for this long — but a single very slow document must not be
#: mistaken for a crash either.
STALE_AFTER = timedelta(minutes=15)

#: Strong references to in-flight tasks. asyncio only holds a weak reference to
#: a running task, so without this the garbage collector may cancel an import
#: mid-way. This is a liveness guard, never a source of truth — every status
#: answer comes from the database.
_running: set[asyncio.Task] = set()


def active_run_count() -> int:
    """In-flight runs in *this* process. For diagnostics, not for correctness."""
    return len(_running)


async def _execute(import_id: uuid.UUID, company_id: uuid.UUID, user: CurrentUser) -> None:
    """Run one import to completion in its own session and tenant context."""
    from app.services.migration import runner  # local: runner imports this module

    try:
        async with async_session_factory() as db:
            await set_company_context(db, company_id)
            session = await db.get(MigrationImport, import_id)
            if session is None or session.company_id != company_id:
                logger.warning("migration_run_vanished", import_id=str(import_id))
                return
            await runner.run_import(db, session, user)
            logger.info(
                "migration_run_finished", import_id=str(import_id), status=session.status
            )
    except Exception:  # noqa: BLE001 - nothing above us can report this
        # run_import already records phase failures on the session. This is the
        # backstop for anything that escaped it (a dead connection, say), so the
        # session cannot be left in "Importing" with no task behind it.
        logger.exception("migration_run_crashed", import_id=str(import_id))
        await _mark_failed(import_id, "The import stopped unexpectedly.")


async def _mark_failed(import_id: uuid.UUID, message: str) -> None:
    try:
        async with async_session_factory() as db:
            await db.execute(
                update(MigrationImport)
                .where(MigrationImport.id == import_id, MigrationImport.status == "Importing")
                .values(
                    status="Failed",
                    error_message=message,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - the reaper is the next line of defence
        logger.exception("migration_run_mark_failed_failed", import_id=str(import_id))


def start_run(import_id: uuid.UUID, company_id: uuid.UUID, user: CurrentUser) -> None:
    """Schedule an import run. Returns as soon as the task is created."""
    task = asyncio.create_task(
        _execute(import_id, company_id, user), name=f"migration-import-{import_id}"
    )
    _running.add(task)
    task.add_done_callback(_running.discard)


async def reap_stale_runs(*, stale_after: timedelta = STALE_AFTER) -> int:
    """Fail runs whose heartbeat stopped. Returns how many were retired.

    Runs on a schedule and once at startup — a process restart is exactly the
    event that strands a run, and the task that was executing it is gone.
    """
    cutoff = datetime.now(timezone.utc) - stale_after
    retired = 0
    async with async_session_factory() as db:
        stale = (
            await db.execute(
                select(MigrationImport).where(
                    MigrationImport.status == "Importing",
                    # A run with no heartbeat at all predates this mechanism.
                    MigrationImport.heartbeat_at.is_(None)
                    | (MigrationImport.heartbeat_at < cutoff),
                )
            )
        ).scalars().all()
        for session in stale:
            session.status = "Failed"
            session.finished_at = datetime.now(timezone.utc)
            session.error_message = (
                "This import stopped responding and was ended automatically — "
                "usually the server restarted mid-run. Documents already created "
                "were kept. Roll it back to undo them, or run it again to import "
                "what is left; records already imported will be skipped."
            )
            retired += 1
        if retired:
            await db.commit()
            logger.warning("migration_runs_reaped", count=retired)
    return retired
