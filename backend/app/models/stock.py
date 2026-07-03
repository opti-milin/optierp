"""Module 03 (Stock) models.

Masters: Item Group (tree), Warehouse (tree), Item, Price List, Item Price.
Ledger: Stock Ledger Entry (append-only, like GL Entry) + Bin (the maintained
item/warehouse balance table — erpnext calls it Bin; the migration spec calls
it item_warehouse_balance).
Transactions: Stock Entry, Material Request, Purchase Receipt, Delivery Note.

Assumptions (flagged for manual review):
  * Valuation: Moving Average implemented; FIFO is a column value reserved for
    later (creating a FIFO item raises a validation error for now).
  * Stock Reconciliation shipped (Phase 1); landed cost is handled as charges on
    the Purchase Receipt (Phase 3, no separate Landed Cost Voucher). Batch /
    Serial No remain deferred.
  * Purchase Receipts / Delivery Notes carry no tax rows; taxes apply on the
    invoice (orders DO carry taxes so their grand totals match invoices).
    DN/PR returns (Phase 3) mirror this — they post only stock + COGS/SRBNB GL.
"""

import uuid
from datetime import date, datetime
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
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.accounts import InvoiceItemMixin, VoucherMixin
from app.models.base import Base, CompanyScopedMixin, DocumentMixin

VALUATION_METHODS = ("Moving Average", "FIFO")
STOCK_ENTRY_PURPOSES = ("Material Receipt", "Material Issue", "Material Transfer")
MATERIAL_REQUEST_TYPES = ("Purchase", "Material Transfer", "Material Issue")
STOCK_RECONCILIATION_PURPOSES = ("Opening Stock", "Stock Reconciliation")


# ============================================================================
# Masters
# ============================================================================


