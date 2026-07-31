"""Module 02 (Accounts) — masters (CoA, cost center, fiscal year, taxes, payment terms, banks)."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin
from app.models.types import Ltree

from app.models.accounts.common import (
    ROOT_TYPE_BALANCE, TaxRowMixin, cm_class_enum, report_type_enum, root_type_enum,
)

class Account(Base, DocumentMixin, CompanyScopedMixin):
    """Chart of Accounts node. Source: erpnext/accounts/doctype/account."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "account_name", "parent_account_id", name="uq_account_name"),
        Index("ix_accounts_path", "path", postgresql_using="gist"),
        Index("ix_accounts_company_root", "company_id", "root_type"),
        Index("ix_accounts_company_cm_class", "company_id", "cm_class"),
    )

    account_name: Mapped[str] = mapped_column(String(140), nullable=False)
    account_number: Mapped[str | None] = mapped_column(String(40))
    parent_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    root_type: Mapped[str] = mapped_column(root_type_enum, nullable=False)
    report_type: Mapped[str] = mapped_column(report_type_enum, nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(60))  # Bank, Cash, Receivable, ...
    account_category: Mapped[str | None] = mapped_column(String(80))
    # Contribution Margin class (P&L leaves). Null = unclassified.
    cm_class: Mapped[str | None] = mapped_column(cm_class_enum)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    account_currency: Mapped[str | None] = mapped_column(String(3))
    freeze_account: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    path: Mapped[str] = mapped_column(Ltree, nullable=False)

    @property
    def balance_must_be(self) -> str:
        return ROOT_TYPE_BALANCE[self.root_type]


class CostCenter(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/cost_center (tree)."""

    __tablename__ = "cost_centers"
    __table_args__ = (
        UniqueConstraint("company_id", "cost_center_name", "parent_cost_center_id", name="uq_cost_center"),
    )

    cost_center_name: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id", ondelete="RESTRICT")
    )
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class FiscalYear(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/fiscal_year.

    Assumption: scoped per company (ERPNext keeps fiscal years global with a
    company child table; per-company is simpler under shared-DB multi-tenancy).
    """

    __tablename__ = "fiscal_years"
    __table_args__ = (UniqueConstraint("company_id", "year", name="uq_fiscal_year"),)

    year: Mapped[str] = mapped_column(String(40), nullable=False)
    year_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    year_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    auto_created: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


# ============================================================================
# Module 02 — Accounts: masters
# ============================================================================

class TaxCategory(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/tax_category — groups parties so the
    right tax template resolves per invoice (e.g. In-State / Out-of-State /
    Reverse Charge). Linked from tax templates and from Customer/Supplier."""

    __tablename__ = "tax_categories"
    __table_args__ = (UniqueConstraint("company_id", "title", name="uq_tax_category"),)

    title: Mapped[str] = mapped_column(String(140), nullable=False)
    # True ⇒ inter-state (IGST); False ⇒ intra-state (CGST+SGST). Lets GST auto-resolve
    # the place of supply from the party's GSTIN state vs the company's.
    is_inter_state: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class TaxTemplate(Base, DocumentMixin, CompanyScopedMixin):
    """Sales/Purchase Taxes and Charges Template (kind discriminator)."""

    __tablename__ = "tax_templates"
    __table_args__ = (UniqueConstraint("company_id", "title", "kind", name="uq_tax_template"),)

    title: Mapped[str] = mapped_column(String(140), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # sales | purchase
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    tax_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_categories.id")
    )

    details: Mapped[list["TaxTemplateDetail"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="TaxTemplateDetail.idx"
    )


class TaxTemplateDetail(Base, DocumentMixin, TaxRowMixin):
    __tablename__ = "tax_template_details"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_templates.id", ondelete="CASCADE"), nullable=False
    )
    add_deduct_tax: Mapped[str] = mapped_column(
        String(10), nullable=False, default="Add", server_default=text("'Add'")
    )
    category: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Total", server_default=text("'Total'")
    )

    template: Mapped[TaxTemplate] = relationship(back_populates="details")


class PaymentTerm(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/payment_term."""

    __tablename__ = "payment_terms"
    __table_args__ = (UniqueConstraint("company_id", "term_name", name="uq_payment_term"),)

    term_name: Mapped[str] = mapped_column(String(140), nullable=False)
    invoice_portion: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=100, server_default=text("100")
    )
    due_date_based_on: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Day(s) after invoice date",
        server_default=text("'Day(s) after invoice date'"),
    )
    credit_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class PaymentTermsTemplate(Base, DocumentMixin, CompanyScopedMixin):
    __tablename__ = "payment_terms_templates"
    __table_args__ = (UniqueConstraint("company_id", "template_name", name="uq_ptt_name"),)

    template_name: Mapped[str] = mapped_column(String(140), nullable=False)

    terms: Mapped[list["PaymentTermsTemplateDetail"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="PaymentTermsTemplateDetail.idx"
    )


class PaymentTermsTemplateDetail(Base, DocumentMixin):
    __tablename__ = "payment_terms_template_details"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_terms_templates.id", ondelete="CASCADE"), nullable=False
    )
    payment_term_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_terms.id"))
    description: Mapped[str | None] = mapped_column(Text)  # friendly label, e.g. "Advance"
    invoice_portion: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=100, server_default=text("100")
    )
    due_date_based_on: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Day(s) after invoice date",
        server_default=text("'Day(s) after invoice date'"),
    )
    credit_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    template: Mapped[PaymentTermsTemplate] = relationship(back_populates="terms")


