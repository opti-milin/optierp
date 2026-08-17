"""Voucher importers — Tally transactions become OptiERP documents.

A Tally voucher is a flat list of ledger postings plus (optionally) a list of
inventory movements. Everything else — "is this a sales invoice?", "which side
is the customer?" — is inferred from the voucher type and the ledgers involved.
This module does that inference and hands a normal ``*Create`` payload to the
same service functions the API uses, so an imported invoice posts GL, stock and
GST exactly like a hand-keyed one. No shortcut writes into ``gl_entries``.

Two Tally conventions to keep in mind while reading:

* **Sign.** In ``ALLLEDGERENTRIES.LIST`` a *negative* AMOUNT is a debit and a
  positive one is a credit — the opposite of most people's instinct.
  :func:`_ledger_rows` converts once, up front, into explicit debit/credit.
* **Embedded units.** Quantities read ``"100 Nos"`` and rates read
  ``"250.00/Nos"``. :func:`_split_qty` and :func:`_split_rate` pull them apart.

Tax handling is deliberately literal: Tally's tax ledger amounts are carried
across as ``Actual`` tax rows rather than recomputed from a rate. An import must
reproduce the customer's books to the paisa, not improve on them.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.exceptions import ValidationError
from app.models.accounts import Account, PurchaseInvoice, SalesInvoice
from app.models.tally import TallyStagingRecord
from app.schemas.accounts import (
    JournalEntryAccountIn,
    JournalEntryCreate,
    PaymentEntryCreate,
    PaymentReferenceIn,
    PurchaseInvoiceCreate,
    SalesInvoiceCreate,
)
from app.schemas.accounts.common import InvoiceItemIn, TaxRowIn
from app.schemas.buying import OrderItemIn, PurchaseOrderCreate
from app.schemas.selling import SalesOrderCreate
from app.schemas.stock import (
    DeliveryNoteCreate,
    DeliveryNoteItemIn,
    PurchaseReceiptCreate,
    PurchaseReceiptItemIn,
    StockEntryCreate,
    StockEntryItemIn,
    StockReconciliationCreate,
    StockReconciliationItemIn,
)
from app.services import (
    delivery_note as delivery_note_service,
    journal_entry as journal_entry_service,
    payment_entry as payment_entry_service,
    purchase_invoice as purchase_invoice_service,
    purchase_order as purchase_order_service,
    purchase_receipt as purchase_receipt_service,
    sales_invoice as sales_invoice_service,
    sales_order as sales_order_service,
    stock_entry as stock_entry_service,
    stock_reconciliation as stock_reconciliation_service,
)
from app.services.tally.catalogue import DUTY_HEADS, VOUCHER_TYPES, normalise_unit
from app.services.tally.context import ImportContext
from app.services.tally.parser import as_list, parse_amount, parse_bool, text_of

ZERO = Decimal("0")
_QTY = re.compile(r"^\s*(-?[\d,]*\.?\d+)\s*(.*)$")


# --------------------------------------------------------------------------------------
# Normalised voucher shapes
# --------------------------------------------------------------------------------------


@dataclass
class LedgerRow:
    """One accounting posting on a Tally voucher, with the sign already resolved."""

    ledger: str
    debit: Decimal
    credit: Decimal
    is_party: bool = False
    cost_centre: str | None = None
    #: bill-wise allocations: [{"ref": "INV-001", "type": "Agst Ref", "amount": Decimal}]
    bills: list[dict[str, Any]] = field(default_factory=list)

    @property
    def amount(self) -> Decimal:
        return self.debit if self.debit else self.credit


@dataclass
class InventoryRow:
    """One stock movement on a Tally voucher."""

    item: str
    qty: Decimal
    uom: str | None
    rate: Decimal
    amount: Decimal
    godown: str | None = None
    batch: str | None = None
    ledger: str | None = None  # the income/expense ledger this line posts to


def _split_qty(value: str | None) -> tuple[Decimal, str | None]:
    """``"100 Nos"`` -> ``(Decimal("100"), "Nos")``."""
    if not value:
        return ZERO, None
    match = _QTY.match(str(value))
    if not match:
        return parse_amount(value), None
    number, unit = match.groups()
    return parse_amount(number), normalise_unit(unit.strip()) if unit.strip() else None


def _split_rate(value: str | None) -> Decimal:
    """``"250.00/Nos"`` -> ``Decimal("250.00")``."""
    if not value:
        return ZERO
    return parse_amount(str(value).split("/")[0])


def _bill_allocations(node: dict[str, Any]) -> list[dict[str, Any]]:
    bills = []
    for bill in as_list(node.get("BILLALLOCATIONS.LIST")):
        if not isinstance(bill, dict):
            continue
        ref = text_of(bill, "NAME")
        if not ref:
            continue
        bills.append(
            {
                "ref": ref.strip(),
                "type": text_of(bill, "BILLTYPE") or "New Ref",
                "amount": abs(parse_amount(text_of(bill, "AMOUNT"))),
            }
        )
    return bills


def _ledger_rows(data: dict[str, Any]) -> list[LedgerRow]:
    """Flatten every ledger posting on a voucher into explicit debit/credit."""
    rows: list[LedgerRow] = []
    for key in (
        "ALLLEDGERENTRIES.LIST",
        "LEDGERENTRIES.LIST",  # older Tally releases
    ):
        for node in as_list(data.get(key)):
            if not isinstance(node, dict):
                continue
            ledger = text_of(node, "LEDGERNAME")
            if not ledger:
                continue
            amount = parse_amount(text_of(node, "AMOUNT"))
            cost_centre = None
            for allocation in as_list(node.get("CATEGORYALLOCATIONS.LIST")):
                if isinstance(allocation, dict):
                    for centre in as_list(allocation.get("COSTCENTREALLOCATIONS.LIST")):
                        if isinstance(centre, dict):
                            cost_centre = text_of(centre, "NAME") or cost_centre
            rows.append(
                LedgerRow(
                    ledger=ledger.strip(),
                    # negative = debit in Tally's ledger entries
                    debit=abs(amount) if amount < ZERO else ZERO,
                    credit=amount if amount > ZERO else ZERO,
                    is_party=parse_bool(text_of(node, "ISPARTYLEDGER")),
                    cost_centre=cost_centre,
                    bills=_bill_allocations(node),
                )
            )
    return rows


def _inventory_rows(data: dict[str, Any], *, key: str = "ALLINVENTORYENTRIES.LIST") -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for node in as_list(data.get(key)):
        if not isinstance(node, dict):
            continue
        item = text_of(node, "STOCKITEMNAME")
        if not item:
            continue
        qty, uom = _split_qty(text_of(node, "BILLEDQTY", "ACTUALQTY", "QUANTITY"))
        godown = None
        batch = None
        for allocation in as_list(node.get("BATCHALLOCATIONS.LIST")):
            if isinstance(allocation, dict):
                godown = text_of(allocation, "GODOWNNAME") or godown
                batch_name = text_of(allocation, "BATCHNAME")
                if batch_name and batch_name.strip().casefold() != "primary batch":
                    batch = batch_name.strip()
        ledger = None
        for allocation in as_list(node.get("ACCOUNTINGALLOCATIONS.LIST")):
            if isinstance(allocation, dict):
                ledger = text_of(allocation, "LEDGERNAME") or ledger
        rows.append(
            InventoryRow(
                item=item.strip(),
                qty=abs(qty),
                uom=uom,
                rate=_split_rate(text_of(node, "RATE")),
                amount=abs(parse_amount(text_of(node, "AMOUNT"))),
                godown=godown,
                batch=batch,
                ledger=ledger,
            )
        )
    return rows


def _raw(record: TallyStagingRecord) -> dict[str, Any]:
    data = (record.raw or {}).get("data")
    return data if isinstance(data, dict) else {}


def _posting_date(context: ImportContext, record: TallyStagingRecord) -> date:
    if record.posting_date:
        return record.posting_date
    raise ValidationError("The voucher has no date")


def _remarks(record: TallyStagingRecord) -> str:
    """Narration plus a breadcrumb back to the Tally voucher."""
    narration = (record.raw or {}).get("narration") or ""
    tag = f"[Tally {record.tally_voucher_type} {record.voucher_number or ''}]".strip()
    return f"{narration} {tag}".strip()[:500]


async def _account_type(context: ImportContext, account_id: uuid.UUID | None) -> str | None:
    if account_id is None:
        return None
    account = await context.db.get(Account, account_id)
    return account.account_type if account else None


def _is_tax_ledger(ledger: str) -> bool:
    """Does this ledger name look like a GST/duty head?"""
    folded = ledger.casefold()
    return any(head.casefold() in folded for head in DUTY_HEADS)


# --------------------------------------------------------------------------------------
# Journal / Reversing Journal -> Journal Entry
# --------------------------------------------------------------------------------------


async def import_journals(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    for record in records:
        async with context.row(record):
            if _skip_cancelled(context, record):
                continue
            data = _raw(record)
            rows = _ledger_rows(data)
            if len(rows) < 2:
                raise ValidationError(
                    "A Tally journal with fewer than two ledger postings cannot "
                    "become a Journal Entry"
                )

            accounts: list[JournalEntryAccountIn] = []
            for row in rows:
                account_id = context.book.account(row.ledger)
                if account_id is None:
                    raise ValidationError(f"Ledger '{row.ledger}' is not mapped to an account")
                party_type = context.book.party_type_of(row.ledger)
                party_id = (
                    context.book.customer(row.ledger)
                    if party_type == "Customer"
                    else context.book.supplier(row.ledger)
                    if party_type == "Supplier"
                    else None
                )
                accounts.append(
                    JournalEntryAccountIn(
                        account_id=account_id,
                        debit=row.debit,
                        credit=row.credit,
                        party_type=party_type,
                        party_id=party_id,
                        cost_center_id=context.book.cost_center(row.cost_centre),
                        user_remark=row.ledger[:180],
                    )
                )

            payload = JournalEntryCreate(
                posting_date=_posting_date(context, record),
                voucher_type="Journal Entry",
                remarks=_remarks(record),
                accounts=accounts,
            )
            entry = await journal_entry_service.create_journal_entry(
                context.db, payload, context.user
            )
            if context.option("submit_vouchers", True):
                entry = await journal_entry_service.submit_journal_entry(
                    context.db, entry.id, context.user
                )
            context.created(record, "Journal Entry", entry.id, entry.name)


# --------------------------------------------------------------------------------------
# Sales / Purchase / Credit Note / Debit Note -> Invoices
# --------------------------------------------------------------------------------------


def _invoice_lines(
    context: ImportContext, record: TallyStagingRecord, rows: list[LedgerRow], inventory: list[InventoryRow]
) -> list[InvoiceItemIn]:
    """Item lines from Tally's inventory entries, or a service line from the ledger."""
    lines: list[InvoiceItemIn] = []
    for row in inventory:
        item_id = context.book.item(row.item)
        if item_id is None:
            context.warn(
                f"Stock item '{row.item}' is not mapped; the line was kept as free text.",
                "items",
            )
        qty = row.qty if row.qty > ZERO else Decimal("1")
        rate = row.rate if row.rate > ZERO else (row.amount / qty if qty else ZERO)
        lines.append(
            InvoiceItemIn(
                item_id=item_id,
                item_code=row.item[:140] if item_id else None,
                item_name=row.item[:140],
                qty=qty,
                uom=row.uom,
                rate=rate,
                account_id=context.book.account(row.ledger) if row.ledger else None,
            )
        )
    if lines:
        return lines

    # No inventory: an accounting-only invoice (services, expenses). Each
    # non-party, non-tax ledger becomes one line at its own amount.
    for row in rows:
        if row.is_party or _is_tax_ledger(row.ledger):
            continue
        amount = row.amount
        if amount <= ZERO:
            continue
        lines.append(
            InvoiceItemIn(
                item_name=row.ledger[:140],
                qty=Decimal("1"),
                rate=amount,
                account_id=context.book.account(row.ledger),
                cost_center_id=context.book.cost_center(row.cost_centre),
            )
        )
    if not lines:
        raise ValidationError("The voucher has no item or income/expense lines to import")
    context.info("Accounting-only voucher: each ledger became a single service line.")
    return lines


