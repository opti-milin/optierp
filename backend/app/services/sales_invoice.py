"""Sales Invoice service — Module 02.

Source: erpnext/accounts/doctype/sales_invoice. Totals come from the
taxes_and_totals engine; submission posts GL (Dr receivable / Cr income +
taxes + rounding); cancellation reverses GL and restores any return links.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.gst_states import gst_state_label_of
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import SalesInvoice, SalesInvoiceItem, SalesInvoiceTax
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.schemas.accounts import SalesInvoiceCreate
from app.services import gl
from app.services.accounts_common import (
    NAMING_SERIES,
    auto_gst_from_items,
    _line_key,
    base_payable_total,
    get_company,
    get_customer,
    get_receivable_account,
    item_tax_rates,
    require_draft,
    require_submitted,
    set_invoice_status,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.stock_common import resolve_conversion_factor
from app.services.taxes_and_totals import ItemRow, TaxRow, calculate_taxes_and_totals

ZERO = Decimal("0")


async def _validate_cycle_links(
    db: AsyncSession,
    customer,
    company_id: uuid.UUID,
    currency: str,
    rows: list,  # objects with qty/amount-or-rate + link ids (payload or ORM rows)
    sign: Decimal,
) -> None:
    """SO/DN rows referenced by invoice lines must belong to the same customer,
    be submitted, and keep their billed trackers within [0, cap]. Signed deltas
    make this one check cover invoices AND credit notes; aggregation makes
    duplicate rows against the same source line count cumulatively. Runs at
    create AND again at submit (against the then-current state)."""
    from app.services.cycle_links import DN_BILLING, SO_BILLING, aggregate, validate_link_deltas

    def amount_of(row) -> Decimal:
        amount = getattr(row, "amount", None)
        return Decimal(amount) if amount is not None else Decimal(row.qty) * Decimal(row.rate)

    await validate_link_deltas(
        db, DN_BILLING, company_id=company_id, party_id=customer.id,
        deltas=aggregate([
            (row.delivery_note_item_id, sign * Decimal(row.qty)) for row in rows
        ]),
    )
    await validate_link_deltas(
        db, SO_BILLING, company_id=company_id, party_id=customer.id, currency=currency,
        deltas=aggregate([
            (row.sales_order_item_id, sign * amount_of(row)) for row in rows
        ]),
    )


async def _apply_cycle_links(db: AsyncSession, invoice: SalesInvoice, sign: int) -> None:
    """Accrue (+1 on submit) or release (-1 on cancel) billed trackers on
    linked SO/DN rows. Bounds are enforced by _validate_cycle_links, so the
    arithmetic stays exact and cancel restores precisely what submit applied
    (row amounts are already signed for returns)."""
    from app.models.selling import SalesOrderItem
    from app.models.stock import DeliveryNoteItem
    from app.services.delivery_note import get_delivery_note, set_delivery_note_status
    from app.services.sales_order import get_sales_order, set_sales_order_status

    touched_so: set[uuid.UUID] = set()
    touched_dn: set[uuid.UUID] = set()
    for item in invoice.items:
        if item.sales_order_item_id is not None:
            so_item = await db.get(SalesOrderItem, item.sales_order_item_id)
            if so_item is not None:
                so_item.billed_amt = so_item.billed_amt + sign * item.amount
                touched_so.add(so_item.order_id)
        if item.delivery_note_item_id is not None:
            dn_item = await db.get(DeliveryNoteItem, item.delivery_note_item_id)
            if dn_item is not None:
                dn_item.billed_qty = dn_item.billed_qty + sign * item.qty
                touched_dn.add(dn_item.delivery_note_id)
    for so_id in touched_so:
        so = await get_sales_order(db, so_id, invoice.company_id)
        set_sales_order_status(so)
    for dn_id in touched_dn:
        dn = await get_delivery_note(db, dn_id, invoice.company_id)
        set_delivery_note_status(dn)


async def _load_tax_rows(db: AsyncSession, payload: SalesInvoiceCreate, customer) -> list:
    """Inline taxes win; then an explicit template; then the template resolved
    from the customer's tax category / company default (erpnext get_party_details)."""
    if payload.taxes:
        return payload.taxes
    from app.models.accounts import TaxTemplate

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
    from app.schemas.accounts import TaxRowIn

    return [
        TaxRowIn(
            charge_type=d.charge_type,
            rate=d.rate,
            tax_amount=d.tax_amount,
            row_id=d.row_id,
            account_head_id=d.account_head_id,
            cost_center_id=d.cost_center_id,
            description=d.description,
            included_in_print_rate=d.included_in_print_rate,
        )
        for d in template.details
    ]


