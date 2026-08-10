"""Tax compliance reminder log — Phase 7."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin


class TaxComplianceReminder(Base, DocumentMixin, CompanyScopedMixin):
    """Idempotent log of advance-tax / ITR due reminders already sent."""

    __tablename__ = "tax_compliance_reminders"

    ay_code: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'email'"))
    status: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'Sent'"))
    shortfall_amount: Mapped[Decimal | None] = mapped_column(Numeric(21, 6))
    recipients: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String(500))
