"""CM planning package — pre-sales Contribution Margin Plan + Cost Driver engine."""

from app.services.cm_planning.waterfall import (
    DRIVER_TO_CM_CLASS,
    VARIABLE_DRIVERS,
    WaterfallResult,
    build_waterfall,
    diff_waterfalls,
    estimate_from_lines,
    min_selling_from_variable,
)

__all__ = [
    "DRIVER_TO_CM_CLASS",
    "VARIABLE_DRIVERS",
    "WaterfallResult",
    "build_waterfall",
    "diff_waterfalls",
    "estimate_from_lines",
    "min_selling_from_variable",
]
