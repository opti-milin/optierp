"""Payment Reconciliation — Module 02.

Source: erpnext/accounts/doctype/payment_reconciliation (a tool, not a
stored document): matches a party's outstanding invoices against the
unallocated portion of their submitted payment entries.

Allocating appends Payment Entry Reference rows and applies the spec's
outstanding formula to each invoice (outstanding -= allocated), exactly as
payment_entry submission does.

Assumption (flagged): ERPNext reposts the payment's party-side GL rows with
the new against_voucher links; our gl_entries ledger is append-only, so the
original rows keep their linkage. Party balances are unaffected (the rows
already posted against the party) and AR/AP aging reads invoice outstanding,
not GL, so reports stay consistent.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser
from app.models.accounts import (
    AdvanceGstAdjustment,
    PaymentEntry,
    PaymentEntryReference,
    PurchaseInvoice,
    SalesInvoice,
)
from app.schemas.accounts import (
    PaymentReconciliationIn,
    PaymentReconciliationResponse,
    ReconciledInvoiceRow,
    UnreconciledInvoiceRow,
    UnreconciledPaymentRow,
    UnreconciledResponse,
)
from app.services import gl
from app.services.accounts_common import (
    advance_gst_from_amount,
    get_company,
    get_customer,
    get_supplier,
    require_submitted,
    set_invoice_status,
)
from app.services.audit import log_audit
from app.services.payment_entry import get_payment_entry
from app.services.purchase_invoice import get_purchase_invoice
from app.services.sales_invoice import get_sales_invoice

ZERO = Decimal("0")


async def _validate_party(
    db: AsyncSession, party_type: str, party_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    if party_type == "Customer":
        await get_customer(db, party_id, company_id)
    else:
        await get_supplier(db, party_id, company_id)


async def get_unreconciled(
    db: AsyncSession, company_id: uuid.UUID | None, party_type: str, party_id: uuid.UUID
) -> UnreconciledResponse:
    """Outstanding invoices + payments with an unallocated remainder for a party."""
    if company_id is None:
        raise ValidationError("An active company is required")
    await _validate_party(db, party_type, party_id, company_id)

    if party_type == "Customer":
        invoice_model, party_field, invoice_type = SalesInvoice, "customer_id", "Sales Invoice"
        payment_direction = "Receive"
    else:
        invoice_model, party_field, invoice_type = PurchaseInvoice, "supplier_id", "Purchase Invoice"
        payment_direction = "Pay"

    invoices = (
        (
            await db.execute(
                select(invoice_model)
                .where(
                    invoice_model.company_id == company_id,
                    getattr(invoice_model, party_field) == party_id,
                    invoice_model.docstatus == 1,
                    invoice_model.outstanding_amount > 0,
                )
                .order_by(invoice_model.posting_date)
            )
        )
        .scalars()
        .all()
    )
    payments = (
        (
            await db.execute(
                select(PaymentEntry)
                .where(
                    PaymentEntry.company_id == company_id,
                    PaymentEntry.party_type == party_type,
                    PaymentEntry.party_id == party_id,
                    PaymentEntry.payment_type == payment_direction,
                    PaymentEntry.docstatus == 1,
                    PaymentEntry.unallocated_amount > 0,
                )
                .order_by(PaymentEntry.posting_date)
            )
        )
        .scalars()
        .all()
    )
    return UnreconciledResponse(
        invoices=[
            UnreconciledInvoiceRow(
                invoice_type=invoice_type,
                invoice_id=i.id,
                name=i.name,
                posting_date=i.posting_date,
                grand_total=i.base_grand_total,
                outstanding_amount=i.outstanding_amount,
            )
            for i in invoices
        ],
        payments=[
            UnreconciledPaymentRow(
                payment_entry_id=p.id,
                name=p.name,
                posting_date=p.posting_date,
                paid_amount=p.paid_amount,
                unallocated_amount=p.unallocated_amount,
            )
            for p in payments
        ],
    )


async def _reverse_advance_gst(
    db: AsyncSession, entry: PaymentEntry, invoice: SalesInvoice, allocated_amount: Decimal, user: CurrentUser
) -> None:
    """When a GST-bearing advance is adjusted to an invoice, reverse the tax on the adjusted
    portion (Dr Output GST / Cr 'GST on Advances' control) — proportional and capped at the
    advance's remaining tax — so the invoice's own output GST isn't counted twice. Writes an
    AdvanceGstAdjustment row (GSTR-1 Table 11B + GSTR-3B 3.1(a) netting source).

    Posted under the Payment Entry voucher, so cancelling the payment auto-reverses it too."""
    if entry.payment_type != "Receive" or entry.advance_gst_outstanding <= ZERO:
        return
    if not entry.advance_gst_rate or entry.advance_gst_rate <= ZERO:
        return

    company = await get_company(db, entry.company_id)
    customer = await get_customer(db, entry.party_id, entry.company_id)
    adv = await advance_gst_from_amount(
        db, company=company, party_gstin=customer.tax_id, place_of_supply=invoice.place_of_supply,
        advance_amount=allocated_amount, rate=entry.advance_gst_rate,
    )
    if adv is None:
        return  # accounts unresolvable — leave the advance tax outstanding

    reverse_total = min(adv.total, entry.advance_gst_outstanding)
    if reverse_total <= ZERO:
        return

    control_id = adv.control_id
    if entry.advance_is_inter_state:
        igst_id = adv.output_rows[0][0]
        cgst = sgst = ZERO
        igst = reverse_total
        debit_rows = [(igst_id, reverse_total)]
    else:
        cgst_id, sgst_id = adv.output_rows[0][0], adv.output_rows[1][0]
        cgst = (reverse_total / 2).quantize(Decimal("0.01"))
        sgst = reverse_total - cgst
        igst = ZERO
        debit_rows = [(cgst_id, cgst), (sgst_id, sgst)]

    rows = [gl.GLRow(account_id=acc, debit=amt, remarks="Reverse GST on advance adjusted") for acc, amt in debit_rows]
    rows.append(gl.GLRow(account_id=control_id, credit=reverse_total, remarks="Reverse GST on advance adjusted"))
    await gl.make_gl_entries(
        db, company_id=entry.company_id, voucher_type="Payment Entry", voucher_id=entry.id,
        voucher_no=entry.name, posting_date=invoice.posting_date, rows=rows, user_id=user.id,
    )

    db.add(
        AdvanceGstAdjustment(
            company_id=entry.company_id,
            payment_entry_id=entry.id,
            invoice_id=invoice.id,
            posting_date=invoice.posting_date,
            place_of_supply=invoice.place_of_supply,
            rate=entry.advance_gst_rate,
            base=allocated_amount,
            cgst=cgst,
            sgst=sgst,
            igst=igst,
            cess=ZERO,
            owner=user.id,
        )
    )
    entry.advance_gst_outstanding -= reverse_total


async def reconcile(
    db: AsyncSession, payload: PaymentReconciliationIn, user: CurrentUser
) -> PaymentReconciliationResponse:
    """Apply allocations: link payments to invoices and reduce outstanding."""
    if user.company_id is None:
        raise ValidationError("An active company is required")
    await _validate_party(db, payload.party_type, payload.party_id, user.company_id)
    expected_invoice_type = (
        "Sales Invoice" if payload.party_type == "Customer" else "Purchase Invoice"
    )

    touched_invoices: dict[uuid.UUID, SalesInvoice | PurchaseInvoice] = {}
    touched_payments: set[uuid.UUID] = set()
    next_ref_idx: dict[uuid.UUID, int] = {}  # rows added here aren't visible on entry.references

    for alloc in payload.allocations:
        if alloc.invoice_type != expected_invoice_type:
            raise ValidationError(
                f"{payload.party_type} reconciliation must reference {expected_invoice_type}s",
                field="allocations",
            )

        entry = await get_payment_entry(db, alloc.payment_entry_id, user.company_id)
        require_submitted(entry.docstatus)
        if entry.party_type != payload.party_type or entry.party_id != payload.party_id:
            raise ValidationError(
                f"Payment Entry {entry.name} belongs to another party", field="allocations"
            )
        if alloc.allocated_amount > entry.unallocated_amount:
            raise ValidationError(
                f"Allocated {alloc.allocated_amount} exceeds unallocated "
                f"{entry.unallocated_amount} on {entry.name}",
                field="allocations",
            )

        if alloc.invoice_id in touched_invoices:
            invoice = touched_invoices[alloc.invoice_id]
        elif alloc.invoice_type == "Sales Invoice":
            invoice = await get_sales_invoice(db, alloc.invoice_id, user.company_id)
        else:
            invoice = await get_purchase_invoice(db, alloc.invoice_id, user.company_id)
        require_submitted(invoice.docstatus)
        if alloc.allocated_amount > invoice.outstanding_amount:
            raise ValidationError(
                f"Allocated {alloc.allocated_amount} exceeds outstanding "
                f"{invoice.outstanding_amount} on {invoice.name}",
                field="allocations",
            )

        idx = next_ref_idx.setdefault(entry.id, len(entry.references) + 1)
        next_ref_idx[entry.id] = idx + 1
        db.add(
            PaymentEntryReference(
                payment_entry_id=entry.id,
                idx=idx,
                reference_doctype=alloc.invoice_type,
                reference_id=invoice.id,
                reference_name=invoice.name,
                total_amount=invoice.grand_total,
                outstanding_amount=invoice.outstanding_amount,
                allocated_amount=alloc.allocated_amount,
            )
        )
        entry.total_allocated_amount += alloc.allocated_amount
        entry.unallocated_amount -= alloc.allocated_amount
        entry.modified_by = user.id
        invoice.outstanding_amount -= alloc.allocated_amount
        set_invoice_status(invoice)

        # If this payment carried GST on an advance, reverse the tax on the adjusted portion
        # (Sales Invoices only — advances are on the customer/Receive side).
        if alloc.invoice_type == "Sales Invoice":
            await _reverse_advance_gst(db, entry, invoice, alloc.allocated_amount, user)

        touched_invoices[invoice.id] = invoice
        touched_payments.add(entry.id)
        await db.flush()

    for entry_id in touched_payments:
        await log_audit(
            db, doctype="Payment Entry", document_id=entry_id, action="UPDATE",
            user_id=user.id, company_id=user.company_id,
        )
    await db.commit()

    return PaymentReconciliationResponse(
        allocations_applied=len(payload.allocations),
        invoices=[
            ReconciledInvoiceRow(
                invoice_id=i.id,
                name=i.name,
                outstanding_amount=i.outstanding_amount,
                status=i.status,
            )
            for i in touched_invoices.values()
        ],
    )
