"""Seed a complete MSME Income Tax dataset for FY 2025-26 (AY 2026-27).

Builds one CA-style year-end scenario on the first company: registration, regime,
P&L journals, fixed assets with IT Act block codes, depreciation sync, BF losses,
MAT credit carry, computation + adjustments, advance/self-assessment challans,
TDS/TCS credits (matched + mismatch + only-in-books), mock Form 26AS recon, and
a computation run so Result / Credits / Depreciation screens show real numbers.

Also fixes "Sync from assets" empty registers by setting ``tax_block_code`` on
asset categories and creating submitted assets linked to those blocks.

Idempotent — safe to re-run. Skips rows keyed by remarks / asset names / challan serials.

Usage (from backend/, owner DB URL):

    python -m scripts.seed_income_tax_msme_demo
    python -m scripts.seed_income_tax_msme_demo \\
        --database-url postgresql+asyncpg://erp_owner:milin@localhost:5432/erp

Prerequisites: ``seed_demo`` (company + COA) and ``scripts.load_statutory`` (packs).
"""

from __future__ import annotations

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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://erp_owner:milin@localhost:5432/erp"
        ),
    )
    p.add_argument(
        "--ay",
        default="2026-27",
        help="Assessment year for FY 2025-26 (default 2026-27)",
    )
    return p.parse_args()


ARGS = _parse_args()
os.environ["DATABASE_URL"] = ARGS.database_url
os.environ.setdefault("MIGRATIONS_DATABASE_URL", ARGS.database_url)
os.environ.setdefault("SECRET_KEY", "seed-income-tax-msme-demo-not-used-0123456789")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory, set_company_context  # noqa: E402
from app.core.security import CurrentUser  # noqa: E402
from app.models import statutory as stat  # noqa: E402
from app.models.accounts import Account, JournalEntry  # noqa: E402
from app.models.assets import Asset, AssetCategory  # noqa: E402
from app.models.core import Company, User  # noqa: E402
from app.models.tax_computation import TaxComputation  # noqa: E402
from app.models.tax_corporate import MatCreditLedger, TaxLossCarryForward  # noqa: E402
from app.models.tax_credits import TaxChallan, TaxCreditEntry  # noqa: E402
from app.registry import get_descriptor  # noqa: E402
from app.schemas.accounts import JournalEntryAccountIn, JournalEntryCreate  # noqa: E402
from app.schemas.accounts.masters import AccountCreate  # noqa: E402
from app.schemas.assets import AssetCreate  # noqa: E402
from app.schemas.taxation import (  # noqa: E402
    TaxChallanCreate,
    TaxComputationAdjustmentLineIn,
    TaxComputationCreate,
    TaxComputationIncomeLineIn,
    TaxCreditEntryCreate,
    TaxDepreciationSyncRequest,
    TaxLossCarryForwardCreate,
    TaxPolicyOverrideCreate,
    TaxRegistrationUpsert,
    TaxRegimeElectionCreate,
)
from app.services import accounts_masters as masters  # noqa: E402
from app.services import asset as asset_service  # noqa: E402
from app.services import journal_entry as je_service  # noqa: E402
from app.services import registry as registry_service  # noqa: E402
from app.services.taxation import challans as challan_service  # noqa: E402
from app.services.taxation import computations as comp_service  # noqa: E402
from app.services.taxation import credits as credit_service  # noqa: E402
from app.services.taxation import depreciation as dep_service  # noqa: E402
from app.services.taxation import loss_setoff as loss_service  # noqa: E402
from app.services.taxation import recon_26as  # noqa: E402
from app.services.taxation import registration as reg_service  # noqa: E402
from app.services.taxation import runs as run_service  # noqa: E402
from app.services.taxation.catalogue.loader import load_all_packs  # noqa: E402

D = Decimal
AY = ARGS.ay
FY_START = date(2025, 4, 1)
FY_END = date(2026, 3, 31)
SEED_TAG = "MSME FY2025-26 seed"
COMP_REMARKS = f"{SEED_TAG} — Precision Components year-end worksheet"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _actor(db: AsyncSession, company: Company) -> CurrentUser:
    user = await db.scalar(select(User).where(User.email == "admin@example.com"))
    if user is None:
        user = await db.scalar(select(User).order_by(User.creation.asc()))
    if user is None:
        raise SystemExit("No user found — run seed_demo first")
    return CurrentUser(
        {
            "sub": str(user.id),
            "email": user.email,
            "company_id": str(company.id),
            "roles": ["System Manager", "Accounts Manager"],
        }
    )


