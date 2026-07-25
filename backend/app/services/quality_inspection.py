"""Quality Inspection service — Accepted/Rejected gate for manufacturing finishes."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.manufacturing import SubcontractJob, WorkOrder
from app.models.quality import (
    QUALITY_INSPECTION_REFERENCE_TYPES,
    QualityInspection,
)
from app.models.stock import Item
from app.schemas.quality import QualityInspectionCreate
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.stock_common import get_item

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")
QI_NAMING_SERIES = "QI-.YYYY.-"


async def get_quality_inspection(
    db: AsyncSession, qi_id: uuid.UUID, company_id: uuid.UUID | None
) -> QualityInspection:
    stmt = select(QualityInspection).where(QualityInspection.id == qi_id)
    if company_id is not None:
        stmt = stmt.where(QualityInspection.company_id == company_id)
    qi = await db.scalar(stmt)
    if qi is None:
        raise NotFoundError("Quality Inspection not found")
    return qi


async def list_quality_inspections(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int,
    page_size: int,
    *,
    status: str | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
) -> tuple[list[QualityInspection], int]:
    stmt = (
        select(QualityInspection)
        .where(QualityInspection.company_id == company_id)
        .order_by(QualityInspection.creation.desc())
    )
    if status:
        stmt = stmt.where(QualityInspection.status == status)
    if reference_type:
        stmt = stmt.where(QualityInspection.reference_type == reference_type)
    if reference_id is not None:
        stmt = stmt.where(QualityInspection.reference_id == reference_id)
    return await paginate(db, stmt, page, page_size)


async def _resolve_reference(
    db: AsyncSession,
    company_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    item_id: uuid.UUID,
) -> str:
    if reference_type not in QUALITY_INSPECTION_REFERENCE_TYPES:
        raise ValidationError(
            f"reference_type must be one of {QUALITY_INSPECTION_REFERENCE_TYPES}",
            field="reference_type",
        )
    if reference_type == "Work Order":
        wo = await db.scalar(
            select(WorkOrder).where(WorkOrder.id == reference_id, WorkOrder.company_id == company_id)
        )
        if wo is None:
            raise NotFoundError("Work Order not found")
        if wo.production_item_id != item_id:
            raise ValidationError(
                "Item must be the Work Order's finished good",
                field="item_id",
            )
        return wo.name
    if reference_type == "Subcontract Job":
        job = await db.scalar(
            select(SubcontractJob).where(
                SubcontractJob.id == reference_id, SubcontractJob.company_id == company_id
            )
        )
        if job is None:
            raise NotFoundError("Subcontract Job not found")
        if job.production_item_id != item_id:
            raise ValidationError(
                "Item must be the Subcontract Job's finished good",
                field="item_id",
            )
        return job.name
    # Stock Entry — soft link (name optional); existence checked lightly
    from app.models.stock import StockEntry

    se = await db.scalar(
        select(StockEntry).where(StockEntry.id == reference_id, StockEntry.company_id == company_id)
    )
    if se is None:
        raise NotFoundError("Stock Entry not found")
    return se.name


async def create_quality_inspection(
    db: AsyncSession, payload: QualityInspectionCreate, user: CurrentUser
) -> QualityInspection:
    company = await get_company(db, user.company_id)
    await get_item(db, payload.item_id, company.id)
    ref_name = await _resolve_reference(
        db, company.id, payload.reference_type, payload.reference_id, payload.item_id
    )
    name = await get_next_name(db, QI_NAMING_SERIES, company.id)
    qi = QualityInspection(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        reference_name=ref_name,
        item_id=payload.item_id,
        qty=payload.qty,
        status="Draft",
        inspection_date=payload.inspection_date or date.today(),
        inspected_by=user.id,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(qi)
    await db.flush()
    await log_audit(
        db,
        doctype="Quality Inspection",
        document_id=qi.id,
        action="INSERT",
        user_id=user.id,
        company_id=company.id,
    )
    await db.commit()
    return await get_quality_inspection(db, qi.id, company.id)


async def submit_quality_inspection(
    db: AsyncSession,
    qi_id: uuid.UUID,
    user: CurrentUser,
    *,
    accept: bool,
) -> QualityInspection:
    qi = await get_quality_inspection(db, qi_id, user.company_id)
    require_draft(qi.docstatus)
    qi.docstatus = DOCSTATUS_SUBMITTED
    qi.status = "Accepted" if accept else "Rejected"
    qi.inspected_by = user.id
    qi.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Quality Inspection",
        document_id=qi.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=qi.company_id,
    )
    await db.commit()
    return await get_quality_inspection(db, qi.id, user.company_id)


async def cancel_quality_inspection(
    db: AsyncSession, qi_id: uuid.UUID, user: CurrentUser
) -> QualityInspection:
    qi = await get_quality_inspection(db, qi_id, user.company_id)
    require_submitted(qi.docstatus)
    qi.docstatus = DOCSTATUS_CANCELLED
    qi.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Quality Inspection",
        document_id=qi.id,
        action="CANCEL",
        user_id=user.id,
        company_id=qi.company_id,
    )
    await db.commit()
    return await get_quality_inspection(db, qi.id, user.company_id)


async def accepted_qty_for_reference(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    item_id: uuid.UUID,
) -> Decimal:
    total = await db.scalar(
        select(func.coalesce(func.sum(QualityInspection.qty), ZERO)).where(
            QualityInspection.company_id == company_id,
            QualityInspection.reference_type == reference_type,
            QualityInspection.reference_id == reference_id,
            QualityInspection.item_id == item_id,
            QualityInspection.docstatus == DOCSTATUS_SUBMITTED,
            QualityInspection.status == "Accepted",
        )
    )
    return Decimal(total or 0)


async def require_accepted_inspection(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    item: Item,
    reference_type: str,
    reference_id: uuid.UUID,
    qty: Decimal,
    already_done: Decimal = ZERO,
) -> None:
    """Block manufacture/receive when the FG requires inspection and Accepted QI is short.

    ``already_done`` is produced/received qty already posted on the reference document;
    Accepted QI total must cover ``already_done + qty``.
    """
    if not item.inspection_required:
        return
    accepted = await accepted_qty_for_reference(
        db,
        company_id=company_id,
        reference_type=reference_type,
        reference_id=reference_id,
        item_id=item.id,
    )
    need = already_done + qty
    if accepted + QTY_EPS < need:
        raise ValidationError(
            f"Quality Inspection required: item '{item.item_code}' needs Accepted "
            f"inspection covering at least {need} (currently Accepted: {accepted}). "
            f"Create and Accept a Quality Inspection against this {reference_type} first.",
            field="inspection_required",
            code="ERR_QI_REQUIRED",
        )
