"""Work Order service — make N units of a finished good per a BOM (bespoke document).

Lifecycle (master §4):
    create   → explode the BOM × qty into required items (Draft)
    submit   → Not Started
    finish   → post a Manufacture Stock Entry (consume raws, produce FG at input cost),
               accrue produced/consumed qty → In Process (partial) or Completed (full)
    stop     → halt further finishing (Stopped); resume returns it to the live state
    cancel   → only before anything is produced

The Manufacture Stock Entry reuses the existing Stock Ledger / valuation / perpetual GL
(app.services.stock_entry) — this service builds the entry rows and drives submit/cancel;
it never re-implements stock or GL posting.

The optional WIP transfer step (``transfer_for_manufacture``) moves raws source → WIP; the
lean default is ``skip_transfer`` (consume straight from the source on finish).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.manufacturing import BOM, WorkOrder, WorkOrderItem
from app.models.stock import Item, StockEntry, StockEntryItem
from app.schemas.manufacturing import (
    MaterialAvailabilityResponse,
    MaterialAvailabilityRow,
    WorkOrderCreate,
    WorkOrderFinishIn,
    WorkOrderTransferIn,
)
from app.schemas.stock import MaterialRequestCreate, MaterialRequestItemIn
from app.services import material_request as mr_service
from app.services import stock_entry as se_service
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.manufacturing_common import (
    MFG_NAMING_SERIES,
    get_manufacturing_settings,
    item_available_qty,
    require_expense_account,
    require_whole_number_qty,
)
from app.services.pagination import paginate
from app.services.stock_common import STOCK_NAMING_SERIES, get_item, get_items, get_warehouse

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")  # tolerance for "fully produced" comparisons


# --- fetch ---------------------------------------------------------------------------


async def get_work_order(
    db: AsyncSession, work_order_id: uuid.UUID, company_id: uuid.UUID | None
) -> WorkOrder:
    wo = await db.scalar(
        select(WorkOrder)
        .options(selectinload(WorkOrder.items))
        .where(WorkOrder.id == work_order_id, WorkOrder.company_id == company_id)
    )
    if wo is None:
        raise NotFoundError("Work Order not found")
    return wo


async def list_work_orders(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> tuple[list[WorkOrder], int]:
    stmt = (
        select(WorkOrder)
        .where(WorkOrder.company_id == company_id)
        .order_by(WorkOrder.creation.desc())
    )
    if status:
        stmt = stmt.where(WorkOrder.status == status)
    return await paginate(db, stmt, page, page_size)


# --- create --------------------------------------------------------------------------


def _blocked_tracking(item: Item) -> bool:
    """Serial / batch tracked items are not yet supported in Manufacturing v1 (the
    Manufacture entry would have to pick serials/lots) — flagged so we fail loudly."""
    return item.has_serial_no or item.has_batch_no


async def create_work_order(db: AsyncSession, payload: WorkOrderCreate, user: CurrentUser) -> WorkOrder:
    company = await get_company(db, user.company_id)
    bom = await db.scalar(
        select(BOM).options(selectinload(BOM.items)).where(
            BOM.id == payload.bom_id, BOM.company_id == company.id
        )
    )
    if bom is None:
        raise NotFoundError("BOM not found")
    if bom.docstatus != DOCSTATUS_SUBMITTED or not bom.is_active:
        raise ValidationError("The BOM must be submitted and active", field="bom_id")
    if not bom.items:
        raise ValidationError("The BOM has no components", field="bom_id")

    production_item = await get_item(db, bom.production_item_id, company.id)
    if _blocked_tracking(production_item):
        raise ValidationError(
            f"'{production_item.item_code}' is serial/batch tracked — manufacturing tracked "
            "finished goods is not supported yet",
            field="bom_id",
        )
    await require_whole_number_qty(db, production_item, payload.qty)

    if payload.operating_cost_account_id is not None:
        await require_expense_account(db, payload.operating_cost_account_id, company.id)

    # warehouses: explicit on the payload, else the company's Manufacturing defaults.
    # An EXPLICIT null for source/wip (the field present in the payload) means "no
    # order-level warehouse" (per-BOM-row warehouses only); only an omitted field falls
    # back to the settings default.
    settings = await get_manufacturing_settings(db, company.id)

    def _default_wh(key: str) -> uuid.UUID | None:
        raw = settings.get(key)
        return uuid.UUID(raw) if raw else None

    async def _check_warehouse(wid: uuid.UUID, *, from_settings: bool, label: str) -> None:
        try:
            await get_warehouse(db, wid, company.id)
        except (NotFoundError, ValidationError) as exc:
            if from_settings:
                raise ValidationError(
                    f"The default {label} warehouse configured in Manufacturing Settings "
                    "is missing or disabled — update Manufacturing Settings",
                    field=f"{label}_warehouse_id",
                ) from exc
            raise

    fg_warehouse_id = payload.fg_warehouse_id or _default_wh("default_fg_warehouse_id")
    fg_from_settings = payload.fg_warehouse_id is None and fg_warehouse_id is not None
    if payload.source_warehouse_id is None and "source_warehouse_id" not in payload.model_fields_set:
        source_warehouse_id = _default_wh("default_source_warehouse_id")
        source_from_settings = source_warehouse_id is not None
    else:
        source_warehouse_id = payload.source_warehouse_id
        source_from_settings = False
    if payload.wip_warehouse_id is None and "wip_warehouse_id" not in payload.model_fields_set:
        wip_warehouse_id = _default_wh("default_wip_warehouse_id")
        wip_from_settings = wip_warehouse_id is not None
    else:
        wip_warehouse_id = payload.wip_warehouse_id
        wip_from_settings = False

    if fg_warehouse_id is None:
        raise ValidationError(
            "A finished-goods warehouse is required — set one on the Work Order or in "
            "Manufacturing Settings",
            field="fg_warehouse_id",
        )
    await _check_warehouse(fg_warehouse_id, from_settings=fg_from_settings, label="finished-goods")
    if source_warehouse_id is not None:
        await _check_warehouse(source_warehouse_id, from_settings=source_from_settings, label="source")
    if wip_warehouse_id is not None:
        await _check_warehouse(wip_warehouse_id, from_settings=wip_from_settings, label="WIP")

    # explode the BOM × qty into required items (in stock UOM). A BOM batch yields
    # bom.quantity units, so scale each component by qty / bom.quantity.
    scale = payload.qty / bom.quantity
    component_items = await get_items(db, {i.item_id for i in bom.items}, company.id)
    op_cost = (bom.operating_cost * scale).quantize(Decimal("0.000001"))

    name = await get_next_name(db, MFG_NAMING_SERIES["Work Order"], company.id)
    wo = WorkOrder(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        production_item_id=production_item.id,
        bom_id=bom.id,
        qty=payload.qty,
        source_warehouse_id=source_warehouse_id,
        wip_warehouse_id=wip_warehouse_id,
        fg_warehouse_id=fg_warehouse_id,
        skip_transfer=payload.skip_transfer,
        operating_cost=op_cost,
        operating_cost_account_id=payload.operating_cost_account_id,
        status="Draft",
        planned_start_date=payload.planned_start_date,
        planned_end_date=payload.planned_end_date,
        sales_order_id=payload.sales_order_id,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(wo)
    await db.flush()

    for idx, comp in enumerate(bom.items, start=1):
        citem = component_items[comp.item_id]
        if _blocked_tracking(citem):
            raise ValidationError(
                f"Component '{citem.item_code}' is serial/batch tracked — not supported in "
                "Manufacturing v1",
                field="bom_id",
            )
        required = (comp.stock_qty * scale).quantize(Decimal("0.000001"))
        db.add(
            WorkOrderItem(
                work_order_id=wo.id,
                idx=idx,
                item_id=comp.item_id,
                required_qty=required,
                source_warehouse_id=comp.source_warehouse_id or source_warehouse_id,
                rate=comp.rate,
                amount=(required * comp.rate).quantize(Decimal("0.000001")),
            )
        )
    await db.flush()
    await log_audit(
        db, doctype="Work Order", document_id=wo.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_work_order(db, wo.id, company.id)


# --- lifecycle -----------------------------------------------------------------------


async def submit_work_order(db: AsyncSession, work_order_id: uuid.UUID, user: CurrentUser) -> WorkOrder:
    wo = await get_work_order(db, work_order_id, user.company_id)
    require_draft(wo.docstatus)
    wo.docstatus = DOCSTATUS_SUBMITTED
    wo.status = "Not Started"
    wo.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Work Order", document_id=wo.id, action="SUBMIT",
        user_id=user.id, company_id=wo.company_id,
    )
    await db.commit()
    return await get_work_order(db, wo.id, user.company_id)


async def cancel_work_order(db: AsyncSession, work_order_id: uuid.UUID, user: CurrentUser) -> WorkOrder:
    """Cancel a Work Order — blocked once anything has been produced (cancel the Manufacture
    Stock Entries first, which rolls back the produced qty)."""
    wo = await get_work_order(db, work_order_id, user.company_id)
    require_submitted(wo.docstatus)
    if wo.produced_qty > ZERO or wo.material_transferred_qty > ZERO:
        raise ValidationError(
            "Cannot cancel: stock has already been produced or transferred for this Work "
            "Order. Cancel its Manufacture / Transfer Stock Entries first.",
            code="ERR_DOCSTATUS",
        )
    wo.docstatus = DOCSTATUS_CANCELLED
    wo.status = "Cancelled"
    wo.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Work Order", document_id=wo.id, action="CANCEL",
        user_id=user.id, company_id=wo.company_id,
    )
    await db.commit()
    return await get_work_order(db, wo.id, user.company_id)


async def stop_work_order(
    db: AsyncSession, work_order_id: uuid.UUID, user: CurrentUser, *, stop: bool
) -> WorkOrder:
    """Stop (halt further finishing) or resume a submitted Work Order."""
    wo = await get_work_order(db, work_order_id, user.company_id)
    require_submitted(wo.docstatus)
    if stop:
        if wo.status == "Completed":
            raise ValidationError("Work Order is already completed", field="status")
        wo.status = "Stopped"
    else:
        if wo.status != "Stopped":
            raise ValidationError("Work Order is not stopped", field="status")
        _recompute_status(wo)
    wo.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_work_order(db, wo.id, user.company_id)


def _recompute_status(wo: WorkOrder) -> None:
    if wo.produced_qty >= wo.qty - QTY_EPS:
        wo.status = "Completed"
    elif wo.produced_qty > ZERO or wo.material_transferred_qty > ZERO:
        wo.status = "In Process"
    else:
        wo.status = "Not Started"


# --- finish (the core: consume raws, produce FG) -------------------------------------


async def finish_work_order(
    db: AsyncSession, work_order_id: uuid.UUID, payload: WorkOrderFinishIn, user: CurrentUser
) -> tuple[WorkOrder, StockEntry]:
    """Manufacture ``payload.qty`` finished units: build + submit a Manufacture Stock Entry
    (consume each required item proportionally, produce the FG at input cost), then advance
    the Work Order's produced/consumed qty and status."""
    wo = await get_work_order(db, work_order_id, user.company_id)
    require_submitted(wo.docstatus)
    if wo.status == "Stopped":
        raise ValidationError("Work Order is stopped — resume it before finishing", field="status")
    production_item = await get_item(db, wo.production_item_id, wo.company_id)
    await require_whole_number_qty(db, production_item, payload.qty)

    # over-production allowance (Manufacturing Settings): finish up to qty × (1 + pct/100)
    settings = await get_manufacturing_settings(db, wo.company_id)
    over_pct = Decimal(str(settings.get("over_production_percentage") or "0"))
    allowed_total = wo.qty * (Decimal("1") + over_pct / Decimal("100"))
    remaining = allowed_total - wo.produced_qty
    produce_qty = payload.qty
    if produce_qty > remaining + QTY_EPS:
        allowance = f" (incl. the {over_pct}% over-production allowance)" if over_pct > ZERO else ""
        raise ValidationError(
            f"Cannot produce {produce_qty}: only {remaining} left on this Work Order{allowance}",
            field="qty",
        )
    company = await get_company(db, wo.company_id)
    if payload.operating_cost_account_id is not None:
        await require_expense_account(db, payload.operating_cost_account_id, wo.company_id)
    op_account = payload.operating_cost_account_id or wo.operating_cost_account_id

    # consume warehouse: from WIP if the transfer step is in use, else the order/source
    consume_default = (
        wo.wip_warehouse_id if (not wo.skip_transfer and wo.wip_warehouse_id) else wo.source_warehouse_id
    )
    scale = produce_qty / wo.qty
    op_cost_now = (wo.operating_cost * scale).quantize(Decimal("0.000001"))

    # build the draft Manufacture Stock Entry
    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Entry"], company.id)
    entry = StockEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose="Manufacture",
        work_order_id=wo.id,
        operating_cost=op_cost_now,
        operating_cost_account_id=op_account,
        remarks=f"Manufacture for Work Order {wo.name}",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()

    idx = 0
    consumed_map: dict[uuid.UUID, Decimal] = {}
    for wi in wo.items:
        consume_qty = (wi.required_qty * scale).quantize(Decimal("0.000001"))
        if consume_qty <= ZERO:
            continue
        source_id = wi.source_warehouse_id or consume_default
        if source_id is None:
            raise ValidationError(
                f"No source warehouse for component '{wi.item_code}' — set one on the Work "
                "Order or the BOM",
                field="source_warehouse_id",
            )
        await get_warehouse(db, source_id, company.id)
        idx += 1
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id, idx=idx, item_id=wi.item_id,
                source_warehouse_id=source_id, target_warehouse_id=None,
                qty=consume_qty, uom=None, conversion_factor=Decimal("1"), stock_qty=consume_qty,
                basic_rate=wi.rate, amount=(consume_qty * wi.rate).quantize(Decimal("0.000001")),
            )
        )
        consumed_map[wi.item_id] = consume_qty

    # finished-good production row (valued at consumed + operating cost, set on submit)
    idx += 1
    estimate = (wo.bom.cost_per_unit if wo.bom else ZERO)
    db.add(
        StockEntryItem(
            stock_entry_id=entry.id, idx=idx, item_id=wo.production_item_id,
            source_warehouse_id=None, target_warehouse_id=wo.fg_warehouse_id,
            qty=produce_qty, uom=None, conversion_factor=Decimal("1"), stock_qty=produce_qty,
            basic_rate=estimate, amount=(produce_qty * estimate).quantize(Decimal("0.000001")),
        )
    )
    await db.flush()
    await db.commit()

    # submit posts the SLE (consume + produce) and the perpetual GL
    await se_service.submit_stock_entry(db, entry.id, user)

    # advance the Work Order in its own transaction
    wo = await get_work_order(db, wo.id, user.company_id)
    wo.produced_qty = (wo.produced_qty + produce_qty).quantize(Decimal("0.000001"))
    for wi in wo.items:
        if wi.item_id in consumed_map:
            wi.consumed_qty = (wi.consumed_qty + consumed_map[wi.item_id]).quantize(Decimal("0.000001"))
    if wo.actual_start_date is None:
        wo.actual_start_date = payload.posting_date
    _recompute_status(wo)
    if wo.status == "Completed":
        wo.actual_end_date = payload.posting_date
    wo.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Work Order", document_id=wo.id, action="UPDATE",
        user_id=user.id, company_id=wo.company_id,
    )
    await db.commit()
    wo = await get_work_order(db, wo.id, user.company_id)
    entry = await se_service.get_stock_entry(db, entry.id, company.id)
    return wo, entry


