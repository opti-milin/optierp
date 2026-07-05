"""Stock Entry service — Module 03.

Purposes: Material Receipt (in), Material Issue (out), Material Transfer
(out of source + into target at the source's outgoing valuation).

Perpetual inventory GL on submit (skipped when the company disables it):
  Receipt:  Dr inventory(target)   / Cr Stock Adjustment
  Issue:    Dr Stock Adjustment    / Cr inventory(source)
  Transfer: Dr inventory(target)   / Cr inventory(source)  (skipped if same account)
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
from app.models.stock import StockEntry, StockEntryItem
from app.schemas.stock import StockEntryCreate
from app.services import gl
from app.services.accounts_common import get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.manufacturing_common import require_expense_account
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
from app.services.stock_batches import (
    check_batch_not_expired,
    clean_batch_no,
    validate_line_batch,
)

ZERO = Decimal("0")


async def create_stock_entry(
    db: AsyncSession, payload: StockEntryCreate, user: CurrentUser
) -> StockEntry:
    company = await get_company(db, user.company_id)
    items = await get_items(db, {row.item_id for row in payload.items}, company.id)

    is_repack = payload.purpose == "Repack"
    needs_source = payload.purpose in ("Material Issue", "Material Transfer")
    needs_target = payload.purpose in ("Material Receipt", "Material Transfer")

    if is_repack:
        # a repack must consume something and produce something
        if not any(r.is_finished_item for r in payload.items):
            raise ValidationError(
                "A Repack needs at least one finished (produced) item row", field="items"
            )
        if all(r.is_finished_item for r in payload.items):
            raise ValidationError(
                "A Repack needs at least one consumed item row", field="items"
            )
        # a half-weighted split would book the blank rows at zero value — reject NOW
        # (a draft has no edit/delete endpoint, so a submit-time failure would strand it)
        finished = [r for r in payload.items if r.is_finished_item]
        weighted = [r for r in finished if r.qty * r.basic_rate > ZERO]
        if weighted and len(weighted) != len(finished):
            raise ValidationError(
                "Give every finished row a value weight, or leave them all blank for an "
                "equal per-unit split",
                field="items",
            )
        # the additional-cost account is checked NOW (exists, this company, Expense type):
        # a draft has no edit/delete endpoint, so a submit-time failure would strand it
        if payload.operating_cost > ZERO and payload.operating_cost_account_id is None:
            raise ValidationError(
                "An additional cost needs an expense account to credit "
                "(e.g. 'Expenses Included In Valuation')",
                field="operating_cost_account_id",
            )
        if payload.operating_cost_account_id is not None:
            await require_expense_account(db, payload.operating_cost_account_id, company.id)
    elif payload.operating_cost > ZERO or payload.operating_cost_account_id is not None:
        raise ValidationError(
            "Operating cost applies only to a Repack entry", field="operating_cost"
        )

    name = await get_next_name(db, STOCK_NAMING_SERIES["Stock Entry"], company.id)
    entry = StockEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        purpose=payload.purpose,
        from_warehouse_id=payload.from_warehouse_id,
        to_warehouse_id=payload.to_warehouse_id,
        operating_cost=payload.operating_cost if is_repack else ZERO,
        operating_cost_account_id=payload.operating_cost_account_id if is_repack else None,
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
        if is_repack:
            # per-row roles: a finished row is produced (target only), everything else
            # is consumed (source only)
            row_needs_source = not row.is_finished_item
            row_needs_target = row.is_finished_item
        else:
            row_needs_source, row_needs_target = needs_source, needs_target
        source_id = (row.source_warehouse_id or payload.from_warehouse_id) if row_needs_source else None
        target_id = (row.target_warehouse_id or payload.to_warehouse_id) if row_needs_target else None
        if row_needs_source and source_id is None:
            raise ValidationError(f"Item row {idx}: source warehouse is required", field="items")
        if row_needs_target and target_id is None:
            raise ValidationError(f"Item row {idx}: target warehouse is required", field="items")
        if row_needs_source:
            await get_warehouse(db, source_id, company.id)
        if row_needs_target:
            await get_warehouse(db, target_id, company.id)
        uom = row.uom or item.stock_uom
        factor = resolve_conversion_factor(item, uom)
        stock_qty = row.qty * factor
        serials = parse_serials(row.serial_nos)
        validate_line_serials(item, serials, stock_qty)
        batch_no = clean_batch_no(row.batch_no)
        await validate_line_batch(db, company.id, item, batch_no)
        if row_needs_source and not row_needs_target:  # pure issue / repack consumption ships out
            await check_batch_not_expired(db, company.id, item, batch_no, payload.posting_date)
        basic_rate = row.basic_rate
        if payload.purpose == "Material Receipt" and basic_rate == ZERO:
            # prefer the live bin valuation; the item-master rate is only an opening
            # default and is never maintained by the ledger. Both are per stock UOM —
            # scale to the line UOM so basic_rate stays per transaction unit.
            bin_row = await get_bin(db, row.item_id, target_id) if target_id else None
            stock_rate = (
                bin_row.valuation_rate
                if bin_row is not None and bin_row.valuation_rate > ZERO
                else item.valuation_rate
            )
            basic_rate = stock_rate * factor
        amount = row.qty * basic_rate
        total_amount += amount
        db.add(
            StockEntryItem(
                stock_entry_id=entry.id,
                idx=idx,
                item_id=row.item_id,
                source_warehouse_id=source_id if row_needs_source else None,
                target_warehouse_id=target_id if row_needs_target else None,
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
        db, doctype="Stock Entry", document_id=entry.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_stock_entry(db, entry.id, company.id)


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
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    purpose: str | None = None,
) -> tuple[list[StockEntry], int]:
    stmt = (
        select(StockEntry)
        .where(StockEntry.company_id == company_id)
        .order_by(StockEntry.posting_date.desc(), StockEntry.creation.desc())
    )
    if purpose:
        stmt = stmt.where(StockEntry.purpose == purpose)
    return await paginate(db, stmt, page, page_size)


async def submit_stock_entry(
    db: AsyncSession, entry_id: uuid.UUID, user: CurrentUser
) -> StockEntry:
    entry = await get_stock_entry(db, entry_id, user.company_id)
    require_draft(entry.docstatus)
    company = await get_company(db, entry.company_id)
    items = await get_items(db, {row.item_id for row in entry.items}, company.id)

    # Re-validate batches against the CURRENT master state — a batch disabled,
    # deleted, expired, re-pointed, or whose item was un-batched between draft and
    # submit must not move. A pure issue ships out, so it also blocks an expired
    # batch (transfers/receipts don't). Mirrors the submit-time link-delta re-check.
    for row in entry.items:
        item = items[row.item_id]
        await validate_line_batch(db, entry.company_id, item, row.batch_no)
        if row.source_warehouse_id is not None and row.target_warehouse_id is None:
            await check_batch_not_expired(db, entry.company_id, item, row.batch_no, entry.posting_date)

    # Outgoing legs first so transfers can't overdraw the source mid-voucher
    sle_rows: list[SLERow] = []
    for row in entry.items:
        if row.source_warehouse_id is not None:
            sle_rows.append(
                SLERow(item_id=row.item_id, warehouse_id=row.source_warehouse_id,
                       actual_qty=-row.stock_qty)
            )
    out_entries = (
        await make_sl_entries(
            db, company_id=company.id, voucher_type="Stock Entry", voucher_id=entry.id,
            voucher_no=entry.name, posting_date=entry.posting_date, rows=sle_rows,
            items=items, user_id=user.id,
        )
        if sle_rows
        else []
    )
    out_by_idx = iter(out_entries)
    out_value: dict[int, Decimal] = {}
    for i, row in enumerate(entry.items):
        if row.source_warehouse_id is not None:
            out_value[i] = next(out_by_idx).stock_value_difference  # negative

    # Manufacture / Repack: the finished rows (target-only) are valued at everything that
    # went into them — the full consumed value plus the flat operating cost. Every source
    # row is a consumed input. Manufacture (driven by a Work Order) produces exactly ONE
    # finished row and gets the whole pool; Repack may produce several, so the pool is
    # split across them by value weight (qty × basic_rate) — equal-per-unit when no
    # weights are given.
    is_manufacture = entry.purpose == "Manufacture"
    is_repack = entry.purpose == "Repack"
    consumed_value_total = sum((-v for v in out_value.values()), ZERO)

    finished_rows = [
        row for row in entry.items
        if row.target_warehouse_id is not None and row.source_warehouse_id is None
    ]
    if is_manufacture and len(finished_rows) != 1:
        # the full-pool valuation below assigns the WHOLE pool to each target-only row —
        # correct only for the single-FG entry a Work Order finish emits. Guard the
        # invariant explicitly so a future multi-output path can't silently over-value.
        raise ValidationError(
            "A Manufacture entry must have exactly one finished (target-only) row"
        )
    repack_rate: dict[uuid.UUID, Decimal] = {}
    if is_repack:
        pool = consumed_value_total + entry.operating_cost
        # weight = qty × basic_rate — the row's own line-UOM amount (basic_rate is per
        # TRANSACTION unit everywhere in this file; multiplying by stock_qty would
        # double-count the UOM conversion factor)
        weights = {row.id: row.qty * row.basic_rate for row in finished_rows}
        weighted = [w for w in weights.values() if w > ZERO]
        if weighted and len(weighted) != len(finished_rows):
            # a half-weighted entry would silently book the blank rows at ZERO value —
            # all-or-nothing keeps the split explicit
            raise ValidationError(
                "Give every finished row a value weight, or leave them all blank for an "
                "equal per-unit split",
                field="items",
            )
        if not weighted:
            weights = {row.id: row.stock_qty for row in finished_rows}
        total_weight = sum(weights.values(), ZERO)
        for row in finished_rows:
            share = (pool * weights[row.id] / total_weight) if total_weight else ZERO
            repack_rate[row.id] = (share / row.stock_qty) if row.stock_qty else ZERO

    in_rows: list[SLERow] = []
    in_row_idx: list[int] = []
    for i, row in enumerate(entry.items):
        if row.target_warehouse_id is not None:
            # incoming_rate is per STOCK unit: transfers carry the source's outgoing
            # value spread over stock_qty; receipts use basic_rate / factor
            if row.source_warehouse_id is not None:
                incoming_rate = (-out_value[i] / row.stock_qty) if row.stock_qty else ZERO
            elif is_manufacture:
                fg_value = consumed_value_total + entry.operating_cost
                incoming_rate = (fg_value / row.stock_qty) if row.stock_qty else ZERO
            elif is_repack:
                incoming_rate = repack_rate[row.id]
            else:
                incoming_rate = (
                    row.basic_rate / row.conversion_factor if row.conversion_factor else row.basic_rate
                )
            in_rows.append(
                SLERow(
                    item_id=row.item_id, warehouse_id=row.target_warehouse_id,
                    actual_qty=row.stock_qty, incoming_rate=incoming_rate,
                )
            )
            in_row_idx.append(i)
    in_entries = (
        await make_sl_entries(
            db, company_id=company.id, voucher_type="Stock Entry", voucher_id=entry.id,
            voucher_no=entry.name, posting_date=entry.posting_date, rows=in_rows,
            items=items, user_id=user.id,
        )
        if in_rows
        else []
    )

    # write the ACTUAL moved values back onto the document rows — a Repack's raw value
    # weights and a Manufacture's estimates would otherwise display as if they were money
    # (rate/amount/total on the detail page, list and print)
    if is_manufacture or is_repack:
        for i, row in enumerate(entry.items):
            if row.source_warehouse_id is not None:
                row.amount = -out_value[i]  # positive consumed value
                row.basic_rate = (row.amount / row.qty) if row.qty else ZERO
        for sle, i in zip(in_entries, in_row_idx):
            row = entry.items[i]
            row.amount = sle.stock_value_difference
            row.basic_rate = (row.amount / row.qty) if row.qty else ZERO
        entry.total_amount = sum((sle.stock_value_difference for sle in in_entries), ZERO)

    # serial lifecycle: receipt creates In Stock; issue consumes (-> Returned);
    # transfer re-homes In Stock to the target warehouse
    for row in entry.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if row.source_warehouse_id is not None and row.target_warehouse_id is not None:
            await move_serials(
                db, company.id, row.item_id, serials,
                from_status="In Stock", to_status="In Stock",
                warehouse_match=row.source_warehouse_id, set_warehouse=row.target_warehouse_id,
            )
        elif row.target_warehouse_id is not None:
            await create_serials(
                db, company.id, row.item_id, row.target_warehouse_id, serials,
                voucher_type="Stock Entry", voucher_id=entry.id,
            )
        elif row.source_warehouse_id is not None:
            await move_serials(
                db, company.id, row.item_id, serials,
                from_status="In Stock", to_status="Returned", warehouse_match=row.source_warehouse_id,
            )

    # --- perpetual inventory GL ---
    if company.enable_perpetual_inventory and (is_manufacture or is_repack):
        # Manufacture / Repack: Cr each consumed input's inventory account (outgoing
        # value), Dr the finished rows' inventory account (consumed value + operating
        # cost), Cr the operating-cost account for the labour capitalised into the FG.
        mfg_rows: list[gl.GLRow] = []
        for i, row in enumerate(entry.items):
            if row.source_warehouse_id is None:
                continue
            source_wh = await get_warehouse(db, row.source_warehouse_id, company.id)
            value = -out_value[i]  # positive
            if value != ZERO:
                mfg_rows.append(gl.GLRow(account_id=inventory_account_for(company, source_wh), credit=value))
        for sle, i in zip(in_entries, in_row_idx):
            row = entry.items[i]
            target_wh = await get_warehouse(db, row.target_warehouse_id, company.id)
            # normally positive (= the row's pool share); crossing a zero/negative bin
            # resets the rate, so the ACTUAL ledger movement can differ — post the actual
            value = sle.stock_value_difference
            fg_account = inventory_account_for(company, target_wh)
            if value > ZERO:
                mfg_rows.append(gl.GLRow(account_id=fg_account, debit=value))
            elif value < ZERO:
                mfg_rows.append(gl.GLRow(account_id=fg_account, credit=-value))
        if entry.operating_cost > ZERO:
            if entry.operating_cost_account_id is None:
                raise ValidationError(
                    "An operating cost account is required to post the labour into the "
                    "finished good's value",
                    field="operating_cost_account_id",
                )
            mfg_rows.append(
                gl.GLRow(account_id=entry.operating_cost_account_id, credit=entry.operating_cost)
            )
        # producing into a zero/negative bin resets the moving-average rate, so the
        # finished rows' actual ledger movement can differ from the consumed pool — the
        # residual is a stock adjustment (same place plain receipts implicitly send it)
        imbalance = sum((r.debit - r.credit for r in mfg_rows), ZERO)
        if imbalance != ZERO:
            if company.stock_adjustment_account_id is None:
                raise ValidationError(
                    "Company has no Stock Adjustment account configured (needed to absorb "
                    "the valuation reset from producing into a negative-stock bin)"
                )
            if imbalance > ZERO:
                mfg_rows.append(
                    gl.GLRow(account_id=company.stock_adjustment_account_id, credit=imbalance)
                )
            else:
                mfg_rows.append(
                    gl.GLRow(account_id=company.stock_adjustment_account_id, debit=-imbalance)
                )
        # net per account (raws and FG may share an inventory account) then post
        mfg_net: dict[uuid.UUID, Decimal] = {}
        for r in mfg_rows:
            mfg_net[r.account_id] = mfg_net.get(r.account_id, ZERO) + r.debit - r.credit
        merged = [
            gl.GLRow(
                account_id=acc, debit=v if v > ZERO else ZERO, credit=-v if v < ZERO else ZERO,
                cost_center_id=company.default_cost_center_id,
            )
            for acc, v in mfg_net.items()
            if v != ZERO
        ]
        if merged:
            await gl.make_gl_entries(
                db, company_id=company.id, voucher_type="Stock Entry", voucher_id=entry.id,
                voucher_no=entry.name, posting_date=entry.posting_date, rows=merged,
                user_id=user.id, remarks=entry.remarks,
            )
    elif company.enable_perpetual_inventory:
        gl_rows: list[gl.GLRow] = []
        # a WIP transfer (Material Transfer for Manufacture) behaves like a plain transfer —
        # value only moves between inventory accounts, so no Stock Adjustment account is needed
        _transfer_like = ("Material Transfer", "Material Transfer for Manufacture")
        if company.stock_adjustment_account_id is None and entry.purpose not in _transfer_like:
            raise ValidationError("Company has no Stock Adjustment account configured")
        # credit source legs at outgoing value
        for i, row in enumerate(entry.items):
            if row.source_warehouse_id is None:
                continue
            source_wh = await get_warehouse(db, row.source_warehouse_id, company.id)
            value = -out_value[i]  # positive
            if value == ZERO:
                continue
            gl_rows.append(gl.GLRow(account_id=inventory_account_for(company, source_wh), credit=value))
            if row.target_warehouse_id is None:  # pure issue
                gl_rows.append(
                    gl.GLRow(
                        account_id=company.stock_adjustment_account_id, debit=value,
                        cost_center_id=company.default_cost_center_id,
                    )
                )
        # debit target legs at incoming value
        for sle, i in zip(in_entries, in_row_idx):
            row = entry.items[i]
            target_wh = await get_warehouse(db, row.target_warehouse_id, company.id)
            value = sle.stock_value_difference  # positive
            if value == ZERO:
                continue
            gl_rows.append(gl.GLRow(account_id=inventory_account_for(company, target_wh), debit=value))
            if row.source_warehouse_id is None:  # pure receipt
                gl_rows.append(
                    gl.GLRow(
                        account_id=company.stock_adjustment_account_id, credit=value,
                        cost_center_id=company.default_cost_center_id,
                    )
                )
        # drop self-cancelling transfer pairs on the same account
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
            # re-balance check happens inside make_gl_entries
            await gl.make_gl_entries(
                db, company_id=company.id, voucher_type="Stock Entry", voucher_id=entry.id,
                voucher_no=entry.name, posting_date=entry.posting_date, rows=merged,
                user_id=user.id, remarks=entry.remarks,
            )

    entry.docstatus = DOCSTATUS_SUBMITTED
    entry.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Stock Entry", document_id=entry.id, action="SUBMIT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_stock_entry(db, entry.id, user.company_id)


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

    # revert serials: undo transfer (re-home to source), receipt (delete), issue (restock)
    for row in entry.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if row.source_warehouse_id is not None and row.target_warehouse_id is not None:
            await move_serials(
                db, entry.company_id, row.item_id, serials,
                from_status="In Stock", to_status="In Stock",
                warehouse_match=row.target_warehouse_id, set_warehouse=row.source_warehouse_id,
            )
        elif row.target_warehouse_id is not None:
            await delete_serials(
                db, entry.company_id, row.item_id, serials,
                warehouse_match=row.target_warehouse_id,
            )
        elif row.source_warehouse_id is not None:
            await move_serials(
                db, entry.company_id, row.item_id, serials,
                from_status="Returned", to_status="In Stock", set_warehouse=row.source_warehouse_id,
            )

    # Manufacture entries drive a Work Order — roll back the produced / consumed qty it
    # booked so the Work Order's state stays consistent after the stock is reversed.
    if entry.purpose == "Manufacture" and entry.work_order_id is not None:
        from app.services.work_order import revert_manufacture_entry

        await revert_manufacture_entry(db, entry, user)

    entry.docstatus = DOCSTATUS_CANCELLED
    entry.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Stock Entry", document_id=entry.id, action="CANCEL",
        user_id=user.id, company_id=entry.company_id,
    )
    await db.commit()
    return await get_stock_entry(db, entry.id, user.company_id)