async def _leaf(
    db: AsyncSession, company_id: uuid.UUID, names: list[str], *, root_type: str | None = None
) -> Account:
    for name in names:
        stmt = select(Account).where(
            Account.company_id == company_id,
            Account.account_name == name,
            Account.is_group.is_(False),
        )
        if root_type:
            stmt = stmt.where(Account.root_type == root_type)
        acc = await db.scalar(stmt)
        if acc is not None:
            return acc
    stmt = select(Account).where(Account.company_id == company_id, Account.is_group.is_(False))
    if root_type:
        stmt = stmt.where(Account.root_type == root_type)
    acc = await db.scalar(stmt)
    if acc is None:
        raise SystemExit(f"No leaf account matching {names}")
    return acc


async def _group(db: AsyncSession, company_id: uuid.UUID, names: list[str]) -> Account:
    for name in names:
        acc = await db.scalar(
            select(Account).where(
                Account.company_id == company_id,
                Account.account_name == name,
                Account.is_group.is_(True),
            )
        )
        if acc is not None:
            return acc
    raise SystemExit(f"Group account not found: {names}")


async def _ensure_account(
    db: AsyncSession,
    actor: CurrentUser,
    company_id: uuid.UUID,
    *,
    name: str,
    parent: Account,
    account_type: str | None = None,
) -> Account:
    existing = await db.scalar(
        select(Account).where(
            Account.company_id == company_id,
            Account.account_name == name,
            Account.is_group.is_(False),
        )
    )
    if existing is not None:
        return existing
    return await masters.create_account(
        db,
        AccountCreate(
            account_name=name,
            parent_account_id=parent.id,
            account_type=account_type,
        ),
        actor,
    )


async def _ensure_statutory(db: AsyncSession) -> None:
    ay = await db.scalar(select(stat.AssessmentYear).where(stat.AssessmentYear.code == AY))
    if ay is not None:
        return
    print(f"Statutory pack for {AY} missing — loading all packs…")
    await load_all_packs(db)
    await db.commit()
    ay = await db.scalar(select(stat.AssessmentYear).where(stat.AssessmentYear.code == AY))
    if ay is None:
        raise SystemExit(f"Failed to load statutory pack for AY {AY}")


# ---------------------------------------------------------------------------
# Seed steps
# ---------------------------------------------------------------------------


async def seed_registration(db: AsyncSession, actor: CurrentUser, company: Company) -> None:
    # Company 4th char of PAN must be C
    if not company.pan or company.pan[3:4].upper() != "C":
        company.pan = "AABCT3921C"
    if not company.tan:
        company.tan = "MUMT39214C"
    await db.flush()

    await reg_service.upsert_registration(
        db,
        TaxRegistrationUpsert(
            pan="AABCT3921C",
            tan="MUMT39214C",
            cin="U28990MH2014PTC259321",
            assessee_class_code="Company",
            residential_status="Resident",
            incorporation_date=date(2014, 8, 18),
            nature_of_business_codes=["Manufacturing - metal products", "Trading"],
            jurisdiction="ITO Ward 12(3), Mumbai",
            default_assessment_year=AY,
            itr_efile_provider="sandbox",
            remarks=f"{SEED_TAG} — MSME private limited (precision components)",
        ),
        actor,
    )
    await reg_service.create_or_update_election(
        db,
        TaxRegimeElectionCreate(
            ay_code=AY,
            regime_code="Normal",
            assessee_class_code="Company",
            elected_on=date(2025, 4, 1),
            remarks=f"{SEED_TAG} — Normal provisions (MAT applicable)",
        ),
        actor,
    )
    # Pin 25% domestic-company schedule (turnover ≪ ₹400 Cr)
    existing = await reg_service.list_overrides(db, actor.company_id, ay_code=AY)  # type: ignore[arg-type]
    if not any(o.target_code == "CO_NORMAL_25" for o in existing):
        await reg_service.create_override(
            db,
            TaxPolicyOverrideCreate(
                ay_code=AY,
                override_kind="pin_rate_schedule",
                target_code="CO_NORMAL_25",
                reason="MSME preceding-year turnover under ₹400 Cr — pin 25% company rate",
                params={"turnover_fy_minus_1": "845000000"},
            ),
            actor,
        )
    print("  ✓ registration + Normal regime + CO_NORMAL_25 pin")


async def seed_tax_accounts(
    db: AsyncSession, actor: CurrentUser, company: Company
) -> tuple[Account, Account, Account]:
    duties = await _group(db, company.id, ["Duties and Taxes", "Duties & Taxes"])
    try:
        expense_group = await _group(
            db, company.id, ["Indirect Expenses", "Expenses", "Direct Expenses"]
        )
    except SystemExit:
        # Fall back: parent of any existing expense leaf
        any_exp = await _leaf(
            db, company.id, ["Office Expenses", "Depreciation", "Rent"], root_type="Expense"
        )
        if any_exp.parent_account_id is None:
            raise SystemExit("Cannot locate expense group for Income Tax Expense")
        expense_group = await db.get(Account, any_exp.parent_account_id)
        assert expense_group is not None
    payable = await _ensure_account(
        db, actor, company.id, name="Income Tax Payable", parent=duties, account_type="Tax"
    )
    expense = await _ensure_account(
        db, actor, company.id, name="Income Tax Expense", parent=expense_group
    )
    bank = await _leaf(db, company.id, ["HDFC Current", "HDFC Bank", "Bank Accounts"], root_type="Asset")
    if company.default_bank_account_id is None:
        company.default_bank_account_id = bank.id
        await db.flush()
    print(f"  ✓ tax GL accounts (payable={payable.account_name}, bank={bank.account_name})")
    return bank, payable, expense


