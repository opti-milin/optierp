"""Pure CM planning math — waterfall, min selling price, scenario diffs.

No DB access. Used by estimate/seed services and unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

ZERO = Decimal("0")
HUNDRED = Decimal("100")
MONEY = Decimal("0.01")

VARIABLE_DRIVERS: tuple[str, ...] = (
    "material",
    "labor",
    "freight",
    "commission",
    "packaging",
)

FIXED_DRIVERS: tuple[str, ...] = (
    "product_channel_fixed",
    "segment_bu_fixed",
    "corporate_overhead",
)

DRIVER_TO_CM_CLASS: dict[str, str] = {
    "material": "variable_cost",
    "labor": "variable_cost",
    "freight": "variable_cost",
    "commission": "variable_cost",
    "packaging": "variable_cost",
    "product_channel_fixed": "product_channel_fixed",
    "segment_bu_fixed": "segment_bu_fixed",
    "corporate_overhead": "corporate_overhead",
}


@dataclass(frozen=True)
class WaterfallResult:
    revenue: Decimal
    material: Decimal = ZERO
    labor: Decimal = ZERO
    freight: Decimal = ZERO
    commission: Decimal = ZERO
    packaging: Decimal = ZERO
    product_channel_fixed: Decimal = ZERO
    segment_bu_fixed: Decimal = ZERO
    corporate_overhead: Decimal = ZERO
    variable_cost: Decimal = ZERO
    cm1: Decimal = ZERO
    cm2: Decimal = ZERO
    cm3: Decimal = ZERO
    operating_profit: Decimal = ZERO
    cm1_pct: Decimal | None = None
    cm2_pct: Decimal | None = None
    cm3_pct: Decimal | None = None
    operating_profit_pct: Decimal | None = None
    min_selling_total: Decimal | None = None


@dataclass
class LineEstimateIn:
    line_key: str
    item_id: Any | None
    qty: Decimal
    selling_rate: Decimal
    material_per_unit: Decimal = ZERO
    labor_per_unit: Decimal = ZERO
    packaging_per_unit: Decimal = ZERO


@dataclass
class EstimateInput:
    lines: list[LineEstimateIn]
    freight_total: Decimal = ZERO
    commission_rate_pct: Decimal = ZERO
    material_cost_factor: Decimal = Decimal("1")
    labor_cost_factor: Decimal = Decimal("1")
    allocations: dict[str, Decimal] = field(default_factory=dict)
    target_cm1_pct: Decimal | None = None
    drivers: list[Any] | None = None
    source_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    basis_overrides: dict[str, Decimal] = field(default_factory=dict)
    pools: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    keep_manual_costs: list[dict[str, Any]] = field(default_factory=list)


def _q(value: Decimal) -> Decimal:
    return value.quantize(MONEY)


def _pct(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole == ZERO:
        return None
    return _q(part * HUNDRED / whole)


def min_selling_from_variable(variable_cost: Decimal, target_cm1_pct: Decimal) -> Decimal | None:
    """Solve revenue R such that (R - V) / R = t → R = V / (1 - t)."""
    if variable_cost < ZERO:
        return None
    t = target_cm1_pct / HUNDRED
    if t >= Decimal("1") or t < ZERO:
        return None
    if variable_cost == ZERO:
        return ZERO
    return _q(variable_cost / (Decimal("1") - t))


def build_waterfall(
    *,
    revenue: Decimal,
    material: Decimal = ZERO,
    labor: Decimal = ZERO,
    freight: Decimal = ZERO,
    commission: Decimal = ZERO,
    packaging: Decimal = ZERO,
    product_channel_fixed: Decimal = ZERO,
    segment_bu_fixed: Decimal = ZERO,
    corporate_overhead: Decimal = ZERO,
    target_cm1_pct: Decimal | None = None,
) -> WaterfallResult:
    variable = _q(material + labor + freight + commission + packaging)
    cm1 = _q(revenue - variable)
    cm2 = _q(cm1 - product_channel_fixed)
    cm3 = _q(cm2 - segment_bu_fixed)
    op = _q(cm3 - corporate_overhead)
    min_sell = (
        min_selling_from_variable(variable, target_cm1_pct)
        if target_cm1_pct is not None
        else None
    )
    return WaterfallResult(
        revenue=_q(revenue),
        material=_q(material),
        labor=_q(labor),
        freight=_q(freight),
        commission=_q(commission),
        packaging=_q(packaging),
        product_channel_fixed=_q(product_channel_fixed),
        segment_bu_fixed=_q(segment_bu_fixed),
        corporate_overhead=_q(corporate_overhead),
        variable_cost=variable,
        cm1=cm1,
        cm2=cm2,
        cm3=cm3,
        operating_profit=op,
        cm1_pct=_pct(cm1, revenue),
        cm2_pct=_pct(cm2, revenue),
        cm3_pct=_pct(cm3, revenue),
        operating_profit_pct=_pct(op, revenue),
        min_selling_total=min_sell,
    )


def estimate_from_lines(
    inp: EstimateInput,
) -> tuple[WaterfallResult, list[dict[str, Any]], Any]:
    """Compute header waterfall + per-line detail dicts from line inputs.

    Returns ``(waterfall, line_details, engine_result)``.
    """
    mat_f = inp.material_cost_factor if inp.material_cost_factor > ZERO else Decimal("1")
    lab_f = inp.labor_cost_factor if inp.labor_cost_factor > ZERO else Decimal("1")

    revenue = ZERO
    material = ZERO
    labor = ZERO
    packaging = ZERO
    line_details: list[dict[str, Any]] = []

    for line in inp.lines:
        selling = _q(line.qty * line.selling_rate)
        mat = _q(line.qty * line.material_per_unit * mat_f)
        lab = _q(line.qty * line.labor_per_unit * lab_f)
        pack = _q(line.qty * line.packaging_per_unit)
        revenue += selling
        material += mat
        labor += lab
        packaging += pack
        line_details.append(
            {
                "line_key": line.line_key,
                "item_id": line.item_id,
                "qty": line.qty,
                "selling_rate": _q(line.selling_rate),
                "selling_amount": selling,
                "material": mat,
                "labor": lab,
                "packaging": pack,
            }
        )

    commission = ZERO
    if inp.commission_rate_pct > ZERO and revenue > ZERO:
        commission = _q(revenue * inp.commission_rate_pct / HUNDRED)

    freight = _q(inp.freight_total)

    # Pro-rate freight onto lines for display (by selling share)
    if freight > ZERO and revenue > ZERO:
        allocated = ZERO
        for i, detail in enumerate(line_details):
            if i == len(line_details) - 1:
                share = freight - allocated
            else:
                share = _q(freight * detail["selling_amount"] / revenue)
                allocated += share
            detail["freight"] = share
    else:
        for detail in line_details:
            detail["freight"] = ZERO

    for detail in line_details:
        line_var = (
            detail["material"]
            + detail["labor"]
            + detail["packaging"]
            + detail["freight"]
            + (
                _q(detail["selling_amount"] * inp.commission_rate_pct / HUNDRED)
                if inp.commission_rate_pct > ZERO
                else ZERO
            )
        )
        detail["variable_cost"] = _q(line_var)
        detail["cm1"] = _q(detail["selling_amount"] - line_var)
        detail["min_selling_rate"] = None
        if inp.target_cm1_pct is not None and detail["qty"] > ZERO:
            min_total = min_selling_from_variable(line_var, inp.target_cm1_pct)
            if min_total is not None:
                detail["min_selling_rate"] = _q(min_total / detail["qty"])

    # Fixed layers (+ optional extra drivers) via Cost Driver engine; MVP allocations
    # translate to percentage × revenue leaves (compat).
    from app.services.cm_planning.compat import drivers_from_allocations
    from app.services.cm_planning.engine import estimate_with_drivers
    from app.services.cm_planning.types import DriverDef

    qty_total = sum((line.qty for line in inp.lines), ZERO)
    source_amounts = {
        "material": material,
        "labor": labor,
        "freight": freight,
        "commission": commission,
        "packaging": packaging,
    }
    drivers: list[DriverDef]
    if inp.drivers:
        drivers = list(inp.drivers)
    else:
        drivers = drivers_from_allocations(inp.allocations, include_variable=True)
    waterfall, eng = estimate_with_drivers(
        revenue=revenue,
        quantity_total=qty_total,
        source_amounts=source_amounts,
        drivers=drivers,
        target_cm1_pct=inp.target_cm1_pct,
        source_meta=inp.source_meta,
        basis_overrides=inp.basis_overrides,
        pools=inp.pools,
        extra=inp.extra,
    )
    return waterfall, line_details, eng


def diff_waterfalls(
    baseline: WaterfallResult,
    other: WaterfallResult,
    *,
    other_label: str = "scenario",
) -> dict[str, Any]:
    """Absolute and % delta of ``other`` vs ``baseline`` for key totals."""

    def one(key: str, a: Decimal, b: Decimal) -> dict[str, Any]:
        delta = _q(b - a)
        pct = None
        if a != ZERO:
            pct = _q(delta * HUNDRED / abs(a))
        return {"field": key, "baseline": a, other_label: b, "delta": delta, "delta_pct": pct}

    keys = (
        "revenue",
        "material",
        "labor",
        "freight",
        "commission",
        "packaging",
        "variable_cost",
        "product_channel_fixed",
        "segment_bu_fixed",
        "corporate_overhead",
        "cm1",
        "cm2",
        "cm3",
        "operating_profit",
    )
    rows = [one(k, getattr(baseline, k), getattr(other, k)) for k in keys]
    return {"baseline": baseline, other_label: other, "rows": rows}