async def _resolve_invoice_taxes(db: AsyncSession, payload: SalesInvoiceCreate, company, customer):
    """Shared tax resolution: template rows (or HSN-derived auto-GST) + the
    per-item rate overrides the engine applies. Returns (tax_rows_in, item_rates).

    Zero-rated sales (SEZ/Export u/s 16 IGST Act): under LUT/bond → NO tax; with payment →
    IGST (always inter-state, force_inter), bypassing the party's tax template."""
    zero_rated = payload.gst_category in ("SEZ", "Export")
    item_rates = await item_tax_rates(db, payload.items)
    if zero_rated and not payload.export_with_payment:
        return [], item_rates  # LUT / bond — a Bill of Export with no GST
    tax_rows_in = [] if (payload.is_opening or zero_rated) else await _load_tax_rows(db, payload, customer)
    if not tax_rows_in and not payload.is_opening:
        auto_rows, auto_overrides = await auto_gst_from_items(
            db, company=company, party_gstin=customer.tax_id,
            place_of_supply=payload.place_of_supply, payload_items=payload.items,
            item_rates=item_rates, force_inter=(zero_rated and payload.export_with_payment),
        )
        if auto_rows:
            tax_rows_in = auto_rows
            for iid, heads in auto_overrides.items():
                item_rates.setdefault(iid, {}).update(heads)
    return tax_rows_in, item_rates


async def preview_sales_invoice(db: AsyncSession, payload: SalesInvoiceCreate, user: CurrentUser):
    """Compute taxes + totals for a DRAFT (no persistence) so the form previews the
    GST that ``create_sales_invoice`` will apply."""
    from app.services.accounts_common import compute_doc_tax_preview

    company = await get_company(db, user.company_id)
    customer = await get_customer(db, payload.customer_id, company.id)
    return await compute_doc_tax_preview(
        db, company=company, party=customer, kind="sales", items=payload.items,
        place_of_supply=payload.place_of_supply, conversion_rate=payload.conversion_rate,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=payload.discount_amount,
        gst_category=payload.gst_category, export_with_payment=payload.export_with_payment,
    )


