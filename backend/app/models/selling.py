"""Module 05 (Selling) models.

Customer (from Module 02) plus the sales chain:
Quotation -> Sales Order -> Delivery Note (Module 03) -> Sales Invoice
(Module 02).

Assumptions: Customer Group / Territory / Sales Person trees and the pricing
rule engine are deferred (Item Price covers rate defaults); quotations are to
Customers only (Lead quotations arrive with CRM, Module 06).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.accounts import InvoiceItemMixin, TaxRowMixin, TotalsMixin, VoucherMixin
from app.models.base import Base, CompanyScopedMixin, DocumentMixin, TreeMixin

SO_STATUSES = (
    "Draft", "To Deliver and Bill", "To Deliver", "To Bill", "Completed", "Cancelled", "Closed",
)


class Customer(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/selling/doctype/customer (stub for Module 02)."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("company_id", "customer_name", name="uq_customer_name"),)

    customer_name: Mapped[str] = mapped_column(String(140), nullable=False)
    customer_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Company", server_default=text("'Company'")
    )  # Company | Individual
    tax_id: Mapped[str | None] = mapped_column(String(80))
    email_id: Mapped[str | None] = mapped_column(String(140))  # for emailing invoices/statements
    default_currency: Mapped[str | None] = mapped_column(String(3))
    # Overrides the company default receivable account when set
    receivable_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    credit_limit: Mapped[float | None] = mapped_column(Numeric(21, 6))
    payment_terms_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_terms_templates.id", use_alter=True, name="fk_customer_ptt")
    )
    tax_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_categories.id", use_alter=True, name="fk_customer_tax_category")
    )
    customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_groups.id", use_alter=True, name="fk_customer_group", ondelete="SET NULL"),
    )
    territory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("territories.id", use_alter=True, name="fk_customer_territory", ondelete="SET NULL"),
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(Text)


class Campaign(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/crm/doctype/campaign (surfaced in the Selling workspace).

    A flat, company-scoped simple master — the first DocType served entirely by
    the metadata engine (app.registry); it has no bespoke model logic.
    """

    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("company_id", "campaign_name", name="uq_campaign_name"),)

    campaign_name: Mapped[str] = mapped_column(String(140), nullable=False)
    campaign_desc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Active", server_default=text("'Active'")
    )  # Active | Inactive
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Territory(Base, DocumentMixin, CompanyScopedMixin, TreeMixin):
    """Source: erpnext/setup/doctype/territory (nested-set tree).

    Tree master served by the metadata engine; ``app.services.tree`` maintains
    the ltree ``path`` from ``parent_territory_id``.
    """

    __tablename__ = "territories"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "territory_name", "parent_territory_id", name="uq_territory_name"
        ),
        Index("ix_territories_path", "path", postgresql_using="gist"),
    )

    territory_name: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_territory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("territories.id", ondelete="RESTRICT")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class CustomerGroup(Base, DocumentMixin, CompanyScopedMixin, TreeMixin):
    """Source: erpnext/setup/doctype/customer_group (nested-set tree)."""

    __tablename__ = "customer_groups"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "customer_group_name", "parent_customer_group_id",
            name="uq_customer_group_name",
        ),
        Index("ix_customer_groups_path", "path", postgresql_using="gist"),
    )

    customer_group_name: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_groups.id", ondelete="RESTRICT")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class SalesPerson(Base, DocumentMixin, CompanyScopedMixin, TreeMixin):
    """Source: erpnext/setup/doctype/sales_person (nested-set tree)."""

    __tablename__ = "sales_persons"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "sales_person_name", "parent_sales_person_id",
            name="uq_sales_person_name",
        ),
        Index("ix_sales_persons_path", "path", postgresql_using="gist"),
    )

    sales_person_name: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_sales_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_persons.id", ondelete="RESTRICT")
    )
    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


# --- Flat simple masters (Phase 2; engine-served, no bespoke logic) ----------