async def revert_manufacture_entry(db: AsyncSession, entry: StockEntry, user: CurrentUser) -> None:
    """Roll back a Work Order's produced / consumed qty when its Manufacture Stock Entry is
    cancelled. Called from cancel_stock_entry inside the same transaction (flush only)."""
    wo = await db.scalar(
        select(WorkOrder).options(selectinload(WorkOrder.items)).where(WorkOrder.id == entry.work_order_id)
    )
    if wo is None:
        return
    produced = sum((r.stock_qty for r in entry.items if r.target_warehouse_id is not None), ZERO)
    consumed_by_item: dict[uuid.UUID, Decimal] = {}
    for r in entry.items:
        if r.source_warehouse_id is not None:
            consumed_by_item[r.item_id] = consumed_by_item.get(r.item_id, ZERO) + r.stock_qty
    wo.produced_qty = max(ZERO, (wo.produced_qty - produced)).quantize(Decimal("0.000001"))
    for wi in wo.items:
        if wi.item_id in consumed_by_item:
            wi.consumed_qty = max(ZERO, wi.consumed_qty - consumed_by_item[wi.item_id]).quantize(
                Decimal("0.000001")
            )
    if wo.status != "Stopped":
        _recompute_status(wo)
    if wo.produced_qty < wo.qty - QTY_EPS:
        wo.actual_end_date = None
    wo.modified_by = user.id
    await db.flush()


