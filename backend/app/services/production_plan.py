"""Production Plan service — MRP-I demand aggregation → Work Orders + Material Requests.

Lean ERPNext flow:
  create draft → get_items (from Sales Orders or keep manual) → get_raw_materials
  → submit → create_work_orders → create_material_requests
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.manufacturing import (
    BOM,
    ProductionPlan,
    ProductionPlanItem,
    ProductionPlanMaterialRequest,
)
from app.models.selling import SalesOrder, SalesOrderItem
from app.schemas.manufacturing import (
    ProductionPlanCreate,
    ProductionPlanCreateResult,
    ProductionPlanItemIn,
    ProductionPlanUpdate,
    WorkOrderCreate,
)
from app.schemas.stock import MaterialRequestCreate, MaterialRequestItemIn
from app.services import material_request as mr_service
from app.services import work_order as wo_service
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.bom_explosion import explode_bom, find_item_bom
from app.services.manufacturing_common import (
    MFG_NAMING_SERIES,
    get_manufacturing_settings,
    item_available_qty,
)
from app.services.pagination import paginate
from app.services.stock_common import get_item, get_items, get_warehouse

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")
OPEN_SO_STATUSES = ("To Deliver and Bill", "To Deliver", "To Bill")


async def get_production_plan(
    db: AsyncSession, plan_id: uuid.UUID, company_id: uuid.UUID | None
) -> ProductionPlan:
    plan = await db.scalar(
        select(ProductionPlan)
        .options(
            selectinload(ProductionPlan.items),
            selectinload(ProductionPlan.material_requests),
        )
        .where(ProductionPlan.id == plan_id, ProductionPlan.company_id == company_id)
    )
    if plan is None:
        raise NotFoundError("Production Plan not found")
    return plan


async def list_production_plans(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> tuple[list[ProductionPlan], int]:
    stmt = (
        select(ProductionPlan)
        .where(ProductionPlan.company_id == company_id)
        .order_by(ProductionPlan.creation.desc())
    )
    if status is not None:
        stmt = stmt.where(ProductionPlan.status == status)
    return await paginate(db, stmt, page, page_size)


async def _resolve_warehouses(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    fg_warehouse_id: uuid.UUID | None,
    source_warehouse_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    settings = await get_manufacturing_settings(db, company_id)

    def _default(key: str) -> uuid.UUID | None:
        raw = settings.get(key)
        return uuid.UUID(raw) if raw else None

    fg = fg_warehouse_id or _default("default_fg_warehouse_id")
    source = source_warehouse_id or _default("default_source_warehouse_id")
    if fg is not None:
        await get_warehouse(db, fg, company_id)
    if source is not None:
        await get_warehouse(db, source, company_id)
    return fg, source


async def _build_fg_rows(
    db: AsyncSession,
    company_id: uuid.UUID,
    rows: list[ProductionPlanItemIn],
) -> list[ProductionPlanItem]:
    if not rows:
        return []
    items = await get_items(db, {r.item_id for r in rows}, company_id)
    built: list[ProductionPlanItem] = []
    for idx, r in enumerate(rows, start=1):
        item = items[r.item_id]
        if not item.is_stock_item:
            raise ValidationError(
                f"Row {idx}: '{item.item_code}' is not a stock item",
                field="items",
            )
        if getattr(item, "include_item_in_manufacturing", True) is False:
            raise ValidationError(
                f"Row {idx}: '{item.item_code}' is excluded from manufacturing",
                field="items",
            )
        bom_id = r.bom_id
        if bom_id is None:
            bom = await find_item_bom(db, company_id, r.item_id)
            if bom is None:
                raise ValidationError(
                    f"Row {idx}: no active BOM for '{item.item_code}'",
                    field="items",
                )
            bom_id = bom.id
        else:
            bom_row = await db.scalar(
                select(BOM).where(
                    BOM.id == bom_id,
                    BOM.company_id == company_id,
                    BOM.production_item_id == r.item_id,
                    BOM.docstatus == DOCSTATUS_SUBMITTED,
                    BOM.is_active.is_(True),
                )
            )
            if bom_row is None:
                raise ValidationError(
                    f"Row {idx}: BOM is not an active submitted BOM for '{item.item_code}'",
                    field="items",
                )
        if r.warehouse_id is not None:
            await get_warehouse(db, r.warehouse_id, company_id)
        if r.sales_order_id is not None:
            so = await db.get(SalesOrder, r.sales_order_id)
            if so is None or so.company_id != company_id:
                raise ValidationError(
                    f"Row {idx}: Sales Order not found",
                    field="items",
                )
        built.append(
            ProductionPlanItem(
                idx=idx,
                item_id=r.item_id,
                bom_id=bom_id,
                sales_order_id=r.sales_order_id,
                planned_qty=r.planned_qty,
                pending_qty=r.planned_qty,
                ordered_qty=ZERO,
                warehouse_id=r.warehouse_id,
                planned_start_date=r.planned_start_date,
                description=r.description,
            )
        )
    return built


async def create_production_plan(
    db: AsyncSession, payload: ProductionPlanCreate, user: CurrentUser
) -> ProductionPlan:
    company = await get_company(db, user.company_id)
    if payload.from_date and payload.to_date and payload.from_date > payload.to_date:
        raise ValidationError("from_date cannot be after to_date", field="from_date")
    fg, source = await _resolve_warehouses(
        db,
        company.id,
        fg_warehouse_id=payload.fg_warehouse_id,
        source_warehouse_id=payload.source_warehouse_id,
    )
    name = await get_next_name(db, MFG_NAMING_SERIES["Production Plan"], company.id)
    plan = ProductionPlan(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        from_date=payload.from_date,
        to_date=payload.to_date,
        get_items_from=payload.get_items_from,
        fg_warehouse_id=fg,
        source_warehouse_id=source,
        status="Draft",
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(plan)
    await db.flush()
    for row in await _build_fg_rows(db, company.id, payload.items):
        row.production_plan_id = plan.id
        db.add(row)
    await db.flush()
    await log_audit(
        db, doctype="Production Plan", document_id=plan.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_production_plan(db, plan.id, company.id)


async def update_production_plan(
    db: AsyncSession, plan_id: uuid.UUID, payload: ProductionPlanUpdate, user: CurrentUser
) -> ProductionPlan:
    plan = await get_production_plan(db, plan_id, user.company_id)
    require_draft(plan.docstatus)
    if payload.posting_date is not None:
        plan.posting_date = payload.posting_date
    if "from_date" in payload.model_fields_set:
        plan.from_date = payload.from_date
    if "to_date" in payload.model_fields_set:
        plan.to_date = payload.to_date
    if payload.get_items_from is not None:
        plan.get_items_from = payload.get_items_from
    if "fg_warehouse_id" in payload.model_fields_set or "source_warehouse_id" in payload.model_fields_set:
        fg, source = await _resolve_warehouses(
            db,
            plan.company_id,
            fg_warehouse_id=(
                payload.fg_warehouse_id
                if "fg_warehouse_id" in payload.model_fields_set
                else plan.fg_warehouse_id
            ),
            source_warehouse_id=(
                payload.source_warehouse_id
                if "source_warehouse_id" in payload.model_fields_set
                else plan.source_warehouse_id
            ),
        )
        if "fg_warehouse_id" in payload.model_fields_set:
            plan.fg_warehouse_id = fg
        if "source_warehouse_id" in payload.model_fields_set:
            plan.source_warehouse_id = source
    if payload.remarks is not None:
        plan.remarks = payload.remarks
    if plan.from_date and plan.to_date and plan.from_date > plan.to_date:
        raise ValidationError("from_date cannot be after to_date", field="from_date")

    if payload.items is not None:
        plan.items.clear()
        plan.material_requests.clear()
        await db.flush()
        for row in await _build_fg_rows(db, plan.company_id, payload.items):
            row.production_plan_id = plan.id
            plan.items.append(row)

    plan.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Production Plan", document_id=plan.id, action="UPDATE",
        user_id=user.id, company_id=plan.company_id,
    )
    await db.commit()
    return await get_production_plan(db, plan.id, user.company_id)


async def get_items_from_sales_orders(
    db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser
) -> ProductionPlan:
    """Replace FG rows with pending qty from open Sales Orders in the plan's date range."""
    plan = await get_production_plan(db, plan_id, user.company_id)
    require_draft(plan.docstatus)
    if not plan.from_date or not plan.to_date:
        raise ValidationError(
            "Set from_date and to_date before pulling Sales Orders",
            field="from_date",
        )

    # Line delivery_date preferred; fall back to header delivery_date.
    delivery_expr = SalesOrderItem.delivery_date
    so_rows = (
        await db.execute(
            select(SalesOrderItem, SalesOrder)
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .where(
                SalesOrder.company_id == plan.company_id,
                SalesOrder.docstatus == DOCSTATUS_SUBMITTED,
                SalesOrder.status.in_(OPEN_SO_STATUSES),
                SalesOrderItem.item_id.is_not(None),
                or_(
                    (delivery_expr >= plan.from_date) & (delivery_expr <= plan.to_date),
                    (delivery_expr.is_(None))
                    & (SalesOrder.delivery_date >= plan.from_date)
                    & (SalesOrder.delivery_date <= plan.to_date),
                ),
            )
            .order_by(SalesOrder.name, SalesOrderItem.idx)
        )
    ).all()

    plan.items.clear()
    plan.material_requests.clear()
    await db.flush()

    idx = 0
    for soi, so in so_rows:
        assert soi.item_id is not None
        item = await get_item(db, soi.item_id, plan.company_id)
        if not item.is_stock_item:
            continue
        if getattr(item, "include_item_in_manufacturing", True) is False:
            continue
        pending = max(ZERO, (soi.stock_qty or soi.qty) - (soi.delivered_qty or ZERO))
        if pending <= QTY_EPS:
            continue
        bom = await find_item_bom(db, plan.company_id, soi.item_id)
        if bom is None:
            continue  # skip items with no BOM (bought-to-order / non-manufactured)
        idx += 1
        plan.items.append(
            ProductionPlanItem(
                production_plan_id=plan.id,
                idx=idx,
                item_id=soi.item_id,
                bom_id=bom.id,
                sales_order_id=so.id,
                sales_order_item_id=soi.id,
                planned_qty=pending,
                pending_qty=pending,
                ordered_qty=ZERO,
                warehouse_id=soi.warehouse_id or plan.fg_warehouse_id,
                planned_start_date=soi.delivery_date or so.delivery_date,
                description=f"From {so.name}",
            )
        )

    plan.get_items_from = "Sales Order"
    plan.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_production_plan(db, plan.id, user.company_id)


