"""Quotation service — Module 05 (customer quotations).

Lifecycle: Draft -> Open (submit) -> Ordered (Sales Order created) /
Cancelled. No stock or GL effect.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import TaxTemplate
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.models.selling import Quotation, QuotationItem, QuotationTax
from app.schemas.accounts import TaxRowIn
from app.schemas.selling import QuotationCreate
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
from app.services.blanket import blanket_rate
from app.services.coupon import resolve_and_consume_coupon
from app.services.pricing import apply_selling_pricing
from app.services.shipping import shipping_tax_row
from app.services.stock_common import STOCK_NAMING_SERIES, get_items, resolve_item_rate
from app.services.taxes_and_totals import ItemRow, TaxRow, calculate_taxes_and_totals

ZERO = Decimal("0")


async def _load_tax_rows(db: AsyncSession, payload: QuotationCreate, customer) -> list[TaxRowIn]:
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
            description=d.description,
        )
        for d in template.details
    ]


async def preview_quotation(db: AsyncSession, payload: QuotationCreate, user: CurrentUser):
    """GST + totals preview for a draft Quotation (no persistence)."""
    company = await get_company(db, user.company_id)
    customer = await get_customer(db, payload.customer_id, company.id)
    return await compute_doc_tax_preview(
        db, company=company, party=customer, kind="sales", items=payload.items,
        conversion_rate=payload.conversion_rate, apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=payload.discount_amount,
    )


async def create_quotation(
    db: AsyncSession, payload: QuotationCreate, user: CurrentUser
) -> Quotation:
    company = await get_company(db, user.company_id)
    customer = await get_customer(db, payload.customer_id, company.id)
    currency = (payload.currency or customer.default_currency or company.default_currency).upper()
    items = await get_items(db, {row.item_id for row in payload.items}, company.id)

    rates: list[Decimal] = []
    for row in payload.items:
        if row.rate is not None:
            base = row.rate
        else:
            base = await blanket_rate(db, company.id, customer.id, row.item_id, payload.posting_date)
            if base is None:
                base, _ = await resolve_item_rate(
                    db, items[row.item_id], buying=False, on_date=payload.posting_date,
                    currency=currency,
                )
        priced = await apply_selling_pricing(
            db, company.id, item=items[row.item_id], customer=customer,
            qty=row.qty, base_rate=base, on_date=payload.posting_date,
        )
        rates.append(priced.rate)

    additional_discount_pct = payload.additional_discount_percentage
    if payload.coupon_code:
        additional_discount_pct = await resolve_and_consume_coupon(
            db, company.id, payload.coupon_code, payload.posting_date
        )

    tax_rows_in = await _load_tax_rows(db, payload, customer)
    item_rates = await item_tax_rates(db, payload.items)  # per-item GST overrides
    # No sales tax template resolved: derive GST from each line's HSN so the
    # quotation shows GST too. Runs before shipping; zero-tax path only.
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
        subtotal = sum((row.qty * rate for row, rate in zip(payload.items, rates)), ZERO)
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
        TaxRow(charge_type=t.charge_type, rate=t.rate, tax_amount=t.tax_amount, row_id=t.row_id,
               account_head_id=t.account_head_id, included_in_print_rate=t.included_in_print_rate)
        for t in tax_rows_in
    ]
    totals = calculate_taxes_and_totals(
        engine_items, engine_taxes,
        conversion_rate=payload.conversion_rate,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=additional_discount_pct,
        discount_amount=payload.discount_amount,
    )

    name = await get_next_name(db, STOCK_NAMING_SERIES["Quotation"], company.id)
    quotation = Quotation(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        customer_id=customer.id,
        posting_date=payload.posting_date,
        valid_till=payload.valid_till,
        order_type=payload.order_type,
        terms=payload.terms,
        customer_address_id=payload.customer_address_id,
        shipping_address_id=payload.shipping_address_id,
        contact_person_id=payload.contact_person_id,
        campaign_id=payload.campaign_id,
        source_id=payload.source_id,
        territory_id=payload.territory_id,
        customer_group_id=payload.customer_group_id,
        sales_partner_id=payload.sales_partner_id,
        payment_terms_template_id=payload.payment_terms_template_id,
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
    db.add(quotation)
    await db.flush()

    for idx, (row, engine_item) in enumerate(zip(payload.items, engine_items), start=1):
        item = items[row.item_id]
        db.add(
            QuotationItem(
                quotation_id=quotation.id,
                idx=idx,
                item_id=item.id,
                item_code=item.item_code,
                item_name=item.item_name,
                description=row.description or item.description,
                qty=engine_item.qty,
                uom=row.uom or item.stock_uom,
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
                cost_center_id=row.cost_center_id,
            )
        )
    for idx, (tax_in, engine_tax) in enumerate(zip(tax_rows_in, engine_taxes), start=1):
        db.add(
            QuotationTax(
                quotation_id=quotation.id,
                idx=idx,
                charge_type=engine_tax.charge_type,
                row_id=engine_tax.row_id,
                rate=engine_tax.rate,
                account_head_id=tax_in.account_head_id,
                cost_center_id=tax_in.cost_center_id,
                description=tax_in.description,
                tax_amount=engine_tax.tax_amount,
                total=engine_tax.total,
                base_tax_amount=engine_tax.base_tax_amount,
                base_total=engine_tax.base_total,
            )
        )
    await db.flush()
    await log_audit(
        db, doctype="Quotation", document_id=quotation.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_quotation(db, quotation.id, company.id)


async def get_quotation(
    db: AsyncSession, quotation_id: uuid.UUID, company_id: uuid.UUID | None
) -> Quotation:
    quotation = await db.scalar(
        select(Quotation)
        .options(selectinload(Quotation.items), selectinload(Quotation.taxes))
        .where(Quotation.id == quotation_id, Quotation.company_id == company_id)
    )
    if quotation is None:
        raise NotFoundError("Quotation not found")
    return quotation


async def list_quotations(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    status: str | None = None, customer_id: uuid.UUID | None = None,
) -> tuple[list[Quotation], int]:
    stmt = (
        select(Quotation)
        .where(Quotation.company_id == company_id)
        .order_by(Quotation.posting_date.desc(), Quotation.creation.desc())
    )
    if status:
        stmt = stmt.where(Quotation.status == status)
    if customer_id is not None:
        stmt = stmt.where(Quotation.customer_id == customer_id)
    return await paginate(db, stmt, page, page_size)


async def submit_quotation(
    db: AsyncSession, quotation_id: uuid.UUID, user: CurrentUser
) -> Quotation:
    quotation = await get_quotation(db, quotation_id, user.company_id)
    require_draft(quotation.docstatus)
    quotation.docstatus = DOCSTATUS_SUBMITTED
    quotation.status = "Open"
    quotation.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Quotation", document_id=quotation.id, action="SUBMIT",
        user_id=user.id, company_id=quotation.company_id,
    )
    await db.commit()
    return await get_quotation(db, quotation.id, user.company_id)


async def cancel_quotation(
    db: AsyncSession, quotation_id: uuid.UUID, user: CurrentUser
) -> Quotation:
    quotation = await get_quotation(db, quotation_id, user.company_id)
    require_submitted(quotation.docstatus)
    quotation.docstatus = DOCSTATUS_CANCELLED
    quotation.status = "Cancelled"
    quotation.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Quotation", document_id=quotation.id, action="CANCEL",
        user_id=user.id, company_id=quotation.company_id,
    )
    await db.commit()
    return await get_quotation(db, quotation.id, user.company_id)
