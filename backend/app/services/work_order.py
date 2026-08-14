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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.manufacturing import (
    BOM,
    JobCard,
    WorkOrder,
    WorkOrderItem,
    WorkOrderOperation,
    Workstation,
)
from app.models.stock import Item, StockEntry, StockEntryItem
from app.schemas.manufacturing import (
    MaterialAvailabilityResponse,
    MaterialAvailabilityRow,
    WorkOrderConsumeIn,
    WorkOrderCreate,
    WorkOrderFinishIn,
    WorkOrderFinishLineIn,
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
from app.services.stock_batches import (
    check_batch_not_expired,
    clean_batch_no,
    validate_line_batch,
)
from app.services.bom_explosion import explode_bom, explode_scrap
from app.services.stock_common import (
    STOCK_NAMING_SERIES,
    get_item,
    get_items,
    get_warehouse,
    require_stock_item,
)
from app.services.stock_serials import parse_serials, serials_to_text, validate_line_serials

ZERO = Decimal("0")
QTY_EPS = Decimal("0.000001")  # tolerance for "fully produced" comparisons


def _consume_source_for_line(wo: WorkOrder, wi: WorkOrderItem) -> uuid.UUID | None:
    """Warehouse to consume a required item from.

    When the WO uses the WIP transfer step, materials are staged in ``wip_warehouse_id`` —
    Finish / Consume / availability must use that warehouse, not the component's original
    ``source_warehouse_id`` (transfer origin only).
    """
    if not wo.skip_transfer and wo.wip_warehouse_id is not None:
        return wo.wip_warehouse_id
    return wi.source_warehouse_id or wo.source_warehouse_id


def _tracking_map(lines: list[WorkOrderFinishLineIn]) -> dict[uuid.UUID, WorkOrderFinishLineIn]:
    return {line.item_id: line for line in lines}


async def _apply_line_tracking(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    item: Item,
    qty: Decimal,
    posting_date: date,
    serial_nos: list[str] | None,
    batch_no: str | None,
    consuming: bool,
) -> tuple[str | None, str | None]:
    """Validate and normalise serial/batch for one Manufacture/Transfer line."""
    serials = parse_serials(serial_nos)
    validate_line_serials(item, serials, qty)
    cleaned_batch = clean_batch_no(batch_no)
    await validate_line_batch(db, company_id, item, cleaned_batch)
    if consuming and cleaned_batch:
        await check_batch_not_expired(db, company_id, item, cleaned_batch, posting_date)
    if item.has_serial_no and not serials:
        raise ValidationError(
            f"Serial numbers are required for '{item.item_code}'",
            field="serial_nos",
        )
    if item.has_batch_no and not cleaned_batch:
        raise ValidationError(
            f"A batch is required for '{item.item_code}'",
            field="batch_no",
        )
    return serials_to_text(serials) if serials else None, cleaned_batch


# --- fetch ---------------------------------------------------------------------------


async def get_work_order(
    db: AsyncSession, work_order_id: uuid.UUID, company_id: uuid.UUID | None
) -> WorkOrder:
    wo = await db.scalar(
        select(WorkOrder)
        .options(selectinload(WorkOrder.items), selectinload(WorkOrder.operations))
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


async def create_work_order(db: AsyncSession, payload: WorkOrderCreate, user: CurrentUser) -> WorkOrder:
    company = await get_company(db, user.company_id)
    bom = await db.scalar(
        select(BOM)
        .options(
            selectinload(BOM.items),
            selectinload(BOM.scrap_items),
            selectinload(BOM.operations),
        )
        .where(BOM.id == payload.bom_id, BOM.company_id == company.id)
    )
    if bom is None:
        raise NotFoundError("BOM not found")
    if bom.docstatus != DOCSTATUS_SUBMITTED or not bom.is_active:
        raise ValidationError("The BOM must be submitted and active", field="bom_id")
    if not bom.items:
        raise ValidationError("The BOM has no components", field="bom_id")

    production_item = await get_item(db, bom.production_item_id, company.id)
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

    # Multi-level / phantom-aware explosion: phantoms flatten to leaves; stocked
    # sub-assemblies remain as a single required line.
    exploded = await explode_bom(db, bom, payload.qty, flatten_all=False)
    if not exploded:
        raise ValidationError("The BOM explosion produced no required components", field="bom_id")
    await get_items(db, {c.item_id for c in exploded}, company.id)
    scale = payload.qty / bom.quantity
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

    for idx, comp in enumerate(exploded, start=1):
        db.add(
            WorkOrderItem(
                work_order_id=wo.id,
                idx=idx,
                item_id=comp.item_id,
                required_qty=comp.stock_qty,
                source_warehouse_id=comp.source_warehouse_id or source_warehouse_id,
                rate=comp.rate,
                amount=(comp.stock_qty * comp.rate).quantize(Decimal("0.000001")),
                allow_alternative_item=comp.allow_alternative_item,
            )
        )
    # Copy BOM operations (time scaled by WO qty / BOM batch qty).
    for idx, bop in enumerate(bom.operations, start=1):
        time_mins = (bop.time_in_mins * scale).quantize(Decimal("0.000001"))
        planned_cost = (bop.operating_cost * scale).quantize(Decimal("0.000001"))
        db.add(
            WorkOrderOperation(
                work_order_id=wo.id,
                idx=idx,
                operation_id=bop.operation_id,
                workstation_id=bop.workstation_id,
                time_in_mins=time_mins,
                hour_rate=bop.hour_rate,
                planned_operating_cost=planned_cost,
                completed_qty=ZERO,
                status="Pending",
                description=bop.description,
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


async def _capacity_warnings(db: AsyncSession, wo: WorkOrder) -> list[str]:
    """Soft workstation overload check (warn only). Gated by Manufacturing Settings."""
    settings = await get_manufacturing_settings(db, wo.company_id)
    if not settings.get("capacity_planning_enabled"):
        return []
    warnings: list[str] = []
    by_ws: dict[uuid.UUID, Decimal] = {}
    for op in wo.operations:
        if op.workstation_id is None:
            continue
        by_ws[op.workstation_id] = by_ws.get(op.workstation_id, ZERO) + op.time_in_mins
    for ws_id, add_mins in by_ws.items():
        ws = await db.get(Workstation, ws_id)
        if ws is None or ws.company_id != wo.company_id:
            continue
        capacity_mins = (ws.working_hours * Decimal("60")).quantize(Decimal("0.000001"))
        if capacity_mins <= ZERO:
            continue
        # Open WO load on this workstation (submitted, not completed/cancelled).
        existing = (
            await db.execute(
                select(func.coalesce(func.sum(WorkOrderOperation.time_in_mins), ZERO)).where(
                    WorkOrderOperation.workstation_id == ws_id,
                    WorkOrderOperation.work_order_id != wo.id,
                    WorkOrderOperation.work_order_id.in_(
                        select(WorkOrder.id).where(
                            WorkOrder.company_id == wo.company_id,
                            WorkOrder.docstatus == DOCSTATUS_SUBMITTED,
                            WorkOrder.status.in_(("Not Started", "In Process", "Stopped")),
                        )
                    ),
                )
            )
        ).scalar_one()
        existing_mins = Decimal(existing)
        total = existing_mins + add_mins
        if total > capacity_mins + QTY_EPS:
            warnings.append(
                f"Workstation '{ws.workstation_name}' may be overloaded: "
                f"{total.quantize(Decimal('0.01'))} planned mins vs "
                f"{capacity_mins.quantize(Decimal('0.01'))} available mins/day"
            )
    return warnings


async def _create_job_cards_for_wo(db: AsyncSession, wo: WorkOrder, user: CurrentUser) -> None:
    """One Job Card per Work Order operation (idempotent via unique WO-op constraint)."""
    if not wo.operations:
        return
    from app.services import job_card as jc_service

    for op in wo.operations:
        existing = await db.scalar(
            select(JobCard.id).where(JobCard.work_order_operation_id == op.id).limit(1)
        )
        if existing is not None:
            continue
        await jc_service.create_job_card_for_operation(db, wo, op, user, commit=False)


async def submit_work_order(
    db: AsyncSession, work_order_id: uuid.UUID, user: CurrentUser
) -> tuple[WorkOrder, list[str]]:
    wo = await get_work_order(db, work_order_id, user.company_id)
    require_draft(wo.docstatus)
    warnings = await _capacity_warnings(db, wo)
    wo.docstatus = DOCSTATUS_SUBMITTED
    wo.status = "Not Started"
    wo.modified_by = user.id
    await db.flush()
    await _create_job_cards_for_wo(db, wo, user)
    await log_audit(
        db, doctype="Work Order", document_id=wo.id, action="SUBMIT",
        user_id=user.id, company_id=wo.company_id,
    )
    await db.commit()
    return await get_work_order(db, wo.id, user.company_id), warnings


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
    if any(wi.consumed_qty > ZERO for wi in wo.items):
        raise ValidationError(
            "Cannot cancel: materials have already been consumed. Cancel Material "
            "Consumption Stock Entries first.",
            code="ERR_DOCSTATUS",
        )
    # Cancel open Job Cards with no completed qty.
    jcs = (
        await db.execute(
            select(JobCard).where(
                JobCard.work_order_id == wo.id,
                JobCard.docstatus != DOCSTATUS_CANCELLED,
            )
        )
    ).scalars().all()
    for jc in jcs:
        if jc.total_completed_qty > ZERO:
            raise ValidationError(
                f"Cannot cancel: Job Card {jc.name} has completed quantity — "
                "cancel or reverse Job Card progress first.",
                code="ERR_DOCSTATUS",
            )
        jc.docstatus = DOCSTATUS_CANCELLED
        jc.status = "Cancelled"
        jc.modified_by = user.id
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
    elif (
        wo.produced_qty > ZERO
        or wo.material_transferred_qty > ZERO
        or any(wi.consumed_qty > ZERO for wi in wo.items)
    ):
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

    from app.services import quality_inspection as qi_service

    await qi_service.require_accepted_inspection(
        db,
        company_id=wo.company_id,
        item=production_item,
        reference_type="Work Order",
        reference_id=wo.id,
        qty=payload.qty,
        already_done=wo.produced_qty,
    )

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

    bom = await db.scalar(
        select(BOM)
        .options(selectinload(BOM.items), selectinload(BOM.scrap_items))
        .where(BOM.id == wo.bom_id, BOM.company_id == wo.company_id)
    )
    if bom is None:
        raise NotFoundError("BOM not found")

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
    consumed_map: dict[uuid.UUID, Decimal] = {}  # keyed by planned WO item_id
    tracking = _tracking_map(payload.consumed)
    planned_ids = {wi.item_id for wi in wo.items}
    substitute_ids = {
        line.substitute_item_id
        for line in payload.consumed
        if line.substitute_item_id is not None
    }
    component_items = await get_items(db, planned_ids | substitute_ids, company.id)

    for wi in wo.items:
        batch_need = (wi.required_qty * scale).quantize(Decimal("0.000001"))
        pending = max(ZERO, wi.required_qty - wi.consumed_qty)
        consume_qty = min(batch_need, pending)
        if consume_qty <= ZERO:
            continue
        source_id = _consume_source_for_line(wo, wi)
        if source_id is None:
            raise ValidationError(
                f"No source warehouse for component '{wi.item_code}' — set one on the Work "
                "Order or the BOM",
                field="source_warehouse_id",
            )
        await get_warehouse(db, source_id, company.id)

        pick = tracking.get(wi.item_id)
        consume_item_id = wi.item_id
        if pick is not None and pick.substitute_item_id is not None:
            if not wi.allow_alternative_item:
                raise ValidationError(
                    f"Component '{wi.item_code}' does not allow an alternate item",
                    field="substitute_item_id",
                )
            if pick.substitute_item_id == wi.item_id:
                raise ValidationError(
                    "substitute_item_id must differ from the planned component",
                    field="substitute_item_id",
                )
            if pick.substitute_item_id == wo.production_item_id:
                raise ValidationError(
                    "Cannot substitute the finished good as a component",
                    field="substitute_item_id",
                )
            from app.services import item_alternative as item_alt_svc

            await item_alt_svc.assert_valid_substitute(
                db,
                company.id,
                planned_item_id=wi.item_id,
                substitute_item_id=pick.substitute_item_id,
                planned_item_code=wi.item_code,
            )
            consume_item_id = pick.substitute_item_id
            require_stock_item(component_items[consume_item_id])

        citem = component_items[consume_item_id]
        serial_text, batch = await _apply_line_tracking(
            db,
            company_id=company.id,
            item=citem,
            qty=consume_qty,
            posting_date=payload.posting_date,
            serial_nos=pick.serial_nos if pick else None,
            batch_no=pick.batch_no if pick else None,
            consuming=True,
        )
        idx += 1
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id, idx=idx, item_id=consume_item_id,
                source_warehouse_id=source_id, target_warehouse_id=None,
                qty=consume_qty, uom=None, conversion_factor=Decimal("1"), stock_qty=consume_qty,
                basic_rate=wi.rate, amount=(consume_qty * wi.rate).quantize(Decimal("0.000001")),
                serial_nos=serial_text, batch_no=batch,
            )
        )
        consumed_map[wi.item_id] = consume_qty

    # finished-good production row (valued at consumed + operating − scrap, set on submit)
    idx += 1
    fg_serial, fg_batch = await _apply_line_tracking(
        db,
        company_id=company.id,
        item=production_item,
        qty=produce_qty,
        posting_date=payload.posting_date,
        serial_nos=payload.finished_serial_nos,
        batch_no=payload.finished_batch_no,
        consuming=False,
    )
    estimate = bom.cost_per_unit if bom.cost_per_unit > ZERO else Decimal("0.000001")
    db.add(
        StockEntryItem(
            stock_entry_id=entry.id, idx=idx, item_id=wo.production_item_id,
            source_warehouse_id=None, target_warehouse_id=wo.fg_warehouse_id,
            qty=produce_qty, uom=None, conversion_factor=Decimal("1"), stock_qty=produce_qty,
            basic_rate=estimate, amount=(produce_qty * estimate).quantize(Decimal("0.000001")),
            serial_nos=fg_serial, batch_no=fg_batch,
        )
    )

    # scrap / by-product finished rows (recovery value becomes their value weight)
    scrap_rows = await explode_scrap(db, bom, produce_qty)
    scrap_item_ids = {s.item_id for s in scrap_rows}
    if scrap_item_ids:
        await get_items(db, scrap_item_ids, company.id)
    for scrap in scrap_rows:
        target = scrap.stock_warehouse_id or wo.fg_warehouse_id
        await get_warehouse(db, target, company.id)
        idx += 1
        scrap_rate = scrap.rate if scrap.rate > ZERO else ZERO
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id, idx=idx, item_id=scrap.item_id,
                source_warehouse_id=None, target_warehouse_id=target,
                qty=scrap.stock_qty, uom=None, conversion_factor=Decimal("1"),
                stock_qty=scrap.stock_qty,
                basic_rate=scrap_rate,
                amount=(scrap.stock_qty * scrap_rate).quantize(Decimal("0.000001")),
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
    produced = sum(
        (
            r.stock_qty
            for r in entry.items
            if r.target_warehouse_id is not None
            and r.source_warehouse_id is None
            and r.item_id == wo.production_item_id
        ),
        ZERO,
    )
    consumed_by_item: dict[uuid.UUID, Decimal] = {}
    for r in entry.items:
        if r.source_warehouse_id is not None and r.target_warehouse_id is None:
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


async def revert_transfer_entry(db: AsyncSession, entry: StockEntry, user: CurrentUser) -> None:
    """Roll back material_transferred_qty when a Transfer-for-Manufacture Stock Entry is cancelled."""
    wo = await db.scalar(
        select(WorkOrder).options(selectinload(WorkOrder.items)).where(WorkOrder.id == entry.work_order_id)
    )
    if wo is None:
        return
    # Transfer rows are source→WIP; FG scale is inferred from the first component's share.
    transferred_by_item: dict[uuid.UUID, Decimal] = {}
    for r in entry.items:
        if r.source_warehouse_id is not None and r.target_warehouse_id is not None:
            transferred_by_item[r.item_id] = transferred_by_item.get(r.item_id, ZERO) + r.stock_qty
    if not transferred_by_item or not wo.items:
        return
    # Back out FG-equivalent qty from any component: transferred / (required/wo.qty)
    sample = wo.items[0]
    per_unit = (sample.required_qty / wo.qty) if wo.qty else ZERO
    fg_qty = ZERO
    if per_unit > ZERO and sample.item_id in transferred_by_item:
        fg_qty = (transferred_by_item[sample.item_id] / per_unit).quantize(Decimal("0.000001"))
    wo.material_transferred_qty = max(ZERO, wo.material_transferred_qty - fg_qty).quantize(
        Decimal("0.000001")
    )
    for wi in wo.items:
        if wi.item_id in transferred_by_item:
            wi.transferred_qty = max(ZERO, wi.transferred_qty - transferred_by_item[wi.item_id]).quantize(
                Decimal("0.000001")
            )
    if wo.status != "Stopped":
        _recompute_status(wo)
    wo.modified_by = user.id
    await db.flush()


async def revert_consumption_entry(db: AsyncSession, entry: StockEntry, user: CurrentUser) -> None:
    """Roll back consumed_qty when a Material Consumption for Manufacture SE is cancelled."""
    wo = await db.scalar(
        select(WorkOrder).options(selectinload(WorkOrder.items)).where(WorkOrder.id == entry.work_order_id)
    )
    if wo is None:
        return
    consumed_by_item: dict[uuid.UUID, Decimal] = {}
    for r in entry.items:
        if r.source_warehouse_id is not None and r.target_warehouse_id is None:
            consumed_by_item[r.item_id] = consumed_by_item.get(r.item_id, ZERO) + r.stock_qty
    for wi in wo.items:
        if wi.item_id in consumed_by_item:
            wi.consumed_qty = max(ZERO, wi.consumed_qty - consumed_by_item[wi.item_id]).quantize(
                Decimal("0.000001")
            )
    if wo.status != "Stopped":
        _recompute_status(wo)
    wo.modified_by = user.id
    await db.flush()


# --- Phase 3: material availability + shortfall Material Request ----------------------


async def material_availability(
    db: AsyncSession, work_order_id: uuid.UUID, company_id: uuid.UUID | None
) -> MaterialAvailabilityResponse:
    """On-hand vs still-required position per component, plus how many finished units the
    current stock can support (the min over components)."""
    wo = await get_work_order(db, work_order_id, company_id)
    remaining_fg = max(ZERO, wo.qty - wo.produced_qty)
    rows: list[MaterialAvailabilityRow] = []
    can_finish = remaining_fg
    for wi in wo.items:
        source_id = _consume_source_for_line(wo, wi)
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
        material_request_type="Manufacture",
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
    tracking = _tracking_map(payload.consumed)
    component_items = await get_items(db, {wi.item_id for wi in wo.items}, company.id)
    for wi in wo.items:
        move_qty = (wi.required_qty * scale).quantize(Decimal("0.000001"))
        if move_qty <= ZERO:
            continue
        source_id = wi.source_warehouse_id or wo.source_warehouse_id
        if source_id is None:
            raise ValidationError(
                f"No source warehouse for component '{wi.item_code}'", field="source_warehouse_id"
            )
        citem = component_items[wi.item_id]
        pick = tracking.get(wi.item_id)
        serial_text, batch = await _apply_line_tracking(
            db,
            company_id=company.id,
            item=citem,
            qty=move_qty,
            posting_date=payload.posting_date,
            serial_nos=pick.serial_nos if pick else None,
            batch_no=pick.batch_no if pick else None,
            consuming=True,
        )
        idx += 1
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id, idx=idx, item_id=wi.item_id,
                source_warehouse_id=source_id, target_warehouse_id=wo.wip_warehouse_id,
                qty=move_qty, uom=None, conversion_factor=Decimal("1"), stock_qty=move_qty,
                basic_rate=wi.rate, amount=(move_qty * wi.rate).quantize(Decimal("0.000001")),
                serial_nos=serial_text, batch_no=batch,
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


async def consume_for_manufacture(
    db: AsyncSession, work_order_id: uuid.UUID, payload: WorkOrderConsumeIn, user: CurrentUser
) -> tuple[WorkOrder, StockEntry]:
    """Consume raws mid-process without finishing (Material Consumption for Manufacture).

    Advances ``consumed_qty``; Finish later only consumes the remaining pending qty.
    """
    wo = await get_work_order(db, work_order_id, user.company_id)
    require_submitted(wo.docstatus)
    if wo.status == "Stopped":
        raise ValidationError("Work Order is stopped — resume it before consuming", field="status")
    if wo.status == "Completed":
        raise ValidationError("Work Order is already completed", field="status")

    settings = await get_manufacturing_settings(db, wo.company_id)
    over_pct = Decimal(str(settings.get("over_production_percentage") or "0"))
    allowed_total = wo.qty * (Decimal("1") + over_pct / Decimal("100"))
    # Cap consumption by remaining FG-equivalent materials still pending.
    remaining_fg = allowed_total - wo.produced_qty
    if payload.qty > remaining_fg + QTY_EPS:
        raise ValidationError(
            f"Cannot consume for {payload.qty}: only {remaining_fg} left on this Work Order",
            field="qty",
        )
    company = await get_company(db, wo.company_id)
    scale = payload.qty / wo.qty

    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Entry"], company.id)
    entry = StockEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose="Material Consumption for Manufacture",
        work_order_id=wo.id,
        remarks=f"Material consumption for Work Order {wo.name}",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()

    idx = 0
    consumed_map: dict[uuid.UUID, Decimal] = {}
    tracking = _tracking_map(payload.consumed)
    component_items = await get_items(db, {wi.item_id for wi in wo.items}, company.id)
    for wi in wo.items:
        batch_need = (wi.required_qty * scale).quantize(Decimal("0.000001"))
        pending = max(ZERO, wi.required_qty - wi.consumed_qty)
        consume_qty = min(batch_need, pending)
        if consume_qty <= ZERO:
            continue
        source_id = _consume_source_for_line(wo, wi)
        if source_id is None:
            raise ValidationError(
                f"No source warehouse for component '{wi.item_code}'",
                field="source_warehouse_id",
            )
        await get_warehouse(db, source_id, company.id)
        citem = component_items[wi.item_id]
        pick = tracking.get(wi.item_id)
        serial_text, batch = await _apply_line_tracking(
            db,
            company_id=company.id,
            item=citem,
            qty=consume_qty,
            posting_date=payload.posting_date,
            serial_nos=pick.serial_nos if pick else None,
            batch_no=pick.batch_no if pick else None,
            consuming=True,
        )
        idx += 1
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id, idx=idx, item_id=wi.item_id,
                source_warehouse_id=source_id, target_warehouse_id=None,
                qty=consume_qty, uom=None, conversion_factor=Decimal("1"), stock_qty=consume_qty,
                basic_rate=wi.rate, amount=(consume_qty * wi.rate).quantize(Decimal("0.000001")),
                serial_nos=serial_text, batch_no=batch,
            )
        )
        consumed_map[wi.item_id] = consume_qty

    if not consumed_map:
        raise ValidationError(
            "Nothing left to consume — materials are already fully consumed",
            field="qty",
        )

    await db.flush()
    await db.commit()
    # Classic out-only path (like Material Issue) — submit posts SLE + GL.
    await se_service.submit_stock_entry(db, entry.id, user)

    wo = await get_work_order(db, wo.id, user.company_id)
    for wi in wo.items:
        if wi.item_id in consumed_map:
            wi.consumed_qty = (wi.consumed_qty + consumed_map[wi.item_id]).quantize(
                Decimal("0.000001")
            )
    if wo.actual_start_date is None:
        wo.actual_start_date = payload.posting_date
    _recompute_status(wo)
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