class ItemGroup(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/stock/doctype/item_group (tree)."""

    __tablename__ = "item_groups"
    __table_args__ = (
        UniqueConstraint("company_id", "item_group_name", name="uq_item_group_name"),
    )

    item_group_name: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_item_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_groups.id", ondelete="RESTRICT")
    )
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Warehouse(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/stock/doctype/warehouse (tree).

    ``account_id`` overrides the company default inventory account for
    perpetual-inventory GL postings from this warehouse.
    """

    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("company_id", "warehouse_name", name="uq_warehouse_name"),
    )

    warehouse_name: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT")
    )
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    warehouse_type: Mapped[str | None] = mapped_column(String(60))
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Item(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/stock/doctype/item.

    Assumption: items are company-scoped (ERPNext keeps them global with
    per-company Item Defaults; company-scoped is simpler under shared-DB
    multi-tenancy and matches how every other master here works).
    """

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("company_id", "item_code", name="uq_item_code"),
        Index("ix_items_company_group", "company_id", "item_group_id"),
    )

    item_code: Mapped[str] = mapped_column(String(140), nullable=False)
    item_name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    item_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_groups.id")
    )
    stock_uom: Mapped[str] = mapped_column(
        String(140), nullable=False, default="Nos", server_default=text("'Nos'")
    )
    # Multi-UOM (Phase 4): optional buy/sell UOMs with a conversion factor to the
    # stock UOM (stock units per 1 of that UOM, e.g. Box -> 12 Nos). stock_uom is
    # implicitly factor 1. A line may use stock_uom, purchase_uom or sales_uom.
    purchase_uom: Mapped[str | None] = mapped_column(String(140))
    purchase_uom_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    sales_uom: Mapped[str | None] = mapped_column(String(140))
    sales_uom_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    is_stock_item: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_sales_item: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_purchase_item: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Tracking (Phase 5): serialised units (warranty/RMA) tracked individually in
    # the serial_nos master; batched items label movements with a lot batch_no
    # (with optional expiry). Either way valuation stays Moving Average.
    has_serial_no: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    has_batch_no: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    valuation_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Moving Average", server_default=text("'Moving Average'")
    )
    standard_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # default selling rate when no Item Price matches
    valuation_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # default incoming rate for the first receipt
    last_purchase_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    income_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    # Optional per-item GST override (Item Tax Template) applied on invoices.
    item_tax_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_tax_templates.id")
    )
    # India GST: HSN (goods) / SAC (services) code printed on tax invoices + grouped in
    # the GSTR-1 HSN summary; gst_treatment buckets the item (Taxable/Nil-Rated/Exempt/Non-GST).
    hsn_sac_code: Mapped[str | None] = mapped_column(String(8))
    gst_treatment: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Taxable", server_default=text("'Taxable'")
    )
    default_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    reorder_level: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    reorder_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    brand: Mapped[str | None] = mapped_column(String(140))
    barcode: Mapped[str | None] = mapped_column(String(140))
    # Fixed-asset flag (Assets module Phase 3): a Purchase Invoice line for such an item
    # auto-creates a draft Asset under this category. Point the item's expense account at
    # the Fixed Asset COA account so the purchase debits the asset, not an expense.
    is_fixed_asset: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    asset_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_categories.id")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    item_group = relationship("ItemGroup", lazy="joined", viewonly=True)

    @property
    def item_group_name(self) -> str | None:
        return self.item_group.item_group_name if self.item_group else None


class PriceList(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/stock/doctype/price_list."""

    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("company_id", "price_list_name", name="uq_price_list_name"),)

    price_list_name: Mapped[str] = mapped_column(String(140), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    buying: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    selling: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class ItemPrice(Base, DocumentMixin, CompanyScopedMixin):
    """Source: erpnext/stock/doctype/item_price."""

    __tablename__ = "item_prices"
    __table_args__ = (
        UniqueConstraint("item_id", "price_list_id", "valid_from", name="uq_item_price"),
        Index("ix_item_prices_item", "item_id"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    price_list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False
    )
    price_list_rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_upto: Mapped[date | None] = mapped_column(Date)


class SerialNo(Base, DocumentMixin, CompanyScopedMixin):
    """Per-unit serial number (Phase 5) for warranty/RMA tracking.

    Created ``In Stock`` on a receipt, flips to ``Delivered`` on a delivery, and
    reverts on a return/cancel; ``Returned`` means it left our stock other than to
    a customer (back to supplier, or issued/consumed). Availability for delivery /
    issue / transfer / supplier-return = status ``In Stock`` at the right warehouse.
    Tracking layer only — valuation stays Moving Average on the item's Bin.
    """

    __tablename__ = "serial_nos"
    __table_args__ = (
        UniqueConstraint("company_id", "serial_no", name="uq_serial_no"),
        Index("ix_serial_nos_company_item_status", "company_id", "item_id", "status"),
    )

    serial_no: Mapped[str] = mapped_column(String(140), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="In Stock", server_default=text("'In Stock'")
    )
    purchase_voucher_type: Mapped[str | None] = mapped_column(String(60))
    purchase_voucher_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    delivery_voucher_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    warranty_expiry: Mapped[date | None] = mapped_column(Date)

    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


SERIAL_NO_STATUSES = ("In Stock", "Delivered", "Returned")


class Batch(Base, DocumentMixin, CompanyScopedMixin):
    """Lot / batch (Phase 5B). Descriptor-backed master (free CRUD at /m/batch).

    Unlike a serial, a batch has NO per-unit status — it is just a label on a
    stock movement. The transaction line carries the ``batch_no``; this master
    holds the batch's item and optional ``expiry_date``. Delivering past the
    expiry is blocked at submit. Valuation stays Moving Average regardless.
    """

    __tablename__ = "batches"
    __table_args__ = (
        # unique per (company, item) — the resolver keys on item too, so two SKUs may
        # legitimately reuse a supplier's lot string
        UniqueConstraint("company_id", "item_id", "batch_no", name="uq_batch_no"),
        Index("ix_batches_company_item", "company_id", "item_id"),
    )

    batch_no: Mapped[str] = mapped_column(String(140), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


# ============================================================================
# Ledger
# ============================================================================


class Bin(Base, CompanyScopedMixin):
    """Maintained per (item, warehouse) balance — erpnext/stock/doctype/bin.

    Updated only by app.services.stock_ledger (row-locked); the migration spec
    suggests a DB trigger, but moving-average valuation needs Python-side
    logic, mirroring how ERPNext maintains Bin from its controllers.
    """

    __tablename__ = "bins"
    __table_args__ = (
        UniqueConstraint("item_id", "warehouse_id", name="uq_bin_item_warehouse"),
        Index("ix_bins_company_item", "company_id", "item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    creation: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    modified: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"), onupdate=text("now()")
    )
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    actual_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    reserved_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    ordered_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    valuation_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    stock_value: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )


class StockLedgerEntry(Base, CompanyScopedMixin):
    """Append-only stock ledger (Section 3, Module 03).

    INSERT-only like gl_entries: a DB trigger blocks UPDATE/DELETE.
    Cancellation writes reversing entries (is_cancellation=true) valued at the
    original entry's rates — assumption: no full repost engine; ERPNext
    instead flags is_cancelled and reposts future entries.

    Written exclusively through app.services.stock_ledger.make_sl_entries.
    """

    __tablename__ = "stock_ledger_entries"
    __table_args__ = (
        Index("ix_sle_item_warehouse_date", "item_id", "warehouse_id", "posting_date"),
        Index("ix_sle_voucher", "voucher_type", "voucher_id"),
        Index("ix_sle_company_date", "company_id", "posting_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    creation: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    owner: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    voucher_type: Mapped[str] = mapped_column(String(60), nullable=False)
    voucher_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    voucher_no: Mapped[str] = mapped_column(String(140), nullable=False)
    actual_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)  # +in / -out
    qty_after_transaction: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    incoming_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    valuation_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    stock_value: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    stock_value_difference: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    is_cancellation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


# ============================================================================
# Transactions
# ============================================================================


class StockEntry(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin):
    """Source: erpnext/stock/doctype/stock_entry (Receipt/Issue/Transfer)."""

    __tablename__ = "stock_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_stock_entry_name"),
        Index("ix_stock_entries_company_docstatus", "company_id", "docstatus"),
    )

    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    from_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    to_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    items: Mapped[list["StockEntryItem"]] = relationship(
        back_populates="stock_entry", cascade="all, delete-orphan", order_by="StockEntryItem.idx"
    )


class StockEntryItem(Base, DocumentMixin):
    __tablename__ = "stock_entry_items"

    stock_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_entries.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    target_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    uom: Mapped[str | None] = mapped_column(String(140))
    conversion_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    stock_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # qty in stock UOM
    basic_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # incoming rate for receipts; ignored for issues (valuation applies)
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    serial_nos: Mapped[str | None] = mapped_column(Text)  # newline-separated, count == stock_qty
    batch_no: Mapped[str | None] = mapped_column(String(140))  # lot label (batched items)

    stock_entry: Mapped[StockEntry] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


class MaterialRequest(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin):
    """Source: erpnext/stock/doctype/material_request."""

    __tablename__ = "material_requests"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_material_request_name"),
        Index("ix_material_requests_company_docstatus", "company_id", "docstatus"),
    )

    material_request_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Purchase", server_default=text("'Purchase'")
    )
    schedule_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )  # Draft | Pending | Partially Ordered | Ordered | Cancelled
    per_ordered: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=0, server_default=text("0")
    )

    items: Mapped[list["MaterialRequestItem"]] = relationship(
        back_populates="material_request", cascade="all, delete-orphan", order_by="MaterialRequestItem.idx"
    )