async def seed_pnl_journals(db: AsyncSession, actor: CurrentUser, company: Company) -> None:
    """FY 2025-26 summary journals so Trial Balance / P&L have substance."""
    already = await db.scalar(
        select(JournalEntry.id).where(
            JournalEntry.company_id == company.id,
            JournalEntry.remarks == f"{SEED_TAG} P&L close summary",
        )
    )
    if already is not None:
        print("  · P&L journals already present — skip")
        return

    sales = await _leaf(db, company.id, ["Sales"], root_type="Income")
    cogs = await _leaf(db, company.id, ["Cost of Goods Sold"], root_type="Expense")
    bank = await _leaf(db, company.id, ["HDFC Current"], root_type="Asset")
    salary = await _leaf(
        db, company.id, ["Salaries and Wages", "Salary", "Payroll Expenses"], root_type="Expense"
    )
    rent = await _leaf(db, company.id, ["Rent", "Office Rent", "Rent Expense"], root_type="Expense")
    dep = await _leaf(db, company.id, ["Depreciation"], root_type="Expense")
    interest_exp = await _leaf(
        db, company.id, ["Interest Expense", "Bank Interest", "Finance Charges"], root_type="Expense"
    )
    other_exp = await _leaf(
        db,
        company.id,
        ["Office Expenses", "Miscellaneous Expenses", "Administrative Expenses"],
        root_type="Expense",
    )
    interest_inc = await _leaf(
        db, company.id, ["Interest Income", "Other Income", "Indirect Income"], root_type="Income"
    )

    # Condensed year-end books for a mid-size precision-components MSME
    # Sales 8.45 Cr; COGS 5.12 Cr; emp 1.18 Cr; rent 24L; dep 18.2L; interest 9.6L;
    # other opex 42.5L; interest income 1.85L → PBT ≈ 1.22 Cr (worksheet uses tax PGBP separately)
    rows = [
        # Bank / Sales
        (bank, D("84500000"), D("0")),
        (sales, D("0"), D("84500000")),
    ]
    je1 = await je_service.create_journal_entry(
        db,
        JournalEntryCreate(
            posting_date=date(2026, 3, 31),
            remarks=f"{SEED_TAG} FY sales realisation",
            accounts=[
                JournalEntryAccountIn(account_id=a.id, debit=dr, credit=cr) for a, dr, cr in rows
            ],
        ),
        actor,
    )
    await je_service.submit_journal_entry(db, je1.id, actor)

    expense_rows = [
        (cogs, D("51200000"), "COGS"),
        (salary, D("11800000"), "Payroll"),
        (rent, D("2400000"), "Factory + HO rent"),
        (dep, D("1820000"), "Book depreciation"),
        (interest_exp, D("960000"), "Term-loan interest"),
        (other_exp, D("4250000"), "Power, freight, repairs, admin"),
    ]
    total_exp = sum((amt for _, amt, _ in expense_rows), D("0"))
    je2 = await je_service.create_journal_entry(
        db,
        JournalEntryCreate(
            posting_date=date(2026, 3, 31),
            remarks=f"{SEED_TAG} P&L close summary",
            accounts=[
                *[
                    JournalEntryAccountIn(
                        account_id=acc.id, debit=amt, credit=D("0"), user_remark=label
                    )
                    for acc, amt, label in expense_rows
                ],
                JournalEntryAccountIn(
                    account_id=interest_inc.id, debit=D("0"), credit=D("185000"),
                    user_remark="FD / savings interest",
                ),
                JournalEntryAccountIn(
                    account_id=bank.id,
                    debit=D("0"),
                    credit=total_exp - D("185000"),
                    user_remark="Net cash / working-capital wash",
                ),
            ],
        ),
        actor,
    )
    await je_service.submit_journal_entry(db, je2.id, actor)
    print("  ✓ FY 2025-26 P&L summary journals (sales ₹8.45 Cr)")


CATEGORY_BLOCKS: dict[str, str] = {
    "Plant & Machinery": "PLANT_15",
    "Computers & IT": "PLANT_40",
    "Furniture & Fixtures": "FURNITURE_10",
    "Vehicles": "PLANT_30",
    "Office Equipment": "PLANT_15",
    "Buildings": "BUILDING_10",
    # MSME-specific categories created below
    "MSME Plant & Machinery": "PLANT_15",
    "MSME Computers": "PLANT_40",
    "MSME Furniture": "FURNITURE_10",
    "MSME Vehicles": "PLANT_30",
    "MSME Buildings": "BUILDING_10",
}


