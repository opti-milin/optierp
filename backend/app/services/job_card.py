"""Job Card service — per Work Order × Operation shop-floor tracking (bespoke).

Job Cards are optional: created automatically when a Work Order with operations is
submitted. Time logs track start/stop; completed qty rolls up to the Work Order operation.
No stock/GL posting — optional Material Consumption is triggered from the Work Order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED
from app.models.manufacturing import JobCard, JobCardTimeLog, WorkOrder, WorkOrderOperation
from app.schemas.manufacturing import JobCardCompleteIn
from app.services.audit import log_audit
from app.services.manufacturing_common import MFG_NAMING_SERIES
from app.services.pagination import paginate

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")


async def get_job_card(
    db: AsyncSession, job_card_id: uuid.UUID, company_id: uuid.UUID | None
) -> JobCard:
    jc = await db.scalar(
        select(JobCard)
        .options(selectinload(JobCard.time_logs))
        .where(JobCard.id == job_card_id, JobCard.company_id == company_id)
    )
    if jc is None:
        raise NotFoundError("Job Card not found")
    return jc


async def list_job_cards(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int = 1,
    page_size: int = 20,
    work_order_id: uuid.UUID | None = None,
    status: str | None = None,
) -> tuple[list[JobCard], int]:
    stmt = (
        select(JobCard)
        .options(selectinload(JobCard.time_logs))
        .where(JobCard.company_id == company_id)
        .order_by(JobCard.creation.desc())
    )
    if work_order_id is not None:
        stmt = stmt.where(JobCard.work_order_id == work_order_id)
    if status is not None:
        stmt = stmt.where(JobCard.status == status)
    return await paginate(db, stmt, page, page_size)


async def create_job_card_for_operation(
    db: AsyncSession,
    wo: WorkOrder,
    op: WorkOrderOperation,
    user: CurrentUser,
    *,
    commit: bool = True,
) -> JobCard:
    """Create a draft Job Card for one WO operation (flush-only when commit=False)."""
    name = await get_next_name(db, MFG_NAMING_SERIES["Job Card"], wo.company_id)
    jc = JobCard(
        id=uuid.uuid4(),
        company_id=wo.company_id,
        name=name,
        work_order_id=wo.id,
        work_order_operation_id=op.id,
        operation_id=op.operation_id,
        workstation_id=op.workstation_id,
        for_quantity=wo.qty,
        total_completed_qty=ZERO,
        time_in_mins=ZERO,
        status="Open",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(jc)
    await db.flush()
    await log_audit(
        db, doctype="Job Card", document_id=jc.id, action="INSERT",
        user_id=user.id, company_id=wo.company_id,
    )
    if commit:
        await db.commit()
        return await get_job_card(db, jc.id, wo.company_id)
    return jc


def _open_time_log(jc: JobCard) -> JobCardTimeLog | None:
    for log in jc.time_logs:
        if log.to_time is None:
            return log
    return None


async def start_job_card(
    db: AsyncSession, job_card_id: uuid.UUID, user: CurrentUser
) -> JobCard:
    jc = await get_job_card(db, job_card_id, user.company_id)
    if jc.docstatus == DOCSTATUS_CANCELLED:
        raise ValidationError("Job Card is cancelled", code="ERR_DOCSTATUS")
    if jc.status == "Completed":
        raise ValidationError("Job Card is already completed", field="status")
    if _open_time_log(jc) is not None:
        raise ValidationError("A time log is already running — stop it first", field="time_logs")

    idx = len(jc.time_logs) + 1
    db.add(
        JobCardTimeLog(
            job_card_id=jc.id,
            idx=idx,
            from_time=datetime.now(UTC),
            to_time=None,
            time_in_mins=ZERO,
            completed_qty=ZERO,
        )
    )
    jc.status = "Work In Progress"
    if jc.docstatus == DOCSTATUS_DRAFT:
        jc.docstatus = DOCSTATUS_SUBMITTED
    jc.modified_by = user.id
    await _sync_wo_operation_status(db, jc, status="Work In Progress")
    await db.flush()
    await db.commit()
    return await get_job_card(db, jc.id, user.company_id)


async def complete_job_card(
    db: AsyncSession,
    job_card_id: uuid.UUID,
    payload: JobCardCompleteIn,
    user: CurrentUser,
) -> JobCard:
    """Stop the open time log (if any) and accrue completed qty."""
    jc = await get_job_card(db, job_card_id, user.company_id)
    if jc.docstatus == DOCSTATUS_CANCELLED:
        raise ValidationError("Job Card is cancelled", code="ERR_DOCSTATUS")
    if jc.status == "Completed":
        raise ValidationError("Job Card is already completed", field="status")

    now = payload.to_time or datetime.now(UTC)
    open_log = _open_time_log(jc)
    if open_log is not None:
        if now < open_log.from_time:
            raise ValidationError("to_time cannot be before from_time", field="to_time")
        mins = Decimal(str((now - open_log.from_time).total_seconds())) / Decimal("60")
        open_log.to_time = now
        open_log.time_in_mins = mins.quantize(Decimal("0.000001"))
        open_log.completed_qty = payload.completed_qty
        jc.time_in_mins = (jc.time_in_mins + open_log.time_in_mins).quantize(Decimal("0.000001"))
    elif payload.completed_qty > ZERO:
        # Record qty without an open timer.
        idx = len(jc.time_logs) + 1
        db.add(
            JobCardTimeLog(
                job_card_id=jc.id,
                idx=idx,
                from_time=now,
                to_time=now,
                time_in_mins=ZERO,
                completed_qty=payload.completed_qty,
            )
        )

    if payload.completed_qty > ZERO:
        remaining = jc.for_quantity - jc.total_completed_qty
        if payload.completed_qty > remaining + QTY_EPS:
            raise ValidationError(
                f"Cannot complete {payload.completed_qty}: only {remaining} left on this Job Card",
                field="completed_qty",
            )
        jc.total_completed_qty = (jc.total_completed_qty + payload.completed_qty).quantize(
            Decimal("0.000001")
        )

    if jc.total_completed_qty >= jc.for_quantity - QTY_EPS:
        jc.status = "Completed"
        await _sync_wo_operation_status(db, jc, status="Completed", completed=jc.total_completed_qty)
    else:
        jc.status = "Work In Progress" if jc.total_completed_qty > ZERO else "Open"
        await _sync_wo_operation_status(
            db, jc, status="Work In Progress", completed=jc.total_completed_qty
        )

    if jc.docstatus == DOCSTATUS_DRAFT:
        jc.docstatus = DOCSTATUS_SUBMITTED
    jc.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Job Card", document_id=jc.id, action="UPDATE",
        user_id=user.id, company_id=jc.company_id,
    )
    await db.commit()
    return await get_job_card(db, jc.id, user.company_id)


async def _sync_wo_operation_status(
    db: AsyncSession,
    jc: JobCard,
    *,
    status: str,
    completed: Decimal | None = None,
) -> None:
    op = await db.get(WorkOrderOperation, jc.work_order_operation_id)
    if op is None:
        return
    op.status = status
    if completed is not None:
        op.completed_qty = completed
    await db.flush()


async def cancel_job_card(
    db: AsyncSession, job_card_id: uuid.UUID, user: CurrentUser
) -> JobCard:
    jc = await get_job_card(db, job_card_id, user.company_id)
    if jc.docstatus == DOCSTATUS_CANCELLED:
        raise ValidationError("Job Card is already cancelled", code="ERR_DOCSTATUS")
    if jc.total_completed_qty > ZERO:
        raise ValidationError(
            "Cannot cancel: Job Card has completed quantity",
            code="ERR_DOCSTATUS",
        )
    if _open_time_log(jc) is not None:
        raise ValidationError("Stop the running time log before cancelling", field="time_logs")
    jc.docstatus = DOCSTATUS_CANCELLED
    jc.status = "Cancelled"
    jc.modified_by = user.id
    await _sync_wo_operation_status(db, jc, status="Pending", completed=ZERO)
    await db.flush()
    await log_audit(
        db, doctype="Job Card", document_id=jc.id, action="CANCEL",
        user_id=user.id, company_id=jc.company_id,
    )
    await db.commit()
    return await get_job_card(db, jc.id, user.company_id)
