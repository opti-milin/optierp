"""Shared types for the Cost Driver engine (method × basis × hierarchy × explain)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

ZERO = Decimal("0")
HUNDRED = Decimal("100")
MONEY = Decimal("0.01")

CM_CLASSES: tuple[str, ...] = (
    "revenue",
    "variable_cost",
    "product_channel_fixed",
    "segment_bu_fixed",
    "corporate_overhead",
)

METHOD_LABELS: dict[str, str] = {
    "from_source": "From source adapter",
    "fixed_amount": "Fixed amount",
    "percentage": "Percentage of basis",
    "rate_times_basis": "Rate × basis",
    "pool_share": "Cost center / pool share",
    "pct_of_driver": "Percentage of driver",
    "formula": "Formula",
    "abc_activity": "Activity-based costing",
}

BASIS_LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "quantity": "Quantity",
    "transaction_count": "Transaction count",
    "weight": "Weight",
    "volume": "Volume",
    "machine_hours": "Machine hours",
    "labor_hours": "Labor hours",
    "floor_area": "Floor area",
    "headcount": "Headcount",
    "storage_days": "Storage days",
    "custom_formula": "Custom formula",
}


def q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY)


def _jsonable(value: Any) -> Any:
    """Convert Decimals (and nested dict/list) for JSONB / json.dumps."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


@dataclass
class DriverDef:
    """One node in a cost-structure tree (group or leaf)."""

    code: str
    label: str
    cm_class: str
    is_group: bool = False
    parent_code: str | None = None
    allocation_method: str | None = None
    allocation_basis: str | None = None
    method_params: dict[str, Any] = field(default_factory=dict)
    basis_params: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    scope: str = "header"
    sort_order: int = 0
    enabled: bool = True
    is_system: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "cm_class": self.cm_class,
            "is_group": self.is_group,
            "parent_code": self.parent_code,
            "allocation_method": self.allocation_method,
            "allocation_basis": self.allocation_basis,
            "method_params": _jsonable(self.method_params),
            "basis_params": _jsonable(self.basis_params),
            "source": self.source,
            "scope": self.scope,
            "sort_order": self.sort_order,
            "enabled": self.enabled,
            "is_system": self.is_system,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DriverDef:
        return cls(
            code=str(data["code"]),
            label=str(data.get("label") or data["code"]),
            cm_class=str(data.get("cm_class") or "variable_cost"),
            is_group=bool(data.get("is_group", False)),
            parent_code=data.get("parent_code"),
            allocation_method=data.get("allocation_method"),
            allocation_basis=data.get("allocation_basis"),
            method_params=dict(data.get("method_params") or {}),
            basis_params=dict(data.get("basis_params") or {}),
            source=data.get("source"),
            scope=str(data.get("scope") or "header"),
            sort_order=int(data.get("sort_order") or 0),
            enabled=bool(data.get("enabled", True)),
            is_system=bool(data.get("is_system", False)),
        )


@dataclass
class BasisResolution:
    value: Decimal
    total: Decimal | None = None
    unit: str | None = None
    label: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok | incomplete
    warning: str | None = None


@dataclass
class Explanation:
    driver_code: str
    driver_label: str
    parent_code: str | None
    cm_class: str
    method: str
    method_label: str
    basis: str | None = None
    basis_label: str | None = None
    basis_value: Decimal | None = None
    basis_total: Decimal | None = None
    pool_ref: str | None = None
    pool_label: str | None = None
    pool_amount: Decimal | None = None
    rate_or_pct: Decimal | None = None
    allocated_amount: Decimal = ZERO
    formula_display: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    warning: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        def _s(v: Decimal | None) -> str | None:
            return str(v) if v is not None else None

        return {
            "driver_code": self.driver_code,
            "driver_label": self.driver_label,
            "parent_code": self.parent_code,
            "cm_class": self.cm_class,
            "method": self.method,
            "method_label": self.method_label,
            "basis": self.basis,
            "basis_label": self.basis_label,
            "basis_value": _s(self.basis_value),
            "basis_total": _s(self.basis_total),
            "pool_ref": self.pool_ref,
            "pool_label": self.pool_label,
            "pool_amount": _s(self.pool_amount),
            "rate_or_pct": _s(self.rate_or_pct),
            "allocated_amount": str(self.allocated_amount),
            "formula_display": self.formula_display,
            "inputs": _jsonable(self.inputs),
            "status": self.status,
            "warning": self.warning,
            "source": self.source,
        }


@dataclass
class EngineContext:
    """Runtime inputs for one scenario compute."""

    revenue: Decimal = ZERO
    quantity_total: Decimal = ZERO
    source_amounts: dict[str, Decimal] = field(default_factory=dict)
    source_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    basis_overrides: dict[str, Decimal] = field(default_factory=dict)
    pools: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LeafResult:
    driver: DriverDef
    amount: Decimal
    explanation: Explanation


@dataclass
class EngineResult:
    leaf_amounts: dict[str, Decimal]
    group_amounts: dict[str, Decimal]
    class_totals: dict[str, Decimal]
    leaves: list[LeafResult]
    warnings: list[str] = field(default_factory=list)
