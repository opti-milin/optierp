"""Payment Entry service — Module 02.

Source: erpnext/accounts/doctype/payment_entry.

Receive:  Dr bank/cash (paid_to)    Cr receivable (paid_from, party)
Pay:      Dr payable (paid_to, party)  Cr bank/cash (paid_from)
Internal: Dr paid_to                Cr paid_from

Each invoice reference posts its own party-side GL row with
against_voucher so AR/AP aging can attribute settlements. On submission the
spec's outstanding formula is applied to every referenced invoice:
outstanding = grand_total - advance_paid - paid_amount + return_amount —
here as an incremental change of -allocated_amount (and +allocated on cancel).
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import PaymentEntry, PaymentEntryDeduction, PaymentEntryReference
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.schemas.accounts import PaymentEntryCreate
from app.services import gl
from app.services.accounts_common import (
    NAMING_SERIES,
    advance_gst_from_amount,
    get_company,
    get_customer,
    get_payable_account,
    get_receivable_account,
    get_supplier,
    require_draft,
    require_submitted,
    set_invoice_status,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.purchase_invoice import get_purchase_invoice
from app.services.sales_invoice import get_sales_invoice

ZERO = Decimal("0")


async def _resolve_invoice(db: AsyncSession, doctype: str, ref_id: uuid.UUID, company_id: uuid.UUID):
    if doctype == "Sales Invoice":
        return await get_sales_invoice(db, ref_id, company_id)
    return await get_purchase_invoice(db, ref_id, company_id)


async def create_payment_entry(
    db: AsyncSession, payload: PaymentEntryCreate, user: CurrentUser
) -> PaymentEntry:
    company = await get_company(db, user.company_id)

    paid_from_id = payload.paid_from_id
    paid_to_id = payload.paid_to_id

    if payload.payment_type in ("Receive", "Pay"):
        if not (payload.party_type and payload.party_id):
            raise ValidationError("party_type and party_id are required", field="party_id")
        if payload.payment_type == "Receive":
            if payload.party_type != "Customer":
                raise ValidationError("Receive payments must be from a Customer", field="party_type")
            customer = await get_customer(db, payload.party_id, company.id)
            paid_from_id = paid_from_id or get_receivable_account(company, customer)
            if paid_to_id is None:
                raise ValidationError("paid_to_id (bank/cash account) is required", field="paid_to_id")
        else:
            if payload.party_type != "Supplier":
                raise ValidationError("Pay payments must be to a Supplier", field="party_type")
            supplier = await get_supplier(db, payload.party_id, company.id)
            paid_to_id = paid_to_id or get_payable_account(company, supplier)
            if paid_from_id is None:
                raise ValidationError("paid_from_id (bank/cash account) is required", field="paid_from_id")
    else:  # Internal Transfer
        if paid_from_id is None or paid_to_id is None:
            raise ValidationError("paid_from_id and paid_to_id are required for transfers")

    received_amount = payload.received_amount or payload.paid_amount
    base_paid = payload.paid_amount * payload.source_exchange_rate
    base_received = received_amount * payload.target_exchange_rate
    total_deductions = sum((d.amount for d in payload.deductions), ZERO)
    if abs(base_paid - base_received - total_deductions) > Decimal("0.01"):
        raise ValidationError(
            "Unbalanced payment: paid != received + deductions. Use a deduction row "
            "(e.g. Exchange Gain/Loss or Bank Charges) for the difference.",
            field="deductions",
        )

    # validate references against live invoice state
    total_allocated = ZERO
    resolved_refs = []
    for ref in payload.references:
        if payload.payment_type == "Receive" and ref.reference_doctype != "Sales Invoice":
            raise ValidationError("Receive payments can only reference Sales Invoices")
        if payload.payment_type == "Pay" and ref.reference_doctype != "Purchase Invoice":
            raise ValidationError("Pay payments can only reference Purchase Invoices")
        invoice = await _resolve_invoice(db, ref.reference_doctype, ref.reference_id, company.id)
        require_submitted(invoice.docstatus)
        party_field = "customer_id" if ref.reference_doctype == "Sales Invoice" else "supplier_id"
        if getattr(invoice, party_field) != payload.party_id:
            raise ValidationError(f"{ref.reference_doctype} {invoice.name} belongs to another party")
        if ref.allocated_amount > invoice.outstanding_amount:
            raise ValidationError(
                f"Allocated amount {ref.allocated_amount} exceeds outstanding "
                f"{invoice.outstanding_amount} on {invoice.name}",
                field="references",
            )
        total_allocated += ref.allocated_amount
        resolved_refs.append((ref, invoice))

    if total_allocated > base_paid:
        raise ValidationError("Total allocated exceeds the paid amount", field="references")

    # GST on advances received: the unallocated remainder of a Receive is an on-account
    # advance. For a SERVICE advance (goods advances are exempt per Notf. 66/2017), a Regular
    # dealer books inclusive output GST on it — a bare advance has no HSN, so the rate/supply
    # type are explicit. Suppressed for Composition (they issue a Bill of Supply).
    unallocated = base_paid - total_allocated - total_deductions
    adv_gst_amount = ZERO
    adv_gst_rate = None
    adv_inter = False
    adv_supply_type = payload.advance_supply_type
    if payload.payment_type == "Receive" and unallocated > ZERO and payload.advance_gst_rate:
        from app.services.gst_settings import get_gst_settings

        settings = await get_gst_settings(db, company.id)
        supply_type = payload.advance_supply_type or ("Services" if settings.collect_gst_on_advances else None)
        if (
            settings.registration_type != "Composition"
            and settings.collect_gst_on_advances
            and supply_type == "Services"
        ):
            adv = await advance_gst_from_amount(
                db, company=company, party_gstin=customer.tax_id, place_of_supply=None,
                advance_amount=unallocated, rate=payload.advance_gst_rate,
            )
            if adv is not None:
                adv_gst_amount = adv.total
                adv_gst_rate = Decimal(payload.advance_gst_rate)
                adv_inter = adv.inter
                adv_supply_type = "Services"

    name = await get_next_name(db, NAMING_SERIES["Payment Entry"], company.id)
    entry = PaymentEntry(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        posting_date=payload.posting_date,
        payment_type=payload.payment_type,
        party_type=payload.party_type,
        party_id=payload.party_id,
        paid_from_id=paid_from_id,
        paid_to_id=paid_to_id,
        paid_amount=payload.paid_amount,
        received_amount=received_amount,
        source_exchange_rate=payload.source_exchange_rate,
        target_exchange_rate=payload.target_exchange_rate,
        total_allocated_amount=total_allocated,
        unallocated_amount=unallocated,
        advance_supply_type=adv_supply_type,
        advance_gst_rate=adv_gst_rate,
        advance_gst_amount=adv_gst_amount,
        advance_is_inter_state=adv_inter,
        advance_gst_outstanding=adv_gst_amount,
        mode_of_payment_id=payload.mode_of_payment_id,
        reference_no=payload.reference_no,
        reference_date=payload.reference_date,
        remarks=payload.remarks,
        status="Draft",
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()
    for idx, (ref, invoice) in enumerate(resolved_refs, start=1):
        db.add(
            PaymentEntryReference(
                payment_entry_id=entry.id,
                idx=idx,
                reference_doctype=ref.reference_doctype,
                reference_id=ref.reference_id,
                reference_name=invoice.name,
                total_amount=invoice.grand_total,
                outstanding_amount=invoice.outstanding_amount,
                allocated_amount=ref.allocated_amount,
            )
        )
    for idx, ded in enumerate(payload.deductions, start=1):
        db.add(
            PaymentEntryDeduction(
                payment_entry_id=entry.id,
                idx=idx,
                account_id=ded.account_id,
                cost_center_id=ded.cost_center_id,
                amount=ded.amount,
                description=ded.description,
            )
        )
    await db.flush()
    await log_audit(
        db, doctype="Payment Entry", document_id=entry.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_payment_entry(db, entry.id, company.id)


async def get_payment_entry(
    db: AsyncSession, entry_id: uuid.UUID, company_id: uuid.UUID | None
) -> PaymentEntry:
    entry = await db.scalar(
        select(PaymentEntry)
        .options(selectinload(PaymentEntry.references), selectinload(PaymentEntry.deductions))
        .where(PaymentEntry.id == entry_id, PaymentEntry.company_id == company_id)
    )
    if entry is None:
        raise NotFoundError("Payment Entry not found")
    return entry


async def list_payment_entries(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
) -> tuple[list[PaymentEntry], int]:
    stmt = (
        select(PaymentEntry)
        .where(PaymentEntry.company_id == company_id)
        .order_by(PaymentEntry.posting_date.desc(), PaymentEntry.creation.desc())
    )
    return await paginate(db, stmt, page, page_size)


def _party_gl_rows(entry: PaymentEntry) -> list[gl.GLRow]:
    """Party-side rows: one per reference (with against_voucher) + remainder."""
    rows: list[gl.GLRow] = []
    is_receive = entry.payment_type == "Receive"
    party_account_id = entry.paid_from_id if is_receive else entry.paid_to_id
    base_total = entry.paid_amount * entry.source_exchange_rate
    remainder = base_total

    for ref in entry.references:
        amount = ref.allocated_amount
        remainder -= amount
        rows.append(
            gl.GLRow(
                account_id=party_account_id,
                credit=amount if is_receive else ZERO,
                debit=amount if not is_receive else ZERO,
                party_type=entry.party_type,
                party_id=entry.party_id,
                against_voucher_type=ref.reference_doctype,
                against_voucher_id=ref.reference_id,
            )
        )
    total_deductions = sum((d.amount for d in entry.deductions), ZERO)
    remainder -= total_deductions
    if remainder > ZERO:
        rows.append(
            gl.GLRow(
                account_id=party_account_id,
                credit=remainder if is_receive else ZERO,
                debit=remainder if not is_receive else ZERO,
                party_type=entry.party_type,
                party_id=entry.party_id,
            )
        )
    return rows


def _build_gl_rows(entry: PaymentEntry) -> list[gl.GLRow]:
    rows: list[gl.GLRow] = []
    base_received = entry.received_amount * entry.target_exchange_rate
    base_paid = entry.paid_amount * entry.source_exchange_rate

    if entry.payment_type == "Receive":
        rows.extend(_party_gl_rows(entry))  # Cr receivable
        rows.append(gl.GLRow(account_id=entry.paid_to_id, debit=base_received))  # Dr bank
    elif entry.payment_type == "Pay":
        rows.extend(_party_gl_rows(entry))  # Dr payable
        rows.append(gl.GLRow(account_id=entry.paid_from_id, credit=base_paid))  # Cr bank
    else:  # Internal Transfer
        rows.append(gl.GLRow(account_id=entry.paid_to_id, debit=base_received))
        rows.append(gl.GLRow(account_id=entry.paid_from_id, credit=base_paid))

    for ded in entry.deductions:
        rows.append(
            gl.GLRow(
                account_id=ded.account_id,
                debit=ded.amount if ded.amount > ZERO else ZERO,
                credit=-ded.amount if ded.amount < ZERO else ZERO,
                cost_center_id=ded.cost_center_id,
                remarks=ded.description,
            )
        )
    return rows


async def _advance_gst_gl_rows(db: AsyncSession, entry: PaymentEntry) -> list[gl.GLRow]:
    """The GST-on-advance leg booked at receipt: Dr 'GST on Advances' control / Cr Output GST.
    Self-balancing, so the voucher stays balanced (Dr bank still == Cr receivable). Recomputes
    from the entry's unallocated advance (== the amount at submit) so create == submit."""
    if not entry.advance_gst_amount or entry.advance_gst_amount <= ZERO:
        return []
    company = await get_company(db, entry.company_id)
    customer = await get_customer(db, entry.party_id, entry.company_id)
    adv = await advance_gst_from_amount(
        db, company=company, party_gstin=customer.tax_id, place_of_supply=None,
        advance_amount=entry.unallocated_amount, rate=entry.advance_gst_rate,
    )
    if adv is None:
        raise ValidationError(
            "GST on the advance was expected but its accounts (a 'GST on Advances' control "
            "and Output GST) could not be resolved — create them and retry.",
            field="advance_gst_rate",
        )
    rows = [gl.GLRow(account_id=adv.control_id, debit=adv.total, remarks="GST on advance received")]
    rows += [
        gl.GLRow(account_id=acc, credit=amt, remarks="Output GST on advance")
        for acc, amt in adv.output_rows
    ]
    return rows


