"""Subcontract Job service — send materials to a vendor, receive FG + service cost.

Lean Phase-4 design (docs/MANUFACTURING_GAP_AND_PLAN.md §5.1): one job document, one
Send-to-Subcontractor Stock Entry, one Subcontract Receipt (Manufacture-like cost pool).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.buying import Supplier
from app.models.manufacturing import BOM, SubcontractJob, SubcontractJobItem
from app.models.stock import StockEntry, StockEntryItem
from app.schemas.manufacturing import (
    SubcontractJobCreate,
    SubcontractJobReceiveIn,
    SubcontractJobSendIn,
)
from app.services import stock_entry as se_service
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.bom_explosion import explode_bom
from app.services.manufacturing_common import (
    MFG_NAMING_SERIES,
    ZERO,
    get_manufacturing_settings,
    require_expense_account,
)
from app.services.pagination import paginate
from app.services.stock_common import STOCK_NAMING_SERIES, get_item, get_items, get_warehouse

QTY_EPS = Decimal("0.000001")


async def get_subcontract_job(
    db: AsyncSession, job_id: uuid.UUID, company_id: uuid.UUID | None
) -> SubcontractJob:
    stmt = (
        select(SubcontractJob)
        .options(selectinload(SubcontractJob.items))
        .where(SubcontractJob.id == job_id)
    )
    if company_id is not None:
        stmt = stmt.where(SubcontractJob.company_id == company_id)
    job = await db.scalar(stmt)
    if job is None:
        raise NotFoundError("Subcontract Job not found")
    return job


async def list_subcontract_jobs(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int,
    page_size: int,
    *,
    status: str | None = None,
) -> tuple[list[SubcontractJob], int]:
    stmt = (
        select(SubcontractJob)
        .where(SubcontractJob.company_id == company_id)
        .order_by(SubcontractJob.creation.desc())
    )
    if status:
        stmt = stmt.where(SubcontractJob.status == status)
    return await paginate(db, stmt, page, page_size)


def _recompute_status(job: SubcontractJob) -> None:
    if job.received_qty >= job.qty - QTY_EPS:
        job.status = "Completed"
    elif job.received_qty > ZERO:
        job.status = "Partially Received"
    elif job.sent_qty > ZERO:
        job.status = "Materials Sent"
    else:
        job.status = "Open"


async def create_subcontract_job(
    db: AsyncSession, payload: SubcontractJobCreate, user: CurrentUser
) -> SubcontractJob:
    company = await get_company(db, user.company_id)
    supplier = await db.scalar(
        select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.company_id == company.id)
    )
    if supplier is None:
        raise NotFoundError("Supplier not found")
    if supplier.on_hold:
        raise ValidationError("Supplier is on hold", field="supplier_id")

    bom = await db.scalar(
        select(BOM)
        .options(selectinload(BOM.items))
        .where(BOM.id == payload.bom_id, BOM.company_id == company.id)
    )
    if bom is None:
        raise NotFoundError("BOM not found")
    if bom.docstatus != DOCSTATUS_SUBMITTED or not bom.is_active:
        raise ValidationError("The BOM must be submitted and active", field="bom_id")
    if not bom.items:
        raise ValidationError("The BOM has no components", field="bom_id")

    production_item = await get_item(db, bom.production_item_id, company.id)
    settings = await get_manufacturing_settings(db, company.id)

    def _default_wh(key: str) -> uuid.UUID | None:
        raw = settings.get(key)
        return uuid.UUID(raw) if raw else None

    source_id = payload.source_warehouse_id or _default_wh("default_source_warehouse_id")
    fg_id = payload.fg_warehouse_id or _default_wh("default_fg_warehouse_id")
    if source_id is None:
        raise ValidationError(
            "A source warehouse is required — set one on the job or in Manufacturing Settings",
            field="source_warehouse_id",
        )
    if fg_id is None:
        raise ValidationError(
            "A finished-goods warehouse is required — set one on the job or in Manufacturing Settings",
            field="fg_warehouse_id",
        )
    await get_warehouse(db, source_id, company.id)
    await get_warehouse(db, payload.supplier_warehouse_id, company.id)
    await get_warehouse(db, fg_id, company.id)
    if payload.supplier_warehouse_id == source_id:
        raise ValidationError(
            "Supplier warehouse must differ from the source warehouse",
            field="supplier_warehouse_id",
        )

    if payload.service_cost_account_id is not None:
        await require_expense_account(db, payload.service_cost_account_id, company.id)

    exploded = await explode_bom(db, bom, payload.qty, flatten_all=False)
    if not exploded:
        raise ValidationError("The BOM explosion produced no required components", field="bom_id")
    await get_items(db, {c.item_id for c in exploded}, company.id)

    name = await get_next_name(db, MFG_NAMING_SERIES["Subcontract Job"], company.id)
    job = SubcontractJob(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        supplier_id=supplier.id,
        production_item_id=production_item.id,
        bom_id=bom.id,
        qty=payload.qty,
        source_warehouse_id=source_id,
        supplier_warehouse_id=payload.supplier_warehouse_id,
        fg_warehouse_id=fg_id,
        service_cost=payload.service_cost,
        service_cost_account_id=payload.service_cost_account_id,
        status="Draft",
        posting_date=payload.posting_date,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(job)
    await db.flush()

    for idx, comp in enumerate(exploded, start=1):
        db.add(
            SubcontractJobItem(
                subcontract_job_id=job.id,
                idx=idx,
                item_id=comp.item_id,
                required_qty=comp.stock_qty,
                source_warehouse_id=comp.source_warehouse_id or source_id,
                rate=comp.rate,
                amount=(comp.stock_qty * comp.rate).quantize(Decimal("0.000001")),
            )
        )
    await db.flush()
    await log_audit(
        db,
        doctype="Subcontract Job",
        document_id=job.id,
        action="INSERT",
        user_id=user.id,
        company_id=company.id,
    )
    await db.commit()
    return await get_subcontract_job(db, job.id, company.id)


async def submit_subcontract_job(
    db: AsyncSession, job_id: uuid.UUID, user: CurrentUser
) -> SubcontractJob:
    job = await get_subcontract_job(db, job_id, user.company_id)
    require_draft(job.docstatus)
    if not job.items:
        raise ValidationError("Subcontract Job has no components", field="items")
    job.docstatus = DOCSTATUS_SUBMITTED
    job.status = "Open"
    job.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Subcontract Job",
        document_id=job.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=job.company_id,
    )
    await db.commit()
    return await get_subcontract_job(db, job.id, user.company_id)


async def cancel_subcontract_job(
    db: AsyncSession, job_id: uuid.UUID, user: CurrentUser
) -> SubcontractJob:
    job = await get_subcontract_job(db, job_id, user.company_id)
    require_submitted(job.docstatus)
    if job.sent_qty > ZERO or job.received_qty > ZERO:
        raise ValidationError(
            "Cancel linked Stock Entries (Send / Receipt) before cancelling this job",
            code="ERR_DOCSTATUS",
        )
    job.docstatus = DOCSTATUS_CANCELLED
    job.status = "Cancelled"
    job.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Subcontract Job",
        document_id=job.id,
        action="CANCEL",
        user_id=user.id,
        company_id=job.company_id,
    )
    await db.commit()
    return await get_subcontract_job(db, job.id, user.company_id)


async def send_to_subcontractor(
    db: AsyncSession, job_id: uuid.UUID, payload: SubcontractJobSendIn, user: CurrentUser
) -> tuple[SubcontractJob, StockEntry]:
    """Transfer raws for ``payload.qty`` FG units from source → supplier warehouse."""
    job = await get_subcontract_job(db, job_id, user.company_id)
    require_submitted(job.docstatus)
    remaining = job.qty - job.sent_qty
    if payload.qty > remaining + QTY_EPS:
        raise ValidationError(
            f"Cannot send materials for {payload.qty}: only {remaining} left to send",
            field="qty",
        )
    company = await get_company(db, job.company_id)
    scale = payload.qty / job.qty

    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Entry"], company.id)
    entry = StockEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose="Send to Subcontractor",
        subcontract_job_id=job.id,
        from_warehouse_id=job.source_warehouse_id,
        to_warehouse_id=job.supplier_warehouse_id,
        remarks=f"Send materials for Subcontract Job {job.name}",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()

    sent_map: dict[uuid.UUID, Decimal] = {}
    idx = 0
    for row in job.items:
        send_qty = (row.required_qty * scale).quantize(Decimal("0.000001"))
        pending = max(ZERO, row.required_qty - row.sent_qty)
        send_qty = min(send_qty, pending)
        if send_qty <= ZERO:
            continue
        source_id = row.source_warehouse_id or job.source_warehouse_id
        await get_warehouse(db, source_id, company.id)
        idx += 1
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id,
                idx=idx,
                item_id=row.item_id,
                source_warehouse_id=source_id,
                target_warehouse_id=job.supplier_warehouse_id,
                qty=send_qty,
                uom=None,
                conversion_factor=Decimal("1"),
                stock_qty=send_qty,
                basic_rate=row.rate,
                amount=(send_qty * row.rate).quantize(Decimal("0.000001")),
            )
        )
        sent_map[row.item_id] = send_qty

    if not sent_map:
        raise ValidationError("Nothing left to send for this Subcontract Job", field="qty")

    await db.flush()
    await db.commit()
    await se_service.submit_stock_entry(db, entry.id, user)

    job = await get_subcontract_job(db, job.id, user.company_id)
    job.sent_qty = (job.sent_qty + payload.qty).quantize(Decimal("0.000001"))
    for row in job.items:
        if row.item_id in sent_map:
            row.sent_qty = (row.sent_qty + sent_map[row.item_id]).quantize(Decimal("0.000001"))
    _recompute_status(job)
    job.modified_by = user.id
    await db.flush()
    await db.commit()
    job = await get_subcontract_job(db, job.id, user.company_id)
    entry = await se_service.get_stock_entry(db, entry.id, company.id)
    return job, entry


async def receive_from_subcontractor(
    db: AsyncSession, job_id: uuid.UUID, payload: SubcontractJobReceiveIn, user: CurrentUser
) -> tuple[SubcontractJob, StockEntry]:
    """Consume sent materials at the supplier warehouse and receive FG (+ service cost)."""
    job = await get_subcontract_job(db, job_id, user.company_id)
    require_submitted(job.docstatus)
    if job.sent_qty <= ZERO:
        raise ValidationError(
            "Send materials to the subcontractor before receiving",
            field="sent_qty",
        )
    remaining_recv = job.qty - job.received_qty
    if payload.qty > remaining_recv + QTY_EPS:
        raise ValidationError(
            f"Cannot receive {payload.qty}: only {remaining_recv} left on this job",
            field="qty",
        )
    remaining_sent = job.sent_qty - job.received_qty
    if payload.qty > remaining_sent + QTY_EPS:
        raise ValidationError(
            f"Cannot receive {payload.qty}: only materials for {remaining_sent} have been sent",
            field="qty",
        )

    production_item = await get_item(db, job.production_item_id, job.company_id)
    from app.services import quality_inspection as qi_service

    await qi_service.require_accepted_inspection(
        db,
        company_id=job.company_id,
        item=production_item,
        reference_type="Subcontract Job",
        reference_id=job.id,
        qty=payload.qty,
        already_done=job.received_qty,
    )

    company = await get_company(db, job.company_id)
    if payload.service_cost_account_id is not None:
        await require_expense_account(db, payload.service_cost_account_id, company.id)
    op_account = payload.service_cost_account_id or job.service_cost_account_id
    scale = payload.qty / job.qty
    service_now = (job.service_cost * scale).quantize(Decimal("0.000001"))

    bom = await db.scalar(select(BOM).where(BOM.id == job.bom_id, BOM.company_id == company.id))
    if bom is None:
        raise NotFoundError("BOM not found")

    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Entry"], company.id)
    entry = StockEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose="Subcontract Receipt",
        subcontract_job_id=job.id,
        operating_cost=service_now,
        operating_cost_account_id=op_account,
        remarks=f"Subcontract receipt for {job.name}",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()

    consumed_map: dict[uuid.UUID, Decimal] = {}
    idx = 0
    for row in job.items:
        batch_need = (row.required_qty * scale).quantize(Decimal("0.000001"))
        pending_sent = max(ZERO, row.sent_qty - row.consumed_qty)
        consume_qty = min(batch_need, pending_sent)
        if consume_qty <= ZERO:
            continue
        idx += 1
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id,
                idx=idx,
                item_id=row.item_id,
                source_warehouse_id=job.supplier_warehouse_id,
                target_warehouse_id=None,
                qty=consume_qty,
                uom=None,
                conversion_factor=Decimal("1"),
                stock_qty=consume_qty,
                basic_rate=row.rate,
                amount=(consume_qty * row.rate).quantize(Decimal("0.000001")),
            )
        )
        consumed_map[row.item_id] = consume_qty

    idx += 1
    estimate = bom.cost_per_unit if bom.cost_per_unit > ZERO else Decimal("0.000001")
    db.add(
        StockEntryItem(
            stock_entry_id=entry.id,
            idx=idx,
            item_id=job.production_item_id,
            source_warehouse_id=None,
            target_warehouse_id=job.fg_warehouse_id,
            qty=payload.qty,
            uom=None,
            conversion_factor=Decimal("1"),
            stock_qty=payload.qty,
            basic_rate=estimate,
            amount=(payload.qty * estimate).quantize(Decimal("0.000001")),
        )
    )

    await db.flush()
    await db.commit()
    await se_service.submit_stock_entry(db, entry.id, user)

    job = await get_subcontract_job(db, job.id, user.company_id)
    job.received_qty = (job.received_qty + payload.qty).quantize(Decimal("0.000001"))
    for row in job.items:
        if row.item_id in consumed_map:
            row.consumed_qty = (row.consumed_qty + consumed_map[row.item_id]).quantize(
                Decimal("0.000001")
            )
    _recompute_status(job)
    job.modified_by = user.id
    await db.flush()
    await db.commit()
    job = await get_subcontract_job(db, job.id, user.company_id)
    entry = await se_service.get_stock_entry(db, entry.id, company.id)
    return job, entry


async def revert_send_entry(db: AsyncSession, entry: StockEntry, user: CurrentUser) -> None:
    job = await db.scalar(
        select(SubcontractJob)
        .options(selectinload(SubcontractJob.items))
        .where(SubcontractJob.id == entry.subcontract_job_id)
    )
    if job is None:
        return
    sent_by_item: dict[uuid.UUID, Decimal] = {}
    for r in entry.items:
        if r.source_warehouse_id is not None and r.target_warehouse_id is not None:
            sent_by_item[r.item_id] = sent_by_item.get(r.item_id, ZERO) + r.stock_qty
    if not sent_by_item or not job.items:
        return
    sample = job.items[0]
    per_unit = (sample.required_qty / job.qty) if job.qty else ZERO
    fg_qty = ZERO
    if per_unit > ZERO and sample.item_id in sent_by_item:
        fg_qty = (sent_by_item[sample.item_id] / per_unit).quantize(Decimal("0.000001"))
    job.sent_qty = max(ZERO, job.sent_qty - fg_qty).quantize(Decimal("0.000001"))
    for row in job.items:
        if row.item_id in sent_by_item:
            row.sent_qty = max(ZERO, row.sent_qty - sent_by_item[row.item_id]).quantize(
                Decimal("0.000001")
            )
    _recompute_status(job)
    job.modified_by = user.id
    await db.flush()


async def revert_receipt_entry(db: AsyncSession, entry: StockEntry, user: CurrentUser) -> None:
    job = await db.scalar(
        select(SubcontractJob)
        .options(selectinload(SubcontractJob.items))
        .where(SubcontractJob.id == entry.subcontract_job_id)
    )
    if job is None:
        return
    produced = sum(
        (
            r.stock_qty
            for r in entry.items
            if r.target_warehouse_id is not None
            and r.source_warehouse_id is None
            and r.item_id == job.production_item_id
        ),
        ZERO,
    )
    consumed_by_item: dict[uuid.UUID, Decimal] = {}
    for r in entry.items:
        if r.source_warehouse_id is not None and r.target_warehouse_id is None:
            consumed_by_item[r.item_id] = consumed_by_item.get(r.item_id, ZERO) + r.stock_qty
    job.received_qty = max(ZERO, job.received_qty - produced).quantize(Decimal("0.000001"))
    for row in job.items:
        if row.item_id in consumed_by_item:
            row.consumed_qty = max(ZERO, row.consumed_qty - consumed_by_item[row.item_id]).quantize(
                Decimal("0.000001")
            )
    _recompute_status(job)
    job.modified_by = user.id
    await db.flush()