class ModeOfPayment(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/mode_of_payment. Per-company here, so
    the default account child table collapses to a single column."""

    __tablename__ = "modes_of_payment"
    __table_args__ = (UniqueConstraint("company_id", "mode_name", name="uq_mode_of_payment"),)

    mode_name: Mapped[str] = mapped_column(String(140), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="Cash", server_default=text("'Cash'"))
    default_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class Bank(Base, DocumentMixin, CompanyScopedMixin):
    __tablename__ = "banks"
    __table_args__ = (UniqueConstraint("company_id", "bank_name", name="uq_bank_name"),)

    bank_name: Mapped[str] = mapped_column(String(140), nullable=False)
    swift_number: Mapped[str | None] = mapped_column(String(40))


class BankAccount(Base, DocumentMixin, CompanyScopedMixin):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("company_id", "account_name", name="uq_bank_account_name"),)

    account_name: Mapped[str] = mapped_column(String(140), nullable=False)
    bank_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("banks.id"))
    gl_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    account_number: Mapped[str | None] = mapped_column(String(40))
    iban: Mapped[str | None] = mapped_column(String(40))
    is_company_account: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class TaxWithholdingCategory(Base, DocumentMixin, CompanyScopedMixin):
    """India TDS/TCS category (source: erpnext tax_withholding_category, simplified).

    TDS = tax deducted at source when paying a supplier (reduces the payable);
    TCS = tax collected at source when billing a customer (adds to the receivable).
    ``account_id`` is the TDS/TCS payable (liability) account.
    """

    __tablename__ = "tax_withholding_categories"
    __table_args__ = (UniqueConstraint("company_id", "category_name", name="uq_tax_withholding"),)

    category_name: Mapped[str] = mapped_column(String(140), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(4), nullable=False, default="TDS", server_default=text("'TDS'")
    )  # TDS | TCS
    rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    threshold: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # annual cumulative threshold; 0 = always apply (informational for now)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class DunningType(Base, DocumentMixin, CompanyScopedMixin):
    """An overdue-reminder tier (source: erpnext dunning_type, simplified).

    Tiers escalate by ``grace_period_days`` (apply this tier once an invoice is at
    least that many days overdue). ``interest_rate`` is charged per annum on the
    overdue amount for the days it's late; ``dunning_fee`` is a flat charge. The
    highest tier whose grace period the customer has passed is used for the letter.
    """

    __tablename__ = "dunning_types"
    __table_args__ = (UniqueConstraint("company_id", "dunning_type", name="uq_dunning_type"),)

    dunning_type: Mapped[str] = mapped_column(String(140), nullable=False)  # e.g. "First Notice"
    grace_period_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    interest_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )  # % per annum on the overdue amount
    dunning_fee: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # flat administrative charge
    letter_intro: Mapped[str | None] = mapped_column(Text)  # the tone/message for this tier
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class PaymentRequest(Base, DocumentMixin, CompanyScopedMixin):
    """A request for a customer to pay an amount, optionally against a Sales Invoice.

    Emailed as a themed PDF (with bank details from the print profile). ``payment_url``
    is a seam for a future online-payment gateway link — unused for now (link-less).
    ``status`` is tracked manually: Requested → Paid / Cancelled.
    """

    __tablename__ = "payment_requests"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_payment_request_name"),)

    name: Mapped[str] = mapped_column(String(140), nullable=False)  # naming-series doc number
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    reference_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_invoices.id")
    )
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Requested", server_default=text("'Requested'")
    )  # Requested | Paid | Cancelled
    message: Mapped[str | None] = mapped_column(Text)
    payment_url: Mapped[str | None] = mapped_column(String(500))  # future gateway link

    customer = relationship("Customer", lazy="joined", viewonly=True)

    @property
    def customer_name(self) -> str | None:
        return self.customer.customer_name if self.customer else None


class ItemTaxTemplate(Base, DocumentMixin, CompanyScopedMixin):
    """Per-item tax-rate overrides (source: erpnext item_tax_template). Set on an
    Item so a bill can mix GST slabs (5% / 12% / 18%)."""

    __tablename__ = "item_tax_templates"
    __table_args__ = (UniqueConstraint("company_id", "title", name="uq_item_tax_template"),)

    title: Mapped[str] = mapped_column(String(140), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    details: Mapped[list["ItemTaxTemplateDetail"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class ItemTaxTemplateDetail(Base, DocumentMixin):
    __tablename__ = "item_tax_template_details"

    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_tax_templates.id", ondelete="CASCADE"), nullable=False
    )
    account_head_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))

    template: Mapped[ItemTaxTemplate] = relationship(back_populates="details")


# ============================================================================
# Module 02 — Accounts: transactions
# ============================================================================


