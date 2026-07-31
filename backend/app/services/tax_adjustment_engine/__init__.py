"""Tax adjustment engine — metadata-driven books → taxable income bridge.

Algorithms live in ``methods/``; statutory numbers and eligibility live on
AY-versioned ``TaxAdjustmentRule`` rows (seeded from JSON packs).
"""

from __future__ import annotations

from app.services.tax_adjustment_engine.pipeline import (
    adjustment_snapshot,
    net_from_results,
    run_adjustment_engine,
)
from app.services.tax_adjustment_engine.resolve import resolve_adjustment_pack

__all__ = [
    "adjustment_snapshot",
    "net_from_results",
    "resolve_adjustment_pack",
    "run_adjustment_engine",
]