async def _patch_or_create_category(
    db: AsyncSession,
    actor: CurrentUser,
    company_id: uuid.UUID,
    *,
    name: str,
    block: str,
    dep_accounts: dict,
    fixed_asset_account_id: str,
    method: str = "Straight Line",
    total_deps: int = 60,
) -> uuid.UUID:
    existing = await db.scalar(
        select(AssetCategory).where(
            AssetCategory.company_id == company_id, AssetCategory.category_name == name
        )
    )
    if existing is not None:
        if existing.tax_block_code != block:
            existing.tax_block_code = block
            await db.flush()
            print(f"  · patched category '{name}' → {block}")
        return existing.id
    obj = await registry_service.create_document(
        db,
        get_descriptor("asset-category"),
        {
            "category_name": name,
            "depreciation_method": method,
            "total_number_of_depreciations": total_deps,
            "frequency_of_depreciation_months": 1,
            "salvage_value_percent": 5,
            "fixed_asset_account_id": fixed_asset_account_id,
            "tax_block_code": block,
            **dep_accounts,
        },
        actor,
    )
    print(f"  + category {name} ({block})")
    return uuid.UUID(str(obj["id"]))


async def seed_assets_and_depreciation(
    db: AsyncSession, actor: CurrentUser, company: Company
) -> list:
    dep = await _leaf(db, company.id, ["Depreciation"], root_type="Expense")
    accum = await _leaf(
        db, company.id, ["Accumulated Depreciations", "Accumulated Depreciation"], root_type="Asset"
    )
    pm = await _leaf(db, company.id, ["Plants and Machineries", "Plant and Machinery"], root_type="Asset")
    electronic = await _leaf(
        db, company.id, ["Electronic Equipment", "Computers"], root_type="Asset"
    )
    furniture = await _leaf(db, company.id, ["Furniture and Fixtures"], root_type="Asset")
    capital = await _leaf(db, company.id, ["Capital Equipment", "Vehicles"], root_type="Asset")
    buildings = await _leaf(db, company.id, ["Buildings"], root_type="Asset")

    dep_accounts = {
        "depreciation_expense_account_id": str(dep.id),
        "accumulated_depreciation_account_id": str(accum.id),
    }

    # Patch any existing demo categories so Sync works company-wide
    for cat_name, block in CATEGORY_BLOCKS.items():
        row = await db.scalar(
            select(AssetCategory).where(
                AssetCategory.company_id == company.id,
                AssetCategory.category_name == cat_name,
            )
        )
        if row is not None and row.tax_block_code != block:
            row.tax_block_code = block
            await db.flush()
            print(f"  · patched existing '{cat_name}' → {block}")

    plant_id = await _patch_or_create_category(
        db, actor, company.id, name="MSME Plant & Machinery", block="PLANT_15",
        dep_accounts=dep_accounts, fixed_asset_account_id=str(pm.id), total_deps=60,
    )
    computer_id = await _patch_or_create_category(
        db, actor, company.id, name="MSME Computers", block="PLANT_40",
        dep_accounts=dep_accounts, fixed_asset_account_id=str(electronic.id),
        method="Written Down Value", total_deps=36,
    )
    furn_id = await _patch_or_create_category(
        db, actor, company.id, name="MSME Furniture", block="FURNITURE_10",
        dep_accounts=dep_accounts, fixed_asset_account_id=str(furniture.id), total_deps=120,
    )
    vehicle_id = await _patch_or_create_category(
        db, actor, company.id, name="MSME Vehicles", block="PLANT_30",
        dep_accounts=dep_accounts, fixed_asset_account_id=str(capital.id),
        method="Written Down Value", total_deps=96,
    )
    building_id = await _patch_or_create_category(
        db, actor, company.id, name="MSME Buildings", block="BUILDING_10",
        dep_accounts=dep_accounts, fixed_asset_account_id=str(buildings.id), total_deps=360,
    )

    # Assets: mix of opening (pre-FY), full-year additions, half-rate (<180 days)
    assets_spec = [
        # name, category, gross, put_to_use
        ("MSME-CNC Lathe Ace Super", plant_id, "2450000", date(2023, 6, 12)),  # opening
        ("MSME-Hydraulic Press 100T", plant_id, "1850000", date(2024, 2, 20)),  # opening
        ("MSME-Packaging Line Phase-2", plant_id, "920000", date(2025, 5, 15)),  # full add
        ("MSME-Industrial UPS 80kVA", plant_id, "480000", date(2025, 11, 20)),  # half add
        ("MSME-Dell PowerEdge R760", computer_id, "625000", date(2025, 4, 10)),  # full
        ("MSME-Laptop batch FY26 Q3", computer_id, "312000", date(2025, 12, 1)),  # half
        ("MSME-Factory workstations", furn_id, "275000", date(2024, 1, 15)),  # opening
        ("MSME-Factory shed MIDC", building_id, "6800000", date(2022, 8, 1)),  # opening
        ("MSME-Tata Ace delivery van", vehicle_id, "785000", date(2025, 7, 1)),  # full
    ]

    created = 0
    for name, cat_id, gross, put in assets_spec:
        exists = await db.scalar(
            select(Asset.id).where(Asset.company_id == company.id, Asset.asset_name == name)
        )
        if exists is not None:
            continue
        asset = await asset_service.create_asset(
            db,
            AssetCreate(
                asset_name=name,
                asset_category_id=cat_id,
                gross_purchase_amount=D(gross),
                available_for_use_date=put,
                purchase_date=put,
            ),
            actor,
        )
        await asset_service.submit_asset(db, asset.id, actor)
        created += 1
    print(f"  ✓ assets ({created} new) with tax_block_code categories")

    # Prior AY closing WDV so UI "Sync from assets" (no overrides) carries correct opening.
    # Tax WDV ≠ accounting gross for older blocks.
    prior_ay = "2025-26"
    prior_closing = {
        "PLANT_15": D("3185000"),  # CNC + Press tax WDV at 31-Mar-2025
        "FURNITURE_10": D("220000"),
        "BUILDING_10": D("5200000"),
    }
    from app.models.tax_corporate import TaxDepreciationRegister

    for block_code, closing in prior_closing.items():
        block = await db.scalar(
            select(stat.DepreciationBlock).where(stat.DepreciationBlock.block_code == block_code)
        )
        if block is None:
            continue
        existing = await db.scalar(
            select(TaxDepreciationRegister).where(
                TaxDepreciationRegister.company_id == company.id,
                TaxDepreciationRegister.ay_code == prior_ay,
                TaxDepreciationRegister.block_code == block_code,
            )
        )
        if existing is not None:
            existing.closing_wdv = closing
            existing.opening_wdv = closing
            existing.remarks = f"{SEED_TAG} prior WDV b/f"
            continue
        db.add(
            TaxDepreciationRegister(
                company_id=company.id,
                ay_code=prior_ay,
                block_code=block_code,
                rate_percent=block.rate_percent,
                opening_wdv=closing,
                closing_wdv=closing,
                remarks=f"{SEED_TAG} prior WDV b/f",
                owner=actor.id,
                modified_by=actor.id,
            )
        )
    await db.commit()

    regs = await dep_service.sync_from_assets(
        db,
        TaxDepreciationSyncRequest(ay_code=AY),
        actor,
    )
    total_dep = sum(
        (r.depreciation_amount + r.additional_depreciation_amount for r in regs), D("0")
    )
    print(f"  ✓ depreciation sync → {len(regs)} blocks, tax dep ₹{total_dep:,.2f}")
    for r in regs:
        print(
            f"      {r.block_code}: open={r.opening_wdv:,.0f} full={r.additions_full:,.0f} "
            f"half={r.additions_half:,.0f} dep={r.depreciation_amount:,.0f}"
        )
    return regs


