"""Delivery Note service — Module 03/05.

On submit:
  * SLE out at moving-average valuation
  * perpetual inventory GL: Dr Cost of Goods Sold / Cr inventory(warehouse)
    at stock value (NOT selling price — revenue posts with the invoice)
  * linked SO rows accrue delivered_qty; bin.reserved_qty releases
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
from app.models.selling import SalesOrderItem
from app.models.stock import DeliveryNote, DeliveryNoteItem
from app.schemas.stock import DeliveryNoteCreate
from app.services import gl
from app.services.accounts_common import get_company, get_customer, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.cycle_links import SO_DELIVERY, aggregate, validate_link_deltas
from app.services.pagination import paginate
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
    get_bin,
    make_reverse_sl_entries,
    make_sl_entries,
    update_reserved_qty,
    voucher_unit_rates,
)
from app.services.stock_serials import (
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
HUNDRED = Decimal("100")


def set_delivery_note_status(dn: DeliveryNote) -> None:
    if dn.docstatus == 0:
        dn.status = "Draft"
        return
    if dn.docstatus == 2:
        dn.status = "Cancelled"
        return
    if dn.is_return:
        # a return isn't separately billed in our model (the credit note posts on
        # the invoice); once submitted it's done
        dn.per_billed = ZERO
        dn.status = "Completed"
        return
    total = sum((row.qty for row in dn.items), ZERO)
    billed = sum((min(row.billed_qty, row.qty) for row in dn.items), ZERO)
    dn.per_billed = (billed / total * HUNDRED) if total else ZERO
    dn.status = "Completed" if dn.per_billed >= Decimal("99.999") else "To Bill"


def _validate_return_qty(
    requested: dict[tuple[uuid.UUID, uuid.UUID], Decimal], original: DeliveryNote
) -> None:
    """A return must not exceed what the original delivery shipped, per
    (item, warehouse). (Cumulative netting across multiple separate returns is
    deferred; the SO delivered_qty cap covers the SO-linked case cumulatively.)"""
    original_qty: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for o in original.items:
        key = (o.item_id, o.warehouse_id)
        original_qty[key] = original_qty.get(key, ZERO) + o.stock_qty
    for key, qty in requested.items():  # qty is already in stock units
        if qty > original_qty.get(key, ZERO) + Decimal("0.000001"):
            raise ValidationError(
                f"Return quantity {qty} (stock) exceeds the {original_qty.get(key, ZERO)} delivered "
                f"on {original.name} for this item/warehouse",
                field="items",
            )


async def create_delivery_note(
    db: AsyncSession, payload: DeliveryNoteCreate, user: CurrentUser
) -> DeliveryNote:
    company = await get_company(db, user.company_id)
    customer = await get_customer(db, payload.customer_id, company.id)
    currency = (payload.currency or company.default_currency).upper()
    items = await get_items(db, {r.item_id for r in payload.items}, company.id)

    sign = Decimal("-1") if payload.is_return else Decimal("1")
    return_against_id = payload.return_against_id if payload.is_return else None
    original: DeliveryNote | None = None
    if payload.is_return:
        if return_against_id is None:
            raise ValidationError(
                "return_against_id is required for a return", field="return_against_id"
            )
        original = await get_delivery_note(db, return_against_id, company.id)
        require_submitted(original.docstatus)
        if original.is_return:
            raise ValidationError(
                "Cannot create a return against another return", field="return_against_id"
            )
        if original.customer_id != customer.id:
            raise ValidationError(
                "Return must be against a delivery to the same customer",
                field="return_against_id",
            )

    # SO delivered cap is in stock units, so scale the line qty by its conversion factor
    so_items = await validate_link_deltas(
        db, SO_DELIVERY, company_id=company.id, party_id=customer.id,
        deltas=aggregate([
            (
                r.sales_order_item_id,
                sign
                * resolve_conversion_factor(items[r.item_id], r.uom or items[r.item_id].stock_uom)
                * r.qty,
            )
            for r in payload.items
        ]),
    )

    name = await get_next_name(db, STOCK_NAMING_SERIES["Delivery Note"], company.id)
    dn = DeliveryNote(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        customer_id=customer.id,
        posting_date=payload.posting_date,
        currency=currency,
        conversion_rate=payload.conversion_rate,
        set_warehouse_id=payload.set_warehouse_id,
        customer_address_id=payload.customer_address_id,
        shipping_address_id=payload.shipping_address_id,
        contact_person_id=payload.contact_person_id,
        is_return=payload.is_return,
        return_against_id=return_against_id,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(dn)
    await db.flush()

    total_qty = ZERO
    grand_total = ZERO
    return_qty: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for idx, row in enumerate(payload.items, start=1):
        item = items[row.item_id]
        require_stock_item(item)
        warehouse_id = row.warehouse_id or payload.set_warehouse_id or item.default_warehouse_id
        if warehouse_id is None:
            raise ValidationError(
                f"Item row {idx} ('{item.item_code}'): warehouse is required", field="items"
            )
        await get_warehouse(db, warehouse_id, company.id)
        uom = row.uom or item.stock_uom
        factor = resolve_conversion_factor(item, uom)
        stock_qty = row.qty * factor
        if payload.is_return:
            key = (item.id, warehouse_id)
            return_qty[key] = return_qty.get(key, ZERO) + stock_qty
        serials = parse_serials(row.serial_nos)
        validate_line_serials(item, serials, stock_qty)
        batch_no = clean_batch_no(row.batch_no)
        await validate_line_batch(db, company.id, item, batch_no)
        if not payload.is_return:  # shipping out: an expired batch can't leave
            await check_batch_not_expired(db, company.id, item, batch_no, payload.posting_date)
        so_item = so_items.get(row.sales_order_item_id)
        rate = row.rate
        if rate == ZERO:
            if so_item is not None:
                rate = so_item.rate
            else:
                # resolved rate is per stock UOM; scale to the line UOM
                stock_rate, _ = await resolve_item_rate(
                    db, item, buying=False, on_date=payload.posting_date, currency=currency
                )
                rate = stock_rate * factor
        amount = row.qty * rate
        total_qty += row.qty
        grand_total += amount
        db.add(
            DeliveryNoteItem(
                delivery_note_id=dn.id,
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
                serial_nos=serials_to_text(serials),
                batch_no=batch_no,
                sales_order_item_id=row.sales_order_item_id,
            )
        )
    if original is not None:
        _validate_return_qty(return_qty, original)
    dn.total_qty = total_qty
    dn.grand_total = grand_total
    dn.base_total = grand_total * payload.conversion_rate
    dn.base_grand_total = grand_total * payload.conversion_rate
    await db.flush()
    await log_audit(
        db, doctype="Delivery Note", document_id=dn.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_delivery_note(db, dn.id, company.id)


async def get_delivery_note(
    db: AsyncSession, dn_id: uuid.UUID, company_id: uuid.UUID | None
) -> DeliveryNote:
    dn = await db.scalar(
        select(DeliveryNote)
        .options(selectinload(DeliveryNote.items))
        .where(DeliveryNote.id == dn_id, DeliveryNote.company_id == company_id)
    )
    if dn is None:
        raise NotFoundError("Delivery Note not found")
    return dn


async def list_delivery_notes(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    status: str | None = None, customer_id: uuid.UUID | None = None,
) -> tuple[list[DeliveryNote], int]:
    stmt = (
        select(DeliveryNote)
        .where(DeliveryNote.company_id == company_id)
        .order_by(DeliveryNote.posting_date.desc(), DeliveryNote.creation.desc())
    )
    if status:
        stmt = stmt.where(DeliveryNote.status == status)
    else:
        # A cancelled document keeps its reversing entries — that is the audit
        # trail — but it is noise in the working list. Rolling back an import
        # would otherwise leave every cancelled document on screen, looking
        # like the rollback had not worked. Ask for them with ?status=Cancelled.
        stmt = stmt.where(DeliveryNote.docstatus != DOCSTATUS_CANCELLED)
    if customer_id is not None:
        stmt = stmt.where(DeliveryNote.customer_id == customer_id)
    return await paginate(db, stmt, page, page_size)


async def submit_delivery_note(
    db: AsyncSession, dn_id: uuid.UUID, user: CurrentUser
) -> DeliveryNote:
    dn = await get_delivery_note(db, dn_id, user.company_id)
    require_draft(dn.docstatus)
    company = await get_company(db, dn.company_id)
    customer = await get_customer(db, dn.customer_id, dn.company_id)
    items = await get_items(db, {r.item_id for r in dn.items}, dn.company_id)
    sign = Decimal("-1") if dn.is_return else Decimal("1")
    # re-validate against the CURRENT order state — a stale or duplicate draft must
    # not over-deliver or post against a since-cancelled order (caps are in stock units)
    await validate_link_deltas(
        db, SO_DELIVERY, company_id=dn.company_id, party_id=dn.customer_id,
        deltas=aggregate([(r.sales_order_item_id, sign * r.stock_qty) for r in dn.items]),
    )

    # Re-validate batches against the CURRENT master state — a batch disabled,
    # deleted, expired, re-pointed, or whose item was un-batched between draft and
    # submit must not ship. Shipping out (not a return) also blocks an expired batch.
    for row in dn.items:
        item = items[row.item_id]
        await validate_line_batch(db, dn.company_id, item, row.batch_no)
        if not dn.is_return:
            await check_batch_not_expired(db, dn.company_id, item, row.batch_no, dn.posting_date)

    if dn.is_return:
        # goods come back IN at the original delivery's per-stock-unit rate (reverses
        # the COGS that was booked on the way out); fall back to the current bin
        # rate / item master if the original line isn't found
        rates = await voucher_unit_rates(db, "Delivery Note", dn.return_against_id)
        sle_rows = []
        for row in dn.items:
            rate = rates.get((row.item_id, row.warehouse_id))
            if rate is None or rate <= ZERO:
                bin_row = await get_bin(db, row.item_id, row.warehouse_id)
                rate = (
                    bin_row.valuation_rate
                    if bin_row is not None and bin_row.valuation_rate > ZERO
                    else items[row.item_id].valuation_rate
                )
            sle_rows.append(
                SLERow(
                    item_id=row.item_id, warehouse_id=row.warehouse_id,
                    actual_qty=row.stock_qty, incoming_rate=rate,
                )
            )
    else:
        sle_rows = [
            SLERow(item_id=row.item_id, warehouse_id=row.warehouse_id, actual_qty=-row.stock_qty)
            for row in dn.items
        ]
    sl_entries = await make_sl_entries(
        db, company_id=dn.company_id, voucher_type="Delivery Note", voucher_id=dn.id,
        voucher_no=dn.name, posting_date=dn.posting_date, rows=sle_rows,
        items=items, user_id=user.id,
    )

    # serial lifecycle: a delivery ships serials (In Stock -> Delivered); a return
    # brings them back from the customer (Delivered -> In Stock at the return wh)
    for row in dn.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if dn.is_return:
            # only restock serials the ORIGINAL delivery shipped; clear the stale
            # delivery link so the now-In-Stock unit no longer points at a DN
            await move_serials(
                db, dn.company_id, row.item_id, serials,
                from_status="Delivered", to_status="In Stock",
                delivery_voucher_match=dn.return_against_id,
                set_warehouse=row.warehouse_id, set_delivery_voucher=None,
            )
        else:
            await move_serials(
                db, dn.company_id, row.item_id, serials,
                from_status="In Stock", to_status="Delivered",
                warehouse_match=row.warehouse_id, set_delivery_voucher=dn.id,
            )

    if company.enable_perpetual_inventory:
        if company.default_expense_account_id is None:
            raise ValidationError("Company has no default Cost of Goods Sold account configured")
        gl_rows: list[gl.GLRow] = []
        cogs_total = ZERO
        for row, sle in zip(dn.items, sl_entries):
            warehouse = await get_warehouse(db, row.warehouse_id, dn.company_id)
            value = -sle.stock_value_difference  # positive
            if value == ZERO:
                continue
            cogs_total += value
            gl_rows.append(
                gl.GLRow(
                    account_id=inventory_account_for(company, warehouse),
                    credit=value, against=customer.customer_name,
                )
            )
            item = items[row.item_id]
            gl_rows.append(
                gl.GLRow(
                    account_id=item.expense_account_id or company.default_expense_account_id,
                    debit=value,
                    against=customer.customer_name,
                    cost_center_id=company.default_cost_center_id,
                )
            )
        if cogs_total != ZERO:
            await gl.make_gl_entries(
                db, company_id=dn.company_id, voucher_type="Delivery Note", voucher_id=dn.id,
                voucher_no=dn.name, posting_date=dn.posting_date, rows=gl_rows,
                user_id=user.id, remarks=dn.remarks,
            )

    # SO tracking + reservation release (a return nets delivered_qty back down;
    # it does NOT re-reserve — the reservation was released on the original DN)
    touched_so_ids: set[uuid.UUID] = set()
    for row in dn.items:
        if row.sales_order_item_id is not None:
            so_item = await db.get(SalesOrderItem, row.sales_order_item_id)
            if so_item is not None:
                so_item.delivered_qty += sign * row.stock_qty
                touched_so_ids.add(so_item.order_id)
                if not dn.is_return:
                    await update_reserved_qty(
                        db, dn.company_id, row.item_id,
                        so_item.warehouse_id or row.warehouse_id, -row.stock_qty,
                    )
    if touched_so_ids:
        from app.services.sales_order import get_sales_order, set_sales_order_status

        for so_id in touched_so_ids:
            so = await get_sales_order(db, so_id, dn.company_id)
            set_sales_order_status(so)

    dn.docstatus = DOCSTATUS_SUBMITTED
    set_delivery_note_status(dn)
    dn.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Delivery Note", document_id=dn.id, action="SUBMIT",
        user_id=user.id, company_id=dn.company_id,
    )
    await db.commit()
    return await get_delivery_note(db, dn.id, user.company_id)


async def cancel_delivery_note(
    db: AsyncSession, dn_id: uuid.UUID, user: CurrentUser
) -> DeliveryNote:
    dn = await get_delivery_note(db, dn_id, user.company_id)
    require_submitted(dn.docstatus)
    if any(row.billed_qty > ZERO for row in dn.items):
        raise ValidationError(
            "Cannot cancel: invoices exist against this delivery. Cancel them first."
        )
    items = await get_items(
        db, {r.item_id for r in dn.items}, dn.company_id, allow_disabled=True
    )

    sign = Decimal("-1") if dn.is_return else Decimal("1")
    await make_reverse_sl_entries(
        db, voucher_type="Delivery Note", voucher_id=dn.id, items=items, user_id=user.id
    )
    await gl.make_reverse_gl_entries(
        db, voucher_type="Delivery Note", voucher_id=dn.id, user_id=user.id
    )

    # revert serials: undo this delivery (Delivered -> In Stock, only those this DN
    # shipped) or undo a return (In Stock -> Delivered)
    for row in dn.items:
        serials = serials_from_text(row.serial_nos)
        if not serials:
            continue
        if dn.is_return:
            # inverse of the return submit: the unit must still be In Stock where
            # the return placed it (not transferred away), and re-links to the
            # original DN it was shipped on
            await move_serials(
                db, dn.company_id, row.item_id, serials,
                from_status="In Stock", to_status="Delivered",
                warehouse_match=row.warehouse_id, set_delivery_voucher=dn.return_against_id,
            )
        else:
            await move_serials(
                db, dn.company_id, row.item_id, serials,
                from_status="Delivered", to_status="In Stock",
                delivery_voucher_match=dn.id, set_delivery_voucher=None,
            )

    touched_so_ids: set[uuid.UUID] = set()
    for row in dn.items:
        if row.sales_order_item_id is not None:
            so_item = await db.get(SalesOrderItem, row.sales_order_item_id)
            if so_item is not None:
                so_item.delivered_qty = max(ZERO, so_item.delivered_qty - sign * row.stock_qty)
                touched_so_ids.add(so_item.order_id)
                if not dn.is_return:
                    await update_reserved_qty(
                        db, dn.company_id, row.item_id,
                        so_item.warehouse_id or row.warehouse_id, row.stock_qty,
                    )
    if touched_so_ids:
        from app.services.sales_order import get_sales_order, set_sales_order_status

        for so_id in touched_so_ids:
            so = await get_sales_order(db, so_id, dn.company_id)
            set_sales_order_status(so)

    dn.docstatus = DOCSTATUS_CANCELLED
    set_delivery_note_status(dn)
    dn.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Delivery Note", document_id=dn.id, action="CANCEL",
        user_id=user.id, company_id=dn.company_id,
    )
    await db.commit()
    return await get_delivery_note(db, dn.id, user.company_id)
