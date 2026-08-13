"""Allocation basis adapters — resolve the denominator / driver quantity."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from app.services.cm_planning.types import (
    BASIS_LABELS,
    ZERO,
    BasisResolution,
    DriverDef,
    EngineContext,
    q_money,
)


class BasisAdapter(Protocol):
    code: str

    def resolve(
        self, ctx: EngineContext, params: dict[str, Any], driver: DriverDef
    ) -> BasisResolution: ...


class RevenueBasis:
    code = "revenue"

    def resolve(
        self, ctx: EngineContext, params: dict[str, Any], driver: DriverDef
    ) -> BasisResolution:
        _ = params, driver
        val = q_money(Decimal(ctx.basis_overrides.get("revenue", ctx.revenue)))
        return BasisResolution(
            value=val,
            total=val,
            unit="currency",
            label=BASIS_LABELS["revenue"],
            inputs={"revenue": str(val)},
        )


class QuantityBasis:
    code = "quantity"

    def resolve(
        self, ctx: EngineContext, params: dict[str, Any], driver: DriverDef
    ) -> BasisResolution:
        _ = params, driver
        val = q_money(Decimal(ctx.basis_overrides.get("quantity", ctx.quantity_total)))
        return BasisResolution(
            value=val,
            total=val,
            unit="qty",
            label=BASIS_LABELS["quantity"],
            inputs={"quantity": str(val)},
        )


class TransactionCountBasis:
    code = "transaction_count"

    def resolve(
        self, ctx: EngineContext, params: dict[str, Any], driver: DriverDef
    ) -> BasisResolution:
        _ = driver
        default = Decimal(str(params.get("count", 1)))
        val = Decimal(ctx.basis_overrides.get("transaction_count", default))
        return BasisResolution(
            value=val,
            total=val,
            unit="count",
            label=BASIS_LABELS["transaction_count"],
            inputs={"transaction_count": str(val)},
        )


class _OverrideOrExtraBasis:
    """Generic basis that reads ctx.basis_overrides / ctx.extra then marks incomplete."""

    code: str
    unit: str

    def __init__(self, code: str, unit: str = "units") -> None:
        self.code = code
        self.unit = unit

    def resolve(
        self, ctx: EngineContext, params: dict[str, Any], driver: DriverDef
    ) -> BasisResolution:
        _ = driver
        if self.code in ctx.basis_overrides:
            val = Decimal(ctx.basis_overrides[self.code])
            return BasisResolution(
                value=val,
                total=Decimal(ctx.extra.get(f"{self.code}_total", val)),
                unit=self.unit,
                label=BASIS_LABELS.get(self.code, self.code),
                inputs={self.code: str(val)},
            )
        if self.code in params:
            val = Decimal(str(params[self.code]))
            return BasisResolution(
                value=val,
                total=Decimal(str(params.get(f"{self.code}_total", val))),
                unit=self.unit,
                label=BASIS_LABELS.get(self.code, self.code),
                inputs={self.code: str(val)},
            )
        extra_val = ctx.extra.get(self.code)
        if extra_val is not None:
            val = Decimal(str(extra_val))
            return BasisResolution(
                value=val,
                total=Decimal(str(ctx.extra.get(f"{self.code}_total", val))),
                unit=self.unit,
                label=BASIS_LABELS.get(self.code, self.code),
                inputs={self.code: str(val)},
            )
        return BasisResolution(
            value=ZERO,
            total=ZERO,
            unit=self.unit,
            label=BASIS_LABELS.get(self.code, self.code),
            inputs={},
            status="incomplete",
            warning=f"Basis '{self.code}' not provided — allocated 0",
        )


class CustomFormulaBasis:
    code = "custom_formula"

    def resolve(
        self, ctx: EngineContext, params: dict[str, Any], driver: DriverDef
    ) -> BasisResolution:
        from app.services.cm_planning.formula_safe import eval_formula

        expr = str(params.get("formula") or params.get("expression") or "0")
        env = {
            "revenue": float(ctx.revenue),
            "quantity": float(ctx.quantity_total),
            **{k: float(v) for k, v in ctx.basis_overrides.items()},
            **{
                k: float(v)
                for k, v in ctx.extra.items()
                if isinstance(v, (int, float, Decimal))
            },
        }
        try:
            raw = eval_formula(expr, env)
            val = q_money(Decimal(str(raw)))
            return BasisResolution(
                value=val,
                total=val,
                unit="formula",
                label=BASIS_LABELS["custom_formula"],
                inputs={"formula": expr, "result": str(val)},
            )
        except Exception as exc:  # noqa: BLE001 — surface as incomplete basis
            return BasisResolution(
                value=ZERO,
                total=ZERO,
                unit="formula",
                label=BASIS_LABELS["custom_formula"],
                inputs={"formula": expr},
                status="incomplete",
                warning=f"Formula basis failed for {driver.code}: {exc}",
            )


BASES: dict[str, BasisAdapter] = {
    "revenue": RevenueBasis(),
    "quantity": QuantityBasis(),
    "transaction_count": TransactionCountBasis(),
    "weight": _OverrideOrExtraBasis("weight", "kg"),
    "volume": _OverrideOrExtraBasis("volume", "m3"),
    "machine_hours": _OverrideOrExtraBasis("machine_hours", "hours"),
    "labor_hours": _OverrideOrExtraBasis("labor_hours", "hours"),
    "floor_area": _OverrideOrExtraBasis("floor_area", "sqm"),
    "headcount": _OverrideOrExtraBasis("headcount", "fte"),
    "storage_days": _OverrideOrExtraBasis("storage_days", "days"),
    "custom_formula": CustomFormulaBasis(),
}


def resolve_basis(
    code: str | None, ctx: EngineContext, params: dict[str, Any], driver: DriverDef
) -> BasisResolution | None:
    if not code:
        return None
    adapter = BASES.get(code)
    if adapter is None:
        return BasisResolution(
            value=ZERO,
            total=ZERO,
            label=code,
            status="incomplete",
            warning=f"Unknown allocation basis '{code}'",
        )
    return adapter.resolve(ctx, params, driver)