def _invoice_taxes(context: ImportContext, rows: list[LedgerRow]) -> list[TaxRowIn]:
    """Tally's tax ledger amounts, carried across verbatim as Actual tax rows."""
    taxes: list[TaxRowIn] = []
    for row in rows:
        if row.is_party or not _is_tax_ledger(row.ledger):
            continue
        account_id = context.book.account(row.ledger)
        if account_id is None:
            context.warn(f"Tax ledger '{row.ledger}' is not mapped; its amount was dropped.", "taxes")
            continue
        amount = row.amount
        if amount == ZERO:
            continue
        taxes.append(
            TaxRowIn(
                charge_type="Actual",
                tax_amount=amount,
                account_head_id=account_id,
                description=row.ledger[:180],
                add_deduct_tax="Deduct" if row.debit and row.credit == ZERO else "Add",
            )
        )
    return taxes


def _party_ledger(record: TallyStagingRecord, rows: list[LedgerRow]) -> str | None:
    explicit = (record.raw or {}).get("party")
    if explicit:
        return str(explicit).strip()
    party_row = next((r for r in rows if r.is_party), None)
    return party_row.ledger if party_row else None


async def import_invoices(
    context: ImportContext, records: list[TallyStagingRecord], *, kind: str
) -> None:
    """``kind`` is ``"Sales"`` or ``"Purchase"``; returns are driven by the voucher spec."""
    for record in records:
        async with context.row(record):
            if _skip_cancelled(context, record):
                continue
            spec = VOUCHER_TYPES.get(record.tally_voucher_type or "")
            is_return = bool(spec and spec.is_return)
            data = _raw(record)
            rows = _ledger_rows(data)
            inventory = _inventory_rows(data)

            party_name = _party_ledger(record, rows)
            if not party_name:
                raise ValidationError("The voucher names no party ledger")

            lines = _invoice_lines(context, record, rows, inventory)
            taxes = _invoice_taxes(context, rows)
            posting_date = _posting_date(context, record)
            reference = record.voucher_number
            against = None

            if kind == "Sales":
                customer_id = context.book.customer(party_name)
                if customer_id is None:
                    raise ValidationError(
                        f"'{party_name}' is not mapped to a Customer. Map it on the "
                        "Mappings tab, or include Sundry Debtors in the import."
                    )
                if is_return:
                    # ERPNext requires a credit note to point at the invoice it
                    # reverses; Tally does not always say which one.
                    against = await _find_return_against(
                        context, SalesInvoice, customer_id=customer_id, hint=reference
                    )
                    if against is None:
                        context.warn(
                            "No original invoice matched this credit note; it was "
                            "imported as a standalone negative Journal Entry instead.",
                            "return_against_id",
                        )
                        await _credit_note_as_journal(context, record, rows)
                        continue
                payload = SalesInvoiceCreate(
                    customer_id=customer_id,
                    posting_date=posting_date,
                    remarks=_remarks(record),
                    items=lines,
                    taxes=taxes,
                    is_return=is_return,
                    return_against_id=against.id if against else None,
                    po_no=text_of(data, "BASICPURCHASEORDERNO", "REFERENCE"),
                )
                invoice = await sales_invoice_service.create_sales_invoice(
                    context.db, payload, context.user
                )
                if context.option("submit_vouchers", True):
                    invoice = await sales_invoice_service.submit_sales_invoice(
                        context.db, invoice.id, context.user
                    )
                doctype = "Sales Invoice"
            else:
                supplier_id = context.book.supplier(party_name)
                if supplier_id is None:
                    raise ValidationError(
                        f"'{party_name}' is not mapped to a Supplier. Map it on the "
                        "Mappings tab, or include Sundry Creditors in the import."
                    )
                against = None
                if is_return:
                    against = await _find_return_against(
                        context, PurchaseInvoice, supplier_id=supplier_id, hint=reference
                    )
                    if against is None:
                        context.warn(
                            "No original bill matched this debit note; it was imported "
                            "as a standalone Journal Entry instead.",
                            "return_against_id",
                        )
                        await _credit_note_as_journal(context, record, rows)
                        continue
                payload = PurchaseInvoiceCreate(
                    supplier_id=supplier_id,
                    posting_date=posting_date,
                    remarks=_remarks(record),
                    items=lines,
                    taxes=taxes,
                    is_return=is_return,
                    return_against_id=against.id if against else None,
                    bill_no=text_of(data, "SUPPLIERINVOICENO", "REFERENCE") or reference,
                    bill_date=_reference_date(data) or posting_date,
                )
                invoice = await purchase_invoice_service.create_purchase_invoice(
                    context.db, payload, context.user
                )
                if context.option("submit_vouchers", True):
                    invoice = await purchase_invoice_service.submit_purchase_invoice(
                        context.db, invoice.id, context.user
                    )
                doctype = "Purchase Invoice"

            _remember_bill(context, record, rows, party_name, invoice.id, doctype)
            context.created(record, doctype, invoice.id, invoice.name)


