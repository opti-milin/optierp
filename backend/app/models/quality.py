"""Module — Quality (lean Quality Inspection for manufacturing gates).

Phase 5 of docs/MANUFACTURING_GAP_AND_PLAN.md: optional Accepted QI before
Manufacture / Subcontract Receipt when the finished item has ``inspection_required``.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

QUALITY_INSPECTION_STATUSES = ("Draft", "Accepted", "Rejected")
QUALITY_INSPECTION_REFERENCE_TYPES = ("Work Order", "Subcontract Job", "Stock Entry")


class QualityInspection(Base, DocumentMixin, CompanyScopedMixin):
    """Accepted/Rejected inspection against a manufacturing document + item + qty."""

    __tablename__ = "quality_inspections"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_quality_inspection_name"),
        Index("ix_quality_inspections_company_docstatus", "company_id", "docstatus"),
        Index(
            "ix_quality_inspections_reference",
            "company_id",
            "reference_type",
            "reference_id",
        ),
    )

    name: Mapped[str] = mapped_column(String(140), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reference_name: Mapped[str | None] = mapped_column(String(140))
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(21, 6), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Draft", server_default=text("'Draft'")
    )
    inspection_date: Mapped[date] = mapped_column(Date, nullable=False)
    inspected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    remarks: Mapped[str | None] = mapped_column(Text)

    item = relationship("Item", lazy="joined", viewonly=True)

    @property
    def item_code(self) -> str | None:
        return self.item.item_code if self.item else None

    @property
    def item_name(self) -> str | None:
        return self.item.item_name if self.item else None


__all__ = [
    "QualityInspection",
    "QUALITY_INSPECTION_STATUSES",
    "QUALITY_INSPECTION_REFERENCE_TYPES",
]
