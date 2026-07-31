"""Data-driven Indian income-tax computation engine.

Public entry: ``run_pipeline`` / ``compute_from_resolved``.
All statutory numbers come from resolved masters — never hardcoded AY rates.
"""

from __future__ import annotations

from app.services.income_tax_engine.context import (
    ResolvedTaxRules,
    SpecialIncomeInput,
    TaxEngineInput,
    TaxEngineResult,
)
from app.services.income_tax_engine.pipeline import compute_from_resolved, run_pipeline

__all__ = [
    "ResolvedTaxRules",
    "SpecialIncomeInput",
    "TaxEngineInput",
    "TaxEngineResult",
    "compute_from_resolved",
    "run_pipeline",
]