def _reference_date(data: dict[str, Any]) -> date | None:
    from app.services.tally.parser import parse_tally_date

    return parse_tally_date(text_of(data, "REFERENCEDATE", "BASICDATE"))


async def _find_return_against(
    context: ImportContext,
    model: type,
    *,
    customer_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    hint: str | None = None,
) -> Any:
    """The invoice a Tally credit/debit note reverses.

    Tally records the original bill in the note's bill allocations ("Agst Ref").
    We look that reference up in this run's bill index first, then fall back to
    the party's most recent submitted invoice.
    """
    party_id = customer_id or supplier_id
    if hint:
        indexed = context.bill_index.get(_bill_key(party_id, hint))
        if indexed is not None:
            found = await context.db.get(model, indexed)
            if found is not None:
                return found
    party_field = "customer_id" if customer_id else "supplier_id"
    stmt = (
        select(model)
        .where(
            model.company_id == context.company_id,
            getattr(model, party_field) == party_id,
            model.docstatus == 1,
            model.is_return.is_(False),
        )
        .order_by(model.posting_date.desc())
        .limit(1)
    )
    return (await context.db.execute(stmt)).scalars().first()


def _bill_key(party_id: uuid.UUID | None, reference: str) -> str:
    return f"{party_id}:{reference.strip().casefold()}"


