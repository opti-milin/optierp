"""The compliance calendar — generation, status, and the reminder sweep.

Generation is deliberately conservative. It **inserts** rows that do not exist and
refreshes the due date of rows nobody has touched; it never overwrites a status, an
assignee or an SRN. Re-running it after a rule's due date is corrected therefore fixes
the untouched rows and leaves work in progress alone, which is the only behaviour that
makes a "regenerate" button safe to press.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialComplianceItem,
    SecretarialComplianceReminder,
    SecretarialEntity,
    SecretarialSettings,
)
from app.models.secretarial.compliance import CLOSED_STATUSES, DEFAULT_REMINDER_OFFSETS
from app.schemas.secretarial import (
    ComplianceItemUpdate,
    GenerateCalendarIn,
    GenerateCalendarResult,
)
from app.services.audit import log_audit, serialize_document
from app.services.secretarial import content as content_service
from app.services.secretarial.common import (
    compute_due_date,
    fy_period,
    get_entity,
    is_applicable,
    parse_fy,
)

logger = get_logger(__name__)

_DOCTYPE = "Secretarial Compliance Item"

# Statuses the sweep is allowed to move automatically. Anything a human has picked
# up (in_progress, pending_review, filed…) is left alone.
_AUTO_STATUSES = ("upcoming", "due", "overdue")


async def generate_calendar(
    db: AsyncSession, payload: GenerateCalendarIn, user: CurrentUser
) -> GenerateCalendarResult:
    assert user.company_id is not None
    parse_fy(payload.fy)  # reject a malformed year before touching any entity

    if payload.entity_id:
        entities = [await get_entity(db, payload.entity_id, user.company_id)]
    else:
        entities = list(
            (
                await db.execute(
                    select(SecretarialEntity).where(
                        SecretarialEntity.company_id == user.company_id,
                        SecretarialEntity.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )

    rules = await content_service.published_rules(db, user.company_id)
    all_rules = await content_service.list_rules(db, user.company_id)
    unpublished = len([r for r in all_rules if r.review_status != "published"])

    created = refreshed = skipped = 0

    for entity in entities:
        period_start, period_end = fy_period(payload.fy, entity.fy_end_mmdd)
        is_first_fy = bool(
            entity.incorporated_on and period_start <= entity.incorporated_on <= period_end
        )

        existing = {
            row.rule_code: row
            for row in (
                (
                    await db.execute(
                        select(SecretarialComplianceItem).where(
                            SecretarialComplianceItem.company_id == user.company_id,
                            SecretarialComplianceItem.entity_id == entity.id,
                            SecretarialComplianceItem.fy == payload.fy,
                        )
                    )
                )
                .scalars()
                .all()
            )
        }

        for rule in rules:
            if not is_applicable(rule.applicability, entity):
                skipped += 1
                continue
            # An entity incorporated after the year closed has no obligations for it.
            if entity.incorporated_on and entity.incorporated_on > period_end:
                skipped += 1
                continue

            due_on = compute_due_date(
                rule.due_formula,
                period_start=period_start,
                period_end=period_end,
                is_first_fy=is_first_fy,
            )
            if due_on is None:
                # Event-based rules are created when the event is captured, not here.
                continue

            row = existing.get(rule.code)
            if row is None:
                db.add(
                    SecretarialComplianceItem(
                        company_id=user.company_id,
                        entity_id=entity.id,
                        rule_id=rule.id,
                        rule_code=rule.code,
                        title=rule.title,
                        form_code=rule.form_code,
                        act_section=" ".join(filter(None, [rule.act, rule.section])) or None,
                        fy=payload.fy,
                        period_start=period_start,
                        period_end=period_end,
                        due_on=due_on,
                        status="overdue" if due_on < date.today() else "upcoming",
                        owner=user.id,
                        modified_by=user.id,
                    )
                )
                created += 1
            elif row.status in _AUTO_STATUSES and row.due_on != due_on:
                # Only correct rows nobody has started working on.
                row.due_on = due_on
                row.rule_id = rule.id
                row.title = rule.title
                row.modified_by = user.id
                refreshed += 1

    await db.flush()
    await db.commit()

    if unpublished:
        logger.info(
            "secretarial_calendar_unpublished_rules_ignored",
            company_id=str(user.company_id),
            count=unpublished,
        )

    return GenerateCalendarResult(
        fy=payload.fy,
        entities_processed=len(entities),
        rules_evaluated=len(rules),
        items_created=created,
        items_refreshed=refreshed,
        items_skipped_not_applicable=skipped,
        unpublished_rules_ignored=unpublished,
    )


async def refresh_statuses(db: AsyncSession, company_id: uuid.UUID, *, on: date | None = None) -> int:
    """Move ``upcoming`` → ``due`` → ``overdue`` as dates pass. Touches nothing else."""
    today = on or date.today()
    rows = (
        (
            await db.execute(
                select(SecretarialComplianceItem).where(
                    SecretarialComplianceItem.company_id == company_id,
                    SecretarialComplianceItem.status.in_(_AUTO_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    changed = 0
    for row in rows:
        if row.due_on < today:
            target = "overdue"
        elif (row.due_on - today).days <= 30:
            target = "due"
        else:
            target = "upcoming"
        if row.status != target:
            row.status = target
            changed += 1
    if changed:
        await db.flush()
        await db.commit()
    return changed


async def list_items(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    fy: str | None = None,
    status: str | None = None,
    open_only: bool = False,
    due_before: date | None = None,
    assigned_to: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[tuple[SecretarialComplianceItem, str]], int]:
    stmt = (
        select(SecretarialComplianceItem, SecretarialEntity.entity_name)
        .join(SecretarialEntity, SecretarialEntity.id == SecretarialComplianceItem.entity_id)
        .where(SecretarialComplianceItem.company_id == company_id)
    )
    if entity_id:
        stmt = stmt.where(SecretarialComplianceItem.entity_id == entity_id)
    if fy:
        stmt = stmt.where(SecretarialComplianceItem.fy == fy)
    if status:
        stmt = stmt.where(SecretarialComplianceItem.status == status)
    if open_only:
        stmt = stmt.where(SecretarialComplianceItem.status.notin_(CLOSED_STATUSES))
    if due_before:
        stmt = stmt.where(SecretarialComplianceItem.due_on <= due_before)
    if assigned_to:
        stmt = stmt.where(SecretarialComplianceItem.assigned_to_user_id == assigned_to)

    stmt = stmt.order_by(SecretarialComplianceItem.due_on, SecretarialEntity.entity_name)
    total = (
        await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = (await db.execute(stmt.limit(page_size).offset((page - 1) * page_size))).all()
    return [(row[0], row[1]) for row in rows], int(total)


async def get_item(
    db: AsyncSession, item_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialComplianceItem:
    item = await db.get(SecretarialComplianceItem, item_id)
    if item is None or item.company_id != company_id:
        raise NotFoundError("Compliance item not found")
    return item


async def update_item(
    db: AsyncSession, item_id: uuid.UUID, payload: ComplianceItemUpdate, user: CurrentUser
) -> SecretarialComplianceItem:
    assert user.company_id is not None
    item = await get_item(db, item_id, user.company_id)
    data = payload.model_dump(exclude_unset=True)

    target_status = data.get("status", item.status)
    if target_status == "waived" and not (data.get("waived_reason") or item.waived_reason):
        raise ValidationError(
            "Waiving a statutory obligation needs a reason on the record",
            field="waived_reason",
        )
    if target_status in ("filed", "completed"):
        # Completion without evidence is how a calendar becomes fiction.
        if not (data.get("filed_on") or item.filed_on or data.get("completed_on") or item.completed_on):
            data.setdefault("completed_on", date.today())

    before = serialize_document(item)
    for field, value in data.items():
        setattr(item, field, value)
    item.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=item.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(item),
    )
    await db.commit()
    return item


async def summary(
    db: AsyncSession, company_id: uuid.UUID, *, entity_id: uuid.UUID | None = None
) -> dict[str, int]:
    stmt = select(SecretarialComplianceItem.status, func.count()).where(
        SecretarialComplianceItem.company_id == company_id
    )
    if entity_id:
        stmt = stmt.where(SecretarialComplianceItem.entity_id == entity_id)
    rows = (await db.execute(stmt.group_by(SecretarialComplianceItem.status))).all()
    return {status: int(count) for status, count in rows}


# --- Reminder dispatch ------------------------------------------------------------


async def due_reminders(
    db: AsyncSession, company_id: uuid.UUID, *, on: date | None = None
) -> list[tuple[SecretarialComplianceItem, int]]:
    """Items whose due date falls exactly on one of the configured lead offsets and
    which have not already had that reminder sent."""
    today = on or date.today()
    settings = await db.scalar(
        select(SecretarialSettings).where(SecretarialSettings.company_id == company_id)
    )
    offsets = list(settings.reminder_offsets or DEFAULT_REMINDER_OFFSETS) if settings else list(
        DEFAULT_REMINDER_OFFSETS
    )

    target_dates = {today + timedelta(days=offset): offset for offset in offsets}
    if not target_dates:
        return []

    rows = (
        (
            await db.execute(
                select(SecretarialComplianceItem).where(
                    SecretarialComplianceItem.company_id == company_id,
                    SecretarialComplianceItem.status.notin_(CLOSED_STATUSES),
                    SecretarialComplianceItem.due_on.in_(list(target_dates)),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    already = {
        (r.item_id, r.offset_days)
        for r in (
            (
                await db.execute(
                    select(SecretarialComplianceReminder).where(
                        SecretarialComplianceReminder.item_id.in_([r.id for r in rows])
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    return [
        (row, target_dates[row.due_on])
        for row in rows
        if (row.id, target_dates[row.due_on]) not in already
    ]


async def record_reminder(
    db: AsyncSession,
    item: SecretarialComplianceItem,
    offset_days: int,
    recipients: list[str],
    *,
    error: str | None = None,
) -> None:
    db.add(
        SecretarialComplianceReminder(
            company_id=item.company_id,
            item_id=item.id,
            offset_days=offset_days,
            sent_at=datetime.now(UTC),
            sent_to=recipients,
            status="failed" if error else "sent",
            error_message=error,
        )
    )
    await db.flush()
