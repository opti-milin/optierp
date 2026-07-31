"""Cost Driver engine — execute method×basis rules, hierarchy rollup, explanations."""

from __future__ import annotations

from decimal import Decimal

from app.services.cm_planning.bases import resolve_basis
from app.services.cm_planning.methods import execute_method
from app.services.cm_planning.types import (
    CM_CLASSES,
    ZERO,
    DriverDef,
    EngineContext,
    EngineResult,
    LeafResult,
    q_money,
)
from app.services.cm_planning.waterfall import WaterfallResult, build_waterfall


def _topo_leaves(drivers: list[DriverDef]) -> list[DriverDef]:
    """Enabled leaves sorted by sort_order (pct_of_driver / formula may need prior amounts)."""
    return sorted(
        [d for d in drivers if d.enabled and not d.is_group],
        key=lambda d: (d.sort_order, d.code),
    )


def run_cost_driver_engine(
    drivers: list[DriverDef],
    ctx: EngineContext,
) -> EngineResult:
    amounts: dict[str, Decimal] = {}
    leaves: list[LeafResult] = []
    warnings: list[str] = []

    for driver in _topo_leaves(drivers):
        method = driver.allocation_method or "fixed_amount"
        basis = None
        if method in ("percentage", "rate_times_basis", "pool_share", "abc_activity") or (
            method == "formula" and driver.allocation_basis
        ):
            basis = resolve_basis(driver.allocation_basis, ctx, driver.basis_params, driver)
        amount, explanation = execute_method(driver, basis, ctx, amounts)
        amounts[driver.code] = amount
        leaves.append(LeafResult(driver=driver, amount=amount, explanation=explanation))
        if explanation.warning:
            warnings.append(explanation.warning)

    # Hierarchy: groups = sum of direct children (recursive bottom-up)
    by_parent: dict[str | None, list[DriverDef]] = {}
    for d in drivers:
        if not d.enabled:
            continue
        by_parent.setdefault(d.parent_code, []).append(d)

    group_amounts: dict[str, Decimal] = {}

    def group_total(code: str) -> Decimal:
        if code in group_amounts:
            return group_amounts[code]
        if code in amounts:
            return amounts[code]
        total = ZERO
        for child in by_parent.get(code, []):
            if child.is_group:
                total += group_total(child.code)
            elif child.enabled:
                total += amounts.get(child.code, ZERO)
        group_amounts[code] = q_money(total)
        return group_amounts[code]

    for d in drivers:
        if d.enabled and d.is_group:
            group_total(d.code)

    class_totals: dict[str, Decimal] = {c: ZERO for c in CM_CLASSES if c != "revenue"}
    for leaf in leaves:
        cls = leaf.driver.cm_class
        class_totals[cls] = q_money(class_totals.get(cls, ZERO) + leaf.amount)

    return EngineResult(
        leaf_amounts=amounts,
        group_amounts=group_amounts,
        class_totals=class_totals,
        leaves=leaves,
        warnings=warnings,
    )


def waterfall_from_engine(
    ctx: EngineContext,
    result: EngineResult,
    *,
    target_cm1_pct: Decimal | None = None,
) -> WaterfallResult:
    """Map engine class/leaf totals into the stable WaterfallResult shape."""
    leaves = result.leaf_amounts

    def leaf(*codes: str) -> Decimal:
        return q_money(sum((leaves.get(c, ZERO) for c in codes), ZERO))

    material = leaf("material")
    labor = leaf("labor")
    freight = leaf("freight")
    commission = leaf("commission")
    packaging = leaf("packaging")

    # Prefer class totals for fixed layers (supports hierarchy children under corporate_overhead)
    product = result.class_totals.get("product_channel_fixed", ZERO)
    segment = result.class_totals.get("segment_bu_fixed", ZERO)
    corp = result.class_totals.get("corporate_overhead", ZERO)

    # Variable class may include more than the five named drivers
    variable_class = result.class_totals.get("variable_cost", ZERO)
    named_variable = material + labor + freight + commission + packaging
    # If extra variable leaves exist, fold residual into packaging for typed column compat
    residual = q_money(variable_class - named_variable)
    if residual != ZERO:
        packaging = q_money(packaging + residual)

    return build_waterfall(
        revenue=ctx.revenue,
        material=material,
        labor=labor,
        freight=freight,
        commission=commission,
        packaging=packaging,
        product_channel_fixed=product,
        segment_bu_fixed=segment,
        corporate_overhead=corp,
        target_cm1_pct=target_cm1_pct,
    )


def estimate_with_drivers(
    *,
    revenue: Decimal,
    quantity_total: Decimal,
    source_amounts: dict[str, Decimal],
    drivers: list[DriverDef],
    target_cm1_pct: Decimal | None = None,
    source_meta: dict[str, dict] | None = None,
    basis_overrides: dict[str, Decimal] | None = None,
    pools: dict[str, dict] | None = None,
    extra: dict | None = None,
) -> tuple[WaterfallResult, EngineResult]:
    ctx = EngineContext(
        revenue=revenue,
        quantity_total=quantity_total,
        source_amounts=source_amounts,
        source_meta=source_meta or {},
        basis_overrides=basis_overrides or {},
        pools=pools or {},
        extra=extra or {},
    )
    eng = run_cost_driver_engine(drivers, ctx)
    return waterfall_from_engine(ctx, eng, target_cm1_pct=target_cm1_pct), eng
