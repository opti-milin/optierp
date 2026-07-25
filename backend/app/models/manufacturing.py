"""Module — Manufacturing (BOM → Work Order → Manufacture / Job Card).

Source: erpnext/manufacturing, simplified for discrete / light-assembly manufacturing.
See docs/MANUFACTURING_GAP_AND_PLAN.md.

* **BOM** — recipe: components + operating cost (flat + operation time×rate) + scrap.
  Multi-level / phantom / scrap as Phase 1; operations as Phase 2.
* **Work Order** — make N; Finish posts Manufacture SE. Optional operations → Job Cards.
* **Operation / Workstation / Routing** — engine masters (RLS). Job Card is bespoke.

Company-scoped bespoke tables filter by ``company_id`` explicitly (no RLS). Engine masters
(Operation / Workstation / Routing) use RLS like Item Alternative.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
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

WORK_ORDER_STATUSES = (
    "Draft",
    "Not Started",
    "In Process",
    "Completed",
    "Stopped",
    "Cancelled",
)
WORK_ORDER_OPERATION_STATUSES = ("Pending", "Work In Progress", "Completed")
JOB_CARD_STATUSES = ("Open", "Work In Progress", "Completed", "Cancelled")

ZERO = Decimal("0")


# --- Engine masters (Operation / Workstation / Routing) ------------------------------


class Operation(Base, DocumentMixin, CompanyScopedMixin):
    """A named shop-floor process step (e.g. Cut, Weld, Paint)."""

    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("company_id", "operation_name", name="uq_operation_name"),)

    operation_name: Mapped[str] = mapped_column(String(140), nullable=False)
    default_hour_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    description: Mapped[str | None] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Workstation(Base, DocumentMixin, CompanyScopedMixin):
    """A machine / bench with an hour rate and daily working hours (soft capacity)."""

    __tablename__ = "workstations"
    __table_args__ = (
        UniqueConstraint("company_id", "workstation_name", name="uq_workstation_name"),
    )

    workstation_name: Mapped[str] = mapped_column(String(140), nullable=False)
    hour_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    working_hours: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=8, server_default=text("8")
    )
    description: Mapped[str | None] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Routing(Base, DocumentMixin, CompanyScopedMixin):
    """A reusable ordered list of Operations (optionally with Workstations / times)."""

    __tablename__ = "routings"
    __table_args__ = (UniqueConstraint("company_id", "routing_name", name="uq_routing_name"),)

    routing_name: Mapped[str] = mapped_column(String(140), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    operations: Mapped[list["RoutingOperation"]] = relationship(
        back_populates="routing",
        cascade="all, delete-orphan",
        order_by="RoutingOperation.idx",
        lazy="selectin",
    )


class RoutingOperation(Base, DocumentMixin):
    """One step in a Routing (child)."""

    __tablename__ = "routing_operations"
    __table_args__ = (UniqueConstraint("routing_id", "idx", name="uq_routing_operation_idx"),)

    routing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routings.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    workstation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workstations.id")
    )
    time_in_mins: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    routing: Mapped[Routing] = relationship(back_populates="operations")
    operation = relationship("Operation", lazy="joined", viewonly=True)
    workstation = relationship("Workstation", lazy="joined", viewonly=True)

    @property
    def operation_name(self) -> str | None:
        return self.operation.operation_name if self.operation else None

    @property
    def workstation_name(self) -> str | None:
        return self.workstation.workstation_name if self.workstation else None


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
    # Phantom: when used as a component of a parent BOM / Work Order, explode through to
    # this BOM's own components — the phantom item itself is never stocked on that WO.
    is_phantom: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # flat labour + Σ BOM operation (time × hour_rate); folded into total_cost.
    operating_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    routing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routings.id")
    )
    # costing snapshot (recomputed on save / Update Cost)
    raw_material_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # Σ scrap recovery (qty × rate) — netted out of total_cost
    scrap_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["BOMItem"]] = relationship(
        back_populates="bom", cascade="all, delete-orphan", order_by="BOMItem.idx", lazy="selectin"
    )
    scrap_items: Mapped[list["BOMScrapItem"]] = relationship(
        back_populates="bom",
        cascade="all, delete-orphan",
        order_by="BOMScrapItem.idx",
        lazy="selectin",
    )
    operations: Mapped[list["BOMOperation"]] = relationship(
        back_populates="bom",
        cascade="all, delete-orphan",
        order_by="BOMOperation.idx",
        lazy="selectin",
    )
    # foreign_keys required: Item.default_bom_id also FKs to boms (circular path).
    production_item = relationship(
        "Item", foreign_keys=[production_item_id], lazy="joined", viewonly=True
    )
    routing = relationship("Routing", foreign_keys=[routing_id], lazy="joined", viewonly=True)

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
    # When true, Finish may swap this component for another stock item (alternate).
    allow_alternative_item: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    bom: Mapped[BOM] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


class BOMScrapItem(Base, DocumentMixin):
    """Expected scrap / by-product output of a BOM batch (child of BOM).

    ``qty`` is per BOM batch; ``rate`` is the optional recovery value per stock unit.
    ``amount = stock_qty × rate`` is netted out of the BOM's total cost. On Work Order
    Finish these rows become extra finished Stock Entry lines (scrap lands in stock).
    """

    __tablename__ = "bom_scrap_items"
    __table_args__ = (
        Index("ix_bom_scrap_items_bom", "bom_id"),
        Index("ix_bom_scrap_items_item", "item_id"),
    )

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
    )  # recovery value per stock unit
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    stock_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )  # where scrap lands on Finish (falls back to FG warehouse)

    bom: Mapped[BOM] = relationship(back_populates="scrap_items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


class BOMOperation(Base, DocumentMixin):
    """One process step on a BOM (child). Cost = time_in_mins/60 × hour_rate."""

    __tablename__ = "bom_operations"
    __table_args__ = (Index("ix_bom_operations_bom", "bom_id"),)

    bom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("boms.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    workstation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workstations.id")
    )
    time_in_mins: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    hour_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    operating_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    description: Mapped[str | None] = mapped_column(Text)

    bom: Mapped[BOM] = relationship(back_populates="operations")
    operation = relationship("Operation", lazy="joined", viewonly=True)
    workstation = relationship("Workstation", lazy="joined", viewonly=True)

    @property
    def operation_name(self) -> str | None:
        return self.operation.operation_name if self.operation else None

    @property
    def workstation_name(self) -> str | None:
        return self.workstation.workstation_name if self.workstation else None


class WorkOrder(Base, DocumentMixin, CompanyScopedMixin):
    """An order to manufacture N units of a finished good per a BOM (bespoke document).

    Created Draft with its ``required_items`` exploded from the BOM × ``qty``. On Submit it
    becomes *Not Started* and (when the BOM has operations) Job Cards are created. Each
    **Finish** posts a Manufacture Stock Entry. ``operating_cost`` (from the BOM scaled to
    ``qty``) is folded into the finished good's valuation at finish.
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
    production_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_plans.id")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["WorkOrderItem"]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        order_by="WorkOrderItem.idx",
        lazy="selectin",
    )
    operations: Mapped[list["WorkOrderOperation"]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        order_by="WorkOrderOperation.idx",
        lazy="selectin",
    )
    production_item = relationship(
        "Item", foreign_keys=[production_item_id], lazy="joined", viewonly=True
    )
    bom = relationship("BOM", foreign_keys=[bom_id], lazy="joined", viewonly=True)

    @property
    def production_item_code(self) -> str | None:
        return self.production_item.item_code if self.production_item else None

    @property
    def production_item_name(self) -> str | None:
        return self.production_item.item_name if self.production_item else None

    @property
    def production_has_serial_no(self) -> bool:
        return bool(self.production_item.has_serial_no) if self.production_item else False

    @property
    def production_has_batch_no(self) -> bool:
        return bool(self.production_item.has_batch_no) if self.production_item else False

    @property
    def bom_name(self) -> str | None:
        return self.bom.name if self.bom else None