async def get_raw_materials(
    db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser, *, only_shortfall: bool = True
) -> ProductionPlan:
    """Explode FG rows to leaf raws, net vs stock, write MR proposal rows."""
    plan = await get_production_plan(db, plan_id, user.company_id)
    require_draft(plan.docstatus)
    if not plan.items:
        raise ValidationError("Add finished-good items before getting raw materials", field="items")

    demand: dict[tuple[uuid.UUID, uuid.UUID | None], Decimal] = {}
    for row in plan.items:
        remaining = max(ZERO, row.planned_qty - row.ordered_qty)
        if remaining <= QTY_EPS or row.bom_id is None:
            continue
        bom = await db.scalar(
            select(BOM)
            .options(selectinload(BOM.items), selectinload(BOM.scrap_items))
            .where(BOM.id == row.bom_id, BOM.company_id == plan.company_id)
        )
        if bom is None:
            raise ValidationError(f"BOM missing for plan item {row.item_code}", field="items")
        exploded = await explode_bom(db, bom, remaining, flatten_all=True)
        for comp in exploded:
            wh = comp.source_warehouse_id or plan.source_warehouse_id
            key = (comp.item_id, wh)
            demand[key] = demand.get(key, ZERO) + comp.stock_qty

    plan.material_requests.clear()
    await db.flush()
    idx = 0
    for (item_id, warehouse_id), required in sorted(demand.items(), key=lambda x: str(x[0])):
        available = await item_available_qty(db, item_id, warehouse_id)
        shortfall = max(ZERO, required - available)
        if only_shortfall and shortfall <= QTY_EPS:
            continue
        idx += 1
        plan.material_requests.append(
            ProductionPlanMaterialRequest(
                production_plan_id=plan.id,
                idx=idx,
                item_id=item_id,
                warehouse_id=warehouse_id,
                required_qty=required.quantize(Decimal("0.000001")),
                available_qty=available.quantize(Decimal("0.000001")),
                shortfall_qty=shortfall.quantize(Decimal("0.000001")),
            )
        )

    plan.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_production_plan(db, plan.id, user.company_id)


