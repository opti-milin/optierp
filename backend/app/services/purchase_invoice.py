"""Purchase Invoice service — Module 02 (mirror of sales_invoice).

GL on submit: Cr payable (party) / Dr expense per item / Dr-Cr taxes by
Add/Deduct; Valuation-only rows post nothing (they affect stock cost,
arriving with Module 03).
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
from app.models.accounts import PurchaseInvoice, PurchaseInvoiceItem, PurchaseInvoiceTax, TaxTemplate
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.schemas.accounts import PurchaseInvoiceCreate, TaxRowIn
from app.services import gl
from app.services.accounts_common import (
    NAMING_SERIES,
    auto_gst_from_items,
    base_payable_total,
    compute_doc_tax_preview,
    get_company,
    get_payable_account,
    get_supplier,
    item_tax_rates,
    require_draft,
    require_submitted,
    reverse_charge_tax_rows,
    set_invoice_status,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.stock_common import resolve_conversion_factor
from app.services.taxes_and_totals import ItemRow, TaxRow, calculate_taxes_and_totals

ZERO = Decimal("0")


async def _validate_cycle_links(
    db: AsyncSession,
    supplier,
    company_id: uuid.UUID,
    currency: str,
    rows: list,  # objects with qty/amount-or-rate + link ids (payload or ORM rows)
    sign: Decimal,
) -> None:
    """PO/PR rows referenced by invoice lines must belong to the same supplier,
    be submitted, and keep their billed trackers within [0, cap]. Signed deltas
    cover invoices AND debit notes; aggregation covers duplicate rows. Runs at
    create AND again at submit."""
    from app.services.cycle_links import PO_BILLING, PR_BILLING, aggregate, validate_link_deltas

    def amount_of(row) -> Decimal:
        amount = getattr(row, "amount", None)
        return Decimal(amount) if amount is not None else Decimal(row.qty) * Decimal(row.rate)

    await validate_link_deltas(
        db, PR_BILLING, company_id=company_id, party_id=supplier.id,
        deltas=aggregate([
            (row.purchase_receipt_item_id, sign * Decimal(row.qty)) for row in rows
        ]),
    )
    await validate_link_deltas(
        db, PO_BILLING, company_id=company_id, party_id=supplier.id, currency=currency,
        deltas=aggregate([
            (row.purchase_order_item_id, sign * amount_of(row)) for row in rows
        ]),
    )


async def _apply_cycle_links(db: AsyncSession, invoice: PurchaseInvoice, sign: int) -> None:
    """Accrue (+1 on submit) or release (-1 on cancel) billed trackers on
    linked PO/PR rows. Bounds come from _validate_cycle_links; rows are
    already signed for returns, so cancel restores exactly what submit did.
    Non-stock (service) PO rows also accrue received_qty here — there is no
    receipt for services, so without this the PO could never complete."""
    from app.models.buying import PurchaseOrderItem
    from app.models.stock import Item, PurchaseReceiptItem
    from app.services.purchase_order import get_purchase_order, set_purchase_order_status
    from app.services.purchase_receipt import get_purchase_receipt, set_receipt_status

    touched_po: set[uuid.UUID] = set()
    touched_pr: set[uuid.UUID] = set()
    for item in invoice.items:
        if item.purchase_order_item_id is not None:
            po_item = await db.get(PurchaseOrderItem, item.purchase_order_item_id)
            if po_item is not None:
                po_item.billed_amt = po_item.billed_amt + sign * item.amount
                if po_item.item_id is not None:
                    item_master = await db.get(Item, po_item.item_id)
                    if item_master is not None and not item_master.is_stock_item:
                        po_item.received_qty = min(
                            po_item.qty, max(ZERO, po_item.received_qty + sign * item.qty)
                        )
                touched_po.add(po_item.order_id)
        if item.purchase_receipt_item_id is not None:
            pr_item = await db.get(PurchaseReceiptItem, item.purchase_receipt_item_id)
            if pr_item is not None:
                pr_item.billed_qty = pr_item.billed_qty + sign * item.qty
                touched_pr.add(pr_item.receipt_id)
    for po_id in touched_po:
        po = await get_purchase_order(db, po_id, invoice.company_id)
        set_purchase_order_status(po)
    for pr_id in touched_pr:
        pr = await get_purchase_receipt(db, pr_id, invoice.company_id)
        set_receipt_status(pr)


async def _load_tax_rows(
    db: AsyncSession, payload: PurchaseInvoiceCreate, supplier
) -> list[TaxRowIn]:
    """Inline taxes win; then an explicit template; then the template resolved
    from the supplier's tax category / company default (erpnext get_party_details)."""
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
            charge_type=d.charge_type,
            rate=d.rate,
            tax_amount=d.tax_amount,
            row_id=d.row_id,
            account_head_id=d.account_head_id,
            cost_center_id=d.cost_center_id,
            description=d.description,
            add_deduct_tax=d.add_deduct_tax,
            category=d.category,
            included_in_print_rate=d.included_in_print_rate,
        )
        for d in template.details
    ]