class WorkOrderItem(Base, DocumentMixin):
    """One required component of a Work Order (child), exploded from the BOM × qty.

    ``required_qty`` is in the component's stock UOM. ``consumed_qty`` accrues from
    Material Consumption and Finish Manufacture entries; ``transferred_qty`` from WIP transfer.
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
    allow_alternative_item: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    work_order: Mapped[WorkOrder] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None

    @property
    def has_serial_no(self) -> bool:
        return bool(self.item.has_serial_no) if self.item else False

    @property
    def has_batch_no(self) -> bool:
        return bool(self.item.has_batch_no) if self.item else False


class WorkOrderOperation(Base, DocumentMixin):
    """One process step on a Work Order, copied from the BOM (times scaled by WO qty)."""

    __tablename__ = "work_order_operations"
    __table_args__ = (
        Index("ix_work_order_operations_wo", "work_order_id"),
        Index("ix_work_order_operations_workstation", "workstation_id"),
    )

    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    workstation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workstations.id")
    )
    time_in_mins: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    hour_rate: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    planned_operating_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    completed_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Pending", server_default=text("'Pending'")
    )
    description: Mapped[str | None] = mapped_column(Text)

    work_order: Mapped[WorkOrder] = relationship(back_populates="operations")
    operation = relationship("Operation", lazy="joined", viewonly=True)
    workstation = relationship("Workstation", lazy="joined", viewonly=True)

    @property
    def operation_name(self) -> str | None:
        return self.operation.operation_name if self.operation else None

    @property
    def workstation_name(self) -> str | None:
        return self.workstation.workstation_name if self.workstation else None


class JobCard(Base, DocumentMixin, CompanyScopedMixin):
    """Per Work Order × Operation shop-floor tracking doc (bespoke transaction)."""

    __tablename__ = "job_cards"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_job_card_name"),
        UniqueConstraint("work_order_operation_id", name="uq_job_card_wo_operation"),
        Index("ix_job_cards_company_status", "company_id", "status"),
        Index("ix_job_cards_work_order", "work_order_id"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    work_order_operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_order_operations.id"), nullable=False
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    workstation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workstations.id")
    )
    for_quantity: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    total_completed_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    time_in_mins: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Open", server_default=text("'Open'")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    time_logs: Mapped[list["JobCardTimeLog"]] = relationship(
        back_populates="job_card",
        cascade="all, delete-orphan",
        order_by="JobCardTimeLog.idx",
        lazy="selectin",
    )
    work_order = relationship("WorkOrder", foreign_keys=[work_order_id], lazy="joined", viewonly=True)
    operation = relationship("Operation", lazy="joined", viewonly=True)
    workstation = relationship("Workstation", lazy="joined", viewonly=True)

    @property
    def work_order_name(self) -> str | None:
        return self.work_order.name if self.work_order else None

    @property
    def operation_name(self) -> str | None:
        return self.operation.operation_name if self.operation else None

    @property
    def workstation_name(self) -> str | None:
        return self.workstation.workstation_name if self.workstation else None


class JobCardTimeLog(Base, DocumentMixin):
    """One start/stop time segment on a Job Card."""

    __tablename__ = "job_card_time_logs"
    __table_args__ = (Index("ix_job_card_time_logs_jc", "job_card_id"),)

    job_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_cards.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    from_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    to_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_in_mins: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    completed_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    job_card: Mapped[JobCard] = relationship(back_populates="time_logs")


# --- Production Plan (Phase 3 MRP-I) -------------------------------------------------


PRODUCTION_PLAN_STATUSES = ("Draft", "Submitted", "Cancelled", "Completed")
PRODUCTION_PLAN_GET_FROM = ("Sales Order", "Manual")


class ProductionPlan(Base, DocumentMixin, CompanyScopedMixin):
    """Aggregate FG demand (from Sales Orders or manual rows) → proposed WOs + MRs."""

    __tablename__ = "production_plans"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_production_plan_name"),
        Index("ix_production_plans_company_docstatus", "company_id", "docstatus"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    from_date: Mapped[date | None] = mapped_column(Date)
    to_date: Mapped[date | None] = mapped_column(Date)
    get_items_from: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Sales Order", server_default=text("'Sales Order'")
    )
    fg_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    work_orders_created: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    material_requests_created: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["ProductionPlanItem"]] = relationship(
        back_populates="production_plan",
        cascade="all, delete-orphan",
        order_by="ProductionPlanItem.idx",
        lazy="selectin",
    )
    material_requests: Mapped[list["ProductionPlanMaterialRequest"]] = relationship(
        back_populates="production_plan",
        cascade="all, delete-orphan",
        order_by="ProductionPlanMaterialRequest.idx",
        lazy="selectin",
    )


class ProductionPlanItem(Base, DocumentMixin):
    """One finished-good demand row on a Production Plan."""

    __tablename__ = "production_plan_items"
    __table_args__ = (
        Index("ix_production_plan_items_plan", "production_plan_id"),
        Index("ix_production_plan_items_item", "item_id"),
    )

    production_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_plans.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    bom_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("boms.id"))
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_orders.id")
    )
    sales_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_order_items.id")
    )
    planned_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    pending_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    ordered_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # qty already covered by a created Work Order from this plan
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    planned_start_date: Mapped[date | None] = mapped_column(Date)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id")
    )
    description: Mapped[str | None] = mapped_column(Text)

    production_plan: Mapped[ProductionPlan] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)
    bom = relationship("BOM", foreign_keys=[bom_id], lazy="joined", viewonly=True)
    sales_order = relationship("SalesOrder", foreign_keys=[sales_order_id], lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None

    @property
    def bom_name(self) -> str | None:
        return self.bom.name if self.bom else None

    @property
    def sales_order_name(self) -> str | None:
        return self.sales_order.name if self.sales_order else None


class ProductionPlanMaterialRequest(Base, DocumentMixin):
    """Proposed raw-material shortfall line (netted vs stock); may link to a created MR."""

    __tablename__ = "production_plan_material_requests"
    __table_args__ = (Index("ix_production_plan_mrs_plan", "production_plan_id"),)

    production_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_plans.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id")
    )
    required_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    available_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    shortfall_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    material_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_requests.id")
    )

    production_plan: Mapped[ProductionPlan] = relationship(back_populates="material_requests")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


# --- Subcontract Job (Phase 4) -------------------------------------------------------


SUBCONTRACT_JOB_STATUSES = (
    "Draft",
    "Open",
    "Materials Sent",
    "Partially Received",
    "Completed",
    "Cancelled",
)


class SubcontractJob(Base, DocumentMixin, CompanyScopedMixin):
    """Send materials to a job-work vendor and receive the finished good back.

    Lean substitute for ERPNext's dual Subcontracting Order + Receipt: one job, one
    Send Stock Entry, one Subcontract Receipt (service cost folded into FG valuation).
    """

    __tablename__ = "subcontract_jobs"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_subcontract_job_name"),
        Index("ix_subcontract_jobs_company_docstatus", "company_id", "docstatus"),
        Index("ix_subcontract_jobs_company_status", "company_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False
    )
    production_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    bom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("boms.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    sent_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    received_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    source_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    supplier_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    fg_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    service_cost: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    service_cost_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["SubcontractJobItem"]] = relationship(
        back_populates="subcontract_job",
        cascade="all, delete-orphan",
        order_by="SubcontractJobItem.idx",
        lazy="selectin",
    )
    production_item = relationship(
        "Item", foreign_keys=[production_item_id], lazy="joined", viewonly=True
    )
    bom = relationship("BOM", foreign_keys=[bom_id], lazy="joined", viewonly=True)
    supplier = relationship("Supplier", lazy="joined", viewonly=True)

    @property
    def production_item_code(self) -> str | None:
        return self.production_item.item_code if self.production_item else None

    @property
    def production_item_name(self) -> str | None:
        return self.production_item.item_name if self.production_item else None

    @property
    def bom_name(self) -> str | None:
        return self.bom.name if self.bom else None

    @property
    def supplier_name(self) -> str | None:
        return self.supplier.supplier_name if self.supplier else None


class SubcontractJobItem(Base, DocumentMixin):
    """One BOM component required for a Subcontract Job (child)."""

    __tablename__ = "subcontract_job_items"
    __table_args__ = (Index("ix_subcontract_job_items_item", "item_id"),)

    subcontract_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subcontract_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    required_qty: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    sent_qty: Mapped[Decimal] = mapped_column(
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
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )

    subcontract_job: Mapped[SubcontractJob] = relationship(back_populates="items")
    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


__all__ = [
    "Operation",
    "Workstation",
    "Routing",
    "RoutingOperation",
    "BOM",
    "BOMItem",
    "BOMScrapItem",
    "BOMOperation",
    "WorkOrder",
    "WorkOrderItem",
    "WorkOrderOperation",
    "JobCard",
    "JobCardTimeLog",
    "ProductionPlan",
    "ProductionPlanItem",
    "ProductionPlanMaterialRequest",
    "SubcontractJob",
    "SubcontractJobItem",
    "WORK_ORDER_STATUSES",
    "WORK_ORDER_OPERATION_STATUSES",
    "JOB_CARD_STATUSES",
    "PRODUCTION_PLAN_STATUSES",
    "PRODUCTION_PLAN_GET_FROM",
    "SUBCONTRACT_JOB_STATUSES",
]