async def submit_production_plan(
    db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser
) -> ProductionPlan:
    plan = await get_production_plan(db, plan_id, user.company_id)
    require_draft(plan.docstatus)
    if not plan.items:
        raise ValidationError("A Production Plan needs at least one finished-good row", field="items")
    if any(row.bom_id is None for row in plan.items):
        raise ValidationError("Every finished-good row needs a BOM", field="items")
    plan.docstatus = DOCSTATUS_SUBMITTED
    plan.status = "Submitted"
    plan.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Production Plan", document_id=plan.id, action="SUBMIT",
        user_id=user.id, company_id=plan.company_id,
    )
    await db.commit()
    return await get_production_plan(db, plan.id, user.company_id)


async def cancel_production_plan(
    db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser
) -> ProductionPlan:
    plan = await get_production_plan(db, plan_id, user.company_id)
    require_submitted(plan.docstatus)
    if plan.work_orders_created or any(row.work_order_id for row in plan.items):
        raise ValidationError(
            "Cannot cancel: Work Orders have already been created from this plan",
            code="ERR_DOCSTATUS",
        )
    if plan.material_requests_created or any(
        row.material_request_id for row in plan.material_requests
    ):
        raise ValidationError(
            "Cannot cancel: Material Requests have already been created from this plan",
            code="ERR_DOCSTATUS",
        )
    plan.docstatus = DOCSTATUS_CANCELLED
    plan.status = "Cancelled"
    plan.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Production Plan", document_id=plan.id, action="CANCEL",
        user_id=user.id, company_id=plan.company_id,
    )
    await db.commit()
    return await get_production_plan(db, plan.id, user.company_id)


