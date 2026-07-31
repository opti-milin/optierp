"""Phase D stubs — ICDS / loss set-off / MAT informational bridge.

Full ICDS measurement, carry-forward ledgers, and 115JB book-profit engines
are follow-on work. This module exposes structured placeholders that the
adjustment engine and tax_breakdown can reference without inventing numbers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.tax_adjustment_engine.context import ZERO, q


ICDS_CODES = tuple(f"ICDS-{r}" for r in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"))


def icds_worksheet_stub(schedule_inputs: dict[str, Decimal] | None = None) -> list[dict[str, Any]]:
    """Return per-ICDS rows; amounts from schedule_inputs when provided."""
    inputs = schedule_inputs or {}
    rows: list[dict[str, Any]] = []
    for code in ICDS_CODES:
        amt = q(inputs.get(code, ZERO))
        rows.append(
            {
                "section_code": code,
                "amount": str(amt),
                "status": "NeedsInput" if amt == ZERO else "Manual",
                "disclosure": f"Form 3CD / ICDS schedule for {code}",
            }
        )
    return rows


def loss_setoff_stub(
    *,
    business_loss_cf: Decimal = ZERO,
    capital_loss_cf: Decimal = ZERO,
) -> dict[str, Any]:
    return {
        "s72_business_loss_cf": str(q(business_loss_cf)),
        "s74_capital_loss_cf": str(q(capital_loss_cf)),
        "status": "Informational",
        "note": "Carry-forward ledger not yet wired — enter via SetOff adjustment lines",
    }


def mat_bridge_stub(
    *,
    book_profit_115jb: Decimal = ZERO,
    mat_rate_percent: Decimal = Decimal("15"),
) -> dict[str, Any]:
    tax = q(book_profit_115jb * mat_rate_percent / Decimal("100")) if book_profit_115jb > ZERO else ZERO
    return {
        "section": "115JB",
        "book_profit": str(q(book_profit_115jb)),
        "mat_rate_percent": str(mat_rate_percent),
        "mat_tax": str(tax),
        "status": "Informational" if book_profit_115jb == ZERO else "Computed",
        "note": "MAT comparison vs normal tax is advisory until full 115JB engine ships",
    }


def phase_d_breakdown_slice(
    *,
    schedule_inputs: dict[str, Decimal] | None = None,
    book_profit_115jb: Decimal = ZERO,
) -> dict[str, Any]:
    return {
        "icds": icds_worksheet_stub(schedule_inputs),
        "set_off": loss_setoff_stub(),
        "mat": mat_bridge_stub(book_profit_115jb=book_profit_115jb),
    }
