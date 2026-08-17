"""Nightly secretarial job: roll statuses, chase deadlines, refresh practice rosters.

Runs across every tenant, so it sets the RLS company context per tenant rather than
relying on a request's JWT. That is also what lets it do the one thing an on-demand
refresh cannot: read a delegated client's counts under the *client's* context, then
write them into the *firm's* roster row.

Idempotent by construction — a reminder is recorded against (item, offset) with a
unique constraint, so a second run on the same day sends nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, set_company_context
from app.core.logging import get_logger
from app.models.secretarial import (
    PracticeClientIndex,
    SecretarialComplianceItem,
    SecretarialEngagement,
    SecretarialEntity,
    SecretarialSettings,
)
from app.models.secretarial.compliance import CLOSED_STATUSES
from app.services.email import send_document_email
from app.services.secretarial import compliance as compliance_service

logger = get_logger(__name__)


async def _tenants_with_module(db: AsyncSession) -> list[uuid.UUID]:
    """Companies that have opened the Secretarial module at least once."""
    return list((await db.execute(select(SecretarialSettings.company_id))).scalars().all())


def _reminder_body(item: SecretarialComplianceItem, entity_name: str, days: int) -> str:
    when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    lines = [
        f"{entity_name} — {item.title}",
        "",
        f"Due {when}, on {item.due_on.strftime('%d %B %Y')}.",
    ]
    if item.form_code:
        lines.append(f"Form: {item.form_code}")
    if item.act_section:
        lines.append(f"Under: {item.act_section}")
    lines += ["", f"Financial year {item.fy}.", "", "— OptiReach Secretarial"]
    return "\n".join(lines)


async def _recipients(db: AsyncSession, item: SecretarialComplianceItem) -> list[str]:
    """Who hears about this deadline: the entity's own address, else the tenant's."""
    entity = await db.get(SecretarialEntity, item.entity_id)
    if entity is not None and entity.email:
        return [entity.email]
    return []


async def process_secretarial_reminders(*, on_date: date | None = None) -> int:
    """Send every due reminder across all tenants. Returns the number sent."""
    today = on_date or date.today()
    sent = 0

    async with async_session_factory() as db:
        tenants = await _tenants_with_module(db)

    for company_id in tenants:
        async with async_session_factory() as db:
            await set_company_context(db, company_id)
            try:
                await compliance_service.refresh_statuses(db, company_id, on=today)
                due = await compliance_service.due_reminders(db, company_id, on=today)
                for item, offset in due:
                    recipients = await _recipients(db, item)
                    if not recipients:
                        # No address on file — record the attempt so the gap is visible
                        # rather than silently skipped every night.
                        await compliance_service.record_reminder(
                            db, item, offset, [], error="No email address on the entity"
                        )
                        continue
                    entity = await db.get(SecretarialEntity, item.entity_id)
                    log = await send_document_email(
                        db,
                        company_id=company_id,
                        to=recipients,
                        subject=(
                            f"{item.title} due {item.due_on.strftime('%d %b %Y')} — "
                            f"{entity.entity_name if entity else ''}"
                        ).strip(" —"),
                        body=_reminder_body(item, entity.entity_name if entity else "", offset),
                        reference_doctype="Secretarial Compliance Item",
                        reference_id=item.id,
                    )
                    await compliance_service.record_reminder(
                        db, item, offset, recipients, error=log.error_message
                    )
                    if log.status == "Sent":
                        sent += 1
                await db.commit()
            except Exception:  # noqa: BLE001 — one tenant's failure must not stop the rest
                await db.rollback()
                logger.exception("secretarial_reminders_failed", company_id=str(company_id))

    logger.info("secretarial_reminders_processed", sent=sent, tenants=len(tenants))
    return sent


async def refresh_practice_rosters(*, on_date: date | None = None) -> int:
    """Fill in delegated clients' counts, which cross a tenant boundary.

    Reads under the client's context, writes under the firm's — the only place in the
    module that touches two tenants, and the reason it lives in a job rather than a
    request handler.
    """
    today = on_date or date.today()
    updated = 0

    async with async_session_factory() as db:
        engagements = list(
            (
                await db.execute(
                    select(SecretarialEngagement).where(
                        SecretarialEngagement.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )

    for engagement in engagements:
        # 1. Read the client's numbers under the client's context.
        async with async_session_factory() as db:
            await set_company_context(db, engagement.client_company_id)
            from sqlalchemy import func

            row = (
                await db.execute(
                    select(
                        func.min(SecretarialComplianceItem.due_on),
                        func.count(),
                        func.count().filter(SecretarialComplianceItem.due_on < today),
                    ).where(
                        SecretarialComplianceItem.company_id == engagement.client_company_id,
                        SecretarialComplianceItem.entity_id == engagement.entity_id,
                        SecretarialComplianceItem.status.notin_(CLOSED_STATUSES),
                    )
                )
            ).one()
            next_due, open_count, overdue = row[0], int(row[1] or 0), int(row[2] or 0)

        # 2. Write them into the firm's roster under the firm's context.
        async with async_session_factory() as db:
            await set_company_context(db, engagement.firm_company_id)
            client_row = await db.scalar(
                select(PracticeClientIndex).where(
                    PracticeClientIndex.company_id == engagement.firm_company_id,
                    PracticeClientIndex.entity_id == engagement.entity_id,
                )
            )
            if client_row is None:
                continue
            client_row.next_due_on = next_due
            client_row.open_item_count = open_count
            client_row.overdue_count = overdue
            client_row.refreshed_at = datetime.now(UTC)
            await db.commit()
            updated += 1

    logger.info("secretarial_rosters_refreshed", updated=updated)
    return updated


async def run_nightly(*, on_date: date | None = None) -> dict[str, int]:
    """Entry point registered with the scheduler."""
    sent = await process_secretarial_reminders(on_date=on_date)
    refreshed = await refresh_practice_rosters(on_date=on_date)
    return {"reminders_sent": sent, "rosters_refreshed": refreshed}