# --- Phase 3: material availability + shortfall Material Request ----------------------


async def material_availability(
    db: AsyncSession, work_order_id: uuid.UUID, company_id: uuid.UUID | None
) -> MaterialAvailabilityResponse:
    """On-hand vs still-required position per component, plus how many finished units the
    current stock can support (the min over components)."""
    wo = await get_work_order(db, work_order_id, company_id)
    consume_default = (
        wo.wip_warehouse_id if (not wo.skip_transfer and wo.wip_warehouse_id) else wo.source_warehouse_id
    )
    remaining_fg = max(ZERO, wo.qty - wo.produced_qty)
    rows: list[MaterialAvailabilityRow] = []
    can_finish = remaining_fg
    for wi in wo.items:
        source_id = wi.source_warehouse_id or consume_default
        available = await item_available_qty(db, wi.item_id, source_id)
        pending = max(ZERO, wi.required_qty - wi.consumed_qty)
        shortfall = max(ZERO, pending - available)
        rows.append(
            MaterialAvailabilityRow(
                item_id=wi.item_id, item_code=wi.item_code, item_name=wi.item_name,
                required_qty=wi.required_qty, consumed_qty=wi.consumed_qty, pending_qty=pending,
                source_warehouse_id=source_id, available_qty=available, shortfall_qty=shortfall,
            )
        )
        # per-unit component need (for the remaining FG): pending / remaining_fg
        per_unit = (wi.required_qty / wo.qty) if wo.qty else ZERO
        if per_unit > ZERO:
            can_finish = min(can_finish, available / per_unit)
    return MaterialAvailabilityResponse(
        work_order_id=wo.id,
        can_finish_qty=can_finish.quantize(Decimal("0.000001")) if can_finish > ZERO else ZERO,
        rows=rows,
    )