async def preview_purchase_invoice(db: AsyncSession, payload: PurchaseInvoiceCreate, user: CurrentUser):
    """Compute taxes + totals for a DRAFT purchase invoice (no persistence) so the
    form previews the GST that ``create_purchase_invoice`` will apply."""
    company = await get_company(db, user.company_id)
    supplier = await get_supplier(db, payload.supplier_id, company.id)
    return await compute_doc_tax_preview(
        db, company=company, party=supplier, kind="purchase", items=payload.items,
        place_of_supply=payload.place_of_supply, conversion_rate=payload.conversion_rate,
        apply_discount_on=payload.apply_discount_on,
        additional_discount_percentage=payload.additional_discount_percentage,
        discount_amount=payload.discount_amount,
        is_reverse_charge=payload.is_reverse_charge,
    )


async def create_purchase_invoice(
    db: AsyncSession, payload: PurchaseInvoiceCreate, user: CurrentUser
) -> PurchaseInvoice:
    company = await get_company(db, user.company_id)
    supplier = await get_supplier(db, payload.supplier_id, company.id)
    credit_to_id = payload.credit_to_id or get_payable_account(company, supplier)
    currency = (payload.currency or company.default_currency).upper()

    if payload.is_return and not payload.return_against_id:
        raise ValidationError("return_against_id is required for a return (debit note)",
                              field="return_against_id")

    await _validate_cycle_links(
        db, supplier, company.id, currency, payload.items,
        sign=Decimal("-1") if payload.is_return else Decimal("1"),
    )
    # Opening (migration-in) invoices carry no tax — they just establish the
    # outstanding payable against the Temporary Opening account.
    tax_rows_in = [] if payload.is_opening else await _load_tax_rows(db, payload, supplier)
    item_rates = await item_tax_rates(db, payload.items)
    # India RCM: a reverse-charge inward supply self-assesses GST — the buyer books it as
    # both ITC (Input GST, an Add row → Dr) and a liability (Output CGST/SGST/IGST, Deduct
    # rows → Cr), netting the payable to the base. It overrides the normal tax resolution
    # (the supplier charges no GST) whenever the caller didn't pass explicit tax rows.
    if payload.is_reverse_charge and not payload.is_opening and not payload.taxes:
        rcm_rows, rcm_overrides = await reverse_charge_tax_rows(
            db, company=company, party_gstin=supplier.tax_id,
            place_of_supply=payload.place_of_supply, payload_items=payload.items,
            item_rates=item_rates,
        )
        if rcm_rows:
            tax_rows_in = rcm_rows
            for iid, heads in rcm_overrides.items():
                item_rates.setdefault(iid, {}).update(heads)
    # No tax template resolved (supplier with no GST category and no default): fall
    # back to the line items' own GST (Input GST from the item's HSN / template),
    # so purchase GST applies too. Only fires on the otherwise-zero-tax path.
    if not tax_rows_in and not payload.is_opening:
        auto_rows, auto_overrides = await auto_gst_from_items(
            db, company=company, party_gstin=supplier.tax_id,
            place_of_supply=payload.place_of_supply, payload_items=payload.items,
            item_rates=item_rates, is_sales=False,
        )
        if auto_rows:
            tax_rows_in = auto_rows
            for iid, heads in auto_overrides.items():
                item_rates.setdefault(iid, {}).update(heads)
    engine_items = [
        ItemRow(
            qty=item.qty,
            rate=(item.price_list_rate if item.price_list_rate is not None else item.rate),
            price_list_rate=(item.price_list_rate if item.price_list_rate is not None else item.rate),
            discount_percentage=item.discount_percentage,
            discount_amount=item.discount_amount,
            item_tax_rate=item_rates.get(item.item_id, {}),
        )
        for item in payload.items
    ]
    engine_taxes = [
        TaxRow(
            charge_type=t.charge_type,
            rate=t.rate,
            tax_amount=t.tax_amount,
            row_id=t.row_id,
            add_deduct_tax=t.add_deduct_tax,
            category=t.category,
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
        is_purchase=True,
    )

    # India TDS: withhold rate% of the taxable (net) base — paid to the govt, not the supplier.
    tax_withholding_amount = ZERO
    if payload.tax_withholding_category_id is not None:
        from app.models.accounts import TaxWithholdingCategory

        twc = await db.get(TaxWithholdingCategory, payload.tax_withholding_category_id)
        if twc is None or twc.company_id != company.id:
            raise NotFoundError("Tax withholding category not found")
        if twc.kind != "TDS":
            raise ValidationError(
                "Purchase invoices use a TDS withholding category", field="tax_withholding_category_id"
            )
        tax_withholding_amount = (totals.base_net_total * twc.rate / Decimal("100")).quantize(
            Decimal("0.01")
        )

    sign = Decimal("-1") if payload.is_return else Decimal("1")
    name = await get_next_name(db, NAMING_SERIES["Purchase Invoice"], company.id)
    invoice = PurchaseInvoice(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        supplier_id=supplier.id,
        credit_to_id=credit_to_id,
        posting_date=payload.posting_date,
        due_date=payload.due_date,
        bill_no=payload.bill_no,
        bill_date=payload.bill_date,
        terms=payload.terms,
        supplier_address_id=payload.supplier_address_id,
        shipping_address_id=payload.shipping_address_id,
        contact_person_id=payload.contact_person_id,
        payment_terms_template_id=payload.payment_terms_template_id,
        currency=currency,
        conversion_rate=payload.conversion_rate,
        remarks=payload.remarks,
        is_return=payload.is_return,
        is_opening=payload.is_opening,
        # India GST place of supply for an inward supply = our (company's) state; overridable.
        place_of_supply=payload.place_of_supply or gst_state_label_of(company.tax_id),
        is_reverse_charge=payload.is_reverse_charge,
        return_against_id=payload.return_against_id,
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

    default_expense = company.default_expense_account_id
    for idx, (item_in, engine_item) in enumerate(zip(payload.items, engine_items), start=1):
        from app.models.stock import Item

        item_master = (
            await db.get(Item, item_in.item_id) if item_in.item_id is not None else None
        )
        expense_account_id = item_in.account_id
        if expense_account_id is None and company.enable_perpetual_inventory and (
            item_in.purchase_receipt_item_id is not None
            # bill-before-receipt on a PO for a stock item: the receipt will
            # debit inventory later — expensing here would double-count cost
            or (
                item_in.purchase_order_item_id is not None
                and item_master is not None
                and item_master.is_stock_item
            )
        ):
            # the invoice clears Stock Received But Not Billed, not expense
            expense_account_id = company.stock_received_but_not_billed_account_id
        if expense_account_id is None and item_master is not None:
            expense_account_id = item_master.expense_account_id
        expense_account_id = expense_account_id or default_expense
        if expense_account_id is None:
            raise ValidationError(
                f"Item row {idx}: no expense account given and the company has no default",
                field="items",
            )
        # multi-UOM: stock_qty is informational on an invoice (billing caps stay in
        # document qty / amount), but kept consistent for display/reporting
        factor = (
            resolve_conversion_factor(item_master, item_in.uom, strict=False)
            if item_master else Decimal("1")
        )
        db.add(
            PurchaseInvoiceItem(
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
                expense_account_id=expense_account_id,
                item_id=item_in.item_id,
                purchase_order_item_id=item_in.purchase_order_item_id,
                purchase_receipt_item_id=item_in.purchase_receipt_item_id,
            )
        )
    for idx, (tax_in, engine_tax) in enumerate(zip(tax_rows_in, engine_taxes), start=1):
        db.add(
            PurchaseInvoiceTax(
                invoice_id=invoice.id,
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
                tax_amount=engine_tax.tax_amount * sign,
                total=engine_tax.total * sign,
                base_tax_amount=engine_tax.base_tax_amount * sign,
                base_total=engine_tax.base_total * sign,
            )
        )
    set_invoice_status(invoice)
    await db.flush()
    await log_audit(
        db, doctype="Purchase Invoice", document_id=invoice.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_purchase_invoice(db, invoice.id, company.id)


async def get_purchase_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, company_id: uuid.UUID | None
) -> PurchaseInvoice:
    invoice = await db.scalar(
        select(PurchaseInvoice)
        .options(selectinload(PurchaseInvoice.items), selectinload(PurchaseInvoice.taxes))
        .where(PurchaseInvoice.id == invoice_id, PurchaseInvoice.company_id == company_id)
    )
    if invoice is None:
        raise NotFoundError("Purchase Invoice not found")
    return invoice


async def list_purchase_invoices(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    status: str | None = None, supplier_id: uuid.UUID | None = None,
) -> tuple[list[PurchaseInvoice], int]:
    stmt = (
        select(PurchaseInvoice)
        .where(PurchaseInvoice.company_id == company_id)
        .order_by(PurchaseInvoice.posting_date.desc(), PurchaseInvoice.creation.desc())
    )
    if status:
        stmt = stmt.where(PurchaseInvoice.status == status)
    if supplier_id is not None:
        stmt = stmt.where(PurchaseInvoice.supplier_id == supplier_id)
    return await paginate(db, stmt, page, page_size)


def _build_gl_rows(
    invoice: PurchaseInvoice,
    supplier_name: str,
    srbnb_split: dict[uuid.UUID, tuple[Decimal, uuid.UUID | None]] | None = None,
    tds_account_id: uuid.UUID | None = None,
) -> list[gl.GLRow]:
    rows: list[gl.GLRow] = []
    payable_amount = base_payable_total(invoice)
    srbnb_split = srbnb_split or {}

    # India TDS: withhold from the supplier — the payable shrinks and the
    # withheld amount is credited to the TDS payable (owed to the govt).
    tds = invoice.tax_withholding_amount if tds_account_id is not None else ZERO
    party_amount = payable_amount - tds

    # Cr payable (party row), net of TDS
    rows.append(
        gl.GLRow(
            account_id=invoice.credit_to_id,
            credit=party_amount if party_amount > ZERO else ZERO,
            debit=-party_amount if party_amount < ZERO else ZERO,
            party_type="Supplier",
            party_id=invoice.supplier_id,
            against_voucher_type="Purchase Invoice",
            against_voucher_id=invoice.id,
        )
    )
    if tds != ZERO and tds_account_id is not None:
        rows.append(
            gl.GLRow(
                account_id=tds_account_id,
                credit=tds if tds > ZERO else ZERO,
                debit=-tds if tds < ZERO else ZERO,
                against=supplier_name,
                remarks="TDS withheld",
            )
        )
    # Dr expense per item. Receipt-linked rows clear SRBNB at the RECEIPT's
    # valuation; any rate/discount difference posts to the expense account so
    # SRBNB nets to exactly zero once a receipt is fully billed.
    for item in invoice.items:
        split = srbnb_split.get(item.id)
        if split is not None:
            srbnb_amount, diff_account_id = split
            diff = item.base_net_amount - srbnb_amount
            rows.append(
                gl.GLRow(
                    account_id=item.expense_account_id,
                    debit=srbnb_amount if srbnb_amount > ZERO else ZERO,
                    credit=-srbnb_amount if srbnb_amount < ZERO else ZERO,
                    cost_center_id=item.cost_center_id,
                    against=supplier_name,
                )
            )
            if diff != ZERO:
                rows.append(
                    gl.GLRow(
                        account_id=diff_account_id,
                        debit=diff if diff > ZERO else ZERO,
                        credit=-diff if diff < ZERO else ZERO,
                        cost_center_id=item.cost_center_id,
                        against=supplier_name,
                    )
                )
            continue
        rows.append(
            gl.GLRow(
                account_id=item.expense_account_id,
                debit=item.base_net_amount if item.base_net_amount > ZERO else ZERO,
                credit=-item.base_net_amount if item.base_net_amount < ZERO else ZERO,
                cost_center_id=item.cost_center_id,
                against=supplier_name,
            )
        )
    # taxes: Add -> Dr; Deduct -> Cr; pure Valuation rows post nothing here
    for tax in invoice.taxes:
        if tax.base_tax_amount == ZERO or tax.category == "Valuation":
            continue
        amount = tax.base_tax_amount
        if tax.add_deduct_tax == "Deduct":
            amount = -amount
        rows.append(
            gl.GLRow(
                account_id=tax.account_head_id,
                debit=amount if amount > ZERO else ZERO,
                credit=-amount if amount < ZERO else ZERO,
                cost_center_id=tax.cost_center_id,
                against=supplier_name,
            )
        )
    return rows


async def submit_purchase_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, user: CurrentUser
) -> PurchaseInvoice:
    invoice = await get_purchase_invoice(db, invoice_id, user.company_id)
    require_draft(invoice.docstatus)
    company = await get_company(db, invoice.company_id)
    supplier = await get_supplier(db, invoice.supplier_id, invoice.company_id)

    # receipt-linked rows whose expense head is SRBNB clear it at the receipt's
    # valuation (qty * receipt base rate); the difference goes to expense
    srbnb_split: dict[uuid.UUID, tuple[Decimal, uuid.UUID | None]] = {}
    if company.enable_perpetual_inventory and company.stock_received_but_not_billed_account_id:
        from app.models.stock import Item, PurchaseReceiptItem

        for item in invoice.items:
            if (
                item.purchase_receipt_item_id is None
                or item.expense_account_id != company.stock_received_but_not_billed_account_id
            ):
                continue
            pr_item = await db.get(PurchaseReceiptItem, item.purchase_receipt_item_id)
            if pr_item is None:
                continue
            srbnb_amount = item.qty * pr_item.base_rate
            diff_account_id = company.default_expense_account_id
            if item.item_id is not None:
                item_master = await db.get(Item, item.item_id)
                if item_master is not None and item_master.expense_account_id is not None:
                    diff_account_id = item_master.expense_account_id
            if srbnb_amount != item.base_net_amount and diff_account_id is None:
                raise ValidationError(
                    "Invoice rate differs from the receipt valuation and the company "
                    "has no default expense account for the difference"
                )
            srbnb_split[item.id] = (srbnb_amount, diff_account_id)

    tds_account_id = None
    if invoice.tax_withholding_category_id is not None and invoice.tax_withholding_amount != ZERO:
        from app.models.accounts import TaxWithholdingCategory

        twc = await db.get(TaxWithholdingCategory, invoice.tax_withholding_category_id)
        tds_account_id = twc.account_id if twc else None
    rows = _build_gl_rows(invoice, supplier.supplier_name, srbnb_split, tds_account_id)

    base_rounding = invoice.rounding_adjustment * invoice.conversion_rate
    if base_rounding != ZERO:
        if company.round_off_account_id is None:
            raise ValidationError("Company has no Round Off account configured")
        rows.append(
            gl.GLRow(
                account_id=company.round_off_account_id,
                debit=base_rounding if base_rounding > ZERO else ZERO,
                credit=-base_rounding if base_rounding < ZERO else ZERO,
                cost_center_id=company.default_cost_center_id,
            )
        )

    await gl.make_gl_entries(
        db,
        company_id=invoice.company_id,
        voucher_type="Purchase Invoice",
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
        db, supplier, invoice.company_id, invoice.currency, invoice.items, sign=Decimal("1")
    )
    invoice.docstatus = DOCSTATUS_SUBMITTED
    # you owe the supplier the bill less the TDS withheld
    invoice.outstanding_amount = base_payable_total(invoice) - invoice.tax_withholding_amount
    await _apply_cycle_links(db, invoice, sign=1)

    if invoice.is_return and invoice.return_against_id:
        original = await get_purchase_invoice(db, invoice.return_against_id, invoice.company_id)
        require_submitted(original.docstatus)
        original.outstanding_amount += invoice.outstanding_amount
        set_invoice_status(original)
        invoice.outstanding_amount = ZERO

    set_invoice_status(invoice)
    invoice.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Purchase Invoice", document_id=invoice.id, action="SUBMIT",
        user_id=user.id, company_id=invoice.company_id,
    )
    await db.commit()

    # Auto-create draft Assets for any fixed-asset lines (Assets module Phase 3). Runs
    # after the invoice commits and is best-effort: a failure here must never roll back a
    # posted invoice — the user can create the asset manually instead.
    try:
        from app.services import asset as asset_service

        full = await get_purchase_invoice(db, invoice.id, user.company_id)
        created = await asset_service.create_assets_from_purchase_invoice(db, full, user)
        if created:
            from app.core.logging import get_logger

            get_logger(__name__).info(
                "assets_created_from_invoice", invoice=str(invoice.id), count=created
            )
    except Exception:  # noqa: BLE001 — asset auto-creation must not fail the invoice
        await db.rollback()

    return await get_purchase_invoice(db, invoice.id, user.company_id)