async def create_sales_invoice(
    db: AsyncSession, payload: SalesInvoiceCreate, user: CurrentUser
) -> SalesInvoice:
    company = await get_company(db, user.company_id)
    customer = await get_customer(db, payload.customer_id, company.id)
    debit_to_id = payload.debit_to_id or get_receivable_account(company, customer)
    currency = (payload.currency or company.default_currency).upper()

    if payload.is_return and not payload.return_against_id:
        raise ValidationError("return_against_id is required for a return (credit note)",
                              field="return_against_id")

    await _validate_cycle_links(
        db, customer, company.id, currency, payload.items,
        sign=Decimal("-1") if payload.is_return else Decimal("1"),
    )
    # Opening (migration-in) invoices carry no tax — they just establish the
    # outstanding receivable against the Temporary Opening account.
    # Tax rows (template or HSN-derived auto-GST) + per-item rate overrides —
    # shared with preview_sales_invoice so the form preview matches this exactly.
    tax_rows_in, item_rates = await _resolve_invoice_taxes(db, payload, company, customer)

    # run the calculation engine
    engine_items = [
        ItemRow(
            qty=item.qty,
            rate=(item.price_list_rate if item.price_list_rate is not None else item.rate),
            price_list_rate=(item.price_list_rate if item.price_list_rate is not None else item.rate),
            discount_percentage=item.discount_percentage,
            discount_amount=item.discount_amount,
            item_tax_rate=item_rates.get(_line_key(item, idx), {}),
        )
        for idx, item in enumerate(payload.items)
    ]
    engine_taxes = [
        TaxRow(
            charge_type=t.charge_type,
            rate=t.rate,
            tax_amount=t.tax_amount,
            row_id=t.row_id,
            included_in_print_rate=t.included_in_print_rate,
            account_head_id=t.account_head_id,
        )
        for t in tax_rows_in
    ]
    totals = calculate_taxes_and_totals(
        engine_items,
        engine_taxes,
        conversion_rate=payload.conversion_rate,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=payload.discount_amount,
    )

    # India TCS: collect rate% of the taxable (net) base FROM the customer — added
    # on top of the invoice and owed to the govt (TCS Payable account).
    tax_withholding_amount = ZERO
    if payload.tax_withholding_category_id is not None:
        from app.models.accounts import TaxWithholdingCategory

        twc = await db.get(TaxWithholdingCategory, payload.tax_withholding_category_id)
        if twc is None or twc.company_id != company.id:
            raise NotFoundError("Tax withholding category not found")
        if twc.kind != "TCS":
            raise ValidationError(
                "Sales invoices use a TCS collection category", field="tax_withholding_category_id"
            )
        tax_withholding_amount = (totals.base_net_total * twc.rate / Decimal("100")).quantize(
            Decimal("0.01")
        )

    sign = Decimal("-1") if payload.is_return else Decimal("1")
    name = await get_next_name(db, NAMING_SERIES["Sales Invoice"], company.id)
    # India GST place of supply: the recipient's (customer's) state, else the company's
    # own state for an intra-state B2C sale. Caller may override.
    place_of_supply = (
        payload.place_of_supply
        or gst_state_label_of(customer.tax_id)
        or gst_state_label_of(company.tax_id)
    )
    invoice = SalesInvoice(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        customer_id=customer.id,
        debit_to_id=debit_to_id,
        posting_date=payload.posting_date,
        due_date=payload.due_date,
        currency=currency,
        conversion_rate=payload.conversion_rate,
        remarks=payload.remarks,
        is_return=payload.is_return,
        is_opening=payload.is_opening,
        place_of_supply=place_of_supply,
        is_reverse_charge=payload.is_reverse_charge,
        return_against_id=payload.return_against_id,
        po_no=payload.po_no,
        po_date=payload.po_date,
        ecommerce_gstin=(payload.ecommerce_gstin or None),
        gst_category=payload.gst_category,
        export_with_payment=payload.export_with_payment,
        shipping_bill_no=(payload.shipping_bill_no or None),
        shipping_bill_date=payload.shipping_bill_date,
        port_code=(payload.port_code or None),
        terms=payload.terms,
        customer_address_id=payload.customer_address_id,
        shipping_address_id=payload.shipping_address_id,
        contact_person_id=payload.contact_person_id,
        payment_terms_template_id=payload.payment_terms_template_id,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=totals.discount_amount,
        total_qty=totals.total_qty * sign,
        total=totals.total * sign,
        base_total=totals.base_total * sign,
        net_total=totals.net_total * sign,
        base_net_total=totals.base_net_total * sign,
        total_taxes_and_charges=totals.total_taxes_and_charges * sign,
        base_total_taxes_and_charges=totals.base_total_taxes_and_charges * sign,
        grand_total=totals.grand_total * sign,
        base_grand_total=totals.base_grand_total * sign,
        rounded_total=totals.rounded_total * sign,
        rounding_adjustment=totals.rounding_adjustment * sign,
        outstanding_amount=ZERO,
        tax_withholding_category_id=payload.tax_withholding_category_id,
        tax_withholding_amount=tax_withholding_amount * sign,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(invoice)
    await db.flush()

    default_income = company.default_income_account_id
    for idx, (item_in, engine_item) in enumerate(zip(payload.items, engine_items), start=1):
        item_master = None
        if item_in.item_id is not None:
            from app.models.stock import Item

            item_master = await db.get(Item, item_in.item_id)
        income_account_id = item_in.account_id
        if income_account_id is None and item_master is not None:
            income_account_id = item_master.income_account_id
        income_account_id = income_account_id or default_income
        if income_account_id is None:
            raise ValidationError(
                f"Item row {idx}: no income account given and the company has no default",
                field="items",
            )
        # multi-UOM: stock_qty is informational on an invoice (billing caps stay in
        # document qty / amount), but kept consistent for display/reporting
        factor = (
            resolve_conversion_factor(item_master, item_in.uom, strict=False)
            if item_master else Decimal("1")
        )
        db.add(
            SalesInvoiceItem(
                invoice_id=invoice.id,
                idx=idx,
                item_code=item_in.item_code,
                item_name=item_in.item_name,
                hsn_sac_code=item_in.hsn_sac_code or (item_master.hsn_sac_code if item_master else None),
                description=item_in.description,
                qty=engine_item.qty * sign,
                uom=item_in.uom,
                conversion_factor=factor,
                stock_qty=engine_item.qty * sign * factor,
                price_list_rate=engine_item.price_list_rate or engine_item.rate,
                base_price_list_rate=engine_item.base_price_list_rate,
                discount_percentage=engine_item.discount_percentage,
                discount_amount=engine_item.discount_amount,
                rate=engine_item.rate,
                amount=engine_item.amount * sign,
                base_rate=engine_item.base_rate,
                base_amount=engine_item.base_amount * sign,
                net_amount=engine_item.net_amount * sign,
                base_net_amount=engine_item.base_net_amount * sign,
                cost_center_id=item_in.cost_center_id,
                income_account_id=income_account_id,
                item_id=item_in.item_id,
                sales_order_item_id=item_in.sales_order_item_id,
                delivery_note_item_id=item_in.delivery_note_item_id,
            )
        )
    for idx, (tax_in, engine_tax) in enumerate(zip(tax_rows_in, engine_taxes), start=1):
        db.add(
            SalesInvoiceTax(
                invoice_id=invoice.id,
                idx=idx,
                charge_type=engine_tax.charge_type,
                row_id=engine_tax.row_id,
                rate=engine_tax.rate,
                account_head_id=tax_in.account_head_id,
                cost_center_id=tax_in.cost_center_id,
                description=tax_in.description,
                included_in_print_rate=engine_tax.included_in_print_rate,
                tax_amount=engine_tax.tax_amount * sign,
                total=engine_tax.total * sign,
                base_tax_amount=engine_tax.base_tax_amount * sign,
                base_total=engine_tax.base_total * sign,
            )
        )
    set_invoice_status(invoice)
    await db.flush()
    await log_audit(
        db, doctype="Sales Invoice", document_id=invoice.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_sales_invoice(db, invoice.id, company.id)


async def get_sales_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, company_id: uuid.UUID | None
) -> SalesInvoice:
    invoice = await db.scalar(
        select(SalesInvoice)
        .options(selectinload(SalesInvoice.items), selectinload(SalesInvoice.taxes))
        .where(SalesInvoice.id == invoice_id, SalesInvoice.company_id == company_id)
    )
    if invoice is None:
        raise NotFoundError("Sales Invoice not found")
    return invoice


async def list_sales_invoices(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    status: str | None = None, customer_id: uuid.UUID | None = None,
) -> tuple[list[SalesInvoice], int]:
    stmt = (
        select(SalesInvoice)
        .where(SalesInvoice.company_id == company_id)
        .order_by(SalesInvoice.posting_date.desc(), SalesInvoice.creation.desc())
    )
    if status:
        stmt = stmt.where(SalesInvoice.status == status)
    if customer_id is not None:
        stmt = stmt.where(SalesInvoice.customer_id == customer_id)
    return await paginate(db, stmt, page, page_size)


def _build_gl_rows(
    invoice: SalesInvoice, customer_name: str, tcs_account_id: uuid.UUID | None = None
) -> list[gl.GLRow]:
    rows: list[gl.GLRow] = []
    receivable_amount = base_payable_total(invoice)

    # India TCS: collected from the customer — the receivable GROWS by the TCS and
    # the collected amount is credited to TCS payable (owed to the govt).
    tcs = invoice.tax_withholding_amount if tcs_account_id is not None else ZERO
    party_amount = receivable_amount + tcs

    # Dr receivable (party row), incl. any TCS; against_voucher self-links for AR
    rows.append(
        gl.GLRow(
            account_id=invoice.debit_to_id,
            debit=party_amount if party_amount > ZERO else ZERO,
            credit=-party_amount if party_amount < ZERO else ZERO,
            party_type="Customer",
            party_id=invoice.customer_id,
            against_voucher_type="Sales Invoice",
            against_voucher_id=invoice.id,
        )
    )
    if tcs != ZERO and tcs_account_id is not None:
        rows.append(
            gl.GLRow(
                account_id=tcs_account_id,
                credit=tcs if tcs > ZERO else ZERO,
                debit=-tcs if tcs < ZERO else ZERO,
                against=customer_name,
                remarks="TCS collected",
            )
        )
    # Cr income per item
    for item in invoice.items:
        rows.append(
            gl.GLRow(
                account_id=item.income_account_id,
                credit=item.base_net_amount if item.base_net_amount > ZERO else ZERO,
                debit=-item.base_net_amount if item.base_net_amount < ZERO else ZERO,
                cost_center_id=item.cost_center_id,
                against=customer_name,
            )
        )
    # Cr taxes
    for tax in invoice.taxes:
        if tax.base_tax_amount == ZERO:
            continue
        rows.append(
            gl.GLRow(
                account_id=tax.account_head_id,
                credit=tax.base_tax_amount if tax.base_tax_amount > ZERO else ZERO,
                debit=-tax.base_tax_amount if tax.base_tax_amount < ZERO else ZERO,
                cost_center_id=tax.cost_center_id,
                against=customer_name,
            )
        )
    return rows


async def submit_sales_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, user: CurrentUser
) -> SalesInvoice:
    invoice = await get_sales_invoice(db, invoice_id, user.company_id)
    require_draft(invoice.docstatus)
    company = await get_company(db, invoice.company_id)
    customer = await get_customer(db, invoice.customer_id, invoice.company_id)

    tcs_account_id = None
    if invoice.tax_withholding_category_id is not None and invoice.tax_withholding_amount != ZERO:
        from app.models.accounts import TaxWithholdingCategory

        twc = await db.get(TaxWithholdingCategory, invoice.tax_withholding_category_id)
        tcs_account_id = twc.account_id if twc else None
    rows = _build_gl_rows(invoice, customer.customer_name, tcs_account_id)

    # rounding adjustment balances receivable(rounded) vs net+taxes
    base_rounding = invoice.rounding_adjustment * invoice.conversion_rate
    if base_rounding != ZERO:
        if company.round_off_account_id is None:
            raise ValidationError("Company has no Round Off account configured")
        rows.append(
            gl.GLRow(
                account_id=company.round_off_account_id,
                credit=base_rounding if base_rounding > ZERO else ZERO,
                debit=-base_rounding if base_rounding < ZERO else ZERO,
                cost_center_id=company.default_cost_center_id,
            )
        )

    await gl.make_gl_entries(
        db,
        company_id=invoice.company_id,
        voucher_type="Sales Invoice",
        voucher_id=invoice.id,
        voucher_no=invoice.name,
        posting_date=invoice.posting_date,
        rows=rows,
        user_id=user.id,
        is_opening=invoice.is_opening,
        remarks=invoice.remarks,
    )

    # rows are already signed (returns carry negative qty/amount)
    await _validate_cycle_links(
        db, customer, invoice.company_id, invoice.currency, invoice.items, sign=Decimal("1")
    )
    invoice.docstatus = DOCSTATUS_SUBMITTED
    # the customer owes the bill PLUS any TCS collected on top
    invoice.outstanding_amount = base_payable_total(invoice) + invoice.tax_withholding_amount
    await _apply_cycle_links(db, invoice, sign=1)

    # a credit note (return) reduces the original invoice's outstanding
    if invoice.is_return and invoice.return_against_id:
        original = await get_sales_invoice(db, invoice.return_against_id, invoice.company_id)
        require_submitted(original.docstatus)
        original.outstanding_amount += invoice.outstanding_amount  # negative
        set_invoice_status(original)
        invoice.outstanding_amount = ZERO

    set_invoice_status(invoice)
    invoice.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Sales Invoice", document_id=invoice.id, action="SUBMIT",
        user_id=user.id, company_id=invoice.company_id,
    )
    await db.commit()
    return await get_sales_invoice(db, invoice.id, user.company_id)