async def seed_losses_and_mat(db: AsyncSession, actor: CurrentUser, company: Company) -> None:
    assert actor.company_id is not None
    existing_loss = await db.scalar(
        select(TaxLossCarryForward.id).where(
            TaxLossCarryForward.company_id == company.id,
            TaxLossCarryForward.remarks == f"{SEED_TAG} BF business loss AY2024-25",
        )
    )
    if existing_loss is None:
        await loss_service.create_loss(
            db,
            TaxLossCarryForwardCreate(
                origin_ay_code="2024-25",
                setoff_group="ORDINARY",
                loss_kind="Business",
                amount=D("1250000"),
                expires_after_ay="2032-33",
                remarks=f"{SEED_TAG} BF business loss AY2024-25",
            ),
            actor,
        )
        print("  ✓ brought-forward business loss ₹12.50L (AY 2024-25)")
    else:
        print("  · BF loss already present — skip")

    # Unabsorbed depreciation remnant (smaller)
    existing_uad = await db.scalar(
        select(TaxLossCarryForward.id).where(
            TaxLossCarryForward.company_id == company.id,
            TaxLossCarryForward.remarks == f"{SEED_TAG} unabsorbed dep AY2024-25",
        )
    )
    if existing_uad is None:
        await loss_service.create_loss(
            db,
            TaxLossCarryForwardCreate(
                origin_ay_code="2024-25",
                setoff_group="ORDINARY",
                loss_kind="UnabsorbedDep",
                amount=D("185000"),
                expires_after_ay=None,
                remarks=f"{SEED_TAG} unabsorbed dep AY2024-25",
            ),
            actor,
        )
        print("  ✓ unabsorbed depreciation ₹1.85L")

    existing_mat = await db.scalar(
        select(MatCreditLedger.id).where(
            MatCreditLedger.company_id == company.id,
            MatCreditLedger.remarks == f"{SEED_TAG} prior MAT credit AY2025-26",
        )
    )
    if existing_mat is None:
        db.add(
            MatCreditLedger(
                company_id=company.id,
                ay_code="2025-26",
                entry_kind="Created",
                amount=D("180000"),
                tax_mat=D("980000"),
                tax_normal=D("800000"),
                expires_after_ay="2040-41",
                remarks=f"{SEED_TAG} prior MAT credit AY2025-26",
                owner=actor.id,
                modified_by=actor.id,
            )
        )
        await db.commit()
        print("  ✓ prior MAT credit ₹1.80L (115JAA)")
    else:
        print("  · MAT credit already present — skip")