async def create_shortfall_material_request(
    db: AsyncSession, work_order_id: uuid.UUID, user: CurrentUser, *, posting_date: date | None = None
):
    """Raise a Purchase Material Request for every component short of the pending requirement
    (reuses the existing Material Request document)."""
    avail = await material_availability(db, work_order_id, user.company_id)
    short_rows = [r for r in avail.rows if r.shortfall_qty > ZERO]
    if not short_rows:
        raise ValidationError("No material shortfall for this Work Order", field="items")
    payload = MaterialRequestCreate(
        material_request_type="Purchase",
        posting_date=posting_date or date.today(),
        remarks=f"Shortfall for Work Order {work_order_id}",
        items=[
            MaterialRequestItemIn(
                item_id=r.item_id, qty=r.shortfall_qty, warehouse_id=r.source_warehouse_id
            )
            for r in short_rows
        ],
    )
    return await mr_service.create_material_request(db, payload, user)


# --- Phase 3 (optional): WIP transfer for manufacture --------------------------------


async def transfer_for_manufacture(
    db: AsyncSession, work_order_id: uuid.UUID, payload: WorkOrderTransferIn, user: CurrentUser
) -> tuple[WorkOrder, StockEntry]:
    """Move the raws for ``payload.qty`` finished units from source → WIP warehouse (a
    Material-Transfer-for-Manufacture Stock Entry). Only for Work Orders not skipping the
    transfer step."""
    wo = await get_work_order(db, work_order_id, user.company_id)
    require_submitted(wo.docstatus)
    if wo.skip_transfer or wo.wip_warehouse_id is None:
        raise ValidationError(
            "This Work Order consumes directly from the source (no WIP transfer step)",
            field="wip_warehouse_id",
        )
    # same over-production allowance as finish — the raws for the extra units must be
    # transferable into WIP, or the allowed finish could never be stocked
    settings = await get_manufacturing_settings(db, wo.company_id)
    over_pct = Decimal(str(settings.get("over_production_percentage") or "0"))
    allowed_total = wo.qty * (Decimal("1") + over_pct / Decimal("100"))
    remaining = allowed_total - wo.material_transferred_qty
    if payload.qty > remaining + QTY_EPS:
        raise ValidationError(
            f"Cannot transfer for {payload.qty}: only {remaining} left to transfer", field="qty"
        )
    company = await get_company(db, wo.company_id)
    scale = payload.qty / wo.qty

    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Entry"], company.id)
    entry = StockEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose="Material Transfer for Manufacture",
        work_order_id=wo.id,
        remarks=f"Material transfer for Work Order {wo.name}",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()
    idx = 0
    transferred_map: dict[uuid.UUID, Decimal] = {}
    for wi in wo.items:
        move_qty = (wi.required_qty * scale).quantize(Decimal("0.000001"))
        if move_qty <= ZERO:
            continue
        source_id = wi.source_warehouse_id or wo.source_warehouse_id
        if source_id is None:
            raise ValidationError(
                f"No source warehouse for component '{wi.item_code}'", field="source_warehouse_id"
            )
        idx += 1
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id, idx=idx, item_id=wi.item_id,
                source_warehouse_id=source_id, target_warehouse_id=wo.wip_warehouse_id,
                qty=move_qty, uom=None, conversion_factor=Decimal("1"), stock_qty=move_qty,
                basic_rate=wi.rate, amount=(move_qty * wi.rate).quantize(Decimal("0.000001")),
            )
        )
        transferred_map[wi.item_id] = move_qty
    await db.flush()
    await db.commit()

    # "Material Transfer for Manufacture" moves value between inventory accounts exactly
    # like a plain Material Transfer — submit_stock_entry handles it on the transfer path.
    await se_service.submit_stock_entry(db, entry.id, user)

    wo = await get_work_order(db, wo.id, user.company_id)
    wo.material_transferred_qty = (wo.material_transferred_qty + payload.qty).quantize(Decimal("0.000001"))
    for wi in wo.items:
        if wi.item_id in transferred_map:
            wi.transferred_qty = (wi.transferred_qty + transferred_map[wi.item_id]).quantize(
                Decimal("0.000001")
            )
    if wo.status == "Not Started":
        wo.status = "In Process"
    wo.modified_by = user.id
    await db.flush()
    await db.commit()
    wo = await get_work_order(db, wo.id, user.company_id)
    entry = await se_service.get_stock_entry(db, entry.id, company.id)
    return wo, entry