async def cancel_sales_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, user: CurrentUser
) -> SalesInvoice:
    invoice = await get_sales_invoice(db, invoice_id, user.company_id)
    require_submitted(invoice.docstatus)
    # "no payment yet" ⇒ outstanding == grand + TCS collected (the amount first booked).
    if (
        invoice.outstanding_amount != base_payable_total(invoice) + invoice.tax_withholding_amount
        and not invoice.is_return
    ):
        raise ValidationError(
            "Cannot cancel: payments are allocated against this invoice. Cancel them first."
        )

    await gl.make_reverse_gl_entries(
        db, voucher_type="Sales Invoice", voucher_id=invoice.id, user_id=user.id
    )
    await _apply_cycle_links(db, invoice, sign=-1)
    if invoice.is_return and invoice.return_against_id:
        original = await get_sales_invoice(db, invoice.return_against_id, invoice.company_id)
        original.outstanding_amount -= base_payable_total(invoice)
        set_invoice_status(original)

    invoice.docstatus = DOCSTATUS_CANCELLED
    invoice.outstanding_amount = ZERO
    set_invoice_status(invoice)
    invoice.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Sales Invoice", document_id=invoice.id, action="CANCEL",
        user_id=user.id, company_id=invoice.company_id,
    )
    await db.commit()
    return await get_sales_invoice(db, invoice.id, user.company_id)