async def cancel_purchase_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, user: CurrentUser
) -> PurchaseInvoice:
    invoice = await get_purchase_invoice(db, invoice_id, user.company_id)
    require_submitted(invoice.docstatus)
    # "no payment yet" ⇒ outstanding == grand - TDS withheld (the amount first booked).
    if (
        invoice.outstanding_amount != base_payable_total(invoice) - invoice.tax_withholding_amount
        and not invoice.is_return
    ):
        raise ValidationError(
            "Cannot cancel: payments are allocated against this invoice. Cancel them first."
        )

    await gl.make_reverse_gl_entries(
        db, voucher_type="Purchase Invoice", voucher_id=invoice.id, user_id=user.id
    )
    await _apply_cycle_links(db, invoice, sign=-1)
    if invoice.is_return and invoice.return_against_id:
        original = await get_purchase_invoice(db, invoice.return_against_id, invoice.company_id)
        original.outstanding_amount -= base_payable_total(invoice)
        set_invoice_status(original)

    invoice.docstatus = DOCSTATUS_CANCELLED
    invoice.outstanding_amount = ZERO
    set_invoice_status(invoice)
    invoice.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Purchase Invoice", document_id=invoice.id, action="CANCEL",
        user_id=user.id, company_id=invoice.company_id,
    )
    await db.commit()
    return await get_purchase_invoice(db, invoice.id, user.company_id)
