"""Manufacturing Planning Dashboard — context resolver for SO / Production Plan / Item."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.models.manufacturing import ProductionPlan
from app.models.selling import Quotation, SalesOrder
from app.models.stock import Item

ZERO = Decimal("0")


@dataclass
class PlanningContextLine:
    item_id: uuid.UUID
    item_code: str | None
    item_name: str | None
    qty: Decimal
    delivery_date: date | None
    warehouse_id: uuid.UUID | None
    source_label: str | None = None


@dataclass
class PlanningContext:
    context_type: str
    document_id: uuid.UUID | None
    document_name: str | None
    lines: list[PlanningContextLine]


async def resolve_planning_context(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    context_type: str,
    document_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    qty: Decimal | None = None,
    delivery_date: date | None = None,
    warehouse_id: uuid.UUID | None = None,
) -> PlanningContext:
    """Resolve a planner context into FG lines the dashboard can run Phase 7 tools against."""
    ctx = (context_type or "item").strip().lower()
    if ctx == "item":
        if item_id is None:
            raise ValidationError("item_id is required for item context", field="item_id")
        item = await db.get(Item, item_id)
        if item is None or item.company_id != company_id:
            raise NotFoundError("Item not found")
        q = qty if qty is not None and qty > ZERO else Decimal("1")
        return PlanningContext(
            context_type="item",
            document_id=None,
            document_name=item.item_code,
            lines=[
                PlanningContextLine(
                    item_id=item.id,
                    item_code=item.item_code,
                    item_name=item.item_name,
                    qty=q,
                    delivery_date=delivery_date,
                    warehouse_id=warehouse_id,
                )
            ],
        )

    if document_id is None:
        raise ValidationError("id is required for this context", field="id")

    if ctx == "sales-order":
        so = await db.scalar(
            select(SalesOrder)
            .options(selectinload(SalesOrder.items))
            .where(SalesOrder.id == document_id, SalesOrder.company_id == company_id)
        )
        if so is None:
            raise NotFoundError("Sales Order not found")
        lines: list[PlanningContextLine] = []
        for row in so.items:
            if row.item_id is None:
                continue
            item = await db.get(Item, row.item_id)
            if item is None or not item.is_stock_item:
                continue
            if not getattr(item, "include_item_in_manufacturing", True):
                continue
            lines.append(
                PlanningContextLine(
                    item_id=row.item_id,
                    item_code=row.item_code or (item.item_code if item else None),
                    item_name=row.item_name or (item.item_name if item else None),
                    qty=row.qty,
                    delivery_date=row.delivery_date or so.delivery_date,
                    warehouse_id=row.warehouse_id or so.set_warehouse_id,
                    source_label=so.name,
                )
            )
        return PlanningContext(
            context_type="sales-order",
            document_id=so.id,
            document_name=so.name,
            lines=lines,
        )

    if ctx == "quotation":
        qtn = await db.scalar(
            select(Quotation)
            .options(selectinload(Quotation.items))
            .where(Quotation.id == document_id, Quotation.company_id == company_id)
        )
        if qtn is None:
            raise NotFoundError("Quotation not found")
        lines = []
        for row in qtn.items:
            if row.item_id is None:
                continue
            item = await db.get(Item, row.item_id)
            if item is None or not item.is_stock_item:
                continue
            if not getattr(item, "include_item_in_manufacturing", True):
                continue
            lines.append(
                PlanningContextLine(
                    item_id=row.item_id,
                    item_code=row.item_code or (item.item_code if item else None),
                    item_name=row.item_name or (item.item_name if item else None),
                    qty=row.qty,
                    delivery_date=qtn.valid_till,
                    warehouse_id=row.warehouse_id,
                    source_label=qtn.name,
                )
            )
        return PlanningContext(
            context_type="quotation",
            document_id=qtn.id,
            document_name=qtn.name,
            lines=lines,
        )

    if ctx == "production-plan":
        plan = await db.scalar(
            select(ProductionPlan)
            .options(selectinload(ProductionPlan.items))
            .where(ProductionPlan.id == document_id, ProductionPlan.company_id == company_id)
        )
        if plan is None:
            raise NotFoundError("Production Plan not found")
        lines = []
        for row in plan.items:
            item = await db.get(Item, row.item_id)
            lines.append(
                PlanningContextLine(
                    item_id=row.item_id,
                    item_code=row.item_code or (item.item_code if item else None),
                    item_name=row.item_name or (item.item_name if item else None),
                    qty=row.planned_qty,
                    delivery_date=row.planned_start_date or plan.to_date,
                    warehouse_id=row.warehouse_id or plan.fg_warehouse_id,
                    source_label=plan.name,
                )
            )
        return PlanningContext(
            context_type="production-plan",
            document_id=plan.id,
            document_name=plan.name,
            lines=lines,
        )

    raise ValidationError(
        "context must be item, sales-order, quotation, or production-plan",
        field="context",
    )