def _remember_bill(
    context: ImportContext,
    record: TallyStagingRecord,
    rows: list[LedgerRow],
    party_name: str,
    invoice_id: uuid.UUID,
    doctype: str,
) -> None:
    """Index this invoice under every Tally bill reference that names it.

    Payments arrive later and allocate against bill names ("INV-001"), so this
    index is how a Tally receipt finds the invoice it settles.
    """
    party_id = (
        context.book.customer(party_name)
        if doctype == "Sales Invoice"
        else context.book.supplier(party_name)
    )
    references = {record.voucher_number} if record.voucher_number else set()
    for row in rows:
        if not row.is_party:
            continue
        references.update(bill["ref"] for bill in row.bills if bill.get("ref"))
    for reference in references:
        if reference:
            context.bill_index[_bill_key(party_id, reference)] = invoice_id
            context.bill_doctype[_bill_key(party_id, reference)] = doctype


async def _credit_note_as_journal(
    context: ImportContext, record: TallyStagingRecord, rows: list[LedgerRow]
) -> None:
    """Fallback for a return with no traceable original: post the ledger effect."""
    accounts = []
    for row in rows:
        account_id = context.book.account(row.ledger)
        if account_id is None:
            raise ValidationError(f"Ledger '{row.ledger}' is not mapped to an account")
        party_type = context.book.party_type_of(row.ledger)
        accounts.append(
            JournalEntryAccountIn(
                account_id=account_id,
                debit=row.debit,
                credit=row.credit,
                party_type=party_type,
                party_id=(
                    context.book.customer(row.ledger)
                    if party_type == "Customer"
                    else context.book.supplier(row.ledger)
                    if party_type == "Supplier"
                    else None
                ),
                cost_center_id=context.book.cost_center(row.cost_centre),
                user_remark=row.ledger[:180],
            )
        )
    if len(accounts) < 2:
        raise ValidationError("The note has too few postings to become a Journal Entry")
    entry = await journal_entry_service.create_journal_entry(
        context.db,
        JournalEntryCreate(
            posting_date=_posting_date(context, record),
            remarks=_remarks(record),
            accounts=accounts,
        ),
        context.user,
    )
    if context.option("submit_vouchers", True):
        entry = await journal_entry_service.submit_journal_entry(
            context.db, entry.id, context.user
        )
    context.created(record, "Journal Entry", entry.id, entry.name)


