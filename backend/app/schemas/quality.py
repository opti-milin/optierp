"""Quality Inspection schemas (Phase 5 lean gate)."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import DocumentMeta, ORMModel


class QualityInspectionCreate(BaseModel):
    reference_type: str  # Work Order | Subcontract Job | Stock Entry
    reference_id: uuid.UUID
    item_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    inspection_date: date | None = None
    remarks: str | None = None


class QualityInspectionResponse(DocumentMeta):
    name: str
    reference_type: str
    reference_id: uuid.UUID
    reference_name: str | None = None
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    qty: Decimal
    status: str
    inspection_date: date
    inspected_by: uuid.UUID | None = None
    remarks: str | None = None
    company_id: uuid.UUID


class QualityInspectionListItem(ORMModel):
    id: uuid.UUID
    name: str
    reference_type: str
    reference_name: str | None = None
    item_code: str | None = None
    qty: Decimal
    status: str
    docstatus: int
    inspection_date: date