async def seed_computation(db: AsyncSession, actor: CurrentUser) -> TaxComputation:
    assert actor.company_id is not None
    existing = await db.scalar(
        select(TaxComputation).where(
            TaxComputation.company_id == actor.company_id,
            TaxComputation.remarks == COMP_REMARKS,
        )
    )
    if existing is not None:
        print(f"  · computation {existing.name} already present — reuse")
        return await comp_service.get_computation(db, actor.company_id, existing.id)

    # Tax worksheet PGBP ≈ book PBT adjusted toward tax base before engine adjustments.
    # Engine will further Add/Less adjustment lines + deduct tax dep + set off BF loss.
    doc = await comp_service.create_computation(
        db,
        TaxComputationCreate(
            ay_code=AY,
            assessee_class_code="Company",
            regime_code="Normal",
            from_date=FY_START,
            to_date=FY_END,
            remarks=COMP_REMARKS,
            book_profit_115jb=D("9200000"),
            audit_applicable=True,
            income_lines=[
                TaxComputationIncomeLineIn(
                    seq=1,
                    head="PGBP",
                    income_character_code="ORDINARY",
                    sub_ref="Manufacturing — precision components",
                    gross=D("122150000"),
                    deductions=D("110200000"),
                    net=D("11950000"),
                ),
                TaxComputationIncomeLineIn(
                    seq=2,
                    head="OS",
                    income_character_code="ORDINARY",
                    sub_ref="Interest on FDs / savings",
                    gross=D("185000"),
                    deductions=D("0"),
                    net=D("185000"),
                ),
            ],
            adjustment_lines=[
                TaxComputationAdjustmentLineIn(
                    section_code="37-penalty",
                    provision_section_code="37-penalty",
                    stage="PGBP",
                    description=(
                        "Goods and Services Tax late fee and traffic penalties — not allowable"
                    ),
                    direction="Add",
                    amount=D("48500"),
                ),
                TaxComputationAdjustmentLineIn(
                    section_code="37-CSR",
                    provision_section_code="37-CSR",
                    stage="PGBP",
                    description="Corporate social responsibility spend under the Companies Act",
                    direction="Add",
                    amount=D("200000"),
                ),
                TaxComputationAdjustmentLineIn(
                    section_code="40A(3)",
                    provision_section_code="40A(3)",
                    stage="PGBP",
                    description="Cash purchase of scrap and consumables above ₹10,000",
                    direction="Add",
                    amount=D("185000"),
                ),
                TaxComputationAdjustmentLineIn(
                    section_code="43B",
                    provision_section_code="43B",
                    stage="PGBP",
                    description="Bonus / leave encashment unpaid by return due date",
                    direction="Add",
                    amount=D("345000"),
                ),
                TaxComputationAdjustmentLineIn(
                    section_code="40(a)(ia)",
                    provision_section_code="40(a)(ia)",
                    stage="PGBP",
                    description=(
                        "Thirty per cent of contractor bills — tax not deducted at source"
                    ),
                    direction="Add",
                    amount=D("120000"),
                ),
                TaxComputationAdjustmentLineIn(
                    section_code="43B",
                    provision_section_code="43B",
                    stage="PGBP",
                    description=(
                        "Earlier year's unpaid statutory dues, paid during the financial "
                        "year 2025-26 and now allowable"
                    ),
                    direction="Less",
                    amount=D("210000"),
                ),
                TaxComputationAdjustmentLineIn(
                    section_code="36(1)(va)",
                    provision_section_code="36(1)(va)",
                    stage="PGBP",
                    description=(
                        "Employees' provident fund contribution deposited after the due date"
                    ),
                    direction="Add",
                    amount=D("67500"),
                ),
            ],
        ),
        actor,
    )
    print(f"  ✓ computation {doc.name} (PGBP ₹1.195 Cr + OS ₹1.85L + 7 adjustments)")
    return doc


