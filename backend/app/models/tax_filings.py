"""Tax filing documents — Phase 8 (ITR JSON snapshot + acknowledgement)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin


class TaxFiling(Base, DocumentMixin, CompanyScopedMixin):
    """Generated ITR payload with sha256 hash and optional portal acknowledgement."""

    __tablename__ = "tax_filings"

    name: Mapped[str] = mapped_column(String(40), nullable=False)
    computation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_computations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_computation_runs.id", ondelete="RESTRICT"),
    )
    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    form_code: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    filing_type: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'Original'")
    )
    revises_filing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_filings.id")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'Generated'")
    )
    ack_no: Mapped[str | None] = mapped_column(String(80))
    filed_on: Mapped[date | None] = mapped_column(Date)
    verification_mode: Mapped[str | None] = mapped_column(String(20))
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    remarks: Mapped[str | None] = mapped_column(String(255))
