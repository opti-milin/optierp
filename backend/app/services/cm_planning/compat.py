"""MVP allocations JSONB ↔ Cost Driver tree compatibility."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.cm_planning.types import DriverDef

_FIXED_KEYS: tuple[tuple[str, str, str], ...] = (
    ("product_channel_fixed", "Product / Channel fixed", "product_channel_fixed_pct_of_revenue"),
    ("segment_bu_fixed", "Segment / BU fixed", "segment_bu_fixed_pct_of_revenue"),
    ("corporate_overhead", "Corporate overhead", "corporate_overhead_pct_of_revenue"),
)

_VARIABLE_DEFAULTS: tuple[tuple[str, str, str], ...] = (
    ("material", "Material", "bom_material"),
    ("labor", "Labor", "bom_operating"),
    ("freight", "Freight", "shipping_rule"),
    ("commission", "Commission", "sales_partner"),
    ("packaging", "Packaging", "cost_rate"),
)


def default_variable_drivers(*, parent_code: str | None = "variable_cost") -> list[DriverDef]:
    drivers: list[DriverDef] = []
    if parent_code:
        drivers.append(
            DriverDef(
                code="variable_cost",
                label="Variable Cost",
                cm_class="variable_cost",
                is_group=True,
                sort_order=0,
                is_system=True,
            )
        )
    for i, (code, label, source) in enumerate(_VARIABLE_DEFAULTS, start=1):
        drivers.append(
            DriverDef(
                code=code,
                label=label,
                cm_class="variable_cost",
                parent_code=parent_code,
                allocation_method="from_source",
                source=source,
                scope="line" if code != "commission" else "header",
                sort_order=i,
                is_system=True,
            )
        )
    return drivers


def drivers_from_allocations(
    allocations: dict[str, Any] | None,
    *,
    include_variable: bool = True,
) -> list[DriverDef]:
    """Translate legacy ``*_pct_of_revenue`` settings into leaf drivers."""
    alloc = allocations or {}
    drivers: list[DriverDef] = []
    if include_variable:
        drivers.extend(default_variable_drivers())

    # Flat fixed leaves (MVP shape — no corporate_* children unless template adds them)
    for i, (code, label, key) in enumerate(_FIXED_KEYS, start=10):
        pct = Decimal(str(alloc.get(key, 0)))
        drivers.append(
            DriverDef(
                code=code,
                label=label,
                cm_class=code,
                allocation_method="percentage",
                allocation_basis="revenue",
                method_params={"pct": pct},
                scope="plan",
                sort_order=i,
                is_system=True,
            )
        )
    return drivers


def allocations_from_drivers(drivers: list[DriverDef] | list[dict[str, Any]]) -> dict[str, Decimal]:
    """Extract legacy % keys from drivers that are percentage × revenue on fixed codes."""
    out: dict[str, Decimal] = {
        "product_channel_fixed_pct_of_revenue": Decimal("0"),
        "segment_bu_fixed_pct_of_revenue": Decimal("0"),
        "corporate_overhead_pct_of_revenue": Decimal("0"),
    }
    key_by_code = {code: key for code, _label, key in _FIXED_KEYS}
    for raw in drivers:
        d = DriverDef.from_dict(raw) if isinstance(raw, dict) else raw
        if d.code not in key_by_code:
            continue
        if d.allocation_method != "percentage" or d.allocation_basis != "revenue":
            continue
        out[key_by_code[d.code]] = Decimal(str(d.method_params.get("pct", 0)))
    return out


def sync_allocations_into_drivers(
    drivers: list[DriverDef], allocations: dict[str, Any]
) -> list[DriverDef]:
    """Update fixed leaf pct params from settings allocations (compat shim)."""
    key_by_code = {code: key for code, _label, key in _FIXED_KEYS}
    updated: list[DriverDef] = []
    for d in drivers:
        if d.code in key_by_code and d.allocation_method == "percentage":
            key = key_by_code[d.code]
            if key in allocations:
                params = dict(d.method_params)
                params["pct"] = Decimal(str(allocations[key]))
                updated.append(
                    DriverDef(
                        code=d.code,
                        label=d.label,
                        cm_class=d.cm_class,
                        is_group=d.is_group,
                        parent_code=d.parent_code,
                        allocation_method=d.allocation_method,
                        allocation_basis=d.allocation_basis or "revenue",
                        method_params=params,
                        basis_params=d.basis_params,
                        source=d.source,
                        scope=d.scope,
                        sort_order=d.sort_order,
                        enabled=d.enabled,
                        is_system=d.is_system,
                    )
                )
                continue
        updated.append(d)
    return updated


def flatten_template_drivers(nodes: list[dict[str, Any]], parent_code: str | None = None) -> list[DriverDef]:
    """Flatten nested template ``drivers`` / ``children`` trees into DriverDef rows."""
    out: list[DriverDef] = []
    for i, node in enumerate(nodes):
        children = node.get("children") or []
        is_group = bool(node.get("is_group") or children)
        code = str(node["code"])
        d = DriverDef(
            code=code,
            label=str(node.get("label") or code),
            cm_class=str(node.get("cm_class") or (parent_code or "variable_cost")),
            is_group=is_group,
            parent_code=node.get("parent_code", parent_code),
            allocation_method=None if is_group else node.get("allocation_method"),
            allocation_basis=None if is_group else node.get("allocation_basis"),
            method_params=dict(node.get("method_params") or {}),
            basis_params=dict(node.get("basis_params") or {}),
            source=None if is_group else node.get("source"),
            scope=str(node.get("scope") or "header"),
            sort_order=int(node.get("sort_order", i)),
            enabled=bool(node.get("enabled", True)),
            is_system=bool(node.get("is_system", True)),
        )
        # Prefer explicit cm_class on groups from template
        if node.get("cm_class"):
            d.cm_class = str(node["cm_class"])
        out.append(d)
        if children:
            out.extend(flatten_template_drivers(children, parent_code=code))
    return out
