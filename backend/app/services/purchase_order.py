"""Purchase Order service — Module 04.

On submit: bin.ordered_qty += qty for stock items and linked Material
Request rows accrue ordered_qty. Receipts/invoices later drive
per_received / per_billed and the PO status (erpnext status_updater):
  To Receive and Bill -> To Receive / To Bill -> Completed
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
from app.models.buying import PurchaseOrder, PurchaseOrderItem, PurchaseOrderTax, SupplierQuotation
from app.models.stock import MaterialRequest, MaterialRequestItem
from app.models.accounts import TaxTemplate
from app.schemas.accounts import TaxRowIn
from app.schemas.buying import PurchaseOrderCreate
from app.services import gl  # noqa: F401  (kept for parity; POs post no GL)
from app.services.accounts_common import (
    auto_gst_from_items,
    _line_key,
    compute_doc_tax_preview,
    get_company,
    get_supplier,
    item_tax_rates,
    require_draft,
    require_submitted,
)
from app.services.audit import log_audit
from app.services.material_request import set_material_request_status
from app.services.pagination import paginate
from app.services.stock_common import (
    STOCK_NAMING_SERIES,
    get_items,
    get_warehouse,
    resolve_conversion_factor,
    resolve_item_rate,
)
from app.services.stock_ledger import update_ordered_qty
from app.services.taxes_and_totals import ItemRow, TaxRow, calculate_taxes_and_totals

ZERO = Decimal("0")
HUNDRED = Decimal("100")


async def _load_tax_rows(db: AsyncSession, payload: PurchaseOrderCreate, supplier) -> list[TaxRowIn]:
    if payload.taxes:
        return payload.taxes
    if payload.tax_template_id:
        template = await db.scalar(
            select(TaxTemplate)
            .options(selectinload(TaxTemplate.details))
            .where(TaxTemplate.id == payload.tax_template_id)
        )
        if template is None or template.kind != "purchase":
            raise NotFoundError("Purchase tax template not found")
    else:
        from app.services.accounts_masters import resolve_tax_template

        template = await resolve_tax_template(
            db, supplier.company_id, "purchase", supplier.tax_category_id, party_gstin=supplier.tax_id
        )
        if template is None:
            return []
    return [
        TaxRowIn(
            charge_type=d.charge_type, rate=d.rate, tax_amount=d.tax_amount, row_id=d.row_id,
            account_head_id=d.account_head_id, cost_center_id=d.cost_center_id,
            description=d.description, add_deduct_tax=d.add_deduct_tax, category=d.category,
            included_in_print_rate=d.included_in_print_rate,
        )
        for d in template.details
    ]


async def _validate_mr_links(
    db: AsyncSession, company_id: uuid.UUID, payload: PurchaseOrderCreate
) -> None:
    """material_request_item_id must reference a submitted MR of THIS company,
    for the same item, with enough unordered quantity (cumulative per row)."""
    pairs = [
        (row.material_request_item_id, row)
        for row in payload.items
        if row.material_request_item_id is not None
    ]
    if not pairs:
        return
    link_items = await get_items(
        db, {row.item_id for _, row in pairs}, company_id, allow_disabled=True
    )
    mr_items = {
        m.id: m
        for m in (
            await db.execute(
                select(MaterialRequestItem).where(
                    MaterialRequestItem.id.in_({link_id for link_id, _ in pairs})
                )
            )
        ).scalars()
    }
    parents = {
        m.id: m
        for m in (
            await db.execute(
                select(MaterialRequest).where(
                    MaterialRequest.id.in_(
                        {mi.material_request_id for mi in mr_items.values()}
                    )
                )
            )
        ).scalars()
    }
    requested: dict[uuid.UUID, Decimal] = {}
    for link_id, row in pairs:
        mr_item = mr_items.get(link_id)
        mr = parents.get(mr_item.material_request_id) if mr_item else None
        if mr_item is None or mr is None or mr.company_id != company_id:
            raise NotFoundError("Material Request item not found")
        if mr.docstatus != DOCSTATUS_SUBMITTED:
            raise ValidationError(
                f"Material Request {mr.name} is not submitted", field="items"
            )
        if mr_item.item_id != row.item_id:
            raise ValidationError(
                f"Material Request {mr.name}: linked row is for a different item",
                field="items",
            )
        # cumulative ordered qty is tracked in stock units (MR may be in a
        # different UOM than the PO, e.g. demand in Nos, ordered in Box)
        po_item = link_items[row.item_id]
        stock_qty = row.qty * resolve_conversion_factor(po_item, row.uom or po_item.stock_uom)
        requested[link_id] = requested.get(link_id, ZERO) + stock_qty
    for link_id, qty in requested.items():
        mr_item = mr_items[link_id]
        if mr_item.ordered_qty + qty > mr_item.stock_qty + Decimal("0.000001"):
            mr = parents[mr_item.material_request_id]
            raise ValidationError(
                f"Material Request {mr.name}: ordering {qty} (stock) exceeds the unordered "
                f"quantity {mr_item.stock_qty - mr_item.ordered_qty}",
                field="items",
            )


def set_purchase_order_status(po: PurchaseOrder) -> None:
    if po.docstatus == 0:
        po.status = "Draft"
        return
    if po.docstatus == 2:
        po.status = "Cancelled"
        return
    if po.status == "Closed":
        return
    total_qty = sum((row.qty for row in po.items), ZERO)
    received = sum((min(row.received_qty, row.qty) for row in po.items), ZERO)
    total_amount = sum((row.amount for row in po.items), ZERO)
    billed = sum((min(row.billed_amt, row.amount) for row in po.items), ZERO)
    po.per_received = (received / total_qty * HUNDRED) if total_qty else HUNDRED
    po.per_billed = (billed / total_amount * HUNDRED) if total_amount else ZERO
    received_done = po.per_received >= Decimal("99.999")
    billed_done = po.per_billed >= Decimal("99.999")
    if received_done and billed_done:
        po.status = "Completed"
    elif received_done:
        po.status = "To Bill"
    elif billed_done:
        po.status = "To Receive"
    else:
        po.status = "To Receive and Bill"


async def preview_purchase_order(db: AsyncSession, payload: PurchaseOrderCreate, user: CurrentUser):
    """GST + totals preview for a draft Purchase Order (no persistence)."""
    company = await get_company(db, user.company_id)
    supplier = await get_supplier(db, payload.supplier_id, company.id)
    return await compute_doc_tax_preview(
        db, company=company, party=supplier, kind="purchase", items=payload.items,
        conversion_rate=payload.conversion_rate, apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=payload.discount_amount,
    )


async def create_purchase_order(
    db: AsyncSession, payload: PurchaseOrderCreate, user: CurrentUser
) -> PurchaseOrder:
    company = await get_company(db, user.company_id)
    supplier = await get_supplier(db, payload.supplier_id, company.id)
    currency = (payload.currency or company.default_currency).upper()
    items = await get_items(db, {row.item_id for row in payload.items if row.item_id}, company.id)
    if payload.set_warehouse_id is not None:
        await get_warehouse(db, payload.set_warehouse_id, company.id)
    await _validate_mr_links(db, company.id, payload)
    if payload.supplier_quotation_id is not None:
        sq = await db.get(SupplierQuotation, payload.supplier_quotation_id)
        if sq is None or sq.company_id != company.id or sq.supplier_id != supplier.id:
            raise NotFoundError("Supplier Quotation not found for this supplier")

    # resolve missing rates from buying price lists / item master
    rates: list[Decimal] = []
    for row in payload.items:
        item = items.get(row.item_id) if row.item_id else None
        if row.rate is not None:
            rates.append(row.rate)
        elif item is not None:
            rate, _ = await resolve_item_rate(
                db, item, buying=True, on_date=payload.posting_date, currency=currency,
            )
            rates.append(rate)
        else:
            rates.append(Decimal("0"))  # free-text line with no rate given

    tax_rows_in = await _load_tax_rows(db, payload, supplier)
    item_rates = await item_tax_rates(db, payload.items)  # per-item GST overrides
    # No tax template resolved: derive Input GST from each line's HSN so the PO
    # shows GST too. Only fires on the otherwise-zero-tax path.
    if not tax_rows_in:
        auto_rows, auto_overrides = await auto_gst_from_items(
            db, company=company, party_gstin=supplier.tax_id, place_of_supply=None,
            payload_items=payload.items, item_rates=item_rates, is_sales=False,
        )
        if auto_rows:
            tax_rows_in = auto_rows
            for iid, heads in auto_overrides.items():
                item_rates.setdefault(iid, {}).update(heads)
    engine_items = [
        ItemRow(
            qty=row.qty,
            rate=(row.price_list_rate if row.price_list_rate is not None else rate),
            price_list_rate=(row.price_list_rate if row.price_list_rate is not None else rate),
            discount_percentage=row.discount_percentage,
            discount_amount=row.discount_amount,
            item_tax_rate=item_rates.get(_line_key(row, idx), {}),
        )
        for idx, (row, rate) in enumerate(zip(payload.items, rates))
    ]
    engine_taxes = [
        TaxRow(
            charge_type=t.charge_type, rate=t.rate, tax_amount=t.tax_amount, row_id=t.row_id,
            add_deduct_tax=t.add_deduct_tax, category=t.category,
            account_head_id=t.account_head_id, included_in_print_rate=t.included_in_print_rate,
        )
        for t in tax_rows_in
    ]
    totals = calculate_taxes_and_totals(
        engine_items, engine_taxes,
        conversion_rate=payload.conversion_rate,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=payload.discount_amount,
        is_purchase=True,
    )

    name = await get_next_name(db, STOCK_NAMING_SERIES["Purchase Order"], company.id)
    po = PurchaseOrder(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        supplier_id=supplier.id,
        posting_date=payload.posting_date,
        schedule_date=payload.schedule_date,
        terms=payload.terms,
        supplier_address_id=payload.supplier_address_id,
        shipping_address_id=payload.shipping_address_id,
        contact_person_id=payload.contact_person_id,
        payment_terms_template_id=payload.payment_terms_template_id,
        set_warehouse_id=payload.set_warehouse_id,
        supplier_quotation_id=payload.supplier_quotation_id,
        currency=currency,
        conversion_rate=payload.conversion_rate,
        remarks=payload.remarks,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=totals.discount_amount,
        total_qty=totals.total_qty,
        total=totals.total,
        base_total=totals.base_total,
        net_total=totals.net_total,
        base_net_total=totals.base_net_total,
        total_taxes_and_charges=totals.total_taxes_and_charges,
        base_total_taxes_and_charges=totals.base_total_taxes_and_charges,
        grand_total=totals.grand_total,
        base_grand_total=totals.base_grand_total,
        rounded_total=totals.rounded_total,
        rounding_adjustment=totals.rounding_adjustment,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(po)
    await db.flush()

    for idx, (row, engine_item) in enumerate(zip(payload.items, engine_items), start=1):
        item = items.get(row.item_id) if row.item_id else None
        warehouse_id = row.warehouse_id or payload.set_warehouse_id or (
            item.default_warehouse_id if item else None
        )
        if item is not None and item.is_stock_item and warehouse_id is None:
            raise ValidationError(
                f"Item row {idx}: warehouse is required for stock item '{item.item_code}'",
                field="items",
            )
        if warehouse_id is not None:
            await get_warehouse(db, warehouse_id, company.id)
        if item is not None and not item.is_purchase_item:
            raise ValidationError(
                f"Item '{item.item_code}' is not a purchase item", field="items"
            )
        uom = row.uom or (item.stock_uom if item else None)
        factor = resolve_conversion_factor(item, uom) if item is not None else Decimal("1")
        db.add(
            PurchaseOrderItem(
                order_id=po.id,
                idx=idx,
                item_id=item.id if item else None,
                item_code=item.item_code if item else None,
                item_name=item.item_name if item else row.item_name,
                description=row.description or (item.description if item else None),
                qty=engine_item.qty,
                uom=uom,
                conversion_factor=factor,
                stock_qty=engine_item.qty * factor,
                price_list_rate=engine_item.price_list_rate or engine_item.rate,
                base_price_list_rate=engine_item.base_price_list_rate,
                discount_percentage=engine_item.discount_percentage,
                discount_amount=engine_item.discount_amount,
                rate=engine_item.rate,
                amount=engine_item.amount,
                base_rate=engine_item.base_rate,
                base_amount=engine_item.base_amount,
                net_amount=engine_item.net_amount,
                base_net_amount=engine_item.base_net_amount,
                warehouse_id=warehouse_id,
                schedule_date=row.schedule_date or payload.schedule_date,
                cost_center_id=row.cost_center_id,
                material_request_item_id=row.material_request_item_id,
            )
        )
    for idx, (tax_in, engine_tax) in enumerate(zip(tax_rows_in, engine_taxes), start=1):
        db.add(
            PurchaseOrderTax(
                order_id=po.id,
                idx=idx,
                charge_type=engine_tax.charge_type,
                row_id=engine_tax.row_id,
                rate=engine_tax.rate,
                account_head_id=tax_in.account_head_id,
                cost_center_id=tax_in.cost_center_id,
                description=tax_in.description,
                add_deduct_tax=engine_tax.add_deduct_tax,
                category=engine_tax.category,
                included_in_print_rate=engine_tax.included_in_print_rate,
                tax_amount=engine_tax.tax_amount,
                total=engine_tax.total,
                base_tax_amount=engine_tax.base_tax_amount,
                base_total=engine_tax.base_total,
            )
        )
    await db.flush()
    await log_audit(
        db, doctype="Purchase Order", document_id=po.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_purchase_order(db, po.id, company.id)


async def get_purchase_order(
    db: AsyncSession, po_id: uuid.UUID, company_id: uuid.UUID | None
) -> PurchaseOrder:
    po = await db.scalar(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.taxes))
        .where(PurchaseOrder.id == po_id, PurchaseOrder.company_id == company_id)
    )
    if po is None:
        raise NotFoundError("Purchase Order not found")
    return po


async def list_purchase_orders(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    status: str | None = None, supplier_id: uuid.UUID | None = None,
) -> tuple[list[PurchaseOrder], int]:
    stmt = (
        select(PurchaseOrder)
        .where(PurchaseOrder.company_id == company_id)
        .order_by(PurchaseOrder.posting_date.desc(), PurchaseOrder.creation.desc())
    )
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_id is not None:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    return await paginate(db, stmt, page, page_size)


async def _refresh_material_requests(db: AsyncSession, po: PurchaseOrder, sign: int) -> None:
    """Accrue/release ordered_qty on linked Material Request rows."""
    mr_item_ids = {r.material_request_item_id for r in po.items if r.material_request_item_id}
    if not mr_item_ids:
        return
    mr_items = {
        m.id: m
        for m in (
            await db.execute(
                select(MaterialRequestItem).where(MaterialRequestItem.id.in_(mr_item_ids))
            )
        ).scalars()
    }
    touched_mr_ids = set()
    for row in po.items:
        mr_item = mr_items.get(row.material_request_item_id)
        if mr_item is None:
            continue
        mr_item.ordered_qty = max(ZERO, mr_item.ordered_qty + sign * row.stock_qty)
        touched_mr_ids.add(mr_item.material_request_id)
    for mr_id in touched_mr_ids:
        mr = await db.scalar(
            select(MaterialRequest)
            .options(selectinload(MaterialRequest.items))
            .where(MaterialRequest.id == mr_id)
        )
        if mr is not None and mr.docstatus == DOCSTATUS_SUBMITTED:
            set_material_request_status(mr)


async def submit_purchase_order(
    db: AsyncSession, po_id: uuid.UUID, user: CurrentUser
) -> PurchaseOrder:
    po = await get_purchase_order(db, po_id, user.company_id)
    require_draft(po.docstatus)
    items = await get_items(db, {r.item_id for r in po.items if r.item_id}, po.company_id)

    for row in po.items:
        item = items.get(row.item_id)
        if item is not None and item.is_stock_item and row.warehouse_id is not None:
            await update_ordered_qty(db, po.company_id, row.item_id, row.warehouse_id, row.stock_qty)

    await _refresh_material_requests(db, po, sign=1)

    po.docstatus = DOCSTATUS_SUBMITTED
    set_purchase_order_status(po)
    po.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Purchase Order", document_id=po.id, action="SUBMIT",
        user_id=user.id, company_id=po.company_id,
    )
    await db.commit()
    return await get_purchase_order(db, po.id, user.company_id)


async def cancel_purchase_order(
    db: AsyncSession, po_id: uuid.UUID, user: CurrentUser
) -> PurchaseOrder:
    po = await get_purchase_order(db, po_id, user.company_id)
    require_submitted(po.docstatus)
    if any(row.received_qty > ZERO or row.billed_amt > ZERO for row in po.items):
        raise ValidationError(
            "Cannot cancel: receipts or invoices exist against this order. Cancel them first."
        )
    items = await get_items(db, {r.item_id for r in po.items if r.item_id}, po.company_id)
    for row in po.items:
        item = items.get(row.item_id)
        if item is not None and item.is_stock_item and row.warehouse_id is not None:
            await update_ordered_qty(db, po.company_id, row.item_id, row.warehouse_id, -row.stock_qty)

    await _refresh_material_requests(db, po, sign=-1)

    po.docstatus = DOCSTATUS_CANCELLED
    set_purchase_order_status(po)
    po.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Purchase Order", document_id=po.id, action="CANCEL",
        user_id=user.id, company_id=po.company_id,
    )
    await db.commit()
    return await get_purchase_order(db, po.id, user.company_id)