async def _apply_allocations(
    db: AsyncSession, entry: PaymentEntry, *, direction: int
) -> None:
    """direction +1 on submit (reduce outstanding), -1 on cancel (restore)."""
    for ref in entry.references:
        invoice = await _resolve_invoice(db, ref.reference_doctype, ref.reference_id, entry.company_id)
        invoice.outstanding_amount -= ref.allocated_amount * direction
        set_invoice_status(invoice)


async def submit_payment_entry(
    db: AsyncSession, entry_id: uuid.UUID, user: CurrentUser
) -> PaymentEntry:
    entry = await get_payment_entry(db, entry_id, user.company_id)
    require_draft(entry.docstatus)

    # re-validate allocations against current outstanding (it may have moved)
    for ref in entry.references:
        invoice = await _resolve_invoice(db, ref.reference_doctype, ref.reference_id, entry.company_id)
        if ref.allocated_amount > invoice.outstanding_amount:
            raise ValidationError(
                f"Allocated {ref.allocated_amount} now exceeds outstanding "
                f"{invoice.outstanding_amount} on {invoice.name}"
            )

    rows = _build_gl_rows(entry)
    rows += await _advance_gst_gl_rows(db, entry)
    await gl.make_gl_entries(
        db,
        company_id=entry.company_id,
        voucher_type="Payment Entry",
        voucher_id=entry.id,
        voucher_no=entry.name,
        posting_date=entry.posting_date,
        rows=rows,
        user_id=user.id,
        remarks=entry.remarks,
    )
    await _apply_allocations(db, entry, direction=1)
    entry.docstatus = DOCSTATUS_SUBMITTED
    entry.status = "Submitted"
    entry.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Payment Entry", document_id=entry.id, action="SUBMIT",
        user_id=user.id, company_id=entry.company_id,
    )
    await db.commit()
    return await get_payment_entry(db, entry.id, user.company_id)


