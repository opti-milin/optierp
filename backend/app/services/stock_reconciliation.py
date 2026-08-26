"""Stock Reconciliation service — Module 03.

Sets each (item, warehouse) row to an absolute target qty + valuation rate.
On submit it posts ONLY the difference vs the current Bin:
  * one reconciliation SLE per changed row (qty delta + value delta), which
    overrides the moving-average rate to the entered/resolved target
  * perpetual inventory GL for the net value difference against Stock
    Adjustment:
        value up   -> Dr inventory(warehouse) / Cr Stock Adjustment
        value down -> Dr Stock Adjustment      / Cr inventory(warehouse)

This is the documented way to enter opening balances and correct physical
counts — and the escape hatch when a cancellation elsewhere is blocked because
stock was since consumed at a different valuation.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.stock import StockReconciliation, StockReconciliationItem
from app.schemas.stock import StockReconciliationCreate
from app.services import gl
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.stock_common import (
    STOCK_NAMING_SERIES,
    get_items,
    get_warehouse,
    inventory_account_for,
    require_stock_item,
)
from app.services.stock_ledger import (
    ReconcileRow,
    get_bin,
    make_reconciliation_entries,
    make_reverse_sl_entries,
)

ZERO = Decimal("0")


async def create_stock_reconciliation(
    db: AsyncSession, payload: StockReconciliationCreate, user: CurrentUser
) -> StockReconciliation:
    company = await get_company(db, user.company_id)
    items = await get_items(db, {r.item_id for r in payload.items}, company.id)

    # one row per (item, warehouse) — duplicate targets would be ambiguous
    seen: set[tuple[uuid.UUID, uuid.UUID | None]] = set()

    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Reconciliation"], company.id)
    recon = StockReconciliation(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose=payload.purpose,
        set_warehouse_id=payload.set_warehouse_id,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(recon)
    await db.flush()

    for idx, row in enumerate(payload.items, start=1):
        item = items[row.item_id]
        require_stock_item(item)
        warehouse_id = row.warehouse_id or payload.set_warehouse_id or item.default_warehouse_id
        if warehouse_id is None:
            raise ValidationError(
                f"Item row {idx} ('{item.item_code}'): warehouse is required", field="items"
            )
        await get_warehouse(db, warehouse_id, company.id)
        key = (item.id, warehouse_id)
        if key in seen:
            raise ValidationError(
                f"Item '{item.item_code}' appears twice for the same warehouse", field="items"
            )
        seen.add(key)
        db.add(
            StockReconciliationItem(
                reconciliation_id=recon.id,
                idx=idx,
                item_id=item.id,
                warehouse_id=warehouse_id,
                qty=row.qty,
                uom=row.uom or item.stock_uom,
                valuation_rate=row.valuation_rate or ZERO,
            )
        )
    await db.flush()
    await log_audit(
        db, doctype="Stock Reconciliation", document_id=recon.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_stock_reconciliation(db, recon.id, company.id)


async def get_stock_reconciliation(
    db: AsyncSession, recon_id: uuid.UUID, company_id: uuid.UUID | None
) -> StockReconciliation:
    recon = await db.scalar(
        select(StockReconciliation)
        .options(selectinload(StockReconciliation.items))
        .where(StockReconciliation.id == recon_id, StockReconciliation.company_id == company_id)
    )
    if recon is None:
        raise NotFoundError("Stock Reconciliation not found")
    return recon


async def list_stock_reconciliations(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    purpose: str | None = None,
    *, include_cancelled: bool = False,
) -> tuple[list[StockReconciliation], int]:
    stmt = (
        select(StockReconciliation)
        .where(StockReconciliation.company_id == company_id)
        .order_by(StockReconciliation.posting_date.desc(), StockReconciliation.creation.desc())
    )
    if purpose:
        stmt = stmt.where(StockReconciliation.purpose == purpose)
    # Cancelled documents keep their reversing entries — that is the audit
    # trail — but they are noise in the working list, and a rolled-back import
    # would otherwise leave its cancelled documents on every screen. Pass
    # include_cancelled=True to see them.
    if not include_cancelled:
        stmt = stmt.where(StockReconciliation.docstatus != DOCSTATUS_CANCELLED)
    return await paginate(db, stmt, page, page_size)


async def submit_stock_reconciliation(
    db: AsyncSession, recon_id: uuid.UUID, user: CurrentUser
) -> StockReconciliation:
    recon = await get_stock_reconciliation(db, recon_id, user.company_id)
    require_draft(recon.docstatus)
    company = await get_company(db, recon.company_id)
    items = await get_items(db, {r.item_id for r in recon.items}, company.id)

    # resolve the target rate per row: entered rate, else current bin rate,
    # else item master valuation rate — a positive target qty at a zero rate
    # would silently zero out value, so it is rejected.
    recon_rows: list[ReconcileRow] = []
    for row in recon.items:
        item = items[row.item_id]
        target_rate = row.valuation_rate
        if target_rate == ZERO:
            bin_row = await get_bin(db, row.item_id, row.warehouse_id)
            if bin_row is not None and bin_row.valuation_rate > ZERO:
                target_rate = bin_row.valuation_rate
            elif item.valuation_rate > ZERO:
                target_rate = item.valuation_rate
        if row.qty > ZERO and target_rate == ZERO:
            raise ValidationError(
                f"Item '{item.item_code}': a valuation rate is required to set a positive quantity",
                field="valuation_rate",
            )
        recon_rows.append(
            ReconcileRow(
                item_id=row.item_id, warehouse_id=row.warehouse_id,
                qty=row.qty, valuation_rate=target_rate,
            )
        )

    entries = await make_reconciliation_entries(
        db, company_id=company.id, voucher_type="Stock Reconciliation", voucher_id=recon.id,
        voucher_no=recon.name, posting_date=recon.posting_date, rows=recon_rows,
        items=items, user_id=user.id,
    )
    by_key = {(e.item_id, e.warehouse_id): e for e in entries}

    # store before → after snapshots on each row (derived from the locked SLE)
    difference_amount = ZERO
    for row, recon_row in zip(recon.items, recon_rows):
        entry = by_key.get((row.item_id, row.warehouse_id))
        row.valuation_rate = recon_row.valuation_rate
        if entry is None:  # unchanged row — no ledger movement
            row.current_qty = row.qty
            row.current_valuation_rate = recon_row.valuation_rate
            row.amount_difference = ZERO
            continue
        current_qty = entry.qty_after_transaction - entry.actual_qty
        current_value = entry.stock_value - entry.stock_value_difference
        row.current_qty = current_qty
        row.current_valuation_rate = (current_value / current_qty) if current_qty else ZERO
        row.amount_difference = entry.stock_value_difference
        difference_amount += entry.stock_value_difference
    recon.difference_amount = difference_amount

    # --- perpetual inventory GL: net the difference against Stock Adjustment ---
    if company.enable_perpetual_inventory and entries:
        if company.stock_adjustment_account_id is None:
            raise ValidationError("Company has no Stock Adjustment account configured")
        net: dict[uuid.UUID, Decimal] = {}
        for entry in entries:
            warehouse = await get_warehouse(db, entry.warehouse_id, company.id)
            inv_acc = inventory_account_for(company, warehouse)
            vd = entry.stock_value_difference  # +inventory up / -inventory down
            net[inv_acc] = net.get(inv_acc, ZERO) + vd
            net[company.stock_adjustment_account_id] = (
                net.get(company.stock_adjustment_account_id, ZERO) - vd
            )
        gl_rows = [
            gl.GLRow(
                account_id=acc,
                debit=v if v > ZERO else ZERO,
                credit=-v if v < ZERO else ZERO,
                cost_center_id=(
                    company.default_cost_center_id
                    if acc == company.stock_adjustment_account_id
                    else None
                ),
            )
            for acc, v in net.items()
            if v != ZERO
        ]
        if gl_rows:
            await gl.make_gl_entries(
                db, company_id=company.id, voucher_type="Stock Reconciliation", voucher_id=recon.id,
                voucher_no=recon.name, posting_date=recon.posting_date, rows=gl_rows,
                user_id=user.id, remarks=recon.remarks,
            )

    recon.docstatus = DOCSTATUS_SUBMITTED
    recon.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Stock Reconciliation", document_id=recon.id, action="SUBMIT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_stock_reconciliation(db, recon.id, user.company_id)


async def cancel_stock_reconciliation(
    db: AsyncSession, recon_id: uuid.UUID, user: CurrentUser
) -> StockReconciliation:
    recon = await get_stock_reconciliation(db, recon_id, user.company_id)
    require_submitted(recon.docstatus)
    items = await get_items(
        db, {r.item_id for r in recon.items}, recon.company_id, allow_disabled=True
    )

    await make_reverse_sl_entries(
        db, voucher_type="Stock Reconciliation", voucher_id=recon.id, items=items, user_id=user.id
    )
    await gl.make_reverse_gl_entries(
        db, voucher_type="Stock Reconciliation", voucher_id=recon.id, user_id=user.id
    )

    recon.docstatus = DOCSTATUS_CANCELLED
    recon.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Stock Reconciliation", document_id=recon.id, action="CANCEL",
        user_id=user.id, company_id=recon.company_id,
    )
    await db.commit()
    return await get_stock_reconciliation(db, recon.id, user.company_id)