async def seed_challans(
    db: AsyncSession,
    actor: CurrentUser,
    computation_id: uuid.UUID,
    bank: Account,
    payable: Account,
) -> list[TaxChallan]:
    assert actor.company_id is not None
    specs = [
        # type, serial, date, amount, submit?, remarks
        ("AdvanceTax", "MSME-AT-Q1-0625", date(2025, 6, 14), D("150000"), True, "Q1 advance tax 15%"),
        ("AdvanceTax", "MSME-AT-Q2-0925", date(2025, 9, 13), D("300000"), True, "Q2 advance tax to 45%"),
        ("AdvanceTax", "MSME-AT-Q3-1225", date(2025, 12, 14), D("250000"), True, "Q3 advance tax to 75%"),
        ("AdvanceTax", "MSME-AT-Q4-0326", date(2026, 3, 14), D("200000"), True, "Q4 advance tax to 100%"),
        (
            "SelfAssessment",
            "MSME-SAT-0926",
            date(2026, 9, 12),
            D("75000"),
            True,
            "Self-assessment before filing",
        ),
        # Incorrect draft challan — wrong BSR / serial for UI edit & cancel workflows
        (
            "AdvanceTax",
            "MSME-BAD-DRAFT",
            date(2025, 12, 20),
            D("50000"),
            False,
            f"{SEED_TAG} INCORRECT draft — wrong BSR 9999999, do not submit",
        ),
    ]
    out: list[TaxChallan] = []
    for ctype, serial, dep_date, amount, do_submit, remarks in specs:
        exists = await db.scalar(
            select(TaxChallan.id).where(
                TaxChallan.company_id == actor.company_id,
                TaxChallan.challan_serial == serial,
            )
        )
        if exists is not None:
            row = await challan_service.get_challan(db, actor.company_id, exists)
            out.append(row)
            continue
        row = await challan_service.create_challan(
            db,
            TaxChallanCreate(
                ay_code=AY,
                challan_type=ctype,
                bsr_code="0004321" if serial != "MSME-BAD-DRAFT" else "9999999",
                challan_serial=serial,
                deposit_date=dep_date,
                amount=amount,
                cin=f"CIN{serial[-6:]}X" if do_submit else None,
                bank_account_id=bank.id,
                tax_payable_account_id=payable.id,
                computation_id=computation_id,
                remarks=remarks,
            ),
            actor,
        )
        if do_submit:
            row = await challan_service.submit_challan(db, row.id, actor)
        out.append(row)
    submitted = sum(1 for c in out if c.docstatus == 1)
    print(f"  ✓ challans ({submitted} submitted + 1 incorrect draft)")
    return out