async def cancel_payment_entry(
    db: AsyncSession, entry_id: uuid.UUID, user: CurrentUser
) -> PaymentEntry:
    entry = await get_payment_entry(db, entry_id, user.company_id)
    require_submitted(entry.docstatus)
    await gl.make_reverse_gl_entries(
        db, voucher_type="Payment Entry", voucher_id=entry.id, user_id=user.id
    )
    await _apply_allocations(db, entry, direction=-1)
    entry.docstatus = DOCSTATUS_CANCELLED
    entry.status = "Cancelled"
    entry.modified_by = user.id
    await db.flush()
    # A cancelled entry can no longer back a bank reconciliation — free any line
    # matched to it (lazy import avoids a module-level cycle).
    from app.services import bank_reconciliation_tool

    await bank_reconciliation_tool.release_matched_transactions(
        db, "Payment Entry", entry.id, user_id=user.id
    )
    await log_audit(
        db, doctype="Payment Entry", document_id=entry.id, action="CANCEL",
        user_id=user.id, company_id=entry.company_id,
    )
    await db.commit()
    return await get_payment_entry(db, entry.id, user.company_id)


async def set_clearance_date(
    db: AsyncSession, entry_id: uuid.UUID, clearance_date, user: CurrentUser
) -> PaymentEntry:
    """Bank reconciliation: mark when the bank cleared this payment."""
    entry = await get_payment_entry(db, entry_id, user.company_id)
    require_submitted(entry.docstatus)
    entry.clearance_date = clearance_date
    entry.modified_by = user.id
    await db.commit()
    return await get_payment_entry(db, entry.id, user.company_id)
