"""Module 02 (Accounts) — Sales & Purchase Invoices."""

import uuid
from datetime import date

from sqlalchemy import (
    Boolean, Date, ForeignKey, Index, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

from app.models.accounts.common import (
    InvoiceItemMixin, InvoiceMixin, TaxRowMixin,
)

class SalesInvoice(Base, DocumentMixin, CompanyScopedMixin, InvoiceMixin):
    """Source: erpnext/accounts/doctype/sales_invoice."""

    __tablename__ = "sales_invoices"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_sales_invoice_name"),
        Index("ix_sales_invoices_company_docstatus", "company_id", "docstatus"),
        Index("ix_sales_invoices_customer", "customer_id"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    debit_to_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    return_against_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_invoices.id"))
    update_stock: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    po_no: Mapped[str | None] = mapped_column(String(140))  # customer's PO reference
    po_date: Mapped[date | None] = mapped_column(Date)
    # E-commerce (u/s 52): GSTIN of the e-commerce operator THROUGH which this supply was
    # made. Reported in GSTR-1 Table 14; the operator collects TCS and files GSTR-8. Purely
    # a reporting tag — it does not change the invoice's own GST (the seller still charges it).
    ecommerce_gstin: Mapped[str | None] = mapped_column(String(15))
    # India GST supply category. SEZ/Export are zero-rated (sec 16 IGST Act): without payment
    # (LUT) → no GST; with payment → IGST (always inter-state, even for an SEZ in the same
    # state). Deemed Export is taxed normally but reported apart. Drives GSTR-1 routing
    # (exports → Table 6A; SEZ/DE → B2B with a special invoice type) and GSTR-3B 3.1(b).
    gst_category: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Regular", server_default=text("'Regular'")
    )
    export_with_payment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    shipping_bill_no: Mapped[str | None] = mapped_column(String(20))  # export: shipping bill / bill of export
    shipping_bill_date: Mapped[date | None] = mapped_column(Date)
    port_code: Mapped[str | None] = mapped_column(String(10))  # export: customs port code
    terms: Mapped[str | None] = mapped_column(Text)
    customer_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addresses.id", ondelete="SET NULL")
    )
    shipping_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addresses.id", ondelete="SET NULL")
    )
    contact_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    payment_terms_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_terms_templates.id", use_alter=True, name="fk_si_payment_terms", ondelete="SET NULL"),
    )

    items: Mapped[list["SalesInvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="SalesInvoiceItem.idx"
    )
    taxes: Mapped[list["SalesInvoiceTax"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="SalesInvoiceTax.idx"
    )
    customer = relationship("Customer", lazy="joined", viewonly=True)

    @property
    def customer_name(self) -> str | None:
        return self.customer.customer_name if self.customer else None


class SalesInvoiceItem(Base, DocumentMixin, InvoiceItemMixin):
    __tablename__ = "sales_invoice_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_invoices.id", ondelete="CASCADE"), nullable=False
    )
    income_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    # Module 03/05 cycle links (sales order -> delivery note -> invoice)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", use_alter=True, name="fk_sii_item")
    )
    sales_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_order_items.id", use_alter=True, name="fk_sii_so_item")
    )
    delivery_note_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_note_items.id", use_alter=True, name="fk_sii_dn_item")
    )

    invoice: Mapped[SalesInvoice] = relationship(back_populates="items")


class SalesInvoiceTax(Base, DocumentMixin, TaxRowMixin):
    __tablename__ = "sales_invoice_taxes"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_invoices.id", ondelete="CASCADE"), nullable=False
    )

    invoice: Mapped[SalesInvoice] = relationship(back_populates="taxes")


class PurchaseInvoice(Base, DocumentMixin, CompanyScopedMixin, InvoiceMixin):
    """Source: erpnext/accounts/doctype/purchase_invoice."""

    __tablename__ = "purchase_invoices"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_purchase_invoice_name"),
        Index("ix_purchase_invoices_company_docstatus", "company_id", "docstatus"),
        Index("ix_purchase_invoices_supplier", "supplier_id"),
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    credit_to_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    return_against_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_invoices.id")
    )
    bill_no: Mapped[str | None] = mapped_column(String(140))  # supplier's invoice number
    bill_date: Mapped[date | None] = mapped_column(Date)
    terms: Mapped[str | None] = mapped_column(Text)
    supplier_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addresses.id", ondelete="SET NULL")
    )
    shipping_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addresses.id", ondelete="SET NULL")
    )
    contact_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    payment_terms_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_terms_templates.id", use_alter=True, name="fk_pi_payment_terms", ondelete="SET NULL"),
    )

    items: Mapped[list["PurchaseInvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="PurchaseInvoiceItem.idx"
    )
    taxes: Mapped[list["PurchaseInvoiceTax"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="PurchaseInvoiceTax.idx"
    )
    supplier = relationship("Supplier", lazy="joined", viewonly=True)

    @property
    def supplier_name(self) -> str | None:
        return self.supplier.supplier_name if self.supplier else None


class PurchaseInvoiceItem(Base, DocumentMixin, InvoiceItemMixin):
    __tablename__ = "purchase_invoice_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_invoices.id", ondelete="CASCADE"), nullable=False
    )
    expense_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    # Module 03/04 cycle links (purchase order -> receipt -> invoice)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", use_alter=True, name="fk_pii_item")
    )
    purchase_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_order_items.id", use_alter=True, name="fk_pii_po_item")
    )
    purchase_receipt_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_receipt_items.id", use_alter=True, name="fk_pii_pr_item")
    )

    invoice: Mapped[PurchaseInvoice] = relationship(back_populates="items")


class PurchaseInvoiceTax(Base, DocumentMixin, TaxRowMixin):
    __tablename__ = "purchase_invoice_taxes"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_invoices.id", ondelete="CASCADE"), nullable=False
    )
    add_deduct_tax: Mapped[str] = mapped_column(
        String(10), nullable=False, default="Add", server_default=text("'Add'")
    )
    category: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Total", server_default=text("'Total'")
    )

    invoice: Mapped[PurchaseInvoice] = relationship(back_populates="taxes")