class MaterialRequestItem(Base, DocumentMixin):
    __tablename__ = "material_request_items"

    material_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_requests.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    uom: Mapped[str | None] = mapped_column(String(140))
    conversion_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    stock_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # qty in stock UOM
    ordered_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # accrued from POs, in stock UOM
    schedule_date: Mapped[date | None] = mapped_column(Date)

    material_request: Mapped[MaterialRequest] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


class PurchaseReceipt(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin):
    """Source: erpnext/stock/doctype/purchase_receipt.

    On submit: SLE in (at the PO/receipt rate) and, under perpetual inventory,
    GL Dr Stock In Hand / Cr Stock Received But Not Billed.
    """

    __tablename__ = "purchase_receipts"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_purchase_receipt_name"),
        Index("ix_purchase_receipts_company_docstatus", "company_id", "docstatus"),
        Index("ix_purchase_receipts_supplier", "supplier_id"),
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    conversion_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    set_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    # Returns (Phase 3): a return PR sends goods back to the supplier — SLE out /
    # reverse SRBNB. return_against_id self-links to the original receipt.
    is_return: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    return_against_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_receipts.id")
    )
    supplier_delivery_note: Mapped[str | None] = mapped_column(String(140))  # supplier's DN ref
    total_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    grand_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    base_grand_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )  # Draft | To Bill | Completed | Cancelled
    per_billed: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=0, server_default=text("0")
    )

    items: Mapped[list["PurchaseReceiptItem"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", order_by="PurchaseReceiptItem.idx"
    )
    charges: Mapped[list["PurchaseReceiptCharge"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", order_by="PurchaseReceiptCharge.idx"
    )
    supplier = relationship("Supplier", lazy="joined", viewonly=True)

    @property
    def supplier_name(self) -> str | None:
        return self.supplier.supplier_name if self.supplier else None


class PurchaseReceiptItem(Base, DocumentMixin):
    __tablename__ = "purchase_receipt_items"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_receipts.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)  # ACCEPTED qty (txn UOM)
    uom: Mapped[str | None] = mapped_column(String(140))
    conversion_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    stock_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # accepted qty in stock UOM
    rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # QC split (Phase 3): rejected goods are received into a separate warehouse and
    # valued, but not billed; PO received_qty accrues qty + rejected_qty.
    rejected_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rejected_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    purchase_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_order_items.id", use_alter=True, name="fk_pri_po_item")
    )
    billed_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    serial_nos: Mapped[str | None] = mapped_column(Text)  # newline-separated, count == stock_qty
    batch_no: Mapped[str | None] = mapped_column(String(140))  # lot label (batched items)

    receipt: Mapped[PurchaseReceipt] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