class SalesPartner(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/setup/doctype/sales_partner — external reseller/distributor."""

    __tablename__ = "sales_partners"
    __table_args__ = (UniqueConstraint("company_id", "partner_name", name="uq_sales_partner"),)

    partner_name: Mapped[str] = mapped_column(String(140), nullable=False)
    partner_type: Mapped[str | None] = mapped_column(String(80))
    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class TermsTemplate(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/setup/doctype/terms_and_conditions — reusable T&C text."""

    __tablename__ = "terms_templates"
    __table_args__ = (UniqueConstraint("company_id", "template_name", name="uq_terms_template"),)

    template_name: Mapped[str] = mapped_column(String(140), nullable=False)
    terms: Mapped[str | None] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class UTMSource(Base, DocumentMixin, CompanyScopedMixin):
    """Source: frappe/website/doctype/utm_source — marketing attribution source."""

    __tablename__ = "utm_sources"
    __table_args__ = (UniqueConstraint("company_id", "utm_source_name", name="uq_utm_source"),)

    utm_source_name: Mapped[str] = mapped_column(String(140), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class MonthlyDistribution(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/monthly_distribution — prorates annual
    targets across 12 months. ERPNext uses a child grid; modelled flat here as
    12 percentage columns (config-only master)."""

    __tablename__ = "monthly_distributions"
    __table_args__ = (
        UniqueConstraint("company_id", "distribution_name", name="uq_monthly_distribution"),
    )

    distribution_name: Mapped[str] = mapped_column(String(140), nullable=False)
    month_1: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_2: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_3: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_4: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_5: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_6: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_7: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_8: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_9: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_10: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_11: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))
    month_12: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default=text("0"))


class Address(Base, DocumentMixin, CompanyScopedMixin):
    """Source: frappe/contacts/doctype/address.

    Linked to a Customer and/or Supplier. ERPNext uses polymorphic dynamic links
    (one address -> many parents); simplified here to direct party links, served
    by the engine via the link-source registry. Full dynamic links are a
    follow-up.
    """

    __tablename__ = "addresses"
    __table_args__ = (UniqueConstraint("company_id", "address_title", name="uq_address_title"),)

    address_title: Mapped[str] = mapped_column(String(140), nullable=False)
    address_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Billing", server_default=text("'Billing'")
    )
    address_line1: Mapped[str] = mapped_column(String(240), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(240))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(100))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL")
    )
    # True for the company's own addresses (registered office / billing / dispatch),
    # shown on printed documents; customer_id/supplier_id are NULL for these.
    is_company_address: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Contact(Base, DocumentMixin, CompanyScopedMixin):
    """Source: frappe/contacts/doctype/contact (direct party links — see Address)."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("company_id", "first_name", "last_name", "email_id", name="uq_contact"),
    )

    first_name: Mapped[str] = mapped_column(String(140), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(140))
    email_id: Mapped[str | None] = mapped_column(String(140))
    mobile_no: Mapped[str | None] = mapped_column(String(40))
    phone: Mapped[str | None] = mapped_column(String(40))
    designation: Mapped[str | None] = mapped_column(String(140))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class PricingRule(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/pricing_rule (Phase 3 — pricing engine).

    Applies a discount or rate override on selling lines, matched by item (or
    item group) and optionally customer, within qty/date bounds, by priority.
    v1: customer-group / territory matching is deferred (Customer carries no
    group/territory link yet).
    """

    __tablename__ = "pricing_rules"
    __table_args__ = (UniqueConstraint("company_id", "title", name="uq_pricing_rule_title"),)

    title: Mapped[str] = mapped_column(String(140), nullable=False)
    selling: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    buying: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    apply_on: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Item", server_default=text("'Item'")
    )  # Item | Item Group
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"))
    item_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_groups.id")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_groups.id")
    )
    territory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("territories.id"))
    min_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    max_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_upto: Mapped[date | None] = mapped_column(Date)
    rate_or_discount: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Discount Percentage",
        server_default=text("'Discount Percentage'"),
    )  # Discount Percentage | Discount Amount | Rate
    discount_percentage: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class CouponCode(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/coupon_code (Phase 3).

    A redeemable code granting a % discount on the order, with optional validity
    window and usage cap. Applied via the order's additional discount.
    """

    __tablename__ = "coupon_codes"
    __table_args__ = (UniqueConstraint("company_id", "coupon_code", name="uq_coupon_code"),)

    coupon_code: Mapped[str] = mapped_column(String(140), nullable=False)
    coupon_name: Mapped[str | None] = mapped_column(String(140))
    discount_percentage: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_upto: Mapped[date | None] = mapped_column(Date)
    maximum_use: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class ShippingRule(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/shipping_rule (Phase 3).

    A flat freight charge, optionally waived when the line subtotal reaches a
    threshold. Added to the order as an 'Actual' charge row posted to account_id.
    """

    __tablename__ = "shipping_rules"
    __table_args__ = (UniqueConstraint("company_id", "shipping_rule_name", name="uq_shipping_rule"),)

    shipping_rule_name: Mapped[str] = mapped_column(String(140), nullable=False)
    shipping_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    free_above: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # subtotal at/above which shipping is free (0 = never free)
    # Calendar days warehouse → customer (outbound). Used by supply-aware CTP.
    transit_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class ProductBundle(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/selling/doctype/product_bundle (Phase 3).

    A bundle SKU that expands into component items at billing. Parent + a child
    grid of components — the first engine-served DocType with child tables.
    """

    __tablename__ = "product_bundles"
    __table_args__ = (UniqueConstraint("company_id", "bundle_name", name="uq_product_bundle"),)

    bundle_name: Mapped[str] = mapped_column(String(140), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"))
    description: Mapped[str | None] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class ProductBundleItem(Base, DocumentMixin):
    """Component line of a Product Bundle (engine-managed child rows)."""

    __tablename__ = "product_bundle_items"

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_bundles.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=1, server_default=text("1"))
    description: Mapped[str | None] = mapped_column(String(240))


class BlanketOrder(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/manufacturing/doctype/blanket_order (Phase 3).

    A long-term rate agreement (header + item lines) for a customer/supplier over
    a validity window. Matching selling lines use the agreed rate.
    """

    __tablename__ = "blanket_orders"
    __table_args__ = (UniqueConstraint("company_id", "blanket_order_name", name="uq_blanket_order"),)

    blanket_order_name: Mapped[str] = mapped_column(String(140), nullable=False)
    order_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Selling", server_default=text("'Selling'")
    )  # Selling | Buying
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_upto: Mapped[date | None] = mapped_column(Date)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class BlanketOrderItem(Base, DocumentMixin):
    """Agreed item line of a Blanket Order (engine-managed child rows)."""

    __tablename__ = "blanket_order_items"

    blanket_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("blanket_orders.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))


class PromotionalScheme(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/accounts/doctype/promotional_scheme (Phase 3).

    Quantity-tiered discounts (buy more, save more) for an item / item group and
    optional customer. Tiers are child rows; the best applicable tier applies.
    """

    __tablename__ = "promotional_schemes"
    __table_args__ = (UniqueConstraint("company_id", "scheme_name", name="uq_promotional_scheme"),)

    scheme_name: Mapped[str] = mapped_column(String(140), nullable=False)
    apply_on: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Item", server_default=text("'Item'")
    )  # Item | Item Group
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"))
    item_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_groups.id")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_upto: Mapped[date | None] = mapped_column(Date)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class PromotionalSchemeTier(Base, DocumentMixin):
    """A quantity tier of a Promotional Scheme (engine-managed child rows)."""

    __tablename__ = "promotional_scheme_tiers"

    scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("promotional_schemes.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    min_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    discount_percentage: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=0, server_default=text("0")
    )


class Quotation(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin, TotalsMixin):
    """Source: erpnext/selling/doctype/quotation."""

    __tablename__ = "quotations"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_quotation_name"),
        Index("ix_quotations_company_docstatus", "company_id", "docstatus"),
        Index("ix_quotations_customer", "customer_id"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    valid_till: Mapped[date | None] = mapped_column(Date)
    order_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Sales", server_default=text("'Sales'")
    )  # Sales | Maintenance | Shopping Cart
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
    # More Info section (selling): campaign / source / territory / customer group / partner
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", use_alter=True, name="fk_qtn_campaign", ondelete="SET NULL")
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("utm_sources.id", use_alter=True, name="fk_qtn_source", ondelete="SET NULL")
    )
    territory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("territories.id", use_alter=True, name="fk_qtn_territory", ondelete="SET NULL")
    )
    customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_groups.id", use_alter=True, name="fk_qtn_cust_group", ondelete="SET NULL")
    )
    sales_partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_partners.id", use_alter=True, name="fk_qtn_partner", ondelete="SET NULL")
    )
    shipping_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shipping_rules.id", use_alter=True, name="fk_qtn_shipping_rule", ondelete="SET NULL"),
    )
    payment_terms_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_terms_templates.id", use_alter=True, name="fk_qtn_payment_terms", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )  # Draft | Open | Ordered | Cancelled | Expired

    items: Mapped[list["QuotationItem"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan", order_by="QuotationItem.idx"
    )
    taxes: Mapped[list["QuotationTax"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan", order_by="QuotationTax.idx"
    )
    customer = relationship("Customer", lazy="joined", viewonly=True)

    @property
    def customer_name(self) -> str | None:
        return self.customer.customer_name if self.customer else None


class QuotationItem(Base, DocumentMixin, InvoiceItemMixin):
    __tablename__ = "quotation_items"

    quotation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", use_alter=True, name="fk_qi_item")
    )

    quotation: Mapped[Quotation] = relationship(back_populates="items")


class QuotationTax(Base, DocumentMixin, TaxRowMixin):
    __tablename__ = "quotation_taxes"

    quotation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )

    quotation: Mapped[Quotation] = relationship(back_populates="taxes")


