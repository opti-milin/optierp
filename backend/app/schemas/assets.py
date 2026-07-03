"""Assets module schemas.

Asset Category and Location are served by the metadata engine (no schemas here);
these cover the bespoke Asset document, its depreciation schedule rows, and the result
of a (manual or scheduled) depreciation-posting run.
"""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel


class ManualScheduleRowIn(BaseModel):
    """One user-supplied depreciation row (Manual method only)."""

    schedule_date: date
    depreciation_amount: Decimal = Field(ge=0)


class AssetCreate(BaseModel):
    asset_name: str = Field(min_length=1)
    asset_category_id: uuid.UUID
    location_id: uuid.UUID | None = None
    custodian: str | None = None
    gross_purchase_amount: Decimal = Field(gt=0)
    opening_accumulated_depreciation: Decimal = Field(ge=0, default=Decimal("0"))
    purchase_date: date | None = None
    available_for_use_date: date
    remarks: str | None = None
    # required only when the category's method is Manual
    manual_schedule: list[ManualScheduleRowIn] | None = None


class AssetDisposeIn(BaseModel):
    disposal_type: str  # Sell | Scrap
    disposal_date: date
    sale_amount: Decimal = Field(ge=0, default=Decimal("0"))  # required > 0 for Sell
    proceeds_account_id: uuid.UUID | None = None  # bank/cash/receivable — required for Sell
    gain_loss_account_id: uuid.UUID  # Gain/Loss on Asset Disposal account


class AssetMoveIn(BaseModel):
    movement_date: date
    to_location_id: uuid.UUID | None = None
    to_custodian: str | None = None


class AssetValueAdjustIn(BaseModel):
    adjustment_date: date
    new_asset_value: Decimal = Field(ge=0)  # the asset's new book value after revaluation
    difference_account_id: uuid.UUID  # impairment/expense (write-down) or surplus (write-up)


class AssetCostComponentIn(BaseModel):
    """One cost that goes into a capitalised asset (credited to its source account)."""

    description: str
    amount: Decimal = Field(gt=0)
    account_id: uuid.UUID  # source account credited (e.g. CWIP, Stock In Hand, a labour expense, Bank)


class AssetCapitalizeIn(BaseModel):
    """Build a new asset by capitalising costed components into the Fixed Asset account."""

    asset_name: str = Field(min_length=1)
    asset_category_id: uuid.UUID
    location_id: uuid.UUID | None = None
    custodian: str | None = None
    posting_date: date
    available_for_use_date: date | None = None  # defaults to posting_date
    remarks: str | None = None
    components: list[AssetCostComponentIn] = Field(min_length=1)


class AssetMovementResponse(ORMModel):
    id: uuid.UUID
    movement_date: date
    from_location_name: str | None
    to_location_name: str | None
    from_custodian: str | None
    to_custodian: str | None


class AssetScheduleRowResponse(ORMModel):
    id: uuid.UUID
    idx: int
    schedule_date: date
    depreciation_amount: Decimal
    accumulated_depreciation: Decimal
    posted: bool
    posted_date: date | None
    journal_entry_id: uuid.UUID | None


class AssetResponse(DocumentMeta):
    name: str
    asset_name: str
    asset_category_id: uuid.UUID
    category_name: str | None
    location_id: uuid.UUID | None
    location_name: str | None
    depreciation_method: str | None
    custodian: str | None
    gross_purchase_amount: Decimal
    opening_accumulated_depreciation: Decimal
    accumulated_depreciation: Decimal
    book_value: Decimal
    purchase_date: date | None
    available_for_use_date: date
    status: str
    remarks: str | None
    disposal_date: date | None
    disposal_type: str | None
    disposal_amount: Decimal
    gain_loss_amount: Decimal | None
    disposal_journal_entry_id: uuid.UUID | None
    company_id: uuid.UUID
    schedule: list[AssetScheduleRowResponse]
    movements: list[AssetMovementResponse]


class AssetListItem(ORMModel):
    id: uuid.UUID
    name: str
    asset_name: str
    category_name: str | None
    location_name: str | None
    gross_purchase_amount: Decimal
    accumulated_depreciation: Decimal
    book_value: Decimal
    status: str
    available_for_use_date: date


class DepreciateResult(BaseModel):
    """Outcome of posting an asset's due depreciation rows."""

    asset_id: uuid.UUID
    posted_count: int
    journal_entry_ids: list[uuid.UUID] = Field(default_factory=list)
    status: str
    detail: str | None = None


# --- reports (Phase 4) -------------------------------------------------------------


class FixedAssetRegisterRow(BaseModel):
    """One asset's net-block line as of a date."""

    asset_id: uuid.UUID
    name: str
    asset_name: str
    category_name: str | None
    location_name: str | None
    purchase_date: date | None
    gross_purchase_amount: Decimal
    accumulated_depreciation: Decimal
    book_value: Decimal
    status: str


class DepreciationLedgerRow(BaseModel):
    """One posted depreciation entry."""

    asset_id: uuid.UUID
    asset_no: str
    asset_name: str
    category_name: str | None
    schedule_date: date
    depreciation_amount: Decimal
    accumulated_depreciation: Decimal
    journal_entry_id: uuid.UUID | None
    journal_entry_no: str | None


class MaintenanceDueRow(BaseModel):
    """One scheduled maintenance item, with how overdue it is."""

    id: uuid.UUID
    name: str
    asset_id: uuid.UUID
    asset_name: str | None
    maintenance_type: str
    periodicity: str
    next_due_date: date
    assigned_to: str | None
    status: str
    days_overdue: int  # positive = overdue, negative = days until due
