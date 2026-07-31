"""Company cost-structure CRUD, snapshots, and template apply."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.cm_planning import CmCostDriver, CmCostStructure
from app.services.audit import log_audit
from app.services.cm_planning import settings as planning_settings
from app.services.cm_planning.compat import (
    allocations_from_drivers,
    drivers_from_allocations,
    flatten_template_drivers,
    sync_allocations_into_drivers,
)
from app.services.cm_planning.types import DriverDef


def drivers_to_snapshot(drivers: list[DriverDef]) -> list[dict[str, Any]]:
    return [d.to_dict() for d in sorted(drivers, key=lambda x: (x.sort_order, x.code))]


def snapshot_to_drivers(snapshot: list[dict[str, Any]] | None) -> list[DriverDef]:
    if not snapshot:
        return drivers_from_allocations(None)
    return [DriverDef.from_dict(row) for row in snapshot]


def _orm_to_def(row: CmCostDriver) -> DriverDef:
    return DriverDef(
        code=row.code,
        label=row.label,
        cm_class=row.cm_class,
        is_group=row.is_group,
        parent_code=row.parent_code,
        allocation_method=row.allocation_method,
        allocation_basis=row.allocation_basis,
        method_params=dict(row.method_params or {}),
        basis_params=dict(row.basis_params or {}),
        source=row.source,
        scope=row.scope or "header",
        sort_order=row.sort_order or 0,
        enabled=row.enabled,
        is_system=row.is_system,
    )


async def get_active_structure(
    db: AsyncSession, company_id: uuid.UUID
) -> CmCostStructure | None:
    return await db.scalar(
        select(CmCostStructure)
        .where(
            CmCostStructure.company_id == company_id,
            CmCostStructure.is_active.is_(True),
        )
        .options(selectinload(CmCostStructure.drivers))
        .order_by(CmCostStructure.effective_from.desc())
        .limit(1)
    )


async def ensure_default_structure(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    allocations: dict[str, Any] | None = None,
    template_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> CmCostStructure:
    existing = await get_active_structure(db, company_id)
    if existing is not None:
        return existing
    drivers = drivers_from_allocations(allocations)
    structure = CmCostStructure(
        company_id=company_id,
        name="Default",
        template_id=template_id,
        effective_from=date.today(),
        is_active=True,
        owner=user_id,
        modified_by=user_id,
    )
    db.add(structure)
    await db.flush()
    for d in drivers:
        db.add(
            CmCostDriver(
                cost_structure_id=structure.id,
                code=d.code,
                label=d.label,
                parent_code=d.parent_code,
                is_group=d.is_group,
                cm_class=d.cm_class,
                allocation_method=d.allocation_method,
                allocation_basis=d.allocation_basis,
                method_params={k: str(v) if isinstance(v, Decimal) else v for k, v in d.method_params.items()},
                basis_params=d.basis_params,
                source=d.source,
                scope=d.scope,
                sort_order=d.sort_order,
                enabled=d.enabled,
                is_system=d.is_system,
                owner=user_id,
                modified_by=user_id,
            )
        )
    await db.flush()
    return await get_active_structure(db, company_id)  # type: ignore[return-value]


async def resolve_drivers_for_plan(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    snapshot: list[dict[str, Any]] | None,
    allocations: dict[str, Any] | None,
) -> list[DriverDef]:
    if snapshot:
        return snapshot_to_drivers(snapshot)
    structure = await get_active_structure(db, company_id)
    if structure is not None and structure.drivers:
        drivers = [_orm_to_def(r) for r in structure.drivers]
        if allocations:
            drivers = sync_allocations_into_drivers(drivers, allocations)
        return drivers
    return drivers_from_allocations(allocations)


def structure_to_dict(structure: CmCostStructure) -> dict[str, Any]:
    drivers = [_orm_to_def(r) for r in structure.drivers]
    return {
        "id": str(structure.id),
        "name": structure.name,
        "template_id": structure.template_id,
        "effective_from": structure.effective_from.isoformat() if structure.effective_from else None,
        "is_active": structure.is_active,
        "drivers": drivers_to_snapshot(drivers),
        "allocations": {
            k: str(v) for k, v in allocations_from_drivers(drivers).items()
        },
    }


async def get_cost_structure_payload(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    structure = await ensure_default_structure(
        db,
        company_id,
        allocations=(await planning_settings.get_planning_settings(db, company_id)).allocations,
        template_id=(await planning_settings.get_planning_settings(db, company_id)).default_template,
    )
    return structure_to_dict(structure)


async def replace_structure_drivers(
    db: AsyncSession,
    company_id: uuid.UUID,
    drivers_payload: list[dict[str, Any]],
    user: CurrentUser,
    *,
    name: str | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    structure = await ensure_default_structure(db, company_id, user_id=user.id)
    # Deactivate old and create new effective version when replacing heavily
    structure.is_active = False
    structure.modified_by = user.id

    new = CmCostStructure(
        company_id=company_id,
        name=name or structure.name,
        template_id=template_id if template_id is not None else structure.template_id,
        effective_from=date.today(),
        is_active=True,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(new)
    await db.flush()

    defs = [DriverDef.from_dict(d) for d in drivers_payload]
    for d in defs:
        db.add(
            CmCostDriver(
                cost_structure_id=new.id,
                code=d.code,
                label=d.label,
                parent_code=d.parent_code,
                is_group=d.is_group,
                cm_class=d.cm_class,
                allocation_method=d.allocation_method,
                allocation_basis=d.allocation_basis,
                method_params={k: str(v) if isinstance(v, Decimal) else v for k, v in d.method_params.items()},
                basis_params=d.basis_params,
                source=d.source,
                scope=d.scope,
                sort_order=d.sort_order,
                enabled=d.enabled,
                is_system=d.is_system,
                owner=user.id,
                modified_by=user.id,
            )
        )
    await db.flush()
    await log_audit(
        db,
        doctype="CM Cost Structure",
        document_id=new.id,
        action="UPDATE",
        user_id=user.id,
        company_id=company_id,
    )
    loaded = await get_active_structure(db, company_id)
    if loaded is None:
        raise ValidationError("Failed to load cost structure after save")
    return structure_to_dict(loaded)


async def apply_template_to_structure(
    db: AsyncSession, company_id: uuid.UUID, template_id: str, user: CurrentUser
) -> dict[str, Any]:
    data = planning_settings.load_planning_template(template_id)
    raw_drivers = data.get("drivers")
    if isinstance(raw_drivers, list) and raw_drivers and isinstance(raw_drivers[0], dict) and (
        "allocation_method" in raw_drivers[0] or "is_group" in raw_drivers[0] or "children" in raw_drivers[0]
    ):
        defs = flatten_template_drivers(raw_drivers)
    else:
        # Legacy template: prefer map + allocations
        defs = drivers_from_allocations(data.get("allocations"))
        prefer = data.get("drivers") if isinstance(data.get("drivers"), dict) else {}
        if isinstance(prefer, dict):
            updated: list[DriverDef] = []
            for d in defs:
                pref = prefer.get(d.code) if isinstance(prefer.get(d.code), dict) else None
                if pref and pref.get("prefer"):
                    source_map = {
                        "bom": "bom_material",
                        "bom_operating": "bom_operating",
                        "shipping_rule": "shipping_rule",
                        "sales_partner": "sales_partner",
                        "cost_rate": "cost_rate",
                        "off": "none",
                    }
                    src = source_map.get(str(pref["prefer"]), d.source)
                    updated.append(
                        DriverDef(
                            code=d.code,
                            label=d.label,
                            cm_class=d.cm_class,
                            is_group=d.is_group,
                            parent_code=d.parent_code,
                            allocation_method=d.allocation_method,
                            allocation_basis=d.allocation_basis,
                            method_params=d.method_params,
                            basis_params=d.basis_params,
                            source=src,
                            scope=d.scope,
                            sort_order=d.sort_order,
                            enabled=d.enabled and src != "none",
                            is_system=d.is_system,
                        )
                    )
                else:
                    updated.append(d)
            defs = updated

    payload = await replace_structure_drivers(
        db,
        company_id,
        drivers_to_snapshot(defs),
        user,
        name=str(data.get("label") or template_id),
        template_id=template_id,
    )
    # Update settings targets / allocations without re-syncing structure (already replaced)
    from app.schemas.cm_planning import CmPlanningSettings
    from app.schemas.core import SystemSettingUpsert
    from app.services import settings as settings_service

    current = await planning_settings.get_planning_settings(db, company_id)
    alloc = data.get("allocations") or {}
    updated = CmPlanningSettings(
        target_cm1_pct=Decimal(str(data.get("target_cm1_pct", current.target_cm1_pct))),
        target_cm2_pct=current.target_cm2_pct,
        min_cm1_pct=Decimal(str(data.get("min_cm1_pct", current.min_cm1_pct))),
        submit_policy=current.submit_policy,
        default_template=template_id,
        max_scenarios_per_plan=current.max_scenarios_per_plan,
        allocations={
            "product_channel_fixed_pct_of_revenue": Decimal(
                str(alloc.get("product_channel_fixed_pct_of_revenue", 3))
            ),
            "segment_bu_fixed_pct_of_revenue": Decimal(
                str(alloc.get("segment_bu_fixed_pct_of_revenue", 2))
            ),
            "corporate_overhead_pct_of_revenue": Decimal(
                str(alloc.get("corporate_overhead_pct_of_revenue", 5))
            ),
        },
    )
    await settings_service.upsert_setting(
        db,
        SystemSettingUpsert(
            key=planning_settings.CM_PLANNING_SETTINGS_KEY,
            value=updated.model_dump(mode="json"),
            company_id=company_id,
        ),
        user,
    )
    return payload


async def sync_structure_from_allocations(
    db: AsyncSession, company_id: uuid.UUID, allocations: dict[str, Any], user: CurrentUser
) -> None:
    """When settings allocations change, update active structure fixed leaves."""
    structure = await get_active_structure(db, company_id)
    if structure is None:
        await ensure_default_structure(db, company_id, allocations=allocations, user_id=user.id)
        return
    drivers = sync_allocations_into_drivers([_orm_to_def(r) for r in structure.drivers], allocations)
    await replace_structure_drivers(
        db,
        company_id,
        drivers_to_snapshot(drivers),
        user,
        name=structure.name,
        template_id=structure.template_id,
    )


async def get_structure_or_404(db: AsyncSession, structure_id: uuid.UUID, company_id: uuid.UUID) -> CmCostStructure:
    row = await db.scalar(
        select(CmCostStructure)
        .where(CmCostStructure.id == structure_id, CmCostStructure.company_id == company_id)
        .options(selectinload(CmCostStructure.drivers))
    )
    if row is None:
        raise NotFoundError("Cost structure not found")
    return row
