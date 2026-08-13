"""Allocation method adapters — how an amount is computed given an optional basis."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from app.services.cm_planning.types import (
    HUNDRED,
    METHOD_LABELS,
    ZERO,
    BasisResolution,
    DriverDef,
    EngineContext,
    Explanation,
    q_money,
)


class AllocationMethod(Protocol):
    code: str

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]: ...


def _base_explanation(
    driver: DriverDef,
    *,
    method: str,
    basis: BasisResolution | None,
    amount: Decimal,
    formula_display: str,
    rate_or_pct: Decimal | None = None,
    pool_ref: str | None = None,
    pool_label: str | None = None,
    pool_amount: Decimal | None = None,
    inputs: dict[str, Any] | None = None,
    source: str | None = None,
    status: str = "ok",
    warning: str | None = None,
) -> Explanation:
    return Explanation(
        driver_code=driver.code,
        driver_label=driver.label,
        parent_code=driver.parent_code,
        cm_class=driver.cm_class,
        method=method,
        method_label=METHOD_LABELS.get(method, method),
        basis=driver.allocation_basis,
        basis_label=basis.label if basis else None,
        basis_value=basis.value if basis else None,
        basis_total=basis.total if basis else None,
        pool_ref=pool_ref,
        pool_label=pool_label,
        pool_amount=pool_amount,
        rate_or_pct=rate_or_pct,
        allocated_amount=amount,
        formula_display=formula_display,
        inputs=inputs or {},
        status=status if (basis is None or basis.status == "ok") else basis.status,
        warning=warning or (basis.warning if basis else None),
        source=source or driver.source,
    )


class FromSourceMethod:
    code = "from_source"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        _ = basis, prior_amounts
        key = driver.code
        amount = q_money(Decimal(ctx.source_amounts.get(key, ZERO)))
        meta = ctx.source_meta.get(key, {})
        src = str(meta.get("source") or driver.source or "none")
        expl = _base_explanation(
            driver,
            method=self.code,
            basis=None,
            amount=amount,
            formula_display=f"from_source({src}) = {amount}",
            inputs={"source": src, "amount": str(amount)},
            source=src,
        )
        return amount, expl


class FixedAmountMethod:
    code = "fixed_amount"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        _ = basis, ctx, prior_amounts
        amount = q_money(Decimal(str(driver.method_params.get("amount", 0))))
        expl = _base_explanation(
            driver,
            method=self.code,
            basis=None,
            amount=amount,
            formula_display=f"fixed {amount}",
            inputs={"amount": str(amount)},
        )
        return amount, expl


class PercentageMethod:
    code = "percentage"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        _ = ctx, prior_amounts
        pct = Decimal(str(driver.method_params.get("pct", 0)))
        if basis is None:
            expl = _base_explanation(
                driver,
                method=self.code,
                basis=None,
                amount=ZERO,
                formula_display=f"{pct}% × (missing basis)",
                rate_or_pct=pct,
                status="incomplete",
                warning="percentage method requires an allocation basis",
            )
            return ZERO, expl
        amount = q_money(basis.value * pct / HUNDRED)
        basis_name = driver.allocation_basis or "basis"
        expl = _base_explanation(
            driver,
            method=self.code,
            basis=basis,
            amount=amount,
            formula_display=f"{pct}% × {basis_name} {basis.value}",
            rate_or_pct=pct,
            inputs={**(basis.inputs or {}), "pct": str(pct)},
        )
        return amount, expl


class RateTimesBasisMethod:
    code = "rate_times_basis"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        _ = ctx, prior_amounts
        rate = Decimal(str(driver.method_params.get("rate", 0)))
        if basis is None:
            expl = _base_explanation(
                driver,
                method=self.code,
                basis=None,
                amount=ZERO,
                formula_display=f"{rate} × (missing basis)",
                rate_or_pct=rate,
                status="incomplete",
                warning="rate_times_basis requires an allocation basis",
            )
            return ZERO, expl
        amount = q_money(rate * basis.value)
        basis_name = driver.allocation_basis or "basis"
        expl = _base_explanation(
            driver,
            method=self.code,
            basis=basis,
            amount=amount,
            formula_display=f"{rate} × {basis_name} {basis.value}",
            rate_or_pct=rate,
            inputs={**(basis.inputs or {}), "rate": str(rate)},
        )
        return amount, expl


class PoolShareMethod:
    code = "pool_share"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        _ = prior_amounts
        pool_ref = str(
            driver.method_params.get("pool_ref")
            or driver.method_params.get("pool_id")
            or ""
        )
        pool = ctx.pools.get(pool_ref, {})
        pool_amount = q_money(
            Decimal(
                str(
                    pool.get("amount")
                    if pool.get("amount") is not None
                    else driver.method_params.get("pool_amount", 0)
                )
            )
        )
        pool_label = str(pool.get("label") or driver.method_params.get("pool_label") or pool_ref or "Pool")
        if basis is None or basis.total is None or basis.total == ZERO:
            expl = _base_explanation(
                driver,
                method=self.code,
                basis=basis,
                amount=ZERO,
                formula_display=f"{pool_amount} × (basis/total missing)",
                pool_ref=pool_ref or None,
                pool_label=pool_label,
                pool_amount=pool_amount,
                status="incomplete",
                warning=basis.warning if basis else "pool_share requires a basis with total",
            )
            return ZERO, expl
        share = basis.value / basis.total
        amount = q_money(pool_amount * share)
        expl = _base_explanation(
            driver,
            method=self.code,
            basis=basis,
            amount=amount,
            formula_display=f"{pool_amount} × ({basis.value} / {basis.total})",
            pool_ref=pool_ref or None,
            pool_label=pool_label,
            pool_amount=pool_amount,
            inputs={
                **(basis.inputs or {}),
                "pool_amount": str(pool_amount),
                "share": str(share),
            },
        )
        return amount, expl


class PctOfDriverMethod:
    code = "pct_of_driver"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        _ = basis, ctx
        pct = Decimal(str(driver.method_params.get("pct", 0)))
        base_code = str(driver.method_params.get("base_driver_code") or "")
        base_amt = Decimal(prior_amounts.get(base_code, ZERO))
        amount = q_money(base_amt * pct / HUNDRED)
        expl = _base_explanation(
            driver,
            method=self.code,
            basis=None,
            amount=amount,
            formula_display=f"{pct}% × driver.{base_code} ({base_amt})",
            rate_or_pct=pct,
            inputs={"base_driver_code": base_code, "base_amount": str(base_amt), "pct": str(pct)},
            status="ok" if base_code in prior_amounts else "incomplete",
            warning=None if base_code in prior_amounts else f"Base driver '{base_code}' not computed yet",
        )
        return amount, expl


class FormulaMethod:
    code = "formula"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        from app.services.cm_planning.formula_safe import eval_formula

        expr = str(driver.method_params.get("formula") or driver.method_params.get("expression") or "0")
        env: dict[str, float] = {
            "revenue": float(ctx.revenue),
            "quantity": float(ctx.quantity_total),
            **{f"driver_{k}": float(v) for k, v in prior_amounts.items()},
            **{k: float(v) for k, v in prior_amounts.items()},
        }
        if basis is not None:
            env["basis"] = float(basis.value)
            if basis.total is not None:
                env["basis_total"] = float(basis.total)
        try:
            amount = q_money(Decimal(str(eval_formula(expr, env))))
            expl = _base_explanation(
                driver,
                method=self.code,
                basis=basis,
                amount=amount,
                formula_display=f"{expr} = {amount}",
                inputs={"formula": expr, "result": str(amount)},
            )
            return amount, expl
        except Exception as exc:  # noqa: BLE001
            expl = _base_explanation(
                driver,
                method=self.code,
                basis=basis,
                amount=ZERO,
                formula_display=expr,
                inputs={"formula": expr},
                status="incomplete",
                warning=str(exc),
            )
            return ZERO, expl


class AbcActivityMethod:
    """Phase-4 stub: rate_per_driver × activity basis (or method_params.activity_qty)."""

    code = "abc_activity"

    def execute(
        self,
        driver: DriverDef,
        basis: BasisResolution | None,
        ctx: EngineContext,
        prior_amounts: dict[str, Decimal],
    ) -> tuple[Decimal, Explanation]:
        _ = prior_amounts
        rate = Decimal(str(driver.method_params.get("rate_per_driver", 0)))
        activity_code = str(driver.method_params.get("activity_code") or "")
        qty = (
            basis.value
            if basis is not None
            else Decimal(str(driver.method_params.get("activity_qty", ctx.extra.get(activity_code, 0))))
        )
        amount = q_money(rate * qty)
        expl = _base_explanation(
            driver,
            method=self.code,
            basis=basis,
            amount=amount,
            formula_display=f"ABC {activity_code or 'activity'}: {rate} × {qty}",
            rate_or_pct=rate,
            inputs={"activity_code": activity_code, "rate_per_driver": str(rate), "activity_qty": str(qty)},
            status="ok" if (basis is not None or activity_code or qty) else "incomplete",
        )
        return amount, expl


METHODS: dict[str, AllocationMethod] = {
    "from_source": FromSourceMethod(),
    "fixed_amount": FixedAmountMethod(),
    "percentage": PercentageMethod(),
    "rate_times_basis": RateTimesBasisMethod(),
    "pool_share": PoolShareMethod(),
    "pct_of_driver": PctOfDriverMethod(),
    "formula": FormulaMethod(),
    "abc_activity": AbcActivityMethod(),
}


def execute_method(
    driver: DriverDef,
    basis: BasisResolution | None,
    ctx: EngineContext,
    prior_amounts: dict[str, Decimal],
) -> tuple[Decimal, Explanation]:
    method_code = driver.allocation_method or "fixed_amount"
    method = METHODS.get(method_code)
    if method is None:
        return ZERO, _base_explanation(
            driver,
            method=method_code,
            basis=basis,
            amount=ZERO,
            formula_display="",
            status="incomplete",
            warning=f"Unknown allocation method '{method_code}'",
        )
    return method.execute(driver, basis, ctx, prior_amounts)
