"""Stock Entry service — Module 03 + Manufacturing purposes.

Purposes:
  Material Receipt / Issue / Transfer — classic stock vouchers
  Manufacture — driven by Work Order (consume raws + produce FG at input cost)
  Material Transfer for Manufacture — WIP transfer (same GL as Material Transfer)
  Repack — BOM-less consume + produce (optional operating cost)

Perpetual inventory GL on submit (skipped when the company disables it):
  Receipt:  Dr inventory(target)   / Cr Stock Adjustment
  Issue:    Dr Stock Adjustment    / Cr inventory(source)
  Transfer: Dr inventory(target)   / Cr inventory(source)  (skipped if same account)
  Manufacture / Repack:
            Cr inventory(source) for consumed value
            Dr inventory(target) for finished SLE value
            Cr operating_cost account for additional cost (if any)
            residual (negative-bin rate reset) → Stock Adjustment
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
from app.models.stock import StockEntry, StockEntryItem
from app.schemas.stock import StockEntryCreate
from app.services import gl
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.manufacturing_common import require_expense_account
from app.services.pagination import paginate
from app.services.stock_batches import (
    check_batch_not_expired,
    clean_batch_no,
    validate_line_batch,
)
from app.services.stock_common import (
    STOCK_NAMING_SERIES,
    get_items,
    get_warehouse,
    inventory_account_for,
    require_stock_item,
    resolve_conversion_factor,
)
from app.services.stock_ledger import SLERow, get_bin, make_reverse_sl_entries, make_sl_entries
from app.services.stock_serials import (
    create_serials,
    delete_serials,
    move_serials,
    parse_serials,
    serials_from_text,
    serials_to_text,
    validate_line_serials,
)

ZERO = Decimal("0")
MFG_COST_PURPOSES = frozenset({"Manufacture", "Repack", "Subcontract Receipt"})
TRANSFER_PURPOSES = frozenset(
    {"Material Transfer", "Material Transfer for Manufacture", "Send to Subcontractor"}
)


async def create_stock_entry(
    db: AsyncSession, payload: StockEntryCreate, user: CurrentUser
) -> StockEntry:
    company = await get_company(db, user.company_id)
    items = await get_items(db, {row.item_id for row in payload.items}, company.id)

    if payload.purpose == "Repack":
        await _validate_repack_create(db, payload, company.id)
    elif payload.operating_cost > ZERO or payload.operating_cost_account_id is not None:
        raise ValidationError(
            "Operating cost is only allowed on Repack (or Manufacture via Work Order)",
            field="operating_cost",
        )

    needs_source = payload.purpose in ("Material Issue", "Material Transfer")
    needs_target = payload.purpose in ("Material Receipt", "Material Transfer")
    if payload.purpose == "Repack":
        needs_source = False
        needs_target = False

    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Entry"], company.id)
    entry = StockEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose=payload.purpose,
        from_warehouse_id=payload.from_warehouse_id,
        to_warehouse_id=payload.to_warehouse_id,
        operating_cost=payload.operating_cost if payload.purpose == "Repack" else ZERO,
        operating_cost_account_id=(
            payload.operating_cost_account_id if payload.purpose == "Repack" else None
        ),
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()

    total_amount = ZERO
    for idx, row in enumerate(payload.items, start=1):
        item = items[row.item_id]
        require_stock_item(item)

        if payload.purpose == "Repack":
            if row.is_finished_item:
                source_id = None
                target_id = row.target_warehouse_id or payload.to_warehouse_id
                if target_id is None:
                    raise ValidationError(
                        f"Item row {idx}: finished row needs a target warehouse",
                        field="items",
                    )
            else:
                source_id = row.source_warehouse_id or payload.from_warehouse_id
                target_id = None
                if source_id is None:
                    raise ValidationError(
                        f"Item row {idx}: consumed row needs a source warehouse",
                        field="items",
                    )
        else:
            source_id = row.source_warehouse_id or payload.from_warehouse_id
            target_id = row.target_warehouse_id or payload.to_warehouse_id
            if needs_source and source_id is None:
                raise ValidationError(f"Item row {idx}: source warehouse is required", field="items")
            if needs_target and target_id is None:
                raise ValidationError(f"Item row {idx}: target warehouse is required", field="items")

        if source_id is not None:
            await get_warehouse(db, source_id, company.id)
        if target_id is not None:
            await get_warehouse(db, target_id, company.id)

        uom = row.uom or item.stock_uom
        factor = resolve_conversion_factor(item, uom)
        stock_qty = row.qty * factor
        serials = parse_serials(row.serial_nos)
        validate_line_serials(item, serials, stock_qty)
        batch_no = clean_batch_no(row.batch_no)
        await validate_line_batch(db, company.id, item, batch_no)
        if source_id is not None and target_id is None:
            await check_batch_not_expired(db, company.id, item, batch_no, payload.posting_date)

        basic_rate = row.basic_rate
        if payload.purpose == "Material Receipt" and basic_rate == ZERO:
            bin_row = await get_bin(db, row.item_id, target_id) if target_id else None
            stock_rate = (
                bin_row.valuation_rate
                if bin_row is not None and bin_row.valuation_rate > ZERO
                else item.valuation_rate
            )
            basic_rate = stock_rate * factor
        # Repack finished rows keep basic_rate as an optional value WEIGHT until submit.
        amount = row.qty * basic_rate
        total_amount += amount
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id,
                idx=idx,
                item_id=row.item_id,
                source_warehouse_id=source_id,
                target_warehouse_id=target_id,
                qty=row.qty,
                uom=uom,
                conversion_factor=factor,
                stock_qty=stock_qty,
                basic_rate=basic_rate,
                amount=amount,
                serial_nos=serials_to_text(serials),
                batch_no=batch_no,
            )
        )
    entry.total_amount = total_amount
    await db.flush()
    await log_audit(
        db,
        doctype="Stock Entry",
        document_id=entry.id,
        action="INSERT",
        user_id=user.id,
        company_id=company.id,
    )
    await db.commit()
    return await get_stock_entry(db, entry.id, company.id)


async def _validate_repack_create(
    db: AsyncSession, payload: StockEntryCreate, company_id: uuid.UUID
) -> None:
    finished = [r for r in payload.items if r.is_finished_item]
    consumed = [r for r in payload.items if not r.is_finished_item]
    if not finished:
        raise ValidationError("Repack needs at least one finished (produced) row", field="items")
    if not consumed:
        raise ValidationError("Repack needs at least one consumed row", field="items")

    weights = [r.basic_rate for r in finished]
    any_weight = any(w > ZERO for w in weights)
    if any_weight and any(w <= ZERO for w in weights):
        raise ValidationError(
            "Repack finished rows must all carry a value weight (basic_rate), or all leave it blank",
            field="items",
        )

    if payload.operating_cost > ZERO:
        if payload.operating_cost_account_id is None:
            raise ValidationError(
                "An operating cost account is required when operating cost is set",
                field="operating_cost_account_id",
            )
        await require_expense_account(db, payload.operating_cost_account_id, company_id)
    elif payload.operating_cost_account_id is not None:
        await require_expense_account(db, payload.operating_cost_account_id, company_id)


async def get_stock_entry(
    db: AsyncSession, entry_id: uuid.UUID, company_id: uuid.UUID | None
) -> StockEntry:
    entry = await db.scalar(
        select(StockEntry)
        .options(selectinload(StockEntry.items))
        .where(StockEntry.id == entry_id, StockEntry.company_id == company_id)
    )
    if entry is None:
        raise NotFoundError("Stock Entry not found")
    return entry


async def list_stock_entries(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int = 1,
    page_size: int = 20,
    purpose: str | None = None,
    *, include_cancelled: bool = False,
) -> tuple[list[StockEntry], int]:
    stmt = (
        select(StockEntry)
        .where(StockEntry.company_id == company_id)
        .order_by(StockEntry.posting_date.desc(), StockEntry.creation.desc())
    )
    if purpose:
        stmt = stmt.where(StockEntry.purpose == purpose)
    # Cancelled documents keep their reversing entries — that is the audit
    # trail — but they are noise in the working list, and a rolled-back import
    # would otherwise leave its cancelled documents on every screen. Pass
    # include_cancelled=True to see them.
    if not include_cancelled:
        stmt = stmt.where(StockEntry.docstatus != DOCSTATUS_CANCELLED)
    return await paginate(db, stmt, page, page_size)


def _is_manufacture_cost_entry(entry: StockEntry) -> bool:
    return entry.purpose in MFG_COST_PURPOSES


def _is_transfer_like(entry: StockEntry) -> bool:
    return entry.purpose in TRANSFER_PURPOSES


async def submit_stock_entry(
    db: AsyncSession, entry_id: uuid.UUID, user: CurrentUser
) -> StockEntry:
    entry = await get_stock_entry(db, entry_id, user.company_id)
    require_draft(entry.docstatus)
    company = await get_company(db, entry.company_id)
    items = await get_items(db, {row.item_id for row in entry.items}, company.id)

    for row in entry.items:
        item = items[row.item_id]
        await validate_line_batch(db, entry.company_id, item, row.batch_no)
        if row.source_warehouse_id is not None and row.target_warehouse_id is None:
            await check_batch_not_expired(
                db, entry.company_id, item, row.batch_no, entry.posting_date
            )

    if _is_manufacture_cost_entry(entry):
        return await _submit_manufacture_or_repack(db, entry, company, items, user)

    # --- classic Receipt / Issue / Transfer (+ Transfer for Manufacture) ---
    sle_rows: list[SLERow] = []
    for row in entry.items:
        if row.source_warehouse_id is not None:
            sle_rows.append(
                SLERow(
                    item_id=row.item_id,
                    warehouse_id=row.source_warehouse_id,
                    actual_qty=-row.stock_qty,
                )
            )
    out_entries = (
        await make_sl_entries(
            db,
            company_id=company.id,
            voucher_type="Stock Entry",
            voucher_id=entry.id,
            voucher_no=entry.name,
            posting_date=entry.posting_date,
            rows=sle_rows,
            items=items,
            user_id=user.id,
        )
        if sle_rows
        else []
    )
    out_by_idx = iter(out_entries)
    out_value: dict[int, Decimal] = {}
    for i, row in enumerate(entry.items):
        if row.source_warehouse_id is not None:
            out_value[i] = next(out_by_idx).stock_value_difference  # negative

    in_rows: list[SLERow] = []
    in_row_idx: list[int] = []
    for i, row in enumerate(entry.items):
        if row.target_warehouse_id is not None:
            if row.source_warehouse_id is not None:
                incoming_rate = (-out_value[i] / row.stock_qty) if row.stock_qty else ZERO
            else:
                incoming_rate = (
                    row.basic_rate / row.conversion_factor if row.conversion_factor else row.basic_rate
                )
            in_rows.append(
                SLERow(
                    item_id=row.item_id,
                    warehouse_id=row.target_warehouse_id,
                    actual_qty=row.stock_qty,
                    incoming_rate=incoming_rate,
                )
            )
            in_row_idx.append(i)
    in_entries = (
        await make_sl_entries(
            db,
            company_id=company.id,
            voucher_type="Stock Entry",
            voucher_id=entry.id,
            voucher_no=entry.name,
            posting_date=entry.posting_date,
            rows=in_rows,
            items=items,
            user_id=user.id,
        )
        if in_rows
        else []
    )

    await _apply_serial_lifecycle(db, entry, company.id)
    await _post_classic_gl(db, entry, company, out_value, in_entries, in_row_idx, user)

    entry.docstatus = DOCSTATUS_SUBMITTED
    entry.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Stock Entry",
        document_id=entry.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=company.id,
    )
    await db.commit()
    return await get_stock_entry(db, entry.id, user.company_id)


async def _submit_manufacture_or_repack(
    db: AsyncSession,
    entry: StockEntry,
    company,
    items: dict,
    user: CurrentUser,
) -> StockEntry:
    """Consume source rows, capitalise operating cost into finished target rows."""
    consumed_idx = [
        i
        for i, r in enumerate(entry.items)
        if r.source_warehouse_id is not None and r.target_warehouse_id is None
    ]
    finished_idx = [
        i
        for i, r in enumerate(entry.items)
        if r.target_warehouse_id is not None and r.source_warehouse_id is None
    ]
    if not consumed_idx and not (
        entry.purpose == "Manufacture" and entry.work_order_id is not None
    ):
        raise ValidationError("Manufacture/Repack needs at least one consumed row", field="items")
    if not finished_idx:
        raise ValidationError("Manufacture/Repack needs at least one finished row", field="items")

    if entry.operating_cost > ZERO:
        if entry.operating_cost_account_id is None:
            raise ValidationError(
                "An operating cost account is required when operating cost is set",
                field="operating_cost_account_id",
            )
        await require_expense_account(db, entry.operating_cost_account_id, company.id)

    # 1) consume (may be empty when Work Order materials were already consumed mid-process)
    out_rows = [
        SLERow(
            item_id=entry.items[i].item_id,
            warehouse_id=entry.items[i].source_warehouse_id,
            actual_qty=-entry.items[i].stock_qty,
        )
        for i in consumed_idx
    ]
    out_entries = (
        await make_sl_entries(
            db,
            company_id=company.id,
            voucher_type="Stock Entry",
            voucher_id=entry.id,
            voucher_no=entry.name,
            posting_date=entry.posting_date,
            rows=out_rows,
            items=items,
            user_id=user.id,
        )
        if out_rows
        else []
    )
    consumed_value = ZERO
    for i, sle in zip(consumed_idx, out_entries):
        value = -sle.stock_value_difference  # positive
        consumed_value += value
        row = entry.items[i]
        row.amount = value.quantize(Decimal("0.000001"))
        row.basic_rate = (
            (value / row.qty).quantize(Decimal("0.000001")) if row.qty else ZERO
        )

    pool = consumed_value + (entry.operating_cost or ZERO)
    if pool < ZERO:
        raise ValidationError("Manufacture/Repack cost pool cannot be negative")
    if pool == ZERO and not finished_idx:
        raise ValidationError("Manufacture/Repack cost pool is zero with nothing to produce")

    # 2) allocate pool across finished rows
    # Value weights = qty × basic_rate. Zero-weight rows (e.g. scrap with no recovery)
    # take share 0 when any positive weight exists; otherwise allocate by qty.
    finished_rows = [entry.items[i] for i in finished_idx]
    weights = [r.qty * r.basic_rate for r in finished_rows]
    positive = [w for w in weights if w > ZERO]
    if positive:
        total_weight = sum(positive, ZERO)
        # keep zeros as-is; only positive weights share the pool
    else:
        weights = [r.qty for r in finished_rows]
        total_weight = sum(weights, ZERO)
    if total_weight <= ZERO:
        raise ValidationError("Cannot allocate manufacture cost — finished qty is zero", field="items")

    allocations: list[Decimal] = []
    allocated = ZERO
    positive_indices = [j for j, w in enumerate(weights) if w > ZERO]
    last_positive = positive_indices[-1] if positive_indices else None
    for j, w in enumerate(weights):
        if w <= ZERO:
            allocations.append(ZERO)
            continue
        if j == last_positive:
            share = pool - allocated
        else:
            share = (pool * w / total_weight).quantize(Decimal("0.000001"))
            allocated += share
        allocations.append(share)

    in_rows: list[SLERow] = []
    for i, share in zip(finished_idx, allocations):
        row = entry.items[i]
        incoming_rate = (share / row.stock_qty) if row.stock_qty else ZERO
        in_rows.append(
            SLERow(
                item_id=row.item_id,
                warehouse_id=row.target_warehouse_id,
                actual_qty=row.stock_qty,
                incoming_rate=incoming_rate,
            )
        )
        # document shows actual moved values (not the pre-submit weight)
        row.amount = share.quantize(Decimal("0.000001"))
        row.basic_rate = (
            (share / row.qty).quantize(Decimal("0.000001")) if row.qty else ZERO
        )

    in_entries = await make_sl_entries(
        db,
        company_id=company.id,
        voucher_type="Stock Entry",
        voucher_id=entry.id,
        voucher_no=entry.name,
        posting_date=entry.posting_date,
        rows=in_rows,
        items=items,
        user_id=user.id,
    )

    entry.total_amount = pool.quantize(Decimal("0.000001"))
    await _apply_serial_lifecycle(db, entry, company.id)

    if company.enable_perpetual_inventory:
        await _post_manufacture_gl(
            db,
            entry,
            company,
            consumed_idx=consumed_idx,
            out_entries=out_entries,
            finished_idx=finished_idx,
            in_entries=in_entries,
            user=user,
        )

    entry.docstatus = DOCSTATUS_SUBMITTED
    entry.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Stock Entry",
        document_id=entry.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=company.id,
    )
    await db.commit()
    return await get_stock_entry(db, entry.id, user.company_id)


async def _post_manufacture_gl(
    db: AsyncSession,
    entry: StockEntry,
    company,
    *,
    consumed_idx: list[int],
    out_entries: list,
    finished_idx: list[int],
    in_entries: list,
    user: CurrentUser,
) -> None:
    gl_rows: list[gl.GLRow] = []
    for i, sle in zip(consumed_idx, out_entries):
        row = entry.items[i]
        value = -sle.stock_value_difference
        if value == ZERO:
            continue
        source_wh = await get_warehouse(db, row.source_warehouse_id, company.id)
        gl_rows.append(
            gl.GLRow(account_id=inventory_account_for(company, source_wh), credit=value)
        )

    for i, sle in zip(finished_idx, in_entries):
        row = entry.items[i]
        value = sle.stock_value_difference
        if value == ZERO:
            continue
        target_wh = await get_warehouse(db, row.target_warehouse_id, company.id)
        gl_rows.append(
            gl.GLRow(account_id=inventory_account_for(company, target_wh), debit=value)
        )

    op_cost = entry.operating_cost or ZERO
    if op_cost > ZERO:
        gl_rows.append(
            gl.GLRow(
                account_id=entry.operating_cost_account_id,
                credit=op_cost,
                cost_center_id=company.default_cost_center_id,
            )
        )

    # Residual from negative-bin rate resets: SLE finished value may differ from pool.
    debit = sum((r.debit for r in gl_rows), ZERO)
    credit = sum((r.credit for r in gl_rows), ZERO)
    residual = credit - debit
    if residual != ZERO:
        if company.stock_adjustment_account_id is None:
            raise ValidationError("Company has no Stock Adjustment account configured")
        if residual > ZERO:
            gl_rows.append(
                gl.GLRow(
                    account_id=company.stock_adjustment_account_id,
                    debit=residual,
                    cost_center_id=company.default_cost_center_id,
                )
            )
        else:
            gl_rows.append(
                gl.GLRow(
                    account_id=company.stock_adjustment_account_id,
                    credit=-residual,
                    cost_center_id=company.default_cost_center_id,
                )
            )

    net: dict[uuid.UUID, Decimal] = {}
    for r in gl_rows:
        net[r.account_id] = net.get(r.account_id, ZERO) + r.debit - r.credit
    merged = [
        gl.GLRow(
            account_id=acc,
            debit=v if v > ZERO else ZERO,
            credit=-v if v < ZERO else ZERO,
            cost_center_id=company.default_cost_center_id,
        )
        for acc, v in net.items()
        if v != ZERO
    ]
    if merged:
        await gl.make_gl_entries(
            db,
            company_id=company.id,
            voucher_type="Stock Entry",
            voucher_id=entry.id,
            voucher_no=entry.name,
            posting_date=entry.posting_date,
            rows=merged,
            user_id=user.id,
            remarks=entry.remarks,
        )


async def _post_classic_gl(
    db: AsyncSession,
    entry: StockEntry,
    company,
    out_value: dict[int, Decimal],
    in_entries: list,
    in_row_idx: list[int],
    user: CurrentUser,
) -> None:
    if not company.enable_perpetual_inventory:
        return
    gl_rows: list[gl.GLRow] = []
    if company.stock_adjustment_account_id is None and not _is_transfer_like(entry):
        raise ValidationError("Company has no Stock Adjustment account configured")
    for i, row in enumerate(entry.items):
        if row.source_warehouse_id is None:
            continue
        source_wh = await get_warehouse(db, row.source_warehouse_id, company.id)
        value = -out_value[i]
        if value == ZERO:
            continue
        gl_rows.append(gl.GLRow(account_id=inventory_account_for(company, source_wh), credit=value))
        if row.target_warehouse_id is None:
            gl_rows.append(
                gl.GLRow(
                    account_id=company.stock_adjustment_account_id,
                    debit=value,
                    cost_center_id=company.default_cost_center_id,
                )
            )
    for sle, i in zip(in_entries, in_row_idx):
        row = entry.items[i]
        target_wh = await get_warehouse(db, row.target_warehouse_id, company.id)
        value = sle.stock_value_difference
        if value == ZERO:
            continue
        gl_rows.append(gl.GLRow(account_id=inventory_account_for(company, target_wh), debit=value))
        if row.source_warehouse_id is None:
            gl_rows.append(
                gl.GLRow(
                    account_id=company.stock_adjustment_account_id,
                    credit=value,
                    cost_center_id=company.default_cost_center_id,
                )
            )
    net: dict[uuid.UUID, Decimal] = {}
    for r in gl_rows:
        net[r.account_id] = net.get(r.account_id, ZERO) + r.debit - r.credit
    if any(v != ZERO for v in net.values()):
        merged = [
            gl.GLRow(
                account_id=acc,
                debit=v if v > ZERO else ZERO,
                credit=-v if v < ZERO else ZERO,
                cost_center_id=company.default_cost_center_id,
            )
            for acc, v in net.items()
            if v != ZERO
        ]
        await gl.make_gl_entries(
            db,
            company_id=company.id,
            voucher_type="Stock Entry",
            voucher_id=entry.id,
            voucher_no=entry.name,
            posting_date=entry.posting_date,
            rows=merged,
            user_id=user.id,
            remarks=entry.remarks,
        )


async def _apply_serial_lifecycle(db: AsyncSession, entry: StockEntry, company_id: uuid.UUID) -> None:
    for row in entry.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if row.source_warehouse_id is not None and row.target_warehouse_id is not None:
            await move_serials(
                db,
                company_id,
                row.item_id,
                serials,
                from_status="In Stock",
                to_status="In Stock",
                warehouse_match=row.source_warehouse_id,
                set_warehouse=row.target_warehouse_id,
            )
        elif row.target_warehouse_id is not None:
            await create_serials(
                db,
                company_id,
                row.item_id,
                row.target_warehouse_id,
                serials,
                voucher_type="Stock Entry",
                voucher_id=entry.id,
            )
        elif row.source_warehouse_id is not None:
            await move_serials(
                db,
                company_id,
                row.item_id,
                serials,
                from_status="In Stock",
                to_status="Returned",
                warehouse_match=row.source_warehouse_id,
            )


async def cancel_stock_entry(
    db: AsyncSession, entry_id: uuid.UUID, user: CurrentUser
) -> StockEntry:
    entry = await get_stock_entry(db, entry_id, user.company_id)
    require_submitted(entry.docstatus)
    items = await get_items(
        db, {row.item_id for row in entry.items}, entry.company_id, allow_disabled=True
    )

    await make_reverse_sl_entries(
        db, voucher_type="Stock Entry", voucher_id=entry.id, items=items, user_id=user.id
    )
    await gl.make_reverse_gl_entries(
        db, voucher_type="Stock Entry", voucher_id=entry.id, user_id=user.id
    )

    for row in entry.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if row.source_warehouse_id is not None and row.target_warehouse_id is not None:
            await move_serials(
                db,
                entry.company_id,
                row.item_id,
                serials,
                from_status="In Stock",
                to_status="In Stock",
                warehouse_match=row.target_warehouse_id,
                set_warehouse=row.source_warehouse_id,
            )
        elif row.target_warehouse_id is not None:
            await delete_serials(
                db,
                entry.company_id,
                row.item_id,
                serials,
                warehouse_match=row.target_warehouse_id,
            )
        elif row.source_warehouse_id is not None:
            await move_serials(
                db,
                entry.company_id,
                row.item_id,
                serials,
                from_status="Returned",
                to_status="In Stock",
                set_warehouse=row.source_warehouse_id,
            )

    # Roll Work Order / Subcontract Job qty back before marking the entry cancelled.
    if entry.work_order_id is not None:
        from app.services import work_order as wo_service

        if entry.purpose == "Manufacture":
            await wo_service.revert_manufacture_entry(db, entry, user)
        elif entry.purpose == "Material Transfer for Manufacture":
            await wo_service.revert_transfer_entry(db, entry, user)
        elif entry.purpose == "Material Consumption for Manufacture":
            await wo_service.revert_consumption_entry(db, entry, user)
    if entry.subcontract_job_id is not None:
        from app.services import subcontract_job as scj_service

        if entry.purpose == "Send to Subcontractor":
            await scj_service.revert_send_entry(db, entry, user)
        elif entry.purpose == "Subcontract Receipt":
            await scj_service.revert_receipt_entry(db, entry, user)

    entry.docstatus = DOCSTATUS_CANCELLED
    entry.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Stock Entry",
        document_id=entry.id,
        action="CANCEL",
        user_id=user.id,
        company_id=entry.company_id,
    )
    await db.commit()
    return await get_stock_entry(db, entry.id, user.company_id)
