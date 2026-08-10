"""Pure taxation kernel — Decimal-only, no DB / I/O."""

from app.services.taxation.kernel.cess import apply_cess
from app.services.taxation.kernel.compute import compute, flat_bands
from app.services.taxation.kernel.marginal_relief import (
    rebate_marginal_relief,
    surcharge_marginal_relief,
)
from app.services.taxation.kernel.money import ZERO, money, percent_of, q, round_to_nearest_ten
from app.services.taxation.kernel.rates import flat_tax, tax_on_bands
from app.services.taxation.kernel.rebate import apply_rebate
from app.services.taxation.kernel.surcharge import compute_surcharge, pick_surcharge_rate
from app.services.taxation.kernel.types import (
    IncomeComponent,
    KernelInput,
    KernelResult,
    RateBand,
    RebateSpec,
    SurchargeBand,
    SurchargeSpec,
)

__all__ = [
    "ZERO",
    "IncomeComponent",
    "KernelInput",
    "KernelResult",
    "RateBand",
    "RebateSpec",
    "SurchargeBand",
    "SurchargeSpec",
    "apply_cess",
    "apply_rebate",
    "compute",
    "compute_surcharge",
    "flat_bands",
    "flat_tax",
    "money",
    "percent_of",
    "pick_surcharge_rate",
    "q",
    "rebate_marginal_relief",
    "round_to_nearest_ten",
    "surcharge_marginal_relief",
    "tax_on_bands",
]
