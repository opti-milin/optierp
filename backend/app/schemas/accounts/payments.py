"""Accounts schemas — Payment Entry + reconciliation + bank reconciliation."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel

class PaymentReferenceIn(BaseModel):
    reference_doctype: str = Field(pattern="^(Sales Invoice|Purchase Invoice)$")
    reference_id: uuid.UUID
    allocated_amount: Decimal = Field(gt=0)


class PaymentDeductionIn(BaseModel):
    account_id: uuid.UUID
    cost_center_id: uuid.UUID | None = None
    amount: Decimal
    description: str | None = None


class PaymentEntryCreate(BaseModel):
    posting_date: date
    payment_type: str = Field(pattern="^(Receive|Pay|Internal Transfer)$")
    party_type: str | None = None
    party_id: uuid.UUID | None = None
    paid_from_id: uuid.UUID | None = None  # defaults to party account for Receive
    paid_to_id: uuid.UUID | None = None  # defaults to party account for Pay
    paid_amount: Decimal = Field(gt=0)
    received_amount: Decimal | None = None
    source_exchange_rate: Decimal = Field(gt=0, default=Decimal("1"))
    target_exchange_rate: Decimal = Field(gt=0, default=Decimal("1"))
    mode_of_payment_id: uuid.UUID | None = None
    reference_no: str | None = None
    reference_date: date | None = None
    remarks: str | None = None
    # GST on advances (Receive): a bare advance has no HSN, so the supply type + rate are
    # explicit. Only a "Services" advance at a positive rate books GST (Regular dealers).
    advance_supply_type: str | None = None  # Goods | Services
    advance_gst_rate: Decimal | None = None
    references: list[PaymentReferenceIn] = Field(default_factory=list)
    deductions: list[PaymentDeductionIn] = Field(default_factory=list)


class PaymentReferenceResponse(DocumentMeta):
    idx: int
    reference_doctype: str
    reference_id: uuid.UUID
    reference_name: str | None
    total_amount: Decimal
    outstanding_amount: Decimal
    allocated_amount: Decimal


class PaymentEntryResponse(DocumentMeta):
    name: str
    posting_date: date
    payment_type: str
    party_type: str | None
    party_id: uuid.UUID | None
    paid_from_id: uuid.UUID
    paid_to_id: uuid.UUID
    paid_amount: Decimal
    received_amount: Decimal
    total_allocated_amount: Decimal
    unallocated_amount: Decimal
    advance_gst_amount: Decimal = Decimal("0")
    advance_gst_outstanding: Decimal = Decimal("0")
    advance_supply_type: str | None = None
    advance_gst_rate: Decimal | None = None
    reference_no: str | None
    reference_date: date | None
    clearance_date: date | None
    status: str
    remarks: str | None
    company_id: uuid.UUID
    references: list[PaymentReferenceResponse]


class PaymentEntryListItem(ORMModel):
    id: uuid.UUID
    name: str
    posting_date: date
    payment_type: str
    party_type: str | None = None
    party_id: uuid.UUID | None = None
    paid_amount: Decimal
    unallocated_amount: Decimal | None = None
    reference_no: str | None = None
    status: str
    docstatus: int


# --- Period Closing -------------------------------------------------------------------------


class UnreconciledInvoiceRow(BaseModel):
    invoice_type: str  # Sales Invoice | Purchase Invoice
    invoice_id: uuid.UUID
    name: str
    posting_date: date
    grand_total: Decimal
    outstanding_amount: Decimal


class UnreconciledPaymentRow(BaseModel):
    payment_entry_id: uuid.UUID
    name: str
    posting_date: date
    paid_amount: Decimal
    unallocated_amount: Decimal


class UnreconciledResponse(BaseModel):
    invoices: list[UnreconciledInvoiceRow]
    payments: list[UnreconciledPaymentRow]


class ReconcileAllocationIn(BaseModel):
    payment_entry_id: uuid.UUID
    invoice_type: str = Field(pattern="^(Sales Invoice|Purchase Invoice)$")
    invoice_id: uuid.UUID
    allocated_amount: Decimal = Field(gt=0)


class PaymentReconciliationIn(BaseModel):
    party_type: str = Field(pattern="^(Customer|Supplier)$")
    party_id: uuid.UUID
    allocations: list[ReconcileAllocationIn] = Field(min_length=1)


class ReconciledInvoiceRow(BaseModel):
    invoice_id: uuid.UUID
    name: str
    outstanding_amount: Decimal
    status: str


class PaymentReconciliationResponse(BaseModel):
    allocations_applied: int
    invoices: list[ReconciledInvoiceRow]


# --- Bank Reconciliation Statement ---------------------------------------------------------------


class BankReconUnclearedRow(BaseModel):
    voucher_type: str  # Payment Entry | Journal Entry
    voucher_id: uuid.UUID
    voucher_no: str
    posting_date: date
    reference_no: str | None
    # signed against the bank account: positive = debit (inflow) not yet cleared
    amount: Decimal


class BankReconciliationReport(BaseModel):
    gl_account_id: uuid.UUID
    as_of: date
    balance_per_books: Decimal
    uncleared_amount: Decimal
    balance_per_bank: Decimal
    uncleared_entries: list[BankReconUnclearedRow]


# --- Bank Transaction (statement import + match tool) --------------------------------------------


class BankTransactionImportRow(BaseModel):
    date: date
    description: str | None = None
    reference_number: str | None = None
    deposit: Decimal = Field(default=Decimal("0"), ge=0)
    withdrawal: Decimal = Field(default=Decimal("0"), ge=0)


class BankTransactionImportIn(BaseModel):
    bank_account_id: uuid.UUID
    transactions: list[BankTransactionImportRow] = Field(min_length=1)


class BankTransactionResponse(ORMModel):
    id: uuid.UUID
    name: str
    bank_account_id: uuid.UUID
    date: date
    description: str | None
    reference_number: str | None
    deposit: Decimal
    withdrawal: Decimal
    status: str  # Unreconciled | Reconciled
    matched_voucher_type: str | None
    matched_voucher_id: uuid.UUID | None
    matched_voucher_no: str | None
    created_voucher: bool


class BankTransactionMatchIn(BaseModel):
    voucher_type: str = Field(pattern="^(Payment Entry|Journal Entry)$")
    voucher_id: uuid.UUID


class BankTransactionCreateVoucherIn(BaseModel):
    """Create a Journal Entry for an unmatched line (e.g. bank charges, interest).

    The line's bank movement is posted against ``account_id`` (the contra
    account: an expense for a withdrawal, an income/other for a deposit). The
    JE is submitted, cleared on the line's date, and matched to the line.
    """

    account_id: uuid.UUID
    cost_center_id: uuid.UUID | None = None
    remarks: str | None = None


class InvoiceMatchSuggestion(BaseModel):
    """An open invoice a bank line could settle (deposit ⇒ Sales, withdrawal ⇒ Purchase)."""

    invoice_type: str  # Sales Invoice | Purchase Invoice
    invoice_id: uuid.UUID
    name: str
    party_name: str | None
    posting_date: date
    grand_total: Decimal
    outstanding_amount: Decimal


class BankTransactionPayInvoiceIn(BaseModel):
    """Settle an open invoice from a bank line by auto-creating a Payment Entry."""

    invoice_type: str = Field(pattern="^(Sales Invoice|Purchase Invoice)$")
    invoice_id: uuid.UUID
    mode_of_payment_id: uuid.UUID | None = None


class BankReconciliationSummary(BaseModel):
    bank_account_id: uuid.UUID
    gl_account_id: uuid.UUID | None
    total: int
    reconciled: int
    unreconciled: int
    unreconciled_amount: Decimal  # net (deposits - withdrawals) of lines still unmatched
    balance_per_books: Decimal  # GL balance of the bank's ledger account
    balance_per_bank: Decimal  # books minus still-uncleared vouchers (existing report)