class PurchaseReceiptCharge(Base, DocumentMixin):
    """Landed-cost line on a Purchase Receipt (freight / customs / insurance).

    Apportioned by item value into the incoming valuation at submit (no separate
    Landed Cost Voucher); ``account_id`` is the charge clearing/expense account
    credited on submit (e.g. "Expenses Included In Valuation"), later cleared by
    the actual freight bill.
    """

    __tablename__ = "purchase_receipt_charges"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_receipts.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    description: Mapped[str] = mapped_column(String(180), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # in the receipt's document currency

    receipt: Mapped[PurchaseReceipt] = relationship(back_populates="charges")


class DeliveryNote(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin):
    """Source: erpnext/stock/doctype/delivery_note.

    On submit: SLE out at moving-average valuation and, under perpetual
    inventory, GL Dr Cost of Goods Sold / Cr Stock In Hand (at valuation).
    """

    __tablename__ = "delivery_notes"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_delivery_note_name"),
        Index("ix_delivery_notes_company_docstatus", "company_id", "docstatus"),
        Index("ix_delivery_notes_customer", "customer_id"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    conversion_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    set_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    # Returns (Phase 3): a return DN takes goods back into stock — SLE in /
    # reverse COGS. return_against_id self-links to the original delivery.
    is_return: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    return_against_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_notes.id")
    )
    # Address & Contact (party is the customer; mirrors order/invoice A&C links)
    customer_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addresses.id", ondelete="SET NULL")
    )
    shipping_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addresses.id", ondelete="SET NULL")
    )
    contact_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    total_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_total: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    grand_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    base_grand_total: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )  # Draft | To Bill | Completed | Cancelled
    per_billed: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=0, server_default=text("0")
    )

    items: Mapped[list["DeliveryNoteItem"]] = relationship(
        back_populates="delivery_note", cascade="all, delete-orphan", order_by="DeliveryNoteItem.idx"
    )
    customer = relationship("Customer", lazy="joined", viewonly=True)

    @property
    def customer_name(self) -> str | None:
        return self.customer.customer_name if self.customer else None


class DeliveryNoteItem(Base, DocumentMixin):
    __tablename__ = "delivery_note_items"

    delivery_note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_notes.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    uom: Mapped[str | None] = mapped_column(String(140))
    conversion_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    stock_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # qty in stock UOM
    rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    amount: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_rate: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    base_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    sales_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_order_items.id", use_alter=True, name="fk_dni_so_item")
    )
    billed_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    serial_nos: Mapped[str | None] = mapped_column(Text)  # newline-separated, count == stock_qty
    batch_no: Mapped[str | None] = mapped_column(String(140))  # lot label (batched items)

    delivery_note: Mapped[DeliveryNote] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


class StockReconciliation(Base, DocumentMixin, CompanyScopedMixin, VoucherMixin):
    """Source: erpnext/stock/doctype/stock_reconciliation.

    Sets each (item, warehouse) row to an absolute target qty and valuation
    rate; on submit it posts only the DIFFERENCE vs the current Bin to the
    stock ledger and (under perpetual inventory) to the Stock Adjustment
    account. This is how opening balances and physical-count corrections enter
    the system, and the documented escape hatch when a cancellation is blocked
    because stock was since consumed at a different valuation.

    ``purpose`` is a label only ("Opening Stock" vs "Stock Reconciliation") —
    both behave identically (post the difference). We deliberately do NOT route
    Opening Stock to a separate Temporary Opening account (kept simple).
    """

    __tablename__ = "stock_reconciliations"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_stock_reconciliation_name"),
        Index("ix_stock_reconciliations_company_docstatus", "company_id", "docstatus"),
    )

    purpose: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Stock Reconciliation",
        server_default=text("'Stock Reconciliation'"),
    )
    set_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    difference_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # net value posted to Stock Adjustment on submit

    items: Mapped[list["StockReconciliationItem"]] = relationship(
        back_populates="reconciliation",
        cascade="all, delete-orphan",
        order_by="StockReconciliationItem.idx",
    )


