"""APScheduler setup + job registry (Section 4.6).

Jobs register themselves by appending to SCHEDULED_JOBS; later modules
(stock reorder, depreciation posting, SLA checks, ...) add entries as they
are migrated. Job functions are referenced by dotted path so the registry
stays import-light.
"""

import importlib
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger

logger = get_logger(__name__)

# trigger kwargs follow APScheduler cron/interval semantics
SCHEDULED_JOBS: list[dict[str, Any]] = [
    # Generate due Subscription invoices (idempotent; never double-bills a period).
    {"func": "app.jobs.subscription.process_subscriptions", "trigger": "cron", "hour": 2},
    # Post due asset depreciation (idempotent; never double-books a period).
    {"func": "app.jobs.assets.process_depreciation", "trigger": "cron", "hour": 3},
    # Advance-tax shortfall reminders (idempotent via tax_compliance_reminders).
    {"func": "app.jobs.tax_reminders.process_tax_reminders", "trigger": "cron", "hour": 7},
]

scheduler = AsyncIOScheduler()


def _resolve(dotted: str) -> Callable[..., Any]:
    module_path, func_name = dotted.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), func_name)


def start_scheduler() -> None:
    for job in SCHEDULED_JOBS:
        spec = dict(job)
        func = _resolve(spec.pop("func"))
        trigger = spec.pop("trigger")
        scheduler.add_job(func, trigger, id=job["func"], replace_existing=True, **spec)
        logger.info("job_registered", job=job["func"], trigger=trigger)
    scheduler.start()
    logger.info("scheduler_started", jobs=len(SCHEDULED_JOBS))


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
