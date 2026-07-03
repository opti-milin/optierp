"""Module 02 (Accounts) — Payment Entry + references/deductions."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

from app.models.accounts.common import (
    VoucherMixin,
    party_type_enum,
)

class PaymentEntry(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin):
    """Source: erpnext/accounts/doctype/payment_entry."""

    __tablename__ = "payment_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_payment_entry_name"),
        Index("ix_payment_entries_company_docstatus", "company_id", "docstatus"),
    )

    payment_type: Mapped[str] = mapped_column(String(20), nullable=False)  # Receive | Pay | Internal Transfer
    party_type: Mapped[str | None] = mapped_column(party_type_enum)
    party_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    paid_from_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    paid_to_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    paid_from_account_currency: Mapped[str | None] = mapped_column(String(3))
    paid_to_account_currency: Mapped[str | None] = mapped_column(String(3))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    received_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    source_exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    target_exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    total_allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    unallocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    mode_of_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modes_of_payment.id")
    )
    reference_no: Mapped[str | None] = mapped_column(String(140))  # cheque / txn ref
    reference_date: Mapped[date | None] = mapped_column(Date)
    clearance_date: Mapped[date | None] = mapped_column(Date)  # bank reconciliation
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Draft", server_default=text("'Draft'"))
    # GST on advances received (services only, Regular dealers). Booked inclusive at
    # receipt (Dr 'GST on Advances' control / Cr Output GST) and reversed proportionally as
    # the advance is adjusted to invoices — so the control account nets to zero.
    advance_supply_type: Mapped[str | None] = mapped_column(String(20))  # Goods | Services
    advance_gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    advance_gst_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    advance_is_inter_state: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    advance_gst_outstanding: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    references: Mapped[list["PaymentEntryReference"]] = relationship(
        back_populates="payment_entry", cascade="all, delete-orphan", order_by="PaymentEntryReference.idx"
    )
    deductions: Mapped[list["PaymentEntryDeduction"]] = relationship(
        back_populates="payment_entry", cascade="all, delete-orphan", order_by="PaymentEntryDeduction.idx"
    )


class PaymentEntryReference(Base, DocumentMixin):
    __tablename__ = "payment_entry_references"

    payment_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_entries.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    reference_doctype: Mapped[str] = mapped_column(String(60), nullable=False)  # Sales Invoice / Purchase Invoice
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reference_name: Mapped[str | None] = mapped_column(String(140))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    outstanding_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    payment_entry: Mapped[PaymentEntry] = relationship(back_populates="references")


class PaymentEntryDeduction(Base, DocumentMixin):
    __tablename__ = "payment_entry_deductions"

    payment_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_entries.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cost_centers.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    description: Mapped[str | None] = mapped_column(String(300))

    payment_entry: Mapped[PaymentEntry] = relationship(back_populates="deductions")


class AdvanceGstAdjustment(Base, DocumentMixin, CompanyScopedMixin):
    """One advance-to-invoice adjustment of GST-on-advance (GSTR-1 Table 11B source).

    Written by payment reconciliation when a GST-bearing advance is applied to an invoice:
    records the tax reversed (Dr Output GST / Cr 'GST on Advances' control) so Table 11B and
    the GSTR-3B 3.1(a) netting are queryable per period. The invoice keeps its own full Output
    GST; this reversal is what stops the advance tax being counted twice."""

    __tablename__ = "advance_gst_adjustments"

    payment_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_entries.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    place_of_supply: Mapped[str | None] = mapped_column(String(64))
    rate: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False, default=0, server_default=text("0"))
    base: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    cgst: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    sgst: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    igst: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    cess: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))


class BankTransaction(Base, DocumentMixin, CompanyScopedMixin):
    """A single line from an imported bank statement (source: erpnext Bank Transaction).

    Posts NO GL of its own — it represents what the bank actually did. Reconciling
    it sets the matched voucher's ``clearance_date`` (the existing bank-rec
    mechanism), so the bank-reconciliation report converges. ``deposit`` = money in,
    ``withdrawal`` = money out; exactly one is non-zero. MVP matches 1 line to 1
    existing voucher (Payment Entry / Journal Entry); split allocations are deferred.
    """

    __tablename__ = "bank_transactions"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_bank_transaction_name"),
        Index("ix_bank_transactions_account_status", "bank_account_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)  # naming-series doc number
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_accounts.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)  # statement line date
    description: Mapped[str | None] = mapped_column(String(500))
    reference_number: Mapped[str | None] = mapped_column(String(140))  # UTR / cheque / bank ref
    deposit: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    withdrawal: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Unreconciled", server_default=text("'Unreconciled'")
    )
    # 1:1 link to the cleared voucher (no FK — voucher_type picks the table).
    matched_voucher_type: Mapped[str | None] = mapped_column(String(40))  # Payment Entry | Journal Entry
    matched_voucher_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    matched_voucher_no: Mapped[str | None] = mapped_column(String(140))
    # True when the matched voucher was created BY this tool (from an unmatched
    # line) — so unreconcile cancels it, rather than just un-clearing a pre-existing one.
    created_voucher: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