class SalesOrder(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin, TotalsMixin):
    """Source: erpnext/selling/doctype/sales_order.

    On submit: bin.reserved_qty += pending qty per stock item; credit-limit
    check returns a warning (not a hard block — Section 3, Module 05 rule 3).
    """

    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_sales_order_name"),
        Index("ix_sales_orders_company_docstatus", "company_id", "docstatus"),
        Index("ix_sales_orders_customer", "customer_id"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    delivery_date: Mapped[date | None] = mapped_column(Date)
    order_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Sales", server_default=text("'Sales'")
    )  # Sales | Maintenance | Shopping Cart
    set_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", use_alter=True, name="fk_so_warehouse")
    )
    quotation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotations.id")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    per_delivered: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=0, server_default=text("0")
    )
    per_billed: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=0, server_default=text("0")
    )
    po_no: Mapped[str | None] = mapped_column(String(140))  # customer's PO reference
    po_date: Mapped[date | None] = mapped_column(Date)
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
    # More Info section (selling): campaign / source / territory / customer group / partner
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", use_alter=True, name="fk_so_campaign", ondelete="SET NULL")
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("utm_sources.id", use_alter=True, name="fk_so_source", ondelete="SET NULL")
    )
    territory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("territories.id", use_alter=True, name="fk_so_territory", ondelete="SET NULL")
    )
    customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_groups.id", use_alter=True, name="fk_so_cust_group", ondelete="SET NULL")
    )
    sales_partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_partners.id", use_alter=True, name="fk_so_partner", ondelete="SET NULL")
    )
    shipping_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shipping_rules.id", use_alter=True, name="fk_so_shipping_rule", ondelete="SET NULL"),
    )
    payment_terms_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_terms_templates.id", use_alter=True, name="fk_so_payment_terms", ondelete="SET NULL"),
    )

    items: Mapped[list["SalesOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="SalesOrderItem.idx"
    )
    taxes: Mapped[list["SalesOrderTax"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="SalesOrderTax.idx"
    )
    customer = relationship("Customer", lazy="joined", viewonly=True)

    @property
    def customer_name(self) -> str | None:
        return self.customer.customer_name if self.customer else None


class SalesOrderItem(Base, DocumentMixin, InvoiceItemMixin):
    __tablename__ = "sales_order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", use_alter=True, name="fk_soi_item")
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", use_alter=True, name="fk_soi_warehouse")
    )
    delivery_date: Mapped[date | None] = mapped_column(Date)
    delivered_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    billed_amt: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    quotation_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotation_items.id")
    )

    order: Mapped[SalesOrder] = relationship(back_populates="items")


class SalesOrderTax(Base, DocumentMixin, TaxRowMixin):
    __tablename__ = "sales_order_taxes"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False
    )

    order: Mapped[SalesOrder] = relationship(back_populates="taxes")