# --------------------------------------------------------------------------------------
# Receipt / Payment / Contra -> Payment Entry
# --------------------------------------------------------------------------------------


async def import_payments(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    for record in records:
        async with context.row(record):
            if _skip_cancelled(context, record):
                continue
            spec = VOUCHER_TYPES.get(record.tally_voucher_type or "")
            payment_type = (spec.payment_type if spec else None) or "Receive"
            data = _raw(record)
            rows = _ledger_rows(data)
            if not rows:
                raise ValidationError("The voucher has no ledger postings")

            posting_date = _posting_date(context, record)
            remarks = _remarks(record)

            if payment_type == "Internal Transfer":
                await _import_contra(context, record, rows, posting_date, remarks)
                continue

            party_name = _party_ledger(record, rows)
            party_row = next(
                (r for r in rows if r.ledger == party_name), None
            ) or next((r for r in rows if r.is_party), None)
            if party_row is None:
                # A payment with no party ledger (e.g. direct expense) is a journal.
                context.warn(
                    "No party ledger on this payment; imported as a Journal Entry.",
                    "party",
                )
                await _credit_note_as_journal(context, record, rows)
                continue

            bank_row = next((r for r in rows if r is not party_row), None)
            if bank_row is None:
                raise ValidationError("The payment has no bank or cash side")
            bank_account_id = context.book.account(bank_row.ledger)
            if bank_account_id is None:
                raise ValidationError(f"Ledger '{bank_row.ledger}' is not mapped to an account")

            amount = party_row.amount or bank_row.amount
            if amount <= ZERO:
                raise ValidationError("The payment amount is zero")

            if payment_type == "Receive":
                party_id = context.book.customer(party_row.ledger)
                if party_id is None:
                    raise ValidationError(
                        f"'{party_row.ledger}' is not mapped to a Customer"
                    )
                party_type, paid_to_id, paid_from_id = "Customer", bank_account_id, None
            else:
                party_id = context.book.supplier(party_row.ledger)
                if party_id is None:
                    raise ValidationError(
                        f"'{party_row.ledger}' is not mapped to a Supplier"
                    )
                party_type, paid_from_id, paid_to_id = "Supplier", bank_account_id, None

            references = await _payment_references(
                context, party_row, party_id, payment_type, amount
            )
            payload = PaymentEntryCreate(
                posting_date=posting_date,
                payment_type=payment_type,
                party_type=party_type,
                party_id=party_id,
                paid_from_id=paid_from_id,
                paid_to_id=paid_to_id,
                paid_amount=amount,
                reference_no=record.voucher_number,
                reference_date=posting_date,
                remarks=remarks,
                references=references,
            )
            payment = await payment_entry_service.create_payment_entry(
                context.db, payload, context.user
            )
            if context.option("submit_vouchers", True):
                payment = await payment_entry_service.submit_payment_entry(
                    context.db, payment.id, context.user
                )
            context.created(record, "Payment Entry", payment.id, payment.name)


async def _payment_references(
    context: ImportContext,
    party_row: LedgerRow,
    party_id: uuid.UUID,
    payment_type: str,
    amount: Decimal,
) -> list[PaymentReferenceIn]:
    """Turn Tally's "Agst Ref" bill allocations into invoice references.

    An allocation we cannot resolve is reported and left unallocated — the
    payment still imports, the money just sits on account, which is exactly what
    Tally's "On Account" means anyway.
    """
    doctype = "Sales Invoice" if payment_type == "Receive" else "Purchase Invoice"
    model = SalesInvoice if payment_type == "Receive" else PurchaseInvoice
    references: list[PaymentReferenceIn] = []
    allocated = ZERO

    for bill in party_row.bills:
        if bill["type"].casefold().startswith("new"):
            continue  # "New Ref" opens a bill, it does not settle one
        key = _bill_key(party_id, bill["ref"])
        invoice_id = context.bill_index.get(key)
        if invoice_id is None:
            context.warn(
                f"Bill reference '{bill['ref']}' was not found among the imported "
                "invoices; that part of the payment is unallocated.",
                "references",
            )
            continue
        invoice = await context.db.get(model, invoice_id)
        if invoice is None or invoice.docstatus != 1:
            continue
        allocatable = min(bill["amount"], invoice.outstanding_amount, amount - allocated)
        if allocatable <= ZERO:
            continue
        references.append(
            PaymentReferenceIn(
                reference_doctype=doctype,
                reference_id=invoice_id,
                allocated_amount=allocatable,
            )
        )
        allocated += allocatable
    return references


async def _import_contra(
    context: ImportContext,
    record: TallyStagingRecord,
    rows: list[LedgerRow],
    posting_date: date,
    remarks: str,
) -> None:
    """Contra (bank<->cash, bank<->bank) -> an Internal Transfer Payment Entry."""
    debit_row = next((r for r in rows if r.debit > ZERO), None)
    credit_row = next((r for r in rows if r.credit > ZERO), None)
    if debit_row is None or credit_row is None:
        raise ValidationError("A contra voucher needs one debit and one credit side")
    paid_to_id = context.book.account(debit_row.ledger)
    paid_from_id = context.book.account(credit_row.ledger)
    if paid_to_id is None or paid_from_id is None:
        raise ValidationError("Both contra ledgers must be mapped to accounts")

    payload = PaymentEntryCreate(
        posting_date=posting_date,
        payment_type="Internal Transfer",
        paid_from_id=paid_from_id,
        paid_to_id=paid_to_id,
        paid_amount=debit_row.debit,
        reference_no=record.voucher_number,
        reference_date=posting_date,
        remarks=remarks,
    )
    payment = await payment_entry_service.create_payment_entry(context.db, payload, context.user)
    if context.option("submit_vouchers", True):
        payment = await payment_entry_service.submit_payment_entry(
            context.db, payment.id, context.user
        )
    context.created(record, "Payment Entry", payment.id, payment.name)


# --------------------------------------------------------------------------------------
# Delivery Note / Receipt Note -> Delivery Note / Purchase Receipt
# --------------------------------------------------------------------------------------


async def import_stock_notes(
    context: ImportContext, records: list[TallyStagingRecord], *, kind: str
) -> None:
    """``kind`` is ``"Delivery"`` (outward) or ``"Receipt"`` (inward)."""
    for record in records:
        async with context.row(record):
            if _skip_cancelled(context, record):
                continue
            spec = VOUCHER_TYPES.get(record.tally_voucher_type or "")
            is_return = bool(spec and spec.is_return)
            data = _raw(record)
            inventory = _inventory_rows(data)
            if not inventory:
                raise ValidationError("The note has no stock lines")

            party_name = _party_ledger(record, _ledger_rows(data))
            posting_date = _posting_date(context, record)
            default_warehouse = context.option("default_warehouse_id")

            if kind == "Delivery":
                customer_id = context.book.customer(party_name)
                if customer_id is None:
                    raise ValidationError(f"'{party_name}' is not mapped to a Customer")
                items = []
                for row in inventory:
                    item_id = context.book.item(row.item)
                    if item_id is None:
                        raise ValidationError(f"Stock item '{row.item}' is not mapped")
                    items.append(
                        DeliveryNoteItemIn(
                            item_id=item_id,
                            qty=row.qty or Decimal("1"),
                            rate=row.rate,
                            uom=row.uom,
                            warehouse_id=context.book.warehouse(row.godown),
                            batch_no=row.batch,
                        )
                    )
                if is_return:
                    context.warn(
                        "Rejections In was imported as a plain inward movement; it is "
                        "not linked to the original delivery.",
                        "return_against_id",
                    )
                payload = DeliveryNoteCreate(
                    customer_id=customer_id,
                    posting_date=posting_date,
                    set_warehouse_id=(
                        uuid.UUID(default_warehouse) if default_warehouse else None
                    ),
                    remarks=_remarks(record),
                    items=items,
                )
                note = await delivery_note_service.create_delivery_note(
                    context.db, payload, context.user
                )
                if context.option("submit_vouchers", True):
                    note = await delivery_note_service.submit_delivery_note(
                        context.db, note.id, context.user
                    )
                context.created(record, "Delivery Note", note.id, note.name)
            else:
                supplier_id = context.book.supplier(party_name)
                if supplier_id is None:
                    raise ValidationError(f"'{party_name}' is not mapped to a Supplier")
                items = []
                for row in inventory:
                    item_id = context.book.item(row.item)
                    if item_id is None:
                        raise ValidationError(f"Stock item '{row.item}' is not mapped")
                    items.append(
                        PurchaseReceiptItemIn(
                            item_id=item_id,
                            qty=row.qty or Decimal("1"),
                            rate=row.rate,
                            uom=row.uom,
                            warehouse_id=context.book.warehouse(row.godown),
                            batch_no=row.batch,
                        )
                    )
                payload = PurchaseReceiptCreate(
                    supplier_id=supplier_id,
                    posting_date=posting_date,
                    set_warehouse_id=(
                        uuid.UUID(default_warehouse) if default_warehouse else None
                    ),
                    supplier_delivery_note=record.voucher_number,
                    remarks=_remarks(record),
                    items=items,
                )
                receipt = await purchase_receipt_service.create_purchase_receipt(
                    context.db, payload, context.user
                )
                if context.option("submit_vouchers", True):
                    receipt = await purchase_receipt_service.submit_purchase_receipt(
                        context.db, receipt.id, context.user
                    )
                context.created(record, "Purchase Receipt", receipt.id, receipt.name)


# --------------------------------------------------------------------------------------
# Sales Order / Purchase Order
# --------------------------------------------------------------------------------------


async def import_orders(
    context: ImportContext, records: list[TallyStagingRecord], *, kind: str
) -> None:
    for record in records:
        async with context.row(record):
            if _skip_cancelled(context, record):
                continue
            data = _raw(record)
            inventory = _inventory_rows(data)
            if not inventory:
                raise ValidationError("The order has no item lines")
            party_name = _party_ledger(record, _ledger_rows(data))
            posting_date = _posting_date(context, record)
            due = posting_date + timedelta(days=int(context.option("order_lead_days", 7)))

            items = [
                OrderItemIn(
                    item_id=context.book.item(row.item),
                    item_name=row.item[:140],
                    qty=row.qty or Decimal("1"),
                    rate=row.rate,
                    uom=row.uom,
                    warehouse_id=context.book.warehouse(row.godown),
                    delivery_date=due if kind == "Sales" else None,
                    schedule_date=due if kind == "Purchase" else None,
                )
                for row in inventory
            ]

            if kind == "Sales":
                customer_id = context.book.customer(party_name)
                if customer_id is None:
                    raise ValidationError(f"'{party_name}' is not mapped to a Customer")
                order = await sales_order_service.create_sales_order(
                    context.db,
                    SalesOrderCreate(
                        customer_id=customer_id,
                        posting_date=posting_date,
                        delivery_date=due,
                        remarks=_remarks(record),
                        items=items,
                    ),
                    context.user,
                )
                if context.option("submit_vouchers", True):
                    order = await sales_order_service.submit_sales_order(
                        context.db, order.id, context.user
                    )
                context.created(record, "Sales Order", order.id, order.name)
            else:
                supplier_id = context.book.supplier(party_name)
                if supplier_id is None:
                    raise ValidationError(f"'{party_name}' is not mapped to a Supplier")
                order = await purchase_order_service.create_purchase_order(
                    context.db,
                    PurchaseOrderCreate(
                        supplier_id=supplier_id,
                        posting_date=posting_date,
                        schedule_date=due,
                        remarks=_remarks(record),
                        items=items,
                    ),
                    context.user,
                )
                if context.option("submit_vouchers", True):
                    order = await purchase_order_service.submit_purchase_order(
                        context.db, order.id, context.user
                    )
                context.created(record, "Purchase Order", order.id, order.name)


# --------------------------------------------------------------------------------------
# Stock Journal / Manufacturing Journal / Physical Stock
# --------------------------------------------------------------------------------------


async def import_stock_journals(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    """Stock Journal -> Stock Entry; the purpose follows which sides are present."""
    for record in records:
        async with context.row(record):
            if _skip_cancelled(context, record):
                continue
            data = _raw(record)
            # Tally splits a stock journal into explicit source/destination lists.
            # Older exports (and simple issues) put everything in the plain list.
            sources = _inventory_rows(data, key="SOURCEALLINVENTORYENTRIES.LIST")
            destinations = _inventory_rows(data, key="DESTINATIONALLINVENTORYENTRIES.LIST")
            if not sources and not destinations:
                sources = _inventory_rows(data, key="ALLINVENTORYENTRIES.LIST")
            if not sources and not destinations:
                raise ValidationError("The stock journal has no inventory lines")

            if sources and destinations:
                purpose = "Material Transfer"
            elif destinations:
                purpose = "Material Receipt"
            else:
                purpose = "Material Issue"

            spec = VOUCHER_TYPES.get(record.tally_voucher_type or "")
            if spec and spec.stock_purpose == "Repack" and sources and destinations:
                purpose = "Repack"

            items: list[StockEntryItemIn] = []
            for row in sources:
                item_id = context.book.item(row.item)
                if item_id is None:
                    raise ValidationError(f"Stock item '{row.item}' is not mapped")
                items.append(
                    StockEntryItemIn(
                        item_id=item_id,
                        qty=row.qty or Decimal("1"),
                        basic_rate=row.rate,
                        uom=row.uom,
                        batch_no=row.batch,
                        source_warehouse_id=context.book.warehouse(row.godown),
                        is_finished_item=False,
                    )
                )
            for row in destinations:
                item_id = context.book.item(row.item)
                if item_id is None:
                    raise ValidationError(f"Stock item '{row.item}' is not mapped")
                items.append(
                    StockEntryItemIn(
                        item_id=item_id,
                        qty=row.qty or Decimal("1"),
                        basic_rate=row.rate,
                        uom=row.uom,
                        batch_no=row.batch,
                        target_warehouse_id=context.book.warehouse(row.godown),
                        is_finished_item=purpose == "Repack",
                    )
                )

            entry = await stock_entry_service.create_stock_entry(
                context.db,
                StockEntryCreate(
                    purpose=purpose,
                    posting_date=_posting_date(context, record),
                    remarks=_remarks(record),
                    items=items,
                ),
                context.user,
            )
            if context.option("submit_vouchers", True):
                entry = await stock_entry_service.submit_stock_entry(
                    context.db, entry.id, context.user
                )
            context.created(record, "Stock Entry", entry.id, entry.name)


async def import_physical_stock(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    """Physical Stock -> Stock Reconciliation (absolute counted quantities)."""
    for record in records:
        async with context.row(record):
            if _skip_cancelled(context, record):
                continue
            data = _raw(record)
            inventory = _inventory_rows(data)
            if not inventory:
                raise ValidationError("The physical stock voucher has no lines")

            items = []
            for row in inventory:
                item_id = context.book.item(row.item)
                if item_id is None:
                    raise ValidationError(f"Stock item '{row.item}' is not mapped")
                items.append(
                    StockReconciliationItemIn(
                        item_id=item_id,
                        warehouse_id=context.book.warehouse(row.godown),
                        qty=row.qty,
                        valuation_rate=row.rate if row.rate > ZERO else None,
                        uom=row.uom,
                    )
                )
            reconciliation = await stock_reconciliation_service.create_stock_reconciliation(
                context.db,
                StockReconciliationCreate(
                    purpose="Stock Reconciliation",
                    posting_date=_posting_date(context, record),
                    remarks=_remarks(record),
                    items=items,
                ),
                context.user,
            )
            if context.option("submit_vouchers", True):
                reconciliation = await stock_reconciliation_service.submit_stock_reconciliation(
                    context.db, reconciliation.id, context.user
                )
            context.created(
                record, "Stock Reconciliation", reconciliation.id, reconciliation.name
            )


# --------------------------------------------------------------------------------------
# Recognised but not importable
# --------------------------------------------------------------------------------------


async def skip_entity(
    context: ImportContext, records: list[TallyStagingRecord], reason: str
) -> None:
    """Stage-and-report rows we deliberately do not turn into documents."""
    for record in records:
        async with context.row(record):
            context.skip(record, reason)


def _skip_cancelled(context: ImportContext, record: TallyStagingRecord) -> bool:
    """Tally keeps cancelled and 'optional' vouchers in the file; we must not post them."""
    raw = record.raw or {}
    if raw.get("is_cancelled"):
        context.skip(record, "Cancelled in Tally — not imported.")
        return True
    if raw.get("is_optional"):
        context.skip(record, "Marked optional in Tally (no ledger effect) — not imported.")
        return True
    return False
