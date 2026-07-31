"""Module workspace stats (Selling, Buying) in a generic shape consumed by the
shared ModuleWorkspace page: number cards + a 12-month order-value trend.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import SalesInvoice
from app.models.base import DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED
from app.models.buying import PurchaseOrder
from app.models.compliance import IncomeTaxComputation
from app.models.core import Company, SystemSetting
from app.models.manufacturing import BOM, WorkOrder
from app.models.selling import SalesOrder
from app.models.stock import Item, StockEntry, Warehouse

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _last_12_months(today: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = today.year, today.month
    for _ in range(12):
        months.append((year, month))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(months))


async def _company_currency(db: AsyncSession, company_id: uuid.UUID) -> str:
    return (
        await db.execute(select(Company.default_currency).where(Company.id == company_id))
    ).scalar_one_or_none() or "INR"


async def _order_stats(
    db: AsyncSession, model: type, company_id: uuid.UUID
) -> tuple[int, float, float, list[dict[str, Any]]]:
    cond = (model.company_id == company_id, model.docstatus == DOCSTATUS_SUBMITTED)
    count = (await db.execute(select(func.count()).select_from(model).where(*cond))).scalar_one()
    total = Decimal(
        (await db.execute(select(func.coalesce(func.sum(model.base_grand_total), 0)).where(*cond))).scalar_one()
    )
    average = (total / count) if count else Decimal(0)

    months = _last_12_months(date.today())
    start = date(months[0][0], months[0][1], 1)
    year_col = func.extract("year", model.posting_date)
    month_col = func.extract("month", model.posting_date)
    rows = (
        await db.execute(
            select(
                year_col.label("y"),
                month_col.label("m"),
                func.coalesce(func.sum(model.base_grand_total), 0).label("v"),
            )
            .where(*cond, model.posting_date >= start)
            .group_by(year_col, month_col)
        )
    ).all()
    bucket = {(int(r.y), int(r.m)): float(r.v) for r in rows}
    trend = [{"label": _MONTH_ABBR[m - 1], "value": bucket.get((y, m), 0.0)} for (y, m) in months]
    return int(count), float(total), float(average), trend


async def get_selling_workspace(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    currency = await _company_currency(db, company_id)
    count, total, average, trend = await _order_stats(db, SalesOrder, company_id)
    return {
        "currency": currency,
        "chart_title": "Sales Order Trends",
        "trend_format": "currency",
        "cards": [
            {"label": "Sales Orders", "value": count, "format": "int"},
            {"label": "Total Sales Amount", "value": total, "format": "currency"},
            {"label": "Average Order Value", "value": average, "format": "currency"},
        ],
        "trend": trend,
    }


async def get_buying_workspace(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    currency = await _company_currency(db, company_id)
    count, total, average, trend = await _order_stats(db, PurchaseOrder, company_id)
    return {
        "currency": currency,
        "chart_title": "Purchase Order Trends",
        "trend_format": "currency",
        "cards": [
            {"label": "Purchase Orders", "value": count, "format": "int"},
            {"label": "Total Purchase Amount", "value": total, "format": "currency"},
            {"label": "Average Order Value", "value": average, "format": "currency"},
        ],
        "trend": trend,
    }


async def get_accounting_workspace(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    currency = await _company_currency(db, company_id)
    count, invoiced, _avg, trend = await _order_stats(db, SalesInvoice, company_id)
    outstanding = float(
        Decimal(
            (
                await db.execute(
                    select(func.coalesce(func.sum(SalesInvoice.outstanding_amount), 0)).where(
                        SalesInvoice.company_id == company_id,
                        SalesInvoice.docstatus == DOCSTATUS_SUBMITTED,
                    )
                )
            ).scalar_one()
        )
    )
    return {
        "currency": currency,
        "chart_title": "Sales Invoice Trends",
        "trend_format": "currency",
        "cards": [
            {"label": "Sales Invoices", "value": count, "format": "int"},
            {"label": "Total Invoiced", "value": invoiced, "format": "currency"},
            {"label": "Outstanding", "value": outstanding, "format": "currency"},
        ],
        "trend": trend,
    }


async def get_manufacturing_workspace(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    currency = await _company_currency(db, company_id)
    active_boms = (
        await db.execute(
            select(func.count())
            .select_from(BOM)
            .where(BOM.company_id == company_id, BOM.docstatus == DOCSTATUS_SUBMITTED, BOM.is_active.is_(True))
        )
    ).scalar_one()
    open_cond = (
        WorkOrder.company_id == company_id,
        WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
        WorkOrder.status.in_(("Not Started", "In Process")),
    )
    open_wos = (await db.execute(select(func.count()).select_from(WorkOrder).where(*open_cond))).scalar_one()
    completed = (
        await db.execute(
            select(func.count())
            .select_from(WorkOrder)
            .where(WorkOrder.company_id == company_id, WorkOrder.status == "Completed")
        )
    ).scalar_one()

    # 12-month trend of finished quantity produced (by actual_end_date)
    months = _last_12_months(date.today())
    start = date(months[0][0], months[0][1], 1)
    year_col = func.extract("year", WorkOrder.actual_end_date)
    month_col = func.extract("month", WorkOrder.actual_end_date)
    rows = (
        await db.execute(
            select(
                year_col.label("y"),
                month_col.label("m"),
                func.coalesce(func.sum(WorkOrder.produced_qty), 0).label("v"),
            )
            .where(
                WorkOrder.company_id == company_id,
                WorkOrder.actual_end_date.is_not(None),
                WorkOrder.actual_end_date >= start,
            )
            .group_by(year_col, month_col)
        )
    ).all()
    bucket = {(int(r.y), int(r.m)): float(r.v) for r in rows}
    trend = [{"label": _MONTH_ABBR[m - 1], "value": bucket.get((y, m), 0)} for (y, m) in months]

    return {
        "currency": currency,
        "chart_title": "Finished Quantity Produced",
        "trend_format": "int",
        "cards": [
            {"label": "Active BOMs", "value": int(active_boms), "format": "int"},
            {"label": "Open Work Orders", "value": int(open_wos), "format": "int"},
            {"label": "Completed Work Orders", "value": int(completed), "format": "int"},
        ],
        "trend": trend,
    }


async def get_stock_workspace(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    currency = await _company_currency(db, company_id)
    items = (
        await db.execute(select(func.count()).select_from(Item).where(Item.company_id == company_id))
    ).scalar_one()
    warehouses = (
        await db.execute(
            select(func.count()).select_from(Warehouse).where(Warehouse.company_id == company_id)
        )
    ).scalar_one()

    cond = (StockEntry.company_id == company_id, StockEntry.docstatus == DOCSTATUS_SUBMITTED)
    entries = (await db.execute(select(func.count()).select_from(StockEntry).where(*cond))).scalar_one()

    months = _last_12_months(date.today())
    start = date(months[0][0], months[0][1], 1)
    year_col = func.extract("year", StockEntry.posting_date)
    month_col = func.extract("month", StockEntry.posting_date)
    rows = (
        await db.execute(
            select(year_col.label("y"), month_col.label("m"), func.count().label("v"))
            .where(*cond, StockEntry.posting_date >= start)
            .group_by(year_col, month_col)
        )
    ).all()
    bucket = {(int(r.y), int(r.m)): int(r.v) for r in rows}
    trend = [{"label": _MONTH_ABBR[m - 1], "value": bucket.get((y, m), 0)} for (y, m) in months]

    return {
        "currency": currency,
        "chart_title": "Stock Entry Trends",
        "trend_format": "int",
        "cards": [
            {"label": "Items", "value": int(items), "format": "int"},
            {"label": "Warehouses", "value": int(warehouses), "format": "int"},
            {"label": "Stock Entries", "value": int(entries), "format": "int"},
        ],
        "trend": trend,
    }


async def get_taxation_workspace(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    """Lean Taxation home: ITR draft/submitted counts + GST settings presence."""
    currency = await _company_currency(db, company_id)
    draft_itr = (
        await db.execute(
            select(func.count())
            .select_from(IncomeTaxComputation)
            .where(
                IncomeTaxComputation.company_id == company_id,
                IncomeTaxComputation.docstatus == DOCSTATUS_DRAFT,
            )
        )
    ).scalar_one()
    submitted_itr = (
        await db.execute(
            select(func.count())
            .select_from(IncomeTaxComputation)
            .where(
                IncomeTaxComputation.company_id == company_id,
                IncomeTaxComputation.docstatus == DOCSTATUS_SUBMITTED,
            )
        )
    ).scalar_one()
    gst_row = await db.scalar(
        select(SystemSetting.id).where(
            SystemSetting.company_id == company_id,
            SystemSetting.key == "gst_settings",
        )
    )
    gst_configured = 1 if gst_row is not None else 0

    months = _last_12_months(date.today())
    start = date(months[0][0], months[0][1], 1)
    year_col = func.extract("year", IncomeTaxComputation.creation)
    month_col = func.extract("month", IncomeTaxComputation.creation)
    rows = (
        await db.execute(
            select(year_col.label("y"), month_col.label("m"), func.count().label("v"))
            .where(
                IncomeTaxComputation.company_id == company_id,
                IncomeTaxComputation.creation >= start,
            )
            .group_by(year_col, month_col)
        )
    ).all()
    bucket = {(int(r.y), int(r.m)): int(r.v) for r in rows}
    trend = [{"label": _MONTH_ABBR[m - 1], "value": bucket.get((y, m), 0)} for (y, m) in months]

    return {
        "currency": currency,
        "chart_title": "Income Tax Computations Created",
        "trend_format": "int",
        "cards": [
            {"label": "Draft ITR Worksheets", "value": int(draft_itr), "format": "int"},
            {"label": "Submitted ITR Worksheets", "value": int(submitted_itr), "format": "int"},
            {"label": "GST Settings", "value": int(gst_configured), "format": "int"},
        ],
        "trend": trend,
    }
