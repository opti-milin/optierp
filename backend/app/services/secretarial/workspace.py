"""Workspace statistics for the Secretarial module.

Shape matches ``services.module_workspace`` so the shared ModuleWorkspace page renders
it with no special-casing: number cards plus a 12-month trend.

The trend here is compliance load by month rather than a money figure — for a practice,
"how much is due in November" is the question the dashboard has to answer.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.secretarial import (
    SecretarialAppointment,
    SecretarialComplianceItem,
    SecretarialEntity,
    SecretarialPerson,
)
from app.models.secretarial.compliance import CLOSED_STATUSES
from app.services.secretarial import content as content_service
from app.services.secretarial import roster as roster_service
from app.services.secretarial.entity import get_settings

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _next_12_months(today: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = today.year, today.month
    for _ in range(12):
        months.append((year, month))
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return months


async def get_workspace(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    settings = await get_settings(db, company_id)

    entity_count = (
        await db.execute(
            select(func.count())
            .select_from(SecretarialEntity)
            .where(
                SecretarialEntity.company_id == company_id,
                SecretarialEntity.status == "active",
            )
        )
    ).scalar_one()

    director_count = (
        await db.execute(
            select(func.count(func.distinct(SecretarialAppointment.person_id))).where(
                SecretarialAppointment.company_id == company_id,
                SecretarialAppointment.ceased_on.is_(None),
                SecretarialAppointment.role_type.in_(("director", "designated_partner")),
            )
        )
    ).scalar_one()

    person_count = (
        await db.execute(
            select(func.count())
            .select_from(SecretarialPerson)
            .where(SecretarialPerson.company_id == company_id)
        )
    ).scalar_one()

    open_items = (
        await db.execute(
            select(func.count())
            .select_from(SecretarialComplianceItem)
            .where(
                SecretarialComplianceItem.company_id == company_id,
                SecretarialComplianceItem.status.notin_(CLOSED_STATUSES),
            )
        )
    ).scalar_one()

    overdue_items = (
        await db.execute(
            select(func.count())
            .select_from(SecretarialComplianceItem)
            .where(
                SecretarialComplianceItem.company_id == company_id,
                SecretarialComplianceItem.status.notin_(CLOSED_STATUSES),
                SecretarialComplianceItem.due_on < date.today(),
            )
        )
    ).scalar_one()

    # Due-count per month for the next year.
    months = _next_12_months(date.today())
    year_col = func.extract("year", SecretarialComplianceItem.due_on)
    month_col = func.extract("month", SecretarialComplianceItem.due_on)
    rows = (
        await db.execute(
            select(year_col.label("y"), month_col.label("m"), func.count().label("c"))
            .where(
                SecretarialComplianceItem.company_id == company_id,
                SecretarialComplianceItem.status.notin_(CLOSED_STATUSES),
                SecretarialComplianceItem.due_on >= date(months[0][0], months[0][1], 1),
            )
            .group_by(year_col, month_col)
        )
    ).all()
    bucket = {(int(r.y), int(r.m)): int(r.c) for r in rows}
    trend = [{"label": _MONTH_ABBR[m - 1], "value": bucket.get((y, m), 0)} for (y, m) in months]

    cards: list[dict[str, Any]] = []
    if settings.profile == "practice":
        client_states = await roster_service.counts_by_state(db, company_id)
        cards.append(
            {
                "label": "Active clients",
                "value": client_states.get("active", 0),
                "format": "int",
            }
        )
    cards.extend(
        [
            {"label": "Entities", "value": int(entity_count), "format": "int"},
            {"label": "Directors & partners", "value": int(director_count), "format": "int"},
            {"label": "People on record", "value": int(person_count), "format": "int"},
            {"label": "Open obligations", "value": int(open_items), "format": "int"},
            {"label": "Overdue", "value": int(overdue_items), "format": "int"},
        ]
    )

    # Surfacing the content-review backlog on the dashboard is deliberate: it keeps
    # "the engine works" visibly distinct from "the statutory text is signed off".
    review_counts = await content_service.review_status_counts(db, company_id)
    unpublished = sum(v for k, v in review_counts.items() if k != "published")
    if unpublished:
        cards.append(
            {"label": "Rules awaiting legal review", "value": unpublished, "format": "int"}
        )

    return {
        "profile": settings.profile,
        "entity_count": int(entity_count),
        "cards": cards,
        "chart_title": "Obligations due by month",
        "trend_format": "int",
        "trend": trend,
        "currency": "INR",
    }
