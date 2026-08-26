"""Purchase Receipt service — Module 03/04.

On submit:
  * SLE in at the receipt rate (base currency)
  * perpetual inventory GL: Dr inventory(warehouse) / Cr Stock Received But
    Not Billed (cleared later by the Purchase Invoice)
  * linked PO rows accrue received_qty; bin.ordered_qty releases
  * item.last_purchase_rate updated (erpnext maintain_last_purchase_rate)
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
from app.models.buying import PurchaseOrderItem
from app.models.stock import PurchaseReceipt, PurchaseReceiptCharge, PurchaseReceiptItem
from app.schemas.stock import PurchaseReceiptCreate
from app.services import gl
from app.services.accounts_common import get_company, get_supplier, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.cycle_links import PO_RECEIPT, aggregate, validate_link_deltas
from app.services.pagination import paginate
from app.services.purchase_order import get_purchase_order, set_purchase_order_status
from app.services.stock_common import (
    STOCK_NAMING_SERIES,
    get_items,
    get_warehouse,
    inventory_account_for,
    require_stock_item,
    resolve_conversion_factor,
    resolve_item_rate,
)
from app.services.stock_ledger import (
    SLERow,
    make_reverse_sl_entries,
    make_sl_entries,
    update_ordered_qty,
    voucher_unit_rates,
)
from app.services.stock_serials import (
    create_serials,
    delete_serials,
    move_serials,
    parse_serials,
    serials_from_text,
    serials_to_text,
    validate_line_serials,
)
from app.services.stock_batches import clean_batch_no, validate_line_batch

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def set_receipt_status(pr: PurchaseReceipt) -> None:
    if pr.docstatus == 0:
        pr.status = "Draft"
        return
    if pr.docstatus == 2:
        pr.status = "Cancelled"
        return
    if pr.is_return:
        # a return isn't separately billed (the debit note posts on the invoice)
        pr.per_billed = ZERO
        pr.status = "Completed"
        return
    total = sum((row.qty for row in pr.items), ZERO)
    billed = sum((min(row.billed_qty, row.qty) for row in pr.items), ZERO)
    pr.per_billed = (billed / total * HUNDRED) if total else ZERO
    pr.status = "Completed" if pr.per_billed >= Decimal("99.999") else "To Bill"


def _validate_return_qty(
    requested: dict[tuple[uuid.UUID, uuid.UUID], Decimal], original: PurchaseReceipt
) -> None:
    """A return must not exceed what the original receipt received, per
    (item, warehouse). (Cumulative netting across multiple separate returns is
    deferred; the PO received_qty cap covers the PO-linked case cumulatively.)"""
    original_qty: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for o in original.items:
        key = (o.item_id, o.warehouse_id)
        original_qty[key] = original_qty.get(key, ZERO) + o.stock_qty
    for key, qty in requested.items():  # qty is already in stock units
        if qty > original_qty.get(key, ZERO) + Decimal("0.000001"):
            raise ValidationError(
                f"Return quantity {qty} (stock) exceeds the {original_qty.get(key, ZERO)} received "
                f"on {original.name} for this item/warehouse",
                field="items",
            )


async def create_purchase_receipt(
    db: AsyncSession, payload: PurchaseReceiptCreate, user: CurrentUser
) -> PurchaseReceipt:
    company = await get_company(db, user.company_id)
    supplier = await get_supplier(db, payload.supplier_id, company.id)
    currency = (payload.currency or company.default_currency).upper()
    items = await get_items(db, {r.item_id for r in payload.items}, company.id)

    sign = Decimal("-1") if payload.is_return else Decimal("1")
    return_against_id = payload.return_against_id if payload.is_return else None
    original: PurchaseReceipt | None = None
    if payload.is_return:
        if return_against_id is None:
            raise ValidationError(
                "return_against_id is required for a return", field="return_against_id"
            )
        original = await get_purchase_receipt(db, return_against_id, company.id)
        require_submitted(original.docstatus)
        if original.is_return:
            raise ValidationError(
                "Cannot create a return against another return", field="return_against_id"
            )
        if original.supplier_id != supplier.id:
            raise ValidationError(
                "Return must be against a receipt from the same supplier",
                field="return_against_id",
            )

    # PO received cap is in stock units, so scale the line qty by its conversion factor
    po_items = await validate_link_deltas(
        db, PO_RECEIPT, company_id=company.id, party_id=supplier.id,
        deltas=aggregate([
            (
                r.purchase_order_item_id,
                sign
                * resolve_conversion_factor(items[r.item_id], r.uom or items[r.item_id].stock_uom)
                * (r.qty + r.rejected_qty),
            )
            for r in payload.items
        ]),
    )

    name = await get_next_name(db, STOCK_NAMING_SERIES["Purchase Receipt"], company.id)
    pr = PurchaseReceipt(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        supplier_id=supplier.id,
        posting_date=payload.posting_date,
        currency=currency,
        conversion_rate=payload.conversion_rate,
        set_warehouse_id=payload.set_warehouse_id,
        is_return=payload.is_return,
        return_against_id=return_against_id,
        supplier_delivery_note=payload.supplier_delivery_note,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(pr)
    await db.flush()

    total_qty = ZERO
    grand_total = ZERO
    return_qty: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for idx, row in enumerate(payload.items, start=1):
        item = items[row.item_id]
        require_stock_item(item)
        warehouse_id = row.warehouse_id or payload.set_warehouse_id or item.default_warehouse_id
        if warehouse_id is None:
            raise ValidationError(f"Item row {idx}: warehouse is required", field="items")
        await get_warehouse(db, warehouse_id, company.id)
        uom = row.uom or item.stock_uom
        factor = resolve_conversion_factor(item, uom)
        stock_qty = row.qty * factor
        if payload.is_return:
            key = (item.id, warehouse_id)
            return_qty[key] = return_qty.get(key, ZERO) + stock_qty
        # accepted/rejected QC split: rejected goods land in a separate warehouse
        rejected_warehouse_id = row.rejected_warehouse_id
        if row.rejected_qty > ZERO:
            if payload.is_return:
                raise ValidationError(
                    f"Item row {idx}: rejected quantity is not allowed on a return",
                    field="rejected_qty",
                )
            if rejected_warehouse_id is None:
                raise ValidationError(
                    f"Item row {idx}: rejected warehouse is required when rejecting goods",
                    field="rejected_warehouse_id",
                )
            if item.has_serial_no:
                raise ValidationError(
                    f"Item row {idx}: serialised items don't support the rejected-qty split",
                    field="rejected_qty",
                )
            await get_warehouse(db, rejected_warehouse_id, company.id)
        else:
            rejected_warehouse_id = None
        serials = parse_serials(row.serial_nos)
        validate_line_serials(item, serials, stock_qty)
        batch_no = clean_batch_no(row.batch_no)
        await validate_line_batch(db, company.id, item, batch_no)
        po_item = po_items.get(row.purchase_order_item_id)
        rate = row.rate
        if rate == ZERO and po_item is not None:
            rate = po_item.rate
        if rate == ZERO:
            # zero-valuation receipts silently dilute the moving average — fall back
            # to the buying price list / last purchase / valuation rate (per stock
            # UOM), scaled to the line UOM by the conversion factor
            stock_rate, _ = await resolve_item_rate(
                db, item, buying=True, on_date=payload.posting_date, currency=currency
            )
            rate = stock_rate * factor
        amount = row.qty * rate
        total_qty += row.qty
        grand_total += amount
        db.add(
            PurchaseReceiptItem(
                receipt_id=pr.id,
                idx=idx,
                item_id=item.id,
                warehouse_id=warehouse_id,
                qty=row.qty,
                uom=uom,
                conversion_factor=factor,
                stock_qty=stock_qty,
                rate=rate,
                amount=amount,
                base_rate=rate * payload.conversion_rate,
                base_amount=amount * payload.conversion_rate,
                rejected_qty=row.rejected_qty,
                rejected_warehouse_id=rejected_warehouse_id,
                serial_nos=serials_to_text(serials),
                batch_no=batch_no,
                purchase_order_item_id=row.purchase_order_item_id,
            )
        )
    if original is not None:
        _validate_return_qty(return_qty, original)

    # Landed cost: additional charges fold into incoming valuation at submit
    if payload.charges and payload.is_return:
        raise ValidationError(
            "Landed-cost charges are not allowed on a return", field="charges"
        )
    for cidx, charge in enumerate(payload.charges, start=1):
        db.add(
            PurchaseReceiptCharge(
                receipt_id=pr.id,
                idx=cidx,
                description=charge.description,
                account_id=charge.account_id,
                amount=charge.amount,
            )
        )
    pr.total_qty = total_qty
    pr.grand_total = grand_total
    pr.base_total = grand_total * payload.conversion_rate
    pr.base_grand_total = grand_total * payload.conversion_rate
    await db.flush()
    await log_audit(
        db, doctype="Purchase Receipt", document_id=pr.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_purchase_receipt(db, pr.id, company.id)


async def get_purchase_receipt(
    db: AsyncSession, pr_id: uuid.UUID, company_id: uuid.UUID | None
) -> PurchaseReceipt:
    pr = await db.scalar(
        select(PurchaseReceipt)
        .options(selectinload(PurchaseReceipt.items), selectinload(PurchaseReceipt.charges))
        .where(PurchaseReceipt.id == pr_id, PurchaseReceipt.company_id == company_id)
    )
    if pr is None:
        raise NotFoundError("Purchase Receipt not found")
    return pr


async def list_purchase_receipts(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    status: str | None = None, supplier_id: uuid.UUID | None = None,
) -> tuple[list[PurchaseReceipt], int]:
    stmt = (
        select(PurchaseReceipt)
        .where(PurchaseReceipt.company_id == company_id)
        .order_by(PurchaseReceipt.posting_date.desc(), PurchaseReceipt.creation.desc())
    )
    if status:
        stmt = stmt.where(PurchaseReceipt.status == status)
    else:
        # A cancelled document keeps its reversing entries — that is the audit
        # trail — but it is noise in the working list. Rolling back an import
        # would otherwise leave every cancelled document on screen, looking
        # like the rollback had not worked. Ask for them with ?status=Cancelled.
        stmt = stmt.where(PurchaseReceipt.docstatus != DOCSTATUS_CANCELLED)
    if supplier_id is not None:
        stmt = stmt.where(PurchaseReceipt.supplier_id == supplier_id)
    return await paginate(db, stmt, page, page_size)


async def submit_purchase_receipt(
    db: AsyncSession, pr_id: uuid.UUID, user: CurrentUser
) -> PurchaseReceipt:
    pr = await get_purchase_receipt(db, pr_id, user.company_id)
    require_draft(pr.docstatus)
    company = await get_company(db, pr.company_id)
    supplier = await get_supplier(db, pr.supplier_id, pr.company_id)
    items = await get_items(db, {r.item_id for r in pr.items}, pr.company_id)
    sign = Decimal("-1") if pr.is_return else Decimal("1")
    # Re-validate batches against the CURRENT master state — a batch disabled,
    # deleted, re-pointed, or whose item was un-batched between draft and submit must
    # not be received into. Receiving an expired batch is allowed, so no expiry gate.
    for row in pr.items:
        await validate_line_batch(db, pr.company_id, items[row.item_id], row.batch_no)
    # re-validate against the CURRENT order state (stale/duplicate drafts); the PO
    # received cap is in stock units (accepted + rejected, scaled by the factor)
    await validate_link_deltas(
        db, PO_RECEIPT, company_id=pr.company_id, party_id=pr.supplier_id,
        deltas=aggregate([
            (r.purchase_order_item_id, sign * (r.stock_qty + r.rejected_qty * r.conversion_factor))
            for r in pr.items
        ]),
    )

    # Landed cost: apportion the charge total (base currency) across the accepted
    # item rows by base value, bumping each accepted line's incoming rate. Rejected
    # lines get NO landed cost. The last row absorbs the rounding remainder so the
    # apportioned shares sum back to the charge total exactly.
    charges_total_base = sum((c.amount for c in pr.charges), ZERO) * pr.conversion_rate
    apportioned: dict[uuid.UUID, Decimal] = {}
    if charges_total_base != ZERO:
        base_total = sum((r.base_amount for r in pr.items), ZERO)
        if base_total <= ZERO:
            raise ValidationError(
                "Cannot apportion landed cost across zero-value items", field="charges"
            )
        running = ZERO
        last = len(pr.items) - 1
        for i, r in enumerate(pr.items):
            if i == last:
                apportioned[r.id] = charges_total_base - running
            else:
                share = (charges_total_base * r.base_amount / base_total).quantize(Decimal("0.000001"))
                apportioned[r.id] = share
                running += share

    # A return values its stock-out at the ORIGINAL receipt's rate (per item+
    # warehouse), so the SRBNB reversal matches what was received even after the
    # moving average drifted; the writer falls back to the current average if that
    # rate isn't found or would corrupt the bin.
    return_rates = (
        await voucher_unit_rates(db, "Purchase Receipt", pr.return_against_id)
        if pr.is_return and pr.return_against_id is not None
        else {}
    )

    # Build SLE rows tagged with the warehouse to post their value against. A
    # normal receipt makes one IN row for the accepted qty (at the landed-cost
    # adjusted rate) plus, when goods were rejected, a second IN row into the
    # rejected warehouse at the plain rate. A return makes one OUT row at the
    # original receipt rate (rejected qty / charges are barred on returns).
    sle_meta: list[tuple[uuid.UUID, SLERow]] = []
    for row in pr.items:
        if pr.is_return:
            orig_rate = return_rates.get((row.item_id, row.warehouse_id))
            sle_meta.append(
                (row.warehouse_id,
                 SLERow(item_id=row.item_id, warehouse_id=row.warehouse_id,
                        actual_qty=-row.stock_qty,
                        outgoing_rate=orig_rate if orig_rate and orig_rate > ZERO else None))
            )
        else:
            # incoming_rate is per STOCK unit: (item base + landed cost) / stock_qty
            lc = apportioned.get(row.id, ZERO)
            accepted_rate = (row.base_amount + lc) / row.stock_qty if row.stock_qty else row.base_rate
            sle_meta.append(
                (row.warehouse_id,
                 SLERow(item_id=row.item_id, warehouse_id=row.warehouse_id,
                        actual_qty=row.stock_qty, incoming_rate=accepted_rate))
            )
            if row.rejected_qty > ZERO and row.rejected_warehouse_id is not None:
                rejected_stock_qty = row.rejected_qty * row.conversion_factor
                rejected_rate = row.base_rate / row.conversion_factor if row.conversion_factor else row.base_rate
                sle_meta.append(
                    (row.rejected_warehouse_id,
                     SLERow(item_id=row.item_id, warehouse_id=row.rejected_warehouse_id,
                            actual_qty=rejected_stock_qty, incoming_rate=rejected_rate))
                )
    sl_entries = await make_sl_entries(
        db, company_id=pr.company_id, voucher_type="Purchase Receipt", voucher_id=pr.id,
        voucher_no=pr.name, posting_date=pr.posting_date, rows=[r for _, r in sle_meta],
        items=items, user_id=user.id,
    )

    # serial lifecycle: a receipt creates serials In Stock; a return sends them back
    # to the supplier (In Stock -> Returned)
    for row in pr.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if pr.is_return:
            # only return serials that arrived on the ORIGINAL receipt
            await move_serials(
                db, pr.company_id, row.item_id, serials,
                from_status="In Stock", to_status="Returned", warehouse_match=row.warehouse_id,
                purchase_voucher_match=pr.return_against_id,
            )
        else:
            await create_serials(
                db, pr.company_id, row.item_id, row.warehouse_id, serials,
                voucher_type="Purchase Receipt", voucher_id=pr.id,
            )

    if company.enable_perpetual_inventory:
        if company.stock_received_but_not_billed_account_id is None:
            raise ValidationError(
                "Company has no 'Stock Received But Not Billed' account configured"
            )
        gl_rows: list[gl.GLRow] = []
        total_value = ZERO
        for (warehouse_id, _), sle in zip(sle_meta, sl_entries):
            warehouse = await get_warehouse(db, warehouse_id, pr.company_id)
            value = sle.stock_value_difference
            if value == ZERO:
                continue
            total_value += value
            gl_rows.append(
                gl.GLRow(
                    account_id=inventory_account_for(company, warehouse),
                    debit=value, against=supplier.supplier_name,
                )
            )
        if total_value != ZERO:
            # SRBNB carries the supplier (item-base) portion; the charge accounts
            # carry the landed cost. Dr inventory (base + charges) balances against
            # Cr SRBNB (base) + Cr charge accounts (charges).
            gl_rows.append(
                gl.GLRow(
                    account_id=company.stock_received_but_not_billed_account_id,
                    credit=total_value - charges_total_base,
                    against=supplier.supplier_name,
                    cost_center_id=company.default_cost_center_id,
                )
            )
            for charge in pr.charges:
                gl_rows.append(
                    gl.GLRow(
                        account_id=charge.account_id,
                        credit=charge.amount * pr.conversion_rate,
                        against=supplier.supplier_name,
                        cost_center_id=company.default_cost_center_id,
                    )
                )
            await gl.make_gl_entries(
                db, company_id=pr.company_id, voucher_type="Purchase Receipt", voucher_id=pr.id,
                voucher_no=pr.name, posting_date=pr.posting_date, rows=gl_rows,
                user_id=user.id, remarks=pr.remarks,
            )

    # PO tracking + ordered qty release + last purchase rate (a return nets
    # received_qty back down; it does NOT re-order or move last_purchase_rate)
    touched_po_ids: set[uuid.UUID] = set()
    for row in pr.items:
        if not pr.is_return:
            items[row.item_id].last_purchase_rate = row.base_rate
        if row.purchase_order_item_id is not None:
            po_item = await db.get(PurchaseOrderItem, row.purchase_order_item_id)
            if po_item is not None:
                # received_qty (and the ordered-qty release) count accepted +
                # rejected, in STOCK units, so a partly-rejected receipt still
                # closes the PO line and the Bin moves correctly
                received = row.stock_qty + row.rejected_qty * row.conversion_factor
                po_item.received_qty += sign * received
                touched_po_ids.add(po_item.order_id)
                if not pr.is_return:
                    await update_ordered_qty(
                        db, pr.company_id, row.item_id,
                        po_item.warehouse_id or row.warehouse_id, -received,
                    )
    for po_id in touched_po_ids:
        po = await get_purchase_order(db, po_id, pr.company_id)
        set_purchase_order_status(po)

    pr.docstatus = DOCSTATUS_SUBMITTED
    set_receipt_status(pr)
    pr.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Purchase Receipt", document_id=pr.id, action="SUBMIT",
        user_id=user.id, company_id=pr.company_id,
    )
    await db.commit()
    return await get_purchase_receipt(db, pr.id, user.company_id)


async def cancel_purchase_receipt(
    db: AsyncSession, pr_id: uuid.UUID, user: CurrentUser
) -> PurchaseReceipt:
    pr = await get_purchase_receipt(db, pr_id, user.company_id)
    require_submitted(pr.docstatus)
    if any(row.billed_qty > ZERO for row in pr.items):
        raise ValidationError(
            "Cannot cancel: invoices exist against this receipt. Cancel them first."
        )
    items = await get_items(
        db, {r.item_id for r in pr.items}, pr.company_id, allow_disabled=True
    )

    sign = Decimal("-1") if pr.is_return else Decimal("1")
    await make_reverse_sl_entries(
        db, voucher_type="Purchase Receipt", voucher_id=pr.id, items=items, user_id=user.id
    )
    await gl.make_reverse_gl_entries(
        db, voucher_type="Purchase Receipt", voucher_id=pr.id, user_id=user.id
    )

    # revert serials: a receipt's serials are deleted (blocked if since delivered);
    # a return's serials come back from the supplier (Returned -> In Stock)
    for row in pr.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if pr.is_return:
            await move_serials(
                db, pr.company_id, row.item_id, serials,
                from_status="Returned", to_status="In Stock", set_warehouse=row.warehouse_id,
                purchase_voucher_match=pr.return_against_id,
            )
        else:
            await delete_serials(
                db, pr.company_id, row.item_id, serials, warehouse_match=row.warehouse_id
            )

    touched_po_ids: set[uuid.UUID] = set()
    for row in pr.items:
        if row.purchase_order_item_id is not None:
            po_item = await db.get(PurchaseOrderItem, row.purchase_order_item_id)
            if po_item is not None:
                received = row.stock_qty + row.rejected_qty * row.conversion_factor
                po_item.received_qty = max(ZERO, po_item.received_qty - sign * received)
                touched_po_ids.add(po_item.order_id)
                if not pr.is_return:
                    await update_ordered_qty(
                        db, pr.company_id, row.item_id,
                        po_item.warehouse_id or row.warehouse_id, received,
                    )
    for po_id in touched_po_ids:
        po = await get_purchase_order(db, po_id, pr.company_id)
        set_purchase_order_status(po)

    pr.docstatus = DOCSTATUS_CANCELLED
    set_receipt_status(pr)
    pr.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Purchase Receipt", document_id=pr.id, action="CANCEL",
        user_id=user.id, company_id=pr.company_id,
    )
    await db.commit()
    return await get_purchase_receipt(db, pr.id, user.company_id)