class StockReconciliationItem(Base, DocumentMixin):
    __tablename__ = "stock_reconciliation_items"

    reconciliation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_reconciliations.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)  # target absolute qty
    uom: Mapped[str | None] = mapped_column(String(140))
    valuation_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # target absolute rate (0 = resolve current/item rate at submit)
    # snapshots captured at submit (before → after, for transparency)
    current_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    current_valuation_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    amount_difference: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    reconciliation: Mapped[StockReconciliation] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


SERVICE_CREDIT_STATUSES = ("Active", "Exhausted", "Expired")


class ServiceCredit(Base, DocumentMixin, CompanyScopedMixin):
    """A prepaid block of a *service* measured in units (e.g. 100 hours of
    support / AMC bought up front). Services aren't stock items, so they have no
    Bin; this is a dedicated, lightweight ledger that tracks purchased vs
    consumed units and exposes the remaining balance (the live credit).

    Drawn down by ``ServiceCreditUsage`` rows; ``consumed_qty`` is maintained as
    those are added. Deliberately simpler than the stock ledger — no warehouse,
    no valuation — because a service credit is just a depleting counter.
    """

    __tablename__ = "service_credits"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_service_credit_name"),
        Index("ix_service_credits_company", "company_id"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id")
    )
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    purchased_qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    consumed_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    uom: Mapped[str | None] = mapped_column(String(140))
    valid_upto: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Active", server_default=text("'Active'")
    )
    remarks: Mapped[str | None] = mapped_column(Text)
    # Accounting: the purchase (prepaid asset) is booked by the linked Purchase
    # Invoice; each usage posts Dr expense / Cr prepaid for qty*rate.
    purchase_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_invoices.id", ondelete="SET NULL")
    )
    prepaid_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id")
    )

    usages: Mapped[list["ServiceCreditUsage"]] = relationship(
        back_populates="service_credit",
        cascade="all, delete-orphan",
        order_by="ServiceCreditUsage.idx",
    )
    item = relationship("Item", lazy="joined", viewonly=True)
    supplier = relationship("Supplier", lazy="joined", viewonly=True)
    purchase_invoice = relationship("PurchaseInvoice", lazy="joined", viewonly=True)

    @property
    def purchase_invoice_name(self) -> str | None:
        return self.purchase_invoice.name if self.purchase_invoice else None

    @property
    def balance_qty(self) -> Decimal:
        return self.purchased_qty - self.consumed_qty

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None

    @property
    def supplier_name(self) -> str | None:
        return self.supplier.supplier_name if self.supplier else None


class ServiceCreditUsage(Base, DocumentMixin):
    __tablename__ = "service_credit_usages"

    service_credit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_credits.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    service_credit: Mapped[ServiceCredit] = relationship(back_populates="usages")


# Re-export for services that need the mixin reference
__all__ = [
    "Bin",
    "DeliveryNote",
    "DeliveryNoteItem",
    "InvoiceItemMixin",
    "Item",
    "ItemGroup",
    "ItemPrice",
    "MaterialRequest",
    "MaterialRequestItem",
    "Batch",
    "PriceList",
    "PurchaseReceipt",
    "PurchaseReceiptItem",
    "PurchaseReceiptCharge",
    "SerialNo",
    "SERIAL_NO_STATUSES",
    "StockEntry",
    "StockEntryItem",
    "StockLedgerEntry",
    "StockReconciliation",
    "StockReconciliationItem",
    "ServiceCredit",
    "ServiceCreditUsage",
    "Warehouse",
    "MATERIAL_REQUEST_TYPES",
    "SERVICE_CREDIT_STATUSES",
    "STOCK_ENTRY_PURPOSES",
    "STOCK_RECONCILIATION_PURPOSES",
    "VALUATION_METHODS",
]