async def seed_credits_and_26as(
    db: AsyncSession,
    actor: CurrentUser,
    computation_id: uuid.UUID,
    challans: list[TaxChallan],
) -> None:
    assert actor.company_id is not None
    by_serial = {c.challan_serial: c for c in challans}

    credit_specs: list[dict] = [
        # Matched TDS/TCS
        {
            "kind": "TCS",
            "tan": "MUMT12345A",
            "name": "Western Steel Traders Pvt Ltd",
            "section": "206C(1H)",
            "amount": D("42500"),
            "remarks": f"{SEED_TAG} TCS matched 206C(1H)",
        },
        {
            "kind": "TDS",
            "tan": "PUNET98765B",
            "name": "Pune Auto OEM Ltd",
            "section": "194C",
            "amount": D("85000"),
            "remarks": f"{SEED_TAG} TDS matched 194C",
        },
        # Mismatch — books ₹1.25L vs portal ₹1.10L
        {
            "kind": "TDS",
            "tan": "DELCA5544D",
            "name": "Delhi CAD Services LLP",
            "section": "194J",
            "amount": D("125000"),
            "remarks": f"{SEED_TAG} TDS mismatch 194J (books > 26AS)",
        },
        # Only in books — missing from 26AS
        {
            "kind": "TDS",
            "tan": "MUMR77881E",
            "name": "Midc Realty Rentals",
            "section": "194I",
            "amount": D("48000"),
            "remarks": f"{SEED_TAG} TDS only-in-books 194I (missing on 26AS)",
        },
    ]

    for spec in credit_specs:
        exists = await db.scalar(
            select(TaxCreditEntry.id).where(
                TaxCreditEntry.company_id == actor.company_id,
                TaxCreditEntry.remarks == spec["remarks"],
            )
        )
        if exists is not None:
            continue
        await credit_service.create_credit(
            db,
            TaxCreditEntryCreate(
                ay_code=AY,
                computation_id=computation_id,
                credit_kind=spec["kind"],
                deductor_tan=spec["tan"],
                deductor_name=spec["name"],
                section_code=spec["section"],
                amount_credited=spec["amount"],
                amount_claimed=spec["amount"],
                remarks=spec["remarks"],
                source_refs={"seed": SEED_TAG},
            ),
            actor,
        )

    # Advance tax credits linked to submitted challans
    for serial, label_amt in [
        ("MSME-AT-Q1-0625", D("150000")),
        ("MSME-AT-Q2-0925", D("300000")),
        ("MSME-AT-Q3-1225", D("250000")),
        ("MSME-AT-Q4-0326", D("200000")),
        ("MSME-SAT-0926", D("75000")),
    ]:
        ch = by_serial.get(serial)
        if ch is None:
            continue
        remarks = f"{SEED_TAG} credit ↔ {serial}"
        exists = await db.scalar(
            select(TaxCreditEntry.id).where(
                TaxCreditEntry.company_id == actor.company_id,
                TaxCreditEntry.remarks == remarks,
            )
        )
        if exists is not None:
            continue
        kind = "SelfAssessment" if "SAT" in serial else "AdvanceTax"
        await credit_service.create_credit(
            db,
            TaxCreditEntryCreate(
                ay_code=AY,
                computation_id=computation_id,
                credit_kind=kind,
                amount_credited=label_amt,
                amount_claimed=label_amt,
                challan_id=ch.id,
                remarks=remarks,
                source_refs={"challan_serial": serial},
            ),
            actor,
        )

    # Mock Form 26AS — matched rows + amount mismatch + portal-only row
    form26as = {
        "ay": AY,
        "pan": "AABCT3921C",
        "tds": [
            {
                "deductor_name": "Western Steel Traders Pvt Ltd",
                "deductor_tan": "MUMT12345A",
                "section": "206C(1H)",
                "tds": "42500",
            },
            {
                "deductor_name": "Pune Auto OEM Ltd",
                "deductor_tan": "PUNET98765B",
                "section": "194C",
                "tds": "85000",
            },
            {
                "deductor_name": "Delhi CAD Services LLP",
                "deductor_tan": "DELCA5544D",
                "section": "194J",
                "tds": "110000",  # books 125000 → Mismatch
            },
            # Only in 26AS — not booked
            {
                "deductor_name": "Unknown Deductor Portal Only",
                "deductor_tan": "MUMX99999X",
                "section": "194C",
                "tds": "22000",
            },
            # 194I intentionally omitted → OnlyInBooks for Midc Realty
        ],
    }
    run = await recon_26as.reconcile_26as(
        db,
        user=actor,
        ay_code=AY,
        form26as=form26as,
        computation_id=computation_id,
    )
    print(
        f"  ✓ credits + 26AS recon status={run.status} "
        f"(matched={run.summary.get('matched')}, mismatch={run.summary.get('mismatch')}, "
        f"only_books={run.summary.get('only_in_books')}, only_26as={run.summary.get('only_in_26as')})"
    )


async def seed_run(db: AsyncSession, actor: CurrentUser, computation_id: uuid.UUID) -> None:
    assert actor.company_id is not None
    # Always append a fresh run so Result reflects latest dep / credits / losses.
    run = await run_service.create_run(
        db, computation_id=computation_id, user=actor, trigger="seed"
    )
    res = run.result
    assert res is not None
    print(
        f"  ✓ computation run #{run.run_no}: taxable ₹{res.taxable_income:,.2f}, "
        f"tax ₹{res.total_tax:,.2f}, credits ₹{res.credits_total:,.2f}, "
        f"net payable ₹{res.net_payable:,.2f} ({res.tax_applied_basis})"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    async with async_session_factory() as db:
        company = await db.scalar(select(Company).order_by(Company.creation.asc()))
        if company is None:
            raise SystemExit("No company found — run scripts.seed_demo first")
        await set_company_context(db, company.id)
        actor = await _actor(db, company)
        print(f"Seeding MSME Income Tax demo → '{company.company_name}' (AY {AY} / FY 2025-26)")

        await _ensure_statutory(db)
        await seed_registration(db, actor, company)
        bank, payable, _expense = await seed_tax_accounts(db, actor, company)
        await seed_pnl_journals(db, actor, company)
        await seed_assets_and_depreciation(db, actor, company)
        await seed_losses_and_mat(db, actor, company)
        doc = await seed_computation(db, actor)
        challans = await seed_challans(db, actor, doc.id, bank, payable)
        await seed_credits_and_26as(db, actor, doc.id, challans)
        await seed_run(db, actor, doc.id)

        print()
        print("Done. Open Tax Workspace → AY 2026-27, or:")
        print("  /tax/depreciation  → Sync from assets (registers already seeded)")
        print("  /tax/credits       → 26AS matched / mismatch / only-in-books")
        print("  /tax/challans      → 4 advance + SAT + 1 incorrect draft")
        print("  /tax/workspace     → Run / Result / Adjustments")


if __name__ == "__main__":
    asyncio.run(main())
