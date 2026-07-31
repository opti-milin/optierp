"""Sales Order service — Module 05.

On submit: bin.reserved_qty += qty for stock items; credit-limit check
returns warnings (not a hard block — Section 3, Module 05 rule 3); a linked
Quotation flips to Ordered. Deliveries/invoices drive per_delivered /
per_billed and the status (To Deliver and Bill -> ... -> Completed).
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import SalesInvoice, TaxTemplate
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.selling import Quotation, QuotationItem, SalesOrder, SalesOrderItem, SalesOrderTax
from app.schemas.accounts import TaxRowIn
from app.schemas.selling import SalesOrderCreate
from app.services.accounts_common import (
    auto_gst_from_items,
    _line_key,
    compute_doc_tax_preview,
    get_company,
    get_customer,
    item_tax_rates,
    require_draft,
    require_submitted,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.stock_common import (
    STOCK_NAMING_SERIES,
    get_items,
    get_warehouse,
    resolve_conversion_factor,
    resolve_item_rate,
)
from app.services.blanket import blanket_rate
from app.services.coupon import resolve_and_consume_coupon
from app.services.pricing import apply_selling_pricing
from app.services.shipping import shipping_tax_row
from app.services.stock_ledger import update_reserved_qty
from app.services.taxes_and_totals import ItemRow, TaxRow, calculate_taxes_and_totals

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def set_sales_order_status(so: SalesOrder) -> None:
    if so.docstatus == 0:
        so.status = "Draft"
        return
    if so.docstatus == 2:
        so.status = "Cancelled"
        return
    if so.status == "Closed":
        return
    total_qty = sum((row.qty for row in so.items), ZERO)
    delivered = sum((min(row.delivered_qty, row.qty) for row in so.items), ZERO)
    total_amount = sum((row.amount for row in so.items), ZERO)
    billed = sum((min(row.billed_amt, row.amount) for row in so.items), ZERO)
    so.per_delivered = (delivered / total_qty * HUNDRED) if total_qty else HUNDRED
    so.per_billed = (billed / total_amount * HUNDRED) if total_amount else ZERO
    delivered_done = so.per_delivered >= Decimal("99.999")
    billed_done = so.per_billed >= Decimal("99.999")
    if delivered_done and billed_done:
        so.status = "Completed"
    elif delivered_done:
        so.status = "To Bill"
    elif billed_done:
        so.status = "To Deliver"
    else:
        so.status = "To Deliver and Bill"


async def _load_tax_rows(db: AsyncSession, payload: SalesOrderCreate, customer) -> list[TaxRowIn]:
    if payload.taxes:
        return payload.taxes
    if payload.tax_template_id:
        template = await db.scalar(
            select(TaxTemplate)
            .options(selectinload(TaxTemplate.details))
            .where(TaxTemplate.id == payload.tax_template_id)
        )
        if template is None or template.kind != "sales":
            raise NotFoundError("Sales tax template not found")
    else:
        from app.services.accounts_masters import resolve_tax_template

        template = await resolve_tax_template(
            db, customer.company_id, "sales", customer.tax_category_id, party_gstin=customer.tax_id
        )
        if template is None:
            return []
    return [
        TaxRowIn(
            charge_type=d.charge_type, rate=d.rate, tax_amount=d.tax_amount, row_id=d.row_id,
            account_head_id=d.account_head_id, cost_center_id=d.cost_center_id,
            description=d.description, included_in_print_rate=d.included_in_print_rate,
        )
        for d in template.details
    ]


async def preview_sales_order(db: AsyncSession, payload: SalesOrderCreate, user: CurrentUser):
    """GST + totals preview for a draft Sales Order (no persistence). Uses the
    entered line rates (pricing is resolved on save)."""
    company = await get_company(db, user.company_id)
    customer = await get_customer(db, payload.customer_id, company.id)
    return await compute_doc_tax_preview(
        db, company=company, party=customer, kind="sales", items=payload.items,
        conversion_rate=payload.conversion_rate, apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=payload.discount_amount,
    )


async def create_sales_order(
    db: AsyncSession, payload: SalesOrderCreate, user: CurrentUser
) -> SalesOrder:
    company = await get_company(db, user.company_id)
    customer = await get_customer(db, payload.customer_id, company.id)
    currency = (payload.currency or customer.default_currency or company.default_currency).upper()
    items = await get_items(db, {row.item_id for row in payload.items if row.item_id}, company.id)
    if payload.set_warehouse_id is not None:
        await get_warehouse(db, payload.set_warehouse_id, company.id)

    quotation: Quotation | None = None
    if payload.quotation_id is not None:
        quotation = await db.get(Quotation, payload.quotation_id)
        if quotation is None or quotation.company_id != company.id:
            raise NotFoundError("Quotation not found")
        if quotation.customer_id != customer.id:
            raise ValidationError("Quotation belongs to a different customer", field="quotation_id")
        if quotation.docstatus != DOCSTATUS_SUBMITTED:
            raise ValidationError("Quotation is not submitted", field="quotation_id")
    # quotation_item links must point into THAT quotation (cross-company /
    # cross-document references are rejected)
    linked_qi_ids = {r.quotation_item_id for r in payload.items if r.quotation_item_id}
    if linked_qi_ids:
        if quotation is None:
            raise ValidationError(
                "quotation_item_id given without quotation_id", field="items"
            )
        valid_qi_ids = {
            qi_id
            for qi_id in (
                await db.execute(
                    select(QuotationItem.id).where(QuotationItem.quotation_id == quotation.id)
                )
            ).scalars()
        }
        if linked_qi_ids - valid_qi_ids:
            raise ValidationError(
                "quotation_item_id does not belong to the linked quotation", field="items"
            )

    rates: list[Decimal] = []
    for row in payload.items:
        item = items.get(row.item_id) if row.item_id else None
        if row.rate is not None:
            base = row.rate
        elif item is not None:
            base = await blanket_rate(db, company.id, customer.id, row.item_id, payload.posting_date)
            if base is None:
                base, _ = await resolve_item_rate(
                    db, item, buying=False, on_date=payload.posting_date, currency=currency,
                )
        else:
            base = Decimal("0")  # free-text line with no rate given
        if item is not None:
            priced = await apply_selling_pricing(
                db, company.id, item=item, customer=customer,
                qty=row.qty, base_rate=base, on_date=payload.posting_date,
            )
            rates.append(priced.rate)
        else:
            rates.append(base)  # no pricing rules without an item master

    additional_discount_pct = payload.additional_discount_percentage
    if payload.coupon_code:
        additional_discount_pct = await resolve_and_consume_coupon(
            db, company.id, payload.coupon_code, payload.posting_date
        )

    tax_rows_in = await _load_tax_rows(db, payload, customer)
    item_rates = await item_tax_rates(db, payload.items)  # per-item GST overrides
    # No sales tax template resolved (customer with no GST category): derive GST
    # from each line's HSN so the order shows GST too. Runs before shipping is
    # appended, only on the otherwise-zero-tax path.
    if not tax_rows_in:
        auto_rows, auto_overrides = await auto_gst_from_items(
            db, company=company, party_gstin=customer.tax_id, place_of_supply=None,
            payload_items=payload.items, item_rates=item_rates, is_sales=True,
        )
        if auto_rows:
            tax_rows_in = auto_rows
            for iid, heads in auto_overrides.items():
                item_rates.setdefault(iid, {}).update(heads)
    if payload.shipping_rule_id:
        subtotal = sum((row.qty * rate for row, rate in zip(payload.items, rates)), Decimal("0"))
        ship_row = await shipping_tax_row(db, company.id, payload.shipping_rule_id, subtotal)
        if ship_row is not None:
            tax_rows_in = [*tax_rows_in, ship_row]
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
            account_head_id=t.account_head_id, included_in_print_rate=t.included_in_print_rate,
        )
        for t in tax_rows_in
    ]
    totals = calculate_taxes_and_totals(
        engine_items, engine_taxes,
        conversion_rate=payload.conversion_rate,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=additional_discount_pct,
        discount_amount=payload.discount_amount,
    )

    name = await get_next_name(db, STOCK_NAMING_SERIES["Sales Order"], company.id)
    so = SalesOrder(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        customer_id=customer.id,
        posting_date=payload.posting_date,
        delivery_date=payload.delivery_date,
        order_type=payload.order_type,
        po_no=payload.po_no,
        po_date=payload.po_date,
        terms=payload.terms,
        customer_address_id=payload.customer_address_id,
        shipping_address_id=payload.shipping_address_id,
        contact_person_id=payload.contact_person_id,
        campaign_id=payload.campaign_id,
        source_id=payload.source_id,
        territory_id=payload.territory_id,
        customer_group_id=payload.customer_group_id,
        sales_partner_id=payload.sales_partner_id,
        shipping_rule_id=payload.shipping_rule_id,
        payment_terms_template_id=payload.payment_terms_template_id,
        set_warehouse_id=payload.set_warehouse_id,
        quotation_id=payload.quotation_id,
        currency=currency,
        conversion_rate=payload.conversion_rate,
        remarks=payload.remarks,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=additional_discount_pct,
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
    db.add(so)
    await db.flush()

    for idx, (row, engine_item) in enumerate(zip(payload.items, engine_items), start=1):
        item = items.get(row.item_id) if row.item_id else None
        if item is not None and not item.is_sales_item:
            raise ValidationError(f"Item '{item.item_code}' is not a sales item", field="items")
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
        uom = row.uom or (item.stock_uom if item else None)
        factor = resolve_conversion_factor(item, uom) if item is not None else Decimal("1")
        db.add(
            SalesOrderItem(
                order_id=so.id,
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
                delivery_date=row.delivery_date or payload.delivery_date,
                cost_center_id=row.cost_center_id,
                quotation_item_id=row.quotation_item_id,
            )
        )
    for idx, (tax_in, engine_tax) in enumerate(zip(tax_rows_in, engine_taxes), start=1):
        db.add(
            SalesOrderTax(
                order_id=so.id,
                idx=idx,
                charge_type=engine_tax.charge_type,
                row_id=engine_tax.row_id,
                rate=engine_tax.rate,
                account_head_id=tax_in.account_head_id,
                cost_center_id=tax_in.cost_center_id,
                description=tax_in.description,
                included_in_print_rate=engine_tax.included_in_print_rate,
                tax_amount=engine_tax.tax_amount,
                total=engine_tax.total,
                base_tax_amount=engine_tax.base_tax_amount,
                base_total=engine_tax.base_total,
            )
        )
    await db.flush()
    await log_audit(
        db, doctype="Sales Order", document_id=so.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_sales_order(db, so.id, company.id)


async def get_sales_order(
    db: AsyncSession, so_id: uuid.UUID, company_id: uuid.UUID | None
) -> SalesOrder:
    so = await db.scalar(
        select(SalesOrder)
        .options(selectinload(SalesOrder.items), selectinload(SalesOrder.taxes))
        .where(SalesOrder.id == so_id, SalesOrder.company_id == company_id)
    )
    if so is None:
        raise NotFoundError("Sales Order not found")
    return so


async def list_sales_orders(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    status: str | None = None, customer_id: uuid.UUID | None = None,
) -> tuple[list[SalesOrder], int]:
    stmt = (
        select(SalesOrder)
        .where(SalesOrder.company_id == company_id)
        .order_by(SalesOrder.posting_date.desc(), SalesOrder.creation.desc())
    )
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    if customer_id is not None:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    return await paginate(db, stmt, page, page_size)


async def _credit_limit_warnings(db: AsyncSession, so: SalesOrder, customer) -> list[str]:
    """Outstanding AR + unbilled submitted orders vs customer.credit_limit
    (erpnext credit-limit check, warning-only)."""
    if not customer.credit_limit or customer.credit_limit <= ZERO:
        return []
    invoice_outstanding = (
        await db.execute(
            select(func.coalesce(func.sum(SalesInvoice.outstanding_amount), 0)).where(
                SalesInvoice.customer_id == customer.id,
                SalesInvoice.docstatus == DOCSTATUS_SUBMITTED,
            )
        )
    ).scalar_one()
    unbilled_orders = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(SalesOrder.base_grand_total * (HUNDRED - SalesOrder.per_billed) / HUNDRED),
                    0,
                )
            ).where(
                SalesOrder.customer_id == customer.id,
                SalesOrder.docstatus == DOCSTATUS_SUBMITTED,
                SalesOrder.status.notin_(("Closed", "Completed")),
                SalesOrder.id != so.id,
            )
        )
    ).scalar_one()
    exposure = Decimal(invoice_outstanding) + Decimal(unbilled_orders) + so.base_grand_total
    if exposure > customer.credit_limit:
        return [
            f"Credit limit exceeded for {customer.customer_name}: total exposure "
            f"{exposure:.2f} is above the credit limit {customer.credit_limit:.2f}"
        ]
    return []


async def submit_sales_order(
    db: AsyncSession, so_id: uuid.UUID, user: CurrentUser
) -> tuple[SalesOrder, list[str]]:
    so = await get_sales_order(db, so_id, user.company_id)
    require_draft(so.docstatus)
    customer = await get_customer(db, so.customer_id, so.company_id)
    items = await get_items(db, {r.item_id for r in so.items if r.item_id}, so.company_id)

    warnings = await _credit_limit_warnings(db, so, customer)

    from app.services.order_fulfillment import (
        FulfillmentLineIn,
        check_order_fulfillment,
        raise_if_blocked,
    )

    fulfillment = await check_order_fulfillment(
        db,
        so.company_id,
        [
            FulfillmentLineIn(
                item_id=row.item_id,
                qty=row.qty,
                selling_amount=row.amount or ZERO,
                delivery_date=row.delivery_date,
                warehouse_id=row.warehouse_id or so.set_warehouse_id,
            )
            for row in so.items
            if row.item_id is not None
        ],
        header_delivery_date=so.delivery_date,
        document_context="sales-order",
        document_id=so.id,
    )
    raise_if_blocked(fulfillment)
    warnings.extend(fulfillment.warnings)

    from app.services.cm_planning.plan import assert_margin_policy_for_sales_order

    margin = await assert_margin_policy_for_sales_order(db, so.id, so.company_id)
    if margin.message and not margin.ok and margin.policy == "warn":
        warnings.append(margin.message)

    for row in so.items:
        item = items.get(row.item_id)
        if item is not None and item.is_stock_item and row.warehouse_id is not None:
            await update_reserved_qty(db, so.company_id, row.item_id, row.warehouse_id, row.stock_qty)

    if so.quotation_id is not None:
        quotation = await db.get(Quotation, so.quotation_id)
        if quotation is not None and quotation.docstatus == DOCSTATUS_SUBMITTED:
            quotation.status = "Ordered"

    so.docstatus = DOCSTATUS_SUBMITTED
    set_sales_order_status(so)
    so.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Sales Order", document_id=so.id, action="SUBMIT",
        user_id=user.id, company_id=so.company_id,
    )
    await db.commit()
    return await get_sales_order(db, so.id, user.company_id), warnings


async def check_sales_order_fulfillment(
    db: AsyncSession, so_id: uuid.UUID, company_id: uuid.UUID
):
    """Explicit Check Fulfillment for a saved Sales Order (always runs CTP even if mode=off)."""
    from app.services.order_fulfillment import (
        FulfillmentLineIn,
        check_order_fulfillment,
        fulfillment_result_to_dict,
    )

    so = await get_sales_order(db, so_id, company_id)
    result = await check_order_fulfillment(
        db,
        so.company_id,
        [
            FulfillmentLineIn(
                item_id=row.item_id,
                qty=row.qty,
                selling_amount=row.amount or ZERO,
                delivery_date=row.delivery_date,
                warehouse_id=row.warehouse_id or so.set_warehouse_id,
            )
            for row in so.items
            if row.item_id is not None
        ],
        header_delivery_date=so.delivery_date,
        document_context="sales-order",
        document_id=so.id,
        force=True,
    )
    return fulfillment_result_to_dict(result)


async def preview_sales_order_fulfillment(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    items: list,
    delivery_date,
    set_warehouse_id=None,
    as_of=None,
):
    from app.services.order_fulfillment import (
        FulfillmentLineIn,
        check_order_fulfillment,
        fulfillment_result_to_dict,
    )

    lines: list[FulfillmentLineIn] = []
    for row in items:
        if row.item_id is None:
            continue
        qty = row.qty
        rate = row.rate or ZERO
        amount = (qty * rate).quantize(Decimal("0.01"))
        lines.append(
            FulfillmentLineIn(
                item_id=row.item_id,
                qty=qty,
                selling_amount=amount,
                delivery_date=getattr(row, "delivery_date", None),
                warehouse_id=row.warehouse_id or set_warehouse_id,
            )
        )
    result = await check_order_fulfillment(
        db,
        company_id,
        lines,
        header_delivery_date=delivery_date,
        as_of=as_of,
        document_context="sales-order",
        force=True,
    )
    return fulfillment_result_to_dict(result)


async def cancel_sales_order(
    db: AsyncSession, so_id: uuid.UUID, user: CurrentUser
) -> SalesOrder:
    so = await get_sales_order(db, so_id, user.company_id)
    require_submitted(so.docstatus)
    if any(row.delivered_qty > ZERO or row.billed_amt > ZERO for row in so.items):
        raise ValidationError(
            "Cannot cancel: deliveries or invoices exist against this order. Cancel them first."
        )
    items = await get_items(db, {r.item_id for r in so.items if r.item_id}, so.company_id)
    for row in so.items:
        item = items.get(row.item_id)
        if item is not None and item.is_stock_item and row.warehouse_id is not None:
            await update_reserved_qty(db, so.company_id, row.item_id, row.warehouse_id, -row.stock_qty)

    if so.quotation_id is not None:
        quotation = await db.get(Quotation, so.quotation_id)
        if quotation is not None and quotation.status == "Ordered":
            # only reopen when no OTHER submitted order still references it
            other = await db.scalar(
                select(func.count())
                .select_from(SalesOrder)
                .where(
                    SalesOrder.quotation_id == so.quotation_id,
                    SalesOrder.docstatus == DOCSTATUS_SUBMITTED,
                    SalesOrder.id != so.id,
                )
            )
            if not other:
                quotation.status = "Open"

    so.docstatus = DOCSTATUS_CANCELLED
    set_sales_order_status(so)
    so.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Sales Order", document_id=so.id, action="CANCEL",
        user_id=user.id, company_id=so.company_id,
    )
    await db.commit()
    return await get_sales_order(db, so.id, user.company_id)
