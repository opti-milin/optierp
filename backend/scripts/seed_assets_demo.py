"""Seed a realistic Assets demo dataset into an EXISTING company (idempotent).

Creates asset categories (incl. a non-depreciable Land category), locations, a few
fixed-asset items, assets across categories (submitted, with some depreciation already
posted so book values look real), and maintenance/repair logs — all through the service
layer, so schedules, GL postings and statuses are genuine.

Run against the dev database (defaults to the docker Postgres `erp`):

    cd backend
    ./.venv/Scripts/python.exe -m scripts.seed_assets_demo \
        --database-url postgresql+asyncpg://erp_owner:milin@localhost:5432/erp

Safe to re-run: existing categories / locations / items / assets (matched by name) are
skipped, and maintenance is only seeded when none exists yet.
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", "postgresql+asyncpg://erp_owner:milin@localhost:5432/erp"),
    )
    return parser.parse_args()


ARGS = _parse_args()
os.environ["DATABASE_URL"] = ARGS.database_url
os.environ.setdefault("MIGRATIONS_DATABASE_URL", ARGS.database_url)
os.environ.setdefault("SECRET_KEY", "seed-assets-demo-not-used-0123456789")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory, set_company_context  # noqa: E402
from app.core.security import CurrentUser  # noqa: E402
from app.models.accounts import Account  # noqa: E402
from app.models.assets import Asset, AssetCategory, AssetMaintenance, Location  # noqa: E402
from app.models.core import Company, UserRole  # noqa: E402
from app.registry import get_descriptor  # noqa: E402
from app.schemas.assets import AssetCreate  # noqa: E402
from app.services import asset as asset_service  # noqa: E402
from app.services import registry as registry_service  # noqa: E402


def _fy_start(today: date) -> date:
    return date(today.year, 4, 1) if today.month >= 4 else date(today.year - 1, 4, 1)


async def _account(db: AsyncSession, company_id: uuid.UUID, name: str) -> uuid.UUID:
    acc = await db.scalar(
        select(Account).where(Account.company_id == company_id, Account.account_name == name)
    )
    if acc is None:
        raise SystemExit(f"Account '{name}' not found in the company's chart of accounts")
    return acc.id


async def _get_or_create_category(db, actor, company_id, name, **fields) -> uuid.UUID:
    existing = await db.scalar(
        select(AssetCategory).where(
            AssetCategory.company_id == company_id, AssetCategory.category_name == name
        )
    )
    if existing is not None:
        # Backfill IT Act block so Tax Depreciation → Sync from assets works
        block = fields.get("tax_block_code")
        if block and existing.tax_block_code != block:
            existing.tax_block_code = block
            await db.flush()
            print(f"  · patched category {name} tax_block_code={block}")
        return existing.id
    payload = {"category_name": name, **fields}
    obj = await registry_service.create_document(db, get_descriptor("asset-category"), payload, actor)
    print(f"  + category {name}")
    return uuid.UUID(str(obj["id"]))


async def _get_or_create_location(db, actor, company_id, name) -> uuid.UUID:
    existing = await db.scalar(
        select(Location).where(Location.company_id == company_id, Location.location_name == name)
    )
    if existing is not None:
        return existing.id
    obj = await registry_service.create_document(db, get_descriptor("location"), {"location_name": name}, actor)
    print(f"  + location {name}")
    return uuid.UUID(str(obj["id"]))


async def _create_asset(
    db, actor, company_id, *, name, category_id, gross, in_use, location_id=None,
    custodian=None, depreciate_to=None,
) -> Asset | None:
    existing = await db.scalar(
        select(Asset).where(Asset.company_id == company_id, Asset.asset_name == name)
    )
    if existing is not None:
        return None
    asset = await asset_service.create_asset(
        db,
        AssetCreate(
            asset_name=name, asset_category_id=category_id, gross_purchase_amount=Decimal(gross),
            available_for_use_date=in_use, location_id=location_id, custodian=custodian,
            purchase_date=in_use,
        ),
        actor,
    )
    await asset_service.submit_asset(db, asset.id, actor)
    if depreciate_to is not None:
        await asset_service.depreciate_asset(db, asset.id, actor, on_date=depreciate_to)
    print(f"  + asset {name}")
    return asset


async def main() -> None:
    today = date.today()
    fy_start = _fy_start(today)
    async with async_session_factory() as db:
        company = await db.scalar(select(Company).limit(1))
        if company is None:
            raise SystemExit("No company found — seed/create a company first")
        admin_id = await db.scalar(
            select(UserRole.user_id).where(UserRole.role == "System Manager").limit(1)
        ) or await db.scalar(select(UserRole.user_id).limit(1))
        if admin_id is None:
            raise SystemExit("No user found to attribute the demo data to")
        actor = CurrentUser(
            {"sub": str(admin_id), "company_id": str(company.id), "roles": ["System Manager"]}
        )
        await set_company_context(db, company.id)
        print(f"Seeding Assets demo into '{company.company_name}'…")

        dep = await _account(db, company.id, "Depreciation")
        accum = await _account(db, company.id, "Accumulated Depreciations")
        pm = await _account(db, company.id, "Plants and Machineries")
        electronic = await _account(db, company.id, "Electronic Equipment")
        furniture = await _account(db, company.id, "Furniture and Fixtures")
        office = await _account(db, company.id, "Office Equipment")
        capital = await _account(db, company.id, "Capital Equipment")
        buildings = await _account(db, company.id, "Buildings")

        dep_accounts = {
            "depreciation_expense_account_id": str(dep),
            "accumulated_depreciation_account_id": str(accum),
        }
        # --- categories (tax_block_code drives Tax Depreciation → Sync from assets) ---
        plant = await _get_or_create_category(
            db, actor, company.id, "Plant & Machinery", depreciation_method="Straight Line",
            total_number_of_depreciations=60, frequency_of_depreciation_months=1,
            salvage_value_percent=5, fixed_asset_account_id=str(pm),
            tax_block_code="PLANT_15", **dep_accounts,
        )
        computers = await _get_or_create_category(
            db, actor, company.id, "Computers & IT", depreciation_method="Written Down Value",
            total_number_of_depreciations=36, frequency_of_depreciation_months=1,
            salvage_value_percent=5, fixed_asset_account_id=str(electronic),
            tax_block_code="PLANT_40", **dep_accounts,
        )
        furn = await _get_or_create_category(
            db, actor, company.id, "Furniture & Fixtures", depreciation_method="Straight Line",
            total_number_of_depreciations=120, frequency_of_depreciation_months=1,
            salvage_value_percent=0, fixed_asset_account_id=str(furniture),
            tax_block_code="FURNITURE_10", **dep_accounts,
        )
        vehicles = await _get_or_create_category(
            db, actor, company.id, "Vehicles", depreciation_method="Written Down Value",
            total_number_of_depreciations=96, frequency_of_depreciation_months=1,
            salvage_value_percent=10, fixed_asset_account_id=str(capital),
            tax_block_code="PLANT_30", **dep_accounts,
        )
        office_eq = await _get_or_create_category(
            db, actor, company.id, "Office Equipment", depreciation_method="Straight Line",
            total_number_of_depreciations=60, frequency_of_depreciation_months=1,
            salvage_value_percent=0, fixed_asset_account_id=str(office),
            tax_block_code="PLANT_15", **dep_accounts,
        )
        building_cat = await _get_or_create_category(
            db, actor, company.id, "Buildings", depreciation_method="Straight Line",
            total_number_of_depreciations=360, frequency_of_depreciation_months=1,
            salvage_value_percent=5, fixed_asset_account_id=str(buildings),
            tax_block_code="BUILDING_10", **dep_accounts,
        )
        land = await _get_or_create_category(
            db, actor, company.id, "Land & Freehold", is_non_depreciable=True,
            depreciation_method="Straight Line", total_number_of_depreciations=0,
            fixed_asset_account_id=str(buildings),
        )

        # --- locations ---
        ho = await _get_or_create_location(db, actor, company.id, "Head Office (Mumbai)")
        wh = await _get_or_create_location(db, actor, company.id, "Regional Warehouse (Pune)")
        await _get_or_create_location(db, actor, company.id, "Service Center (Nagpur)")

        # --- assets (depreciate the depreciable ones up to today) ---
        created = []
        created.append(await _create_asset(
            db, actor, company.id, name="Toyota Forklift 2T", category_id=plant, gross="850000",
            in_use=fy_start, location_id=wh, custodian="Warehouse Manager", depreciate_to=today))
        created.append(await _create_asset(
            db, actor, company.id, name="Tata Ace Delivery Van", category_id=vehicles, gross="650000",
            in_use=fy_start, location_id=wh, custodian="Logistics", depreciate_to=today))
        created.append(await _create_asset(
            db, actor, company.id, name="Dell Latitude Laptops (batch of 10)", category_id=computers,
            gross="750000", in_use=fy_start, location_id=ho, custodian="IT Department", depreciate_to=today))
        created.append(await _create_asset(
            db, actor, company.id, name="Office Workstations", category_id=furn, gross="300000",
            in_use=fy_start, location_id=ho, depreciate_to=today))
        created.append(await _create_asset(
            db, actor, company.id, name="Air Conditioning Units", category_id=office_eq, gross="220000",
            in_use=fy_start, location_id=ho, depreciate_to=today))
        created.append(await _create_asset(
            db, actor, company.id, name="Head Office Building", category_id=building_cat,
            gross="15000000", in_use=fy_start, location_id=ho, depreciate_to=today))
        # land: non-depreciable, held at cost (revalue it from the UI to see appreciation)
        created.append(await _create_asset(
            db, actor, company.id, name="Industrial Plot - MIDC", category_id=land, gross="8000000",
            in_use=fy_start, location_id=wh))

        # --- maintenance / repair logs (only if none exist yet) ---
        any_mnt = await db.scalar(
            select(AssetMaintenance).where(AssetMaintenance.company_id == company.id).limit(1)
        )
        forklift = await db.scalar(
            select(Asset).where(Asset.company_id == company.id, Asset.asset_name == "Toyota Forklift 2T")
        )
        van = await db.scalar(
            select(Asset).where(Asset.company_id == company.id, Asset.asset_name == "Tata Ace Delivery Van")
        )
        building = await db.scalar(
            select(Asset).where(Asset.company_id == company.id, Asset.asset_name == "Head Office Building")
        )
        if any_mnt is None and forklift is not None:
            logs = [
                (forklift.id, "Preventive", "Quarterly hydraulic & brake service", 12000, "Completed"),
                (forklift.id, "Repair", "Replaced worn lift chain", 8500, "Completed"),
                (van.id, "Preventive", "10,000 km service + oil change", 6500, "Completed"),
                (van.id, "Repair", "Clutch plate replacement", 14500, "Open"),
                (building.id, "Repair", "Terrace waterproofing", 45000, "Open"),
                (forklift.id, "Inspection", "Annual fixed-asset physical audit", 0, "Open"),
            ]
            for asset_id, mtype, desc, cost, status in logs:
                await registry_service.create_document(
                    db, get_descriptor("asset-maintenance"),
                    {
                        "asset_id": str(asset_id), "maintenance_type": mtype,
                        "maintenance_date": today.isoformat(), "description": desc,
                        "cost": cost, "status": status,
                    },
                    actor,
                )
            print(f"  + {len(logs)} maintenance/repair logs")

    n = len([a for a in created if a is not None])
    print(f"Done. {n} new assets created (re-run is a no-op for existing names).")


if __name__ == "__main__":
    asyncio.run(main())
