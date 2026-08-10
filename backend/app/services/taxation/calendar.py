"""Advance-tax calendar, interest preview, compliance reminders — Phase 7."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.notifications import notify_from_template, send_email
from app.models import statutory as stat
from app.models.core import Company, User, UserRole
from app.models.tax_computation import TaxComputation
from app.models.tax_interest import TaxComplianceReminder
from app.schemas.taxation import (
    AdvanceTaxCalendarOut,
    AdvanceTaxInstalmentOut,
    Interest234PreviewOut,
    Interest234PreviewRequest,
    TaxComplianceReminderOut,
)
from app.services.taxation import interest as interest_service
from app.services.taxation.catalogue import accessors as cat
from app.services.taxation.kernel.interest_234 import Interest234Input, compute_interest_234
from app.services.taxation.kernel.money import ZERO, money, q

logger = get_logger(__name__)

REMINDER_LEAD_DAYS = 7
TEMPLATE_NAME = "tax_advance_instalment_reminder"


async def get_advance_tax_calendar(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    ay_code: str,
    estimated_tax: Decimal | None = None,
    computation_id: uuid.UUID | None = None,
) -> AdvanceTaxCalendarOut:
    projections, net = await interest_service.advance_tax_calendar(
        db,
        company_id,
        ay_code=ay_code,
        estimated_tax=estimated_tax,
        computation_id=computation_id,
    )
    return AdvanceTaxCalendarOut(
        ay_code=ay_code,
        estimated_tax_net=net,
        instalments=[
            AdvanceTaxInstalmentOut(
                seq=p.seq,
                code=p.code,
                label=p.label,
                due_date=p.due_date,
                cumulative_percent=p.cumulative_percent,
                required_cumulative=p.required_cumulative,
                paid_to_date=p.paid_to_date,
                shortfall=p.shortfall,
                suggested_payment=p.suggested_payment,
                status=p.status,
            )
            for p in projections
        ],
    )


async def preview_interest(
    db: AsyncSession,
    company_id: uuid.UUID,
    payload: Interest234PreviewRequest,
) -> Interest234PreviewOut:
    """Standalone interest preview (uses real challans for the AY)."""
    if payload.computation_id is not None:
        doc = await db.get(TaxComputation, payload.computation_id)
        if doc is None or doc.company_id != company_id:
            raise NotFoundError("Tax computation not found")
        doc.return_filed_date = payload.return_filed_date or doc.return_filed_date
        doc.audit_applicable = payload.audit_applicable
        doc.itr_due_date_override = (
            payload.itr_due_date_override or doc.itr_due_date_override
        )
        result = await interest_service.compute_for_computation(
            db,
            doc,
            assessed_tax=payload.assessed_tax,
            as_of_date=payload.as_of_date,
        )
    else:
        due_rules = await cat.list_due_date_rules(db, ay_code=payload.ay_code)
        interest_rules = await interest_service.list_interest_rules(
            db, ay_code=payload.ay_code
        )
        rates = {r.section_code: money(r.rate_percent_per_month) for r in interest_rules}
        instalments = interest_service.resolve_instalment_dates(
            due_rules, ay_code=payload.ay_code
        )
        itr_due = interest_service.resolve_itr_due_date(
            due_rules,
            ay_code=payload.ay_code,
            assessee_class_code="Company",
            audit_applicable=payload.audit_applicable,
            override=payload.itr_due_date_override,
        )
        payments = await interest_service.load_advance_challans(
            db, company_id, payload.ay_code
        )
        ay = await db.scalar(
            select(stat.AssessmentYear).where(stat.AssessmentYear.code == payload.ay_code)
        )
        result = compute_interest_234(
            Interest234Input(
                assessed_tax=payload.assessed_tax,
                advance_payments=tuple(payments),
                instalments=tuple(instalments),
                itr_due_date=itr_due,
                return_filed_date=payload.return_filed_date,
                as_of_date=payload.as_of_date or date.today(),
                rate_234a_percent=rates.get("234A", Decimal("1")),
                rate_234b_percent=rates.get("234B", Decimal("1")),
                rate_234c_percent=rates.get("234C", Decimal("1")),
                ay_start=ay.ay_start if ay else None,
            )
        )
    return Interest234PreviewOut(
        interest_234a=result.interest_234a,
        interest_234b=result.interest_234b,
        interest_234c=result.interest_234c,
        total_interest=result.total_interest,
        assessed_tax=result.assessed_tax,
        tax_after_tds=result.tax_after_tds,
        advance_tax_paid=result.advance_tax_paid,
        breakdown=dict(result.breakdown),
    )


async def list_reminders(
    db: AsyncSession, company_id: uuid.UUID, *, ay_code: str | None = None
) -> list[TaxComplianceReminder]:
    stmt = (
        select(TaxComplianceReminder)
        .where(TaxComplianceReminder.company_id == company_id)
        .order_by(TaxComplianceReminder.due_date.desc())
    )
    if ay_code:
        stmt = stmt.where(TaxComplianceReminder.ay_code == ay_code)
    return list((await db.scalars(stmt)).all())


def reminder_out(row: TaxComplianceReminder) -> TaxComplianceReminderOut:
    return TaxComplianceReminderOut.model_validate(row)


async def _company_recipient_emails(
    db: AsyncSession, company_id: uuid.UUID
) -> list[str]:
    user_ids = list(
        (
            await db.scalars(
                select(UserRole.user_id).where(UserRole.company_id == company_id)
            )
        ).all()
    )
    if not user_ids:
        return []
    users = list(
        (
            await db.scalars(
                select(User).where(User.id.in_(user_ids), User.is_active.is_(True))
            )
        ).all()
    )
    return [u.email for u in users if u.email]


async def send_due_reminders(
    db: AsyncSession,
    *,
    company_id: uuid.UUID | None = None,
    as_of: date | None = None,
    lead_days: int = REMINDER_LEAD_DAYS,
) -> int:
    """Send advance-tax reminders for instalments due within ``lead_days``.

    Idempotent via unique (company, ay, rule_code, due_date) on the reminder log.
    Returns number of reminders newly recorded.
    """
    today = as_of or date.today()
    window_end = today + timedelta(days=lead_days)
    sent = 0

    company_stmt = select(Company)
    if company_id is not None:
        company_stmt = company_stmt.where(Company.id == company_id)
    companies = list((await db.scalars(company_stmt)).all())
    ays = list((await db.scalars(select(stat.AssessmentYear))).all())

    for company in companies:
        recipients = await _company_recipient_emails(db, company.id)
        for ay in ays:
            calendar = await get_advance_tax_calendar(db, company.id, ay_code=ay.code)
            for inst in calendar.instalments:
                if not (today <= inst.due_date <= window_end):
                    continue
                if inst.shortfall <= ZERO:
                    continue
                existing = await db.scalar(
                    select(TaxComplianceReminder).where(
                        TaxComplianceReminder.company_id == company.id,
                        TaxComplianceReminder.ay_code == ay.code,
                        TaxComplianceReminder.rule_code == inst.code,
                        TaxComplianceReminder.due_date == inst.due_date,
                    )
                )
                if existing is not None:
                    continue

                ctx = {
                    "company_name": company.company_name,
                    "ay_code": ay.code,
                    "label": inst.label,
                    "due_date": inst.due_date.isoformat(),
                    "required": format(inst.required_cumulative, "f"),
                    "paid": format(inst.paid_to_date, "f"),
                    "shortfall": format(inst.shortfall, "f"),
                }
                err: str | None = None
                status = "Sent"
                if recipients:
                    try:
                        err = await notify_from_template(
                            db,
                            TEMPLATE_NAME,
                            ctx,
                            recipients,
                            reference_doctype="TaxComplianceReminder",
                        )
                        if err:
                            err = await send_email(
                                recipients,
                                subject=(
                                    f"Advance tax due {inst.due_date} — {company.company_name}"
                                ),
                                body=(
                                    f"Instalment {inst.label} for AY {ay.code} "
                                    f"due {inst.due_date}.\n"
                                    f"Suggested payment: {inst.shortfall}"
                                ),
                            )
                        status = "Sent" if err is None else "Failed"
                    except Exception as exc:  # noqa: BLE001
                        err = str(exc)
                        status = "Failed"
                        logger.exception(
                            "tax_reminder_failed",
                            company_id=str(company.id),
                            rule=inst.code,
                        )
                else:
                    status = "Skipped"
                    err = "no recipients"

                db.add(
                    TaxComplianceReminder(
                        company_id=company.id,
                        ay_code=ay.code,
                        rule_code=inst.code,
                        rule_kind="AdvanceTax",
                        due_date=inst.due_date,
                        sent_at=datetime.now(UTC),
                        channel="email",
                        status=status,
                        shortfall_amount=q(money(inst.shortfall)),
                        recipients=recipients,
                        payload=ctx,
                        error=err[:500] if err else None,
                    )
                )
                sent += 1

    await db.commit()
    return sent
