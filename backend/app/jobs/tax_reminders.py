"""Scheduled job: advance-tax compliance reminders (daily)."""

from datetime import date

from app.core.database import async_session_factory
from app.core.logging import get_logger
from app.services.taxation import calendar as calendar_service

logger = get_logger(__name__)


async def process_tax_reminders(*, on_date: date | None = None) -> int:
    """Email advance-tax shortfall reminders due within the lead window."""
    async with async_session_factory() as db:
        sent = await calendar_service.send_due_reminders(db, as_of=on_date)
        logger.info("tax_reminders_processed", sent=sent)
        return sent
