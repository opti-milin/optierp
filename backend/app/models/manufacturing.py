"""Module — Manufacturing (BOM → Work Order → Manufacture).

Source: erpnext/manufacturing, simplified for a single appliance distributor that does
light assembly / kitting (build a finished SKU from components + a little labour), not a
multi-line factory. See docs/MANUFACTURING_GAP_AND_PLAN.md.

* **BOM (Bill of Materials)** is the *recipe*: "1 Air-Cooler Combo = 1 cooler + 1 stand +
  1 remote + ₹200 assembly labour." A bespoke document because of the costing rollup and
  because it's the spec a Work Order consumes. It never touches stock or the GL by itself.
* **Work Order** is the *act of making it*: "make 10 Combos." A bespoke document that
  explodes the BOM × qty into required items and, on Finish, drives a **Manufacture** Stock
  Entry that consumes the raws and produces the finished good at input cost.

Neither model re-implements valuation or GL — the Manufacture Stock Entry reuses the
existing Stock Ledger / Moving-Average valuation / perpetual-inventory GL (same principle
as Assets driving a Journal Entry). Operations / Workstations / Routing / Job Cards /
Production Plan (MRP) / multi-level explosion are deliberately out of scope (master §2).

Company-scoped tables filter by ``company_id`` explicitly (no RLS, mirroring the rest of
the accounting / stock / assets tables).
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

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

# Work Order lifecycle (master §3). "Not Started" once submitted with nothing produced,
# "In Process" after a partial finish (or a WIP transfer), "Completed" at full qty.
WORK_ORDER_STATUSES = (
    "Draft",
    "Not Started",
    "In Process",
    "Completed",
    "Stopped",
    "Cancelled",
)

ZERO = Decimal("0")


class BOM(Base, DocumentMixin, CompanyScopedMixin):
    """A Bill of Materials — the recipe for one finished good (bespoke document).

    Created Draft, then Submitted to make it usable by Work Orders. ``quantity`` is the
    batch the recipe yields (usually 1). ``raw_material_cost`` / ``total_cost`` are a
    snapshot computed on save from the component items' current valuation; they can be
    refreshed on demand ("Update Cost"). ``cost_per_unit`` is derived, never stored.

    ``is_default`` marks the BOM used by default for its production item — at most one
    active default per item (enforced in the service).
    """

    __tablename__ = "boms"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_bom_name"),
        Index("ix_boms_company_item", "company_id", "production_item_id"),
        Index("ix_boms_company_docstatus", "company_id", "docstatus"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)  # naming-series doc number
    production_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    uom: Mapped[str | None] = mapped_column(String(140))  # FG UOM (defaults to the item's stock UOM)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=1, server_default=text("1")
    )  # batch size this recipe yields
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # flat labour / overhead for the whole batch (master §2 — no named operations).
    operating_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # costing snapshot (recomputed on save / Update Cost)
    raw_material_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["BOMItem"]] = relationship(
        back_populates="bom", cascade="all, delete-orphan", order_by="BOMItem.idx", lazy="selectin"
    )
    production_item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def production_item_code(self) -> str | None:
        return self.production_item.item_code if self.production_item else None

    @property
    def production_item_name(self) -> str | None:
        return self.production_item.item_name if self.production_item else None

    @property
    def cost_per_unit(self) -> Decimal:
        return (self.total_cost / self.quantity) if self.quantity else ZERO


class BOMItem(Base, DocumentMixin):
    """One component line of a BOM (child of BOM).

    ``qty`` is per BOM batch (in ``uom``); ``stock_qty = qty × conversion_factor`` is in the
    component's stock UOM. ``rate`` is the component's current valuation (per stock unit) and
    ``amount = stock_qty × rate`` feeds the BOM's raw-material cost.
    """

    __tablename__ = "bom_items"
    __table_args__ = (Index("ix_bom_items_item", "item_id"),)

    bom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("boms.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    uom: Mapped[str | None] = mapped_column(String(140))
    conversion_factor: Mapped[Decimal] = mapped_column(
        Numeric(21, 9), nullable=False, default=1, server_default=text("1")
    )
    stock_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # per stock unit, sourced from the item's valuation
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )

    bom: Mapped[BOM] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


class WorkOrder(Base, DocumentMixin, CompanyScopedMixin):
    """An order to manufacture N units of a finished good per a BOM (bespoke document).

    Created Draft with its ``required_items`` exploded from the BOM × ``qty``. On Submit it
    becomes *Not Started*; each **Finish** posts a Manufacture Stock Entry (consume raws,
    produce FG at input cost) and advances ``produced_qty`` → *In Process* (partial) or
    *Completed* (full). ``operating_cost`` (flat labour, from the BOM scaled to ``qty``) is
    folded into the finished good's valuation at finish.
    """

    __tablename__ = "work_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_work_order_name"),
        Index("ix_work_orders_company_docstatus", "company_id", "docstatus"),
        Index("ix_work_orders_company_status", "company_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)  # naming-series doc number
    production_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    bom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("boms.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)  # FG units to make (stock UOM)
    produced_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    material_transferred_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )  # where raws are consumed from (default for all components)
    wip_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )  # optional work-in-progress warehouse (transfer step)
    fg_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )  # where the finished good lands
    # lean default: consume raws straight from source on finish (no WIP transfer step).
    skip_transfer: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    operating_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # flat labour/overhead for the whole order (folded into FG cost at finish)
    operating_cost_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )  # expense/clearing account credited when capitalising operating cost into FG (perpetual GL)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    planned_start_date: Mapped[date | None] = mapped_column(Date)
    planned_end_date: Mapped[date | None] = mapped_column(Date)
    actual_start_date: Mapped[date | None] = mapped_column(Date)
    actual_end_date: Mapped[date | None] = mapped_column(Date)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_orders.id")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["WorkOrderItem"]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        order_by="WorkOrderItem.idx",
        lazy="selectin",
    )
    production_item = relationship("Item", lazy="joined", viewonly=True)
    bom = relationship("BOM", lazy="joined", viewonly=True)

    @property
    def production_item_code(self) -> str | None:
        return self.production_item.item_code if self.production_item else None

    @property
    def production_item_name(self) -> str | None:
        return self.production_item.item_name if self.production_item else None

    @property
    def bom_name(self) -> str | None:
        return self.bom.name if self.bom else None


class WorkOrderItem(Base, DocumentMixin):
    """One required component of a Work Order (child), exploded from the BOM × qty.

    ``required_qty`` is in the component's stock UOM. ``consumed_qty`` accrues as Finishes
    post their Manufacture Stock Entries; ``transferred_qty`` accrues if the optional WIP
    transfer step is used.
    """

    __tablename__ = "work_order_items"
    __table_args__ = (Index("ix_work_order_items_item", "item_id"),)

    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    required_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # in stock UOM
    transferred_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    consumed_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # snapshot at creation (BOM rate)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    work_order: Mapped[WorkOrder] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


__all__ = [
    "BOM",
    "BOMItem",
    "WorkOrder",
    "WorkOrderItem",
    "WORK_ORDER_STATUSES",
]
