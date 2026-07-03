"""Module — Assets (fixed-asset register + depreciation).

Source: erpnext/assets, simplified for a single appliance distributor (single finance
book, India GAAP). See docs/ASSETS_GAP_AND_PLAN.md.

* **Asset Category** and **Location** are engine masters (recipe cards in
  app.registry.descriptors) — pure config, no GL.
* **Asset** is a bespoke document: gross value + dates + a generated **depreciation
  schedule** (child). The schedule rows are the snapshot of the depreciation plan; a
  scheduled job posts each due row as a Journal Entry (Dr Depreciation / Cr Accumulated
  Depreciation) reusing the existing GL/JE — the Assets module drives postings, it never
  re-implements them.

Company-scoped tables filter by ``company_id`` explicitly (no RLS, mirroring the rest of
the accounting/share tables).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
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

# Depreciation methods. Phase 1 ships Straight Line + Manual; Written Down Value
# (declining balance) is generated in Phase 2 (the option is reserved here).
DEPRECIATION_METHODS = ("Straight Line", "Written Down Value", "Manual")
# Asset lifecycle.
ASSET_STATUSES = (
    "Draft",
    "Submitted",
    "Partially Depreciated",
    "Fully Depreciated",
    "Sold",
    "Scrapped",
    "Cancelled",
)


class AssetCategory(Base, DocumentMixin, CompanyScopedMixin):
    """Defaults for a class of assets — engine master.

    Holds the depreciation policy (method + how many entries, how often, salvage) and
    the three GL accounts every asset of this class posts to. ERPNext keeps these
    accounts in a per-company / per-finance-book child table; with one company and one
    book we store them directly (master §2 simplification).
    """

    __tablename__ = "asset_categories"
    __table_args__ = (UniqueConstraint("company_id", "category_name", name="uq_asset_category_name"),)

    category_name: Mapped[str] = mapped_column(String(140), nullable=False)
    depreciation_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Straight Line", server_default=text("'Straight Line'")
    )  # Straight Line | Written Down Value | Manual
    # number of depreciation entries over the asset's life × months between each
    # (e.g. 60 × 1 = monthly for 5 years; 5 × 12 = annual for 5 years).
    total_number_of_depreciations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    frequency_of_depreciation_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    salvage_value_percent: Mapped[Decimal] = mapped_column(
        Numeric(9, 4), nullable=False, default=0, server_default=text("0")
    )  # residual value as a % of gross; depreciation never goes below it
    # Written Down Value: an explicit statutory rate (e.g. India IT-Act 15%/40%). When set,
    # it replaces the rate derived from salvage. Null = derive from salvage over the life.
    rate_of_depreciation: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    # day-weight each period's depreciation by its actual day count (Feb < Jan), so an
    # asset gets depreciation proportional to the days in each period (ERPNext daily pro-rata).
    daily_prorata: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # land / freehold property: held at cost, never depreciated (no schedule). Its value
    # only changes via a Value Adjustment (revaluation up = appreciation, down = impairment).
    is_non_depreciable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    fixed_asset_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    depreciation_expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    accumulated_depreciation_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Location(Base, DocumentMixin, CompanyScopedMixin):
    """Where an asset physically sits — engine master.

    ERPNext's Location is a tree with geolocation; we keep a flat lightweight master
    (master §2). Asset Movement (Phase 2) will retarget an asset's location.
    """

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("company_id", "location_name", name="uq_location_name"),)

    location_name: Mapped[str] = mapped_column(String(140), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Asset(Base, DocumentMixin, CompanyScopedMixin):
    """One fixed asset — bespoke document.

    Created Draft (the depreciation schedule is generated at the same time so it can be
    previewed), then Submitted to make it live. A daily job posts each due schedule row;
    the asset flips to Partially / Fully Depreciated as rows post. Book value is derived
    (``gross_purchase_amount`` − accumulated of the *posted* rows), never stored — so it
    can't drift from the ledger.
    """

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_asset_name"),)

    name: Mapped[str] = mapped_column(String(140), nullable=False)  # naming-series doc number
    asset_name: Mapped[str] = mapped_column(String(140), nullable=False)  # human label
    asset_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_categories.id"), nullable=False
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id")
    )
    custodian: Mapped[str | None] = mapped_column(String(140))  # who holds it (free text)
    gross_purchase_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # opening accumulated depreciation for an asset onboarded part-way through its life
    # (default 0 for a brand-new purchase). Reduces the depreciable base.
    opening_accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    # net change to accumulated depreciation from Value Adjustments (Phase 3): a
    # write-down adds (book value falls), a write-up subtracts. Part of book value.
    accumulated_depreciation_adjustment: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    purchase_date: Mapped[date | None] = mapped_column(Date)
    available_for_use_date: Mapped[date] = mapped_column(Date, nullable=False)  # depreciation starts here
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    # Disposal (Phase 2) — set when the asset is sold/scrapped; depreciation then halts.
    disposal_date: Mapped[date | None] = mapped_column(Date)
    disposal_type: Mapped[str | None] = mapped_column(String(10))  # Sell | Scrap
    disposal_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )  # sale proceeds (0 for scrap)
    gain_loss_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(21, 6)
    )  # proceeds − book value: positive = gain, negative = loss
    disposal_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id")
    )

    category = relationship("AssetCategory", lazy="joined", viewonly=True)
    location = relationship("Location", lazy="joined", viewonly=True)
    schedule: Mapped[list["AssetDepreciationSchedule"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="AssetDepreciationSchedule.idx",
        lazy="selectin",
    )
    movements: Mapped[list["AssetMovement"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="AssetMovement.movement_date.desc(), AssetMovement.creation.desc()",
        lazy="selectin",
    )

    @property
    def category_name(self) -> str | None:
        return self.category.category_name if self.category else None

    @property
    def location_name(self) -> str | None:
        return self.location.location_name if self.location else None

    @property
    def depreciation_method(self) -> str | None:
        return self.category.depreciation_method if self.category else None

    @property
    def accumulated_depreciation(self) -> Decimal:
        """Depreciation booked so far = opening + posted rows + value adjustments."""
        posted = sum(
            (r.depreciation_amount for r in self.schedule if r.posted), Decimal("0")
        )
        return (
            self.opening_accumulated_depreciation + posted + self.accumulated_depreciation_adjustment
        )

    @property
    def book_value(self) -> Decimal:
        return self.gross_purchase_amount - self.accumulated_depreciation


class AssetDepreciationSchedule(Base, DocumentMixin):
    """One planned depreciation entry for an asset (child of Asset).

    ``accumulated_depreciation`` is the *planned* cumulative (including the asset's
    opening) after this row. ``posted`` + ``journal_entry_id`` are set when the job
    books the entry — the posted flag is the idempotency guard (a posted row is never
    re-posted).
    """

    __tablename__ = "asset_depreciation_schedules"

    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    schedule_date: Mapped[date] = mapped_column(Date, nullable=False)  # when this entry posts
    depreciation_amount: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(21, 6), nullable=False, default=0, server_default=text("0")
    )
    posted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    posted_date: Mapped[date | None] = mapped_column(Date)
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id")
    )

    asset: Mapped[Asset] = relationship(back_populates="schedule")


class AssetMovement(Base, DocumentMixin, CompanyScopedMixin):
    """A transfer of an asset's location and/or custodian (no GL) — Phase 2.

    A lightweight history row written each time an asset is moved; ERPNext's Asset
    Movement with its multi-asset child grid is collapsed to one row per move (master §2).
    """

    __tablename__ = "asset_movements"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)
    from_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id")
    )
    to_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id")
    )
    from_custodian: Mapped[str | None] = mapped_column(String(140))
    to_custodian: Mapped[str | None] = mapped_column(String(140))

    asset: Mapped[Asset] = relationship(back_populates="movements")
    from_location = relationship("Location", foreign_keys=[from_location_id], lazy="joined", viewonly=True)
    to_location = relationship("Location", foreign_keys=[to_location_id], lazy="joined", viewonly=True)

    @property
    def from_location_name(self) -> str | None:
        return self.from_location.location_name if self.from_location else None

    @property
    def to_location_name(self) -> str | None:
        return self.to_location.location_name if self.to_location else None


# Phase 3 lean log master (engine-served, no GL). ONE master covers both preventive
# maintenance AND breakdown repairs — they were near-identical records, so the "Repair"
# kind is just a maintenance_type rather than a second DocType (master §2 simplification;
# ERPNext's separate Asset Repair + maintenance teams/tasks/SLAs are dropped). A repair
# that should hit the books is a normal Journal Entry, kept separate.
MAINTENANCE_TYPES = ("Preventive", "Calibration", "Inspection", "Repair", "Other")
MAINTENANCE_STATUSES = ("Open", "Completed", "Cancelled")
MAINTENANCE_PERIODICITY = ("One-time", "Monthly", "Quarterly", "Half-yearly", "Yearly")


class AssetMaintenance(Base, DocumentMixin, CompanyScopedMixin):
    """A maintenance / repair log entry for an asset — engine master (auto-numbered, no GL)."""

    __tablename__ = "asset_maintenances"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_asset_maintenance_name"),)

    name: Mapped[str] = mapped_column(String(140), nullable=False)  # series doc number
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    maintenance_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Preventive", server_default=text("'Preventive'")
    )  # Preventive | Calibration | Inspection | Repair | Other
    maintenance_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Open", server_default=text("'Open'")
    )  # Open | Completed | Cancelled
    # scheduling: how often this recurs + when it's next due + who's responsible. The
    # Maintenance Due report surfaces Open items by next_due_date (overdue first).
    periodicity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="One-time", server_default=text("'One-time'")
    )
    next_due_date: Mapped[date | None] = mapped_column(Date)
    assigned_to: Mapped[str | None] = mapped_column(String(140))

    asset = relationship("Asset", lazy="joined", viewonly=True)

    @property
    def asset_name(self) -> str | None:
        return self.asset.asset_name if self.asset else None