async def create_work_orders_from_plan(
    db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser
) -> ProductionPlanCreateResult:
    """Create a Draft Work Order for each FG row that still needs one."""
    plan = await get_production_plan(db, plan_id, user.company_id)
    require_submitted(plan.docstatus)
    if plan.fg_warehouse_id is None:
        raise ValidationError(
            "Set a finished-goods warehouse on the Production Plan (or in Manufacturing Settings)",
            field="fg_warehouse_id",
        )

    created_ids: list[uuid.UUID] = []
    created_names: list[str] = []
    for row in plan.items:
        remaining = max(ZERO, row.planned_qty - row.ordered_qty)
        if remaining <= QTY_EPS or row.work_order_id is not None or row.bom_id is None:
            continue
        wo = await wo_service.create_work_order(
            db,
            WorkOrderCreate(
                bom_id=row.bom_id,
                qty=remaining,
                fg_warehouse_id=row.warehouse_id or plan.fg_warehouse_id,
                source_warehouse_id=plan.source_warehouse_id,
                skip_transfer=True,
                planned_start_date=row.planned_start_date,
                sales_order_id=row.sales_order_id,
                remarks=f"From Production Plan {plan.name}",
            ),
            user,
        )
        # Link back (create_work_order commits; reload + patch)
        wo = await wo_service.get_work_order(db, wo.id, plan.company_id)
        wo.production_plan_id = plan.id
        wo.modified_by = user.id
        await db.flush()

        plan = await get_production_plan(db, plan.id, user.company_id)
        for pi in plan.items:
            if pi.id == row.id:
                pi.work_order_id = wo.id
                pi.ordered_qty = (pi.ordered_qty + remaining).quantize(Decimal("0.000001"))
                break
        await db.flush()
        await db.commit()
        created_ids.append(wo.id)
        created_names.append(wo.name)

    plan = await get_production_plan(db, plan.id, user.company_id)
    if created_ids:
        plan.work_orders_created = True
        if all(max(ZERO, r.planned_qty - r.ordered_qty) <= QTY_EPS for r in plan.items):
            plan.status = "Completed"
        plan.modified_by = user.id
        await db.flush()
        await db.commit()

    return ProductionPlanCreateResult(
        production_plan_id=plan.id,
        created_ids=created_ids,
        created_names=created_names,
        count=len(created_ids),
    )


async def create_material_requests_from_plan(
    db: AsyncSession, plan_id: uuid.UUID, user: CurrentUser
) -> ProductionPlanCreateResult:
    """Raise one Manufacture Material Request for all shortfall rows not yet linked."""
    plan = await get_production_plan(db, plan_id, user.company_id)
    require_submitted(plan.docstatus)
    short_rows = [
        r for r in plan.material_requests
        if r.shortfall_qty > QTY_EPS and r.material_request_id is None
    ]
    if not short_rows:
        raise ValidationError(
            "No shortfall material rows to request — run Get Raw Materials first, "
            "or shortfalls are already covered",
            field="material_requests",
        )

    mr = await mr_service.create_material_request(
        db,
        MaterialRequestCreate(
            material_request_type="Manufacture",
            posting_date=plan.posting_date,
            remarks=f"From Production Plan {plan.name}",
            items=[
                MaterialRequestItemIn(
                    item_id=r.item_id,
                    qty=r.shortfall_qty,
                    warehouse_id=r.warehouse_id,
                )
                for r in short_rows
            ],
        ),
        user,
    )

    plan = await get_production_plan(db, plan.id, user.company_id)
    for r in plan.material_requests:
        if r.shortfall_qty > QTY_EPS and r.material_request_id is None:
            r.material_request_id = mr.id
    plan.material_requests_created = True
    plan.modified_by = user.id
    await db.flush()
    await db.commit()

    return ProductionPlanCreateResult(
        production_plan_id=plan.id,
        created_ids=[mr.id],
        created_names=[mr.name],
        count=1,
    )
