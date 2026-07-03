"""Seed a realistic demo dataset through the service layer.

Everything is created via the same services the API uses, so naming series,
GL postings, outstanding amounts and statuses are all real — every sidebar
page (invoices, journal entries, payments, reconciliation, budgets) and every
financial report shows meaningful data afterwards.

Run against any reachable PostgreSQL (local or another machine):

    python -m scripts.seed_demo \
        --database-url postgresql+asyncpg://erp_owner:<pw>@<host>:5432/erp \
        --admin-email admin@example.com --admin-password "ChangeMe!123"

Prerequisites on the target database: infra/init-db.sql applied (roles +
ltree) and `alembic upgrade head` run — or pass --reset-schema to have this
script drop schema `public` and re-run the migrations itself (owner role
required). The script aborts if the demo company already exists; after a
partial run (e.g. network drop), recover with --reset-schema.
"""

import argparse
import asyncio
import os
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="postgresql+asyncpg DSN; overrides DATABASE_URL",
    )
    parser.add_argument("--admin-email", default=os.environ.get("ADMIN_EMAIL", "admin@example.com"))
    parser.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD", "ChangeMe!123"))
    parser.add_argument("--company-name", default="Mango Appliances Demo")
    parser.add_argument("--abbr", default="MAD")
    parser.add_argument("--currency", default="INR")
    parser.add_argument("--country", default="IN")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (deterministic data)")
    parser.add_argument(
        "--reset-schema",
        action="store_true",
        help="DESTRUCTIVE: drop schema public on the target DB, re-run all "
        "migrations, then seed. Needs the owner role (erp_owner). Use this to "
        "recover from a partial seed or to re-seed from scratch.",
    )
    parser.add_argument(
        "--phase3-topup",
        action="store_true",
        help="Add ONLY the Modules 03-05 demo data (items, warehouses, stock, "
        "orders, fulfilment) to an EXISTING demo company — non-destructive. "
        "Run `alembic upgrade head` first.",
    )
    return parser.parse_args()


ARGS = _parse_args()
if not ARGS.database_url:
    sys.exit("Set --database-url or DATABASE_URL")
# settings are read from env at first import of app modules — set them first.
# MIGRATIONS_DATABASE_URL is pinned too so --reset-schema migrates the SAME
# database even when a local .env points somewhere else.
os.environ["DATABASE_URL"] = ARGS.database_url
os.environ["MIGRATIONS_DATABASE_URL"] = ARGS.database_url
os.environ.setdefault("SECRET_KEY", "seed-demo-secret-key-not-used-0123456789")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("REFRESH_COOKIE_SECURE", "false")

from pathlib import Path  # noqa: E402

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory, engine, set_company_context  # noqa: E402
from app.core.security import CurrentUser  # noqa: E402
from app.models.accounts import (  # noqa: E402
    Account,
    BankAccount,
    BankTransaction,
    FiscalYear,
    ItemTaxTemplate,
    ItemTaxTemplateDetail,
    DunningType,
    JournalEntry,
    JournalEntryAccount,
    PaymentEntry,
    PurchaseInvoice,
    SalesInvoice,
    TaxWithholdingCategory,
)
from app.models.core import Company, User  # noqa: E402
from app.schemas.accounts import (  # noqa: E402
    AccountCreate,
    BudgetAccountIn,
    BudgetCreate,
    CustomerCreate,
    InvoiceItemIn,
    JournalEntryAccountIn,
    JournalEntryCreate,
    PaymentEntryCreate,
    PaymentReferenceIn,
    PurchaseInvoiceCreate,
    SalesInvoiceCreate,
    SupplierCreate,
    TaxCategoryCreate,
    TaxRowIn,
    TaxTemplateCreate,
)
from app.schemas.buying import (  # noqa: E402
    OrderItemIn,
    PurchaseOrderCreate,
    RFQCreate,
    RFQItemIn,
    SupplierQuotationCreate,
    SupplierQuotationItemIn,
)
from app.schemas.core import CompanyCreate, UserCreate  # noqa: E402
from app.schemas.selling import QuotationCreate, SalesOrderCreate  # noqa: E402
from app.schemas.stock import (  # noqa: E402
    DeliveryNoteCreate,
    DeliveryNoteItemIn,
    ItemCreate,
    ItemGroupCreate,
    MaterialRequestCreate,
    MaterialRequestItemIn,
    PurchaseReceiptChargeIn,
    PurchaseReceiptCreate,
    PurchaseReceiptItemIn,
    StockEntryCreate,
    StockEntryItemIn,
    StockReconciliationCreate,
    StockReconciliationItemIn,
    WarehouseCreate,
)
from app.services import accounts_masters as masters  # noqa: E402
from app.services import budget as budget_service  # noqa: E402
from app.services import delivery_note as dn_service  # noqa: E402
from app.services import journal_entry as je_service  # noqa: E402
from app.services import material_request as mr_service  # noqa: E402
from app.services import payment_entry as pe_service  # noqa: E402
from app.services import purchase_invoice as pi_service  # noqa: E402
from app.services import purchase_order as po_service  # noqa: E402
from app.services import purchase_receipt as pr_service  # noqa: E402
from app.services import quotation as qtn_service  # noqa: E402
from app.services import rfq as rfq_service  # noqa: E402
from app.services import sales_invoice as si_service  # noqa: E402
from app.services import sales_order as so_service  # noqa: E402
from app.services import stock_entry as se_service  # noqa: E402
from app.services import stock_masters  # noqa: E402
from app.services import stock_reconciliation as recon_service  # noqa: E402
from app.services import registry as registry_service  # noqa: E402
from app.services.company import create_company  # noqa: E402
from app.services.user import create_user  # noqa: E402
from app.registry import get_descriptor  # noqa: E402
from scripts.seed import seed_admin, seed_masters, seed_permissions  # noqa: E402

rng = random.Random(ARGS.seed)
TODAY = date.today()

CUSTOMERS = [
    ("Globex Retail", "In-State", 500_000),
    ("Initech Solutions", "In-State", 250_000),
    ("Umbrella Traders", "Out-of-State", 300_000),
    ("Stark Electricals", "Out-of-State", 400_000),
    ("Wayne Distributors", "In-State", None),
    ("Acme Home Stores", "In-State", 150_000),
    ("Pied Piper Mart", None, None),  # no tax category -> no auto tax
    ("Hooli Appliances", "Out-of-State", 200_000),
]

SUPPLIERS = [
    ("Vandelay Industries", "In-State"),
    ("Duff Components", "In-State"),
    ("Sirius Cybernetics", "Out-of-State"),
    ("Tyrell Plastics", "In-State"),
    ("Cyberdyne Metals", "Out-of-State"),
    ("Wonka Packaging", None),
]

# (name, standard_rate, HSN code). HSN codes exist in the seeded HSN master and
# carry the post-reform GST rate — appliances/parts are 18% (85xx / 73xx / 39xx).
SALES_ITEMS = [
    ("Mixer Grinder X200", 2850, "85094000"),      # food grinders & mixers
    ("Induction Cooktop Pro", 3499, "85166000"),   # cookers / cooking plates
    ("Air Fryer 4.5L", 5200, "85167900"),          # other electrothermic appliances
    ("Electric Kettle 1.8L", 1150, "85167100"),    # electric kettles / tea makers
    ("Toaster DuoSlice", 1690, "85167200"),        # toasters
    ("Wet Grinder 2L", 6100, "85094000"),          # food grinders
    ("Hand Blender Turbo", 1390, "85094000"),      # blenders
]

PURCHASE_ITEMS = [
    ("Copper Motor Winding 750W", 720, "85030000"),   # parts of electric motors
    ("ABS Body Shell", 240, "39269099"),              # articles of plastic
    ("Stainless Steel Jar Set", 460, "73239300"),     # steel household articles
    ("Heating Element 2000W", 310, "85169000"),       # parts of heating appliances
    ("Control PCB v4", 530, "85340000"),              # printed circuits
    ("Packaging Carton L", 38, "48191000"),           # paper cartons (5% slab)
]


def _d(value: float | int) -> Decimal:
    return Decimal(str(round(value, 2)))


def _date_in_fy(fy_start: date, *, recent_bias: bool = True) -> date:
    span = (TODAY - fy_start).days
    if span < 1:
        return fy_start
    offset = rng.randint(span // 3, span) if recent_bias and span > 6 else rng.randint(0, span)
    return fy_start + timedelta(days=min(offset, span))


async def _leaf_account(
    db: AsyncSession,
    company_id,
    candidates: list[str],
    root_type: str | None = None,
    account_type: str | None = None,
) -> Account:
    """Find a leaf account by name candidates, then account_type, then any
    leaf of the root_type — keeps the seeder working across COA templates."""
    for name in candidates:
        account = await db.scalar(
            select(Account).where(
                Account.company_id == company_id,
                Account.account_name == name,
                Account.is_group.is_(False),
            )
        )
        if account:
            return account
    if account_type:
        account = await db.scalar(
            select(Account).where(
                Account.company_id == company_id,
                Account.account_type == account_type,
                Account.is_group.is_(False),
            )
        )
        if account:
            return account
    stmt = select(Account).where(Account.company_id == company_id, Account.is_group.is_(False))
    if root_type:
        stmt = stmt.where(Account.root_type == root_type)
    account = await db.scalar(stmt)
    if account is None:
        raise SystemExit(f"No leaf account found for {candidates} ({root_type})")
    return account


async def _group_account(db: AsyncSession, company_id, candidates: list[str]) -> Account:
    for name in candidates:
        account = await db.scalar(
            select(Account).where(
                Account.company_id == company_id,
                Account.account_name == name,
                Account.is_group.is_(True),
            )
        )
        if account:
            return account
    raise SystemExit(f"Expected a COA group account named one of {candidates}")


async def _reset_schema() -> None:
    """Drop everything (incl. alembic_version) so migrations re-run cleanly.

    Surgical deletes are not viable: gl_entries has an immutability trigger
    that blocks cascaded DELETEs and accounts has a RESTRICT self-FK.
    Requires the schema owner role (erp_owner).
    """
    print("Resetting schema 'public' on the target database ...")
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'erp_app') "
            "THEN GRANT USAGE ON SCHEMA public TO erp_app; END IF; END $$"
        ))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS ltree"))
    await engine.dispose()


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    command.upgrade(config, "head")


async def seed_extra_masters(db: AsyncSession, actor, company) -> dict[str, uuid.UUID]:
    """Masters added in the parity work — Terms Templates, Payment Terms
    Templates (+ installments), Cost Centers, and the selling 'More Info'
    masters. Created through the metadata-engine service so naming, scoping and
    child rows all run for real. Returns the ids the demo documents reference."""

    async def mk(slug: str, payload: dict) -> uuid.UUID:
        doc = await registry_service.create_document(db, get_descriptor(slug), payload, actor)
        return uuid.UUID(str(doc["id"]))

    # Terms & Conditions (boilerplate text the Terms tab can pull in)
    await mk("terms-template", {
        "template_name": "Standard 30-Day",
        "terms": ("1. Payment due within 30 days of the invoice date.\n"
                  "2. Goods remain our property until paid in full.\n"
                  "3. Warranty: 12 months from delivery."),
    })
    await mk("terms-template", {
        "template_name": "Net 45 (Wholesale)",
        "terms": "Payment due within 45 days. Wholesale / bulk orders only. Returns accepted within 7 days.",
    })

    # Payment Terms Templates (the payment split shown as a due-date breakdown)
    net30 = await mk("payment-terms-template", {
        "template_name": "Net 30",
        "terms": [{"description": "Full payment", "invoice_portion": 100, "credit_days": 30}],
    })
    adv5050 = await mk("payment-terms-template", {
        "template_name": "50% Advance / 50% on Delivery",
        "terms": [
            {"description": "Advance", "invoice_portion": 50, "credit_days": 0},
            {"description": "Balance on delivery", "invoice_portion": 50, "credit_days": 30},
        ],
    })
    split3060 = await mk("payment-terms-template", {
        "template_name": "Net 30/60 (split)",
        "terms": [
            {"description": "First half", "invoice_portion": 50, "credit_days": 30},
            {"description": "Second half", "invoice_portion": 50, "credit_days": 60},
        ],
    })

    # Cost centers (flat) — give the invoice per-line Cost Center picker options
    cc_sales = await mk("cost-center", {"cost_center_name": "Sales & Marketing", "is_group": False})
    cc_ops = await mk("cost-center", {"cost_center_name": "Operations", "is_group": False})

    # Selling "More Info" masters
    campaign = await mk("campaign", {"campaign_name": "Diwali Festival Sale"})
    territory = await mk("territory", {"territory_name": "West Zone"})
    customer_group = await mk("customer-group", {"customer_group_name": "Retail"})
    sales_partner = await mk("sales-partner", {"partner_name": "Channel Partner A"})

    print("Extra masters: 2 terms templates, 3 payment terms templates, 2 cost centers, "
          "+ campaign/territory/customer-group/sales-partner")
    return {
        "ptt_net30": net30, "ptt_5050": adv5050, "ptt_split": split3060,
        "cc_sales": cc_sales, "cc_ops": cc_ops,
        "campaign": campaign, "territory": territory,
        "customer_group": customer_group, "sales_partner": sales_partner,
    }


async def main() -> None:  # noqa: PLR0915 — linear demo scenario, clearer unsplit
    async with async_session_factory() as db:
        # --- base seed (idempotent) + admin ------------------------------------
        await seed_masters(db)
        await seed_permissions(db)
        await seed_admin(db, ARGS.admin_email, ARGS.admin_password)
        await db.commit()

        if await db.scalar(select(Company).where(Company.company_name == ARGS.company_name)):
            sys.exit(f"Company '{ARGS.company_name}' already exists — refusing to double-seed.")

        admin = await db.scalar(select(User).where(User.email == ARGS.admin_email.lower()))
        actor = CurrentUser({"sub": str(admin.id), "email": admin.email, "roles": ["System Manager"]})

        # --- company (seeds COA, cost center, fiscal year, defaults) -----------
        company = await create_company(
            db,
            CompanyCreate(
                company_name=ARGS.company_name,
                abbr=ARGS.abbr,
                default_currency=ARGS.currency,
                country_code=ARGS.country,
                tax_id="27AAEPM1234C1Z5",
            ),
            actor,
        )
        actor = CurrentUser(
            {
                "sub": str(admin.id),
                "email": admin.email,
                "company_id": str(company.id),
                "roles": ["System Manager"],
            }
        )
        await set_company_context(db, company.id)
        print(f"Company: {company.company_name}")

        # without a default company the admin logs into an empty app — the
        # JWT company context comes from users.default_company_id
        admin.default_company_id = company.id
        await db.commit()

        fy = await db.scalar(
            select(FiscalYear)
            .where(FiscalYear.company_id == company.id)
            .order_by(FiscalYear.year_start_date.desc())
        )

        def recent(days_ago: int) -> date:
            """TODAY minus N days, clamped into the fiscal year (GL posting
            fails for dates no fiscal year covers, e.g. in the FY's first week)."""
            return max(fy.year_start_date, TODAY - timedelta(days=days_ago))

        # previous fiscal year + a few old documents: gives the demo non-zero
        # opening balances (Trial Balance / General Ledger) and deep AR/AP
        # aging buckets (61-90 / 90+)
        prior_start = fy.year_start_date.replace(year=fy.year_start_date.year - 1)
        prior_end = fy.year_start_date - timedelta(days=1)
        prior_fy = FiscalYear(
            company_id=company.id,
            year=f"{prior_start.year}-{prior_end.year}",
            year_start_date=prior_start,
            year_end_date=prior_end,
            is_closed=False,
            owner=admin.id,
        )
        db.add(prior_fy)
        await db.commit()

        # --- extra users --------------------------------------------------------
        for email, first, roles in [
            ("manager@demo-erp.com", "Maya",
             ["Accounts Manager", "Stock Manager", "Purchase Manager", "Sales Manager"]),
            ("books@demo-erp.com", "Arun", ["Accounts User"]),
            ("sales@demo-erp.com", "Sara", ["Sales User", "Stock User"]),
        ]:
            if not await db.scalar(select(User).where(User.email == email)):
                await create_user(
                    db,
                    UserCreate(
                        email=email, first_name=first, password="Demo!Pass123",
                        default_company_id=company.id, roles=roles,
                    ),
                    actor,
                )
        print("Users: manager@/books@/sales@demo-erp.com (Demo!Pass123)")

        # --- bank/tax accounts under the seeded COA -----------------------------
        # name candidates cover the bundled templates (standard/in_standard use
        # "Bank Accounts", ae_uae_standard uses "Banks")
        bank_group = await _group_account(db, company.id, ["Bank Accounts", "Banks"])
        duties = await _group_account(db, company.id, ["Duties and Taxes", "Duties & Taxes"])
        hdfc = await masters.create_account(
            db, AccountCreate(account_name="HDFC Current", parent_account_id=bank_group.id,
                              account_type="Bank"), actor)
        icici = await masters.create_account(
            db, AccountCreate(account_name="ICICI Savings", parent_account_id=bank_group.id,
                              account_type="Bank"), actor)
        # Bank Account masters (link the company's real bank accounts to their GL
        # ledger accounts) — required by the Bank Reconciliation tool.
        hdfc_ba = BankAccount(
            id=uuid.uuid4(), company_id=company.id, account_name="HDFC Current A/C",
            gl_account_id=hdfc.id, account_number="500100123456",
            is_company_account=True, is_default=True,
        )
        icici_ba = BankAccount(
            id=uuid.uuid4(), company_id=company.id, account_name="ICICI Savings A/C",
            gl_account_id=icici.id, account_number="002701555000", is_company_account=True,
        )
        db.add_all([hdfc_ba, icici_ba])
        await db.flush()
        out_cgst = await masters.create_account(
            db, AccountCreate(account_name="Output CGST", parent_account_id=duties.id,
                              account_type="Tax"), actor)
        out_sgst = await masters.create_account(
            db, AccountCreate(account_name="Output SGST", parent_account_id=duties.id,
                              account_type="Tax"), actor)
        out_igst = await masters.create_account(
            db, AccountCreate(account_name="Output IGST", parent_account_id=duties.id,
                              account_type="Tax"), actor)
        in_gst = await masters.create_account(
            db, AccountCreate(account_name="Input GST", parent_account_id=duties.id,
                              account_type="Tax"), actor)
        # India withholding: TDS payable (purchase side) + TCS payable (sales side)
        tds_payable = await masters.create_account(
            db, AccountCreate(account_name="TDS Payable", parent_account_id=duties.id,
                              account_type="Tax"), actor)
        tcs_payable = await masters.create_account(
            db, AccountCreate(account_name="TCS Payable", parent_account_id=duties.id,
                              account_type="Tax"), actor)
        # standard India TDS sections + TCS sections
        _twc = [
            ("TDS 194C - Contractor (Company) 2%", "TDS", 2, 30_000, tds_payable.id),
            ("TDS 194C - Contractor (Individual/HUF) 1%", "TDS", 1, 30_000, tds_payable.id),
            ("TDS 194H - Commission/Brokerage 5%", "TDS", 5, 15_000, tds_payable.id),
            ("TDS 194I - Rent: Plant & Machinery 2%", "TDS", 2, 240_000, tds_payable.id),
            ("TDS 194I - Rent: Land/Building 10%", "TDS", 10, 240_000, tds_payable.id),
            ("TDS 194J - Professional/Technical 10%", "TDS", 10, 30_000, tds_payable.id),
            ("TDS 194Q - Purchase of Goods 0.1%", "TDS", 0.1, 5_000_000, tds_payable.id),
            ("TCS 206C - Scrap 1%", "TCS", 1, 0, tcs_payable.id),
            ("TCS 206C(1H) - Sale of Goods 0.1%", "TCS", 0.1, 5_000_000, tcs_payable.id),
            ("TCS 206C - Motor Vehicle (>10L) 1%", "TCS", 1, 1_000_000, tcs_payable.id),
        ]
        db.add_all([
            TaxWithholdingCategory(
                id=uuid.uuid4(), company_id=company.id, category_name=name, kind=kind,
                rate=_d(rate), threshold=_d(thr), account_id=acct,
            )
            for name, kind, rate, thr, acct in _twc
        ])
        await db.flush()

        # Dunning tiers — escalate by days overdue (grace), interest % p.a., flat fee
        db.add_all([
            DunningType(
                id=uuid.uuid4(), company_id=company.id, dunning_type=name,
                grace_period_days=grace, interest_rate=_d(rate), dunning_fee=_d(fee),
                letter_intro=intro,
            )
            for name, grace, rate, fee, intro in [
                ("Payment Reminder", 7, 0, 0,
                 "We notice the following invoices are just past due. This is a friendly reminder — "
                 "please arrange payment at your convenience."),
                ("First Notice", 30, 12, 0,
                 "The following invoices remain unpaid past their due date. Interest has been applied. "
                 "Kindly settle the amount due to avoid further charges."),
                ("Final Notice", 60, 18, 500,
                 "Despite earlier reminders, the following invoices are significantly overdue. Please "
                 "treat this as a final notice and remit the full amount due immediately."),
            ]
        ])
        await db.flush()

        # Item Tax Templates per GST slab — each carries CGST+SGST (intra), IGST
        # (inter) and Input GST (purchase) rows so the per-item override applies on
        # any invoice type. Set on an Item, it drives that line's GST rate.
        for slab in (0, 5, 18, 40):  # post-reform GST 2.0 slabs (12% & 28% abolished)
            itt = ItemTaxTemplate(id=uuid.uuid4(), company_id=company.id, title=f"GST {slab}%")
            db.add(itt)
            await db.flush()
            db.add_all([
                ItemTaxTemplateDetail(id=uuid.uuid4(), template_id=itt.id, account_head_id=out_cgst.id, rate=_d(slab) / 2, idx=1),
                ItemTaxTemplateDetail(id=uuid.uuid4(), template_id=itt.id, account_head_id=out_sgst.id, rate=_d(slab) / 2, idx=2),
                ItemTaxTemplateDetail(id=uuid.uuid4(), template_id=itt.id, account_head_id=out_igst.id, rate=_d(slab), idx=3),
                ItemTaxTemplateDetail(id=uuid.uuid4(), template_id=itt.id, account_head_id=in_gst.id, rate=_d(slab), idx=4),
            ])
        await db.flush()
        cash = await _leaf_account(db, company.id, ["Cash"], "Asset", account_type="Cash")
        # explicit income/expense accounts on every invoice line keeps the
        # seeder independent of per-template company defaults
        income_account = await _leaf_account(db, company.id, ["Sales"], "Income")
        expense_account = await _leaf_account(
            db, company.id, ["Cost of Goods Sold"], "Expense", account_type="Cost of Goods Sold")

        # --- tax categories + templates (sales auto-resolution by party) --------
        in_state = await masters.create_tax_category(db, TaxCategoryCreate(title="In-State"), actor)
        out_state = await masters.create_tax_category(db, TaxCategoryCreate(title="Out-of-State"), actor)
        out_state.is_inter_state = True  # GST derives intra/inter from the GSTIN state code
        await db.flush()
        category_ids = {"In-State": in_state.id, "Out-of-State": out_state.id, None: None}

        await masters.create_tax_template(
            db,
            TaxTemplateCreate(
                title="GST 18% (CGST+SGST)", kind="sales", tax_category_id=in_state.id,
                details=[
                    TaxRowIn(charge_type="On Net Total", rate=_d(9), account_head_id=out_cgst.id,
                             description="CGST @ 9%"),
                    TaxRowIn(charge_type="On Net Total", rate=_d(9), account_head_id=out_sgst.id,
                             description="SGST @ 9%"),
                ],
            ),
            actor,
        )
        await masters.create_tax_template(
            db,
            TaxTemplateCreate(
                title="IGST 18%", kind="sales", tax_category_id=out_state.id,
                details=[TaxRowIn(charge_type="On Net Total", rate=_d(18),
                                  account_head_id=out_igst.id, description="IGST @ 18%")],
            ),
            actor,
        )
        purchase_template = await masters.create_tax_template(
            db,
            TaxTemplateCreate(
                title="GST 18% (Purchase)", kind="purchase", is_default=True,
                details=[TaxRowIn(charge_type="On Net Total", rate=_d(18),
                                  account_head_id=in_gst.id, description="Input GST @ 18%")],
            ),
            actor,
        )

        # --- parties -------------------------------------------------------------
        # GSTIN state code drives auto place-of-supply: company is 27 (Maharashtra),
        # so In-State parties get a 27 GSTIN (→ CGST+SGST), Out-of-State get 29
        # (Karnataka → IGST). Parties with no category get no GSTIN (manual fallback).
        gstin_state = {"In-State": "27", "Out-of-State": "29"}
        customers = []
        for i, (name, category, credit_limit) in enumerate(CUSTOMERS):
            cust = await masters.create_customer(
                db,
                CustomerCreate(
                    customer_name=name,
                    tax_category_id=category_ids[category],
                    credit_limit=_d(credit_limit) if credit_limit else None,
                ),
                actor,
            )
            state = gstin_state.get(category)
            if state:
                cust.tax_id = f"{state}AAAAA{i:04d}A1Z5"  # 15-char GSTIN (only state code matters here)
            slug = "".join(ch for ch in name.lower() if ch.isalnum()) or f"cust{i + 1}"
            cust.email_id = f"{slug}@example.com"  # demo inbox (Mailhog catches all dev mail)
            customers.append(cust)
        await db.flush()
        suppliers = []
        for j, (name, category) in enumerate(SUPPLIERS):
            sup = await masters.create_supplier(
                db, SupplierCreate(supplier_name=name, tax_category_id=category_ids[category]), actor)
            state = gstin_state.get(category)  # In-State→27, Out-of-State→29; None→no GSTIN
            if state:
                sup.tax_id = f"{state}BBBBB{j:04d}B1Z5"  # 15-char GSTIN (state code is what matters)
            slug = "".join(ch for ch in name.lower() if ch.isalnum()) or f"supp{j + 1}"
            sup.email_id = f"{slug}@example.com"
            suppliers.append(sup)
        await db.flush()
        print(f"Parties: {len(customers)} customers, {len(suppliers)} suppliers")

        # --- parity-feature masters + party defaults ----------------------------
        extras = await seed_extra_masters(db, actor, company)
        # default payment terms on a couple of parties; group/territory on a customer
        customers[0].payment_terms_template_id = extras["ptt_net30"]
        customers[3].payment_terms_template_id = extras["ptt_5050"]
        customers[0].customer_group_id = extras["customer_group"]
        customers[0].territory_id = extras["territory"]
        suppliers[0].payment_terms_template_id = extras["ptt_net30"]
        suppliers[3].payment_terms_template_id = extras["ptt_split"]
        await db.commit()

        def sales_items(max_lines: int = 3) -> list[InvoiceItemIn]:
            # sprinkle per-line discounts + cost centers so those columns have data
            return [
                InvoiceItemIn(
                    item_name=item, qty=_d(rng.randint(1, 8)), rate=_d(rate),
                    account_id=income_account.id, hsn_sac_code=hsn,
                    discount_percentage=_d(rng.choice([0, 0, 0, 5, 10])),
                    cost_center_id=extras["cc_sales"] if rng.random() < 0.3 else None,
                )
                for item, rate, hsn in rng.sample(SALES_ITEMS, rng.randint(1, max_lines))
            ]

        def purchase_items(max_lines: int = 3) -> list[InvoiceItemIn]:
            return [
                InvoiceItemIn(
                    item_name=item, qty=_d(rng.randint(10, 120)), rate=_d(rate),
                    account_id=expense_account.id, hsn_sac_code=hsn,
                    discount_percentage=_d(rng.choice([0, 0, 0, 5])),
                    cost_center_id=extras["cc_ops"] if rng.random() < 0.3 else None,
                )
                for item, rate, hsn in rng.sample(PURCHASE_ITEMS, rng.randint(1, max_lines))
            ]

        # --- prior-FY documents: opening balances + deep aging buckets ----------
        for customer_idx, days_before_fy in ((1, 30), (2, 100)):
            posting = prior_end - timedelta(days=days_before_fy)
            old_si = await si_service.create_sales_invoice(
                db,
                SalesInvoiceCreate(
                    customer_id=customers[customer_idx].id,
                    posting_date=posting,
                    due_date=posting + timedelta(days=15),
                    items=sales_items(2),
                    remarks="Carried over from last year",
                ),
                actor,
            )
            await si_service.submit_sales_invoice(db, old_si.id, actor)
        old_posting = prior_end - timedelta(days=60)
        old_pi = await pi_service.create_purchase_invoice(
            db,
            PurchaseInvoiceCreate(
                supplier_id=suppliers[1].id,
                posting_date=old_posting,
                due_date=old_posting + timedelta(days=30),
                bill_no="VEND-OLD-1",
                items=purchase_items(2),
            ),
            actor,
        )
        await pi_service.submit_purchase_invoice(db, old_pi.id, actor)

        # --- sales invoices: drafts / unpaid / overdue / partly & fully paid ----
        sales_invoices = []
        for n in range(24):
            customer = rng.choice(customers)
            posting = _date_in_fy(fy.year_start_date)
            # taxes intentionally omitted: the template resolves from the
            # customer's tax category (or none, for Pied Piper)
            invoice = await si_service.create_sales_invoice(
                db,
                SalesInvoiceCreate(
                    customer_id=customer.id,
                    posting_date=posting,
                    due_date=posting + timedelta(days=rng.choice([7, 15, 30])),
                    items=sales_items(),
                    payment_terms_template_id=extras["ptt_net30"] if rng.random() < 0.35 else None,
                    remarks=f"Demo order #{1000 + n}",
                ),
                actor,
            )
            if n % 6 != 5:  # leave every 6th as a draft
                invoice = await si_service.submit_sales_invoice(db, invoice.id, actor)
            sales_invoices.append(invoice)

        # one cancelled invoice — shows the Cancelled badge and the
        # append-only GL reversal rows for an invoice voucher
        cancelled_si = await si_service.create_sales_invoice(
            db,
            SalesInvoiceCreate(
                customer_id=customers[5].id,
                posting_date=recent(8),
                items=sales_items(1),
                remarks="Duplicate entry — cancelled",
            ),
            actor,
        )
        await si_service.submit_sales_invoice(db, cancelled_si.id, actor)
        await si_service.cancel_sales_invoice(db, cancelled_si.id, actor)

        # --- receive payments: ~60% fully settled, a few partial, 2 on-account --
        submitted_sis = [i for i in sales_invoices if i.docstatus == 1]
        receive_cleared = 0
        for invoice in submitted_sis[: int(len(submitted_sis) * 0.6)]:
            outstanding = Decimal(invoice.outstanding_amount)
            partial = rng.random() < 0.2
            allocated = _d(float(outstanding) * 0.5) if partial else outstanding
            pay_date = min(invoice.posting_date + timedelta(days=rng.randint(1, 20)), TODAY)
            entry = await pe_service.create_payment_entry(
                db,
                PaymentEntryCreate(
                    posting_date=pay_date,
                    payment_type="Receive",
                    party_type="Customer",
                    party_id=invoice.customer_id,
                    paid_to_id=rng.choice([hdfc, icici]).id,
                    paid_amount=allocated,
                    reference_no=f"NEFT-{rng.randint(100000, 999999)}",
                    reference_date=pay_date,
                    references=[PaymentReferenceIn(
                        reference_doctype="Sales Invoice",
                        reference_id=invoice.id,
                        allocated_amount=allocated,
                    )],
                ),
                actor,
            )
            entry = await pe_service.submit_payment_entry(db, entry.id, actor)
            if rng.random() < 0.6:  # bank cleared most of them
                await pe_service.set_clearance_date(
                    db, entry.id, min(pay_date + timedelta(days=2), TODAY), actor)
                receive_cleared += 1

        # on-account receipts (no allocation) -> Payment Reconciliation demo.
        # Each showcased party also gets a dedicated UNPAID invoice so the
        # tool always has both sides to match, whatever the RNG did above.
        for customer in (customers[0], customers[3]):
            open_si = await si_service.create_sales_invoice(
                db,
                SalesInvoiceCreate(
                    customer_id=customer.id,
                    posting_date=recent(12),
                    due_date=recent(12) + timedelta(days=30),
                    items=sales_items(2),
                    remarks="Awaiting reconciliation",
                ),
                actor,
            )
            await si_service.submit_sales_invoice(db, open_si.id, actor)
            entry = await pe_service.create_payment_entry(
                db,
                PaymentEntryCreate(
                    posting_date=recent(rng.randint(1, 10)),
                    payment_type="Receive",
                    party_type="Customer",
                    party_id=customer.id,
                    paid_to_id=hdfc.id,
                    paid_amount=_d(rng.choice([10_000, 25_000, 50_000])),
                    reference_no=f"ADV-{rng.randint(1000, 9999)}",
                ),
                actor,
            )
            await pe_service.submit_payment_entry(db, entry.id, actor)

        # --- purchase invoices + pay ---------------------------------------------
        purchase_invoices = []
        for n in range(14):
            supplier = rng.choice(suppliers)
            posting = _date_in_fy(fy.year_start_date)
            invoice = await pi_service.create_purchase_invoice(
                db,
                PurchaseInvoiceCreate(
                    supplier_id=supplier.id,
                    posting_date=posting,
                    due_date=posting + timedelta(days=rng.choice([15, 30, 45])),
                    bill_no=f"VEND-{2000 + n}",
                    bill_date=posting,
                    items=purchase_items(),
                    tax_template_id=purchase_template.id if rng.random() < 0.7 else None,
                    payment_terms_template_id=extras["ptt_split"] if rng.random() < 0.35 else None,
                ),
                actor,
            )
            if n % 7 != 6:
                invoice = await pi_service.submit_purchase_invoice(db, invoice.id, actor)
            purchase_invoices.append(invoice)

        submitted_pis = [i for i in purchase_invoices if i.docstatus == 1]
        for idx, invoice in enumerate(submitted_pis[: int(len(submitted_pis) * 0.55)]):
            outstanding = Decimal(invoice.outstanding_amount)
            # first settlement is a half payment -> a Partly Paid purchase invoice
            allocated = _d(float(outstanding) * 0.5) if idx == 0 else outstanding
            pay_date = min(invoice.posting_date + timedelta(days=rng.randint(3, 25)), TODAY)
            entry = await pe_service.create_payment_entry(
                db,
                PaymentEntryCreate(
                    posting_date=pay_date,
                    payment_type="Pay",
                    party_type="Supplier",
                    party_id=invoice.supplier_id,
                    paid_from_id=hdfc.id,
                    paid_amount=allocated,
                    reference_no=f"CHQ-{rng.randint(50000, 99999)}",
                    reference_date=pay_date,
                    references=[PaymentReferenceIn(
                        reference_doctype="Purchase Invoice",
                        reference_id=invoice.id,
                        allocated_amount=allocated,
                    )],
                ),
                actor,
            )
            entry = await pe_service.submit_payment_entry(db, entry.id, actor)
            if rng.random() < 0.5:
                await pe_service.set_clearance_date(
                    db, entry.id, min(pay_date + timedelta(days=3), TODAY), actor)

        # on-account supplier payment + a dedicated open bill to match it with
        open_pi = await pi_service.create_purchase_invoice(
            db,
            PurchaseInvoiceCreate(
                supplier_id=suppliers[0].id,
                posting_date=recent(9),
                due_date=recent(9) + timedelta(days=30),
                bill_no="VEND-OPEN-1",
                items=purchase_items(2),
            ),
            actor,
        )
        await pi_service.submit_purchase_invoice(db, open_pi.id, actor)
        entry = await pe_service.create_payment_entry(
            db,
            PaymentEntryCreate(
                posting_date=recent(4),
                payment_type="Pay",
                party_type="Supplier",
                party_id=suppliers[0].id,
                paid_from_id=hdfc.id,
                paid_amount=_d(40_000),
                reference_no="ADV-PO-77",
            ),
            actor,
        )
        await pe_service.submit_payment_entry(db, entry.id, actor)

        # internal transfer bank -> cash
        transfer = await pe_service.create_payment_entry(
            db,
            PaymentEntryCreate(
                posting_date=recent(6),
                payment_type="Internal Transfer",
                paid_from_id=hdfc.id,
                paid_to_id=cash.id,
                paid_amount=_d(15_000),
                remarks="Petty cash top-up",
            ),
            actor,
        )
        await pe_service.submit_payment_entry(db, transfer.id, actor)

        # --- journal entries: monthly opex, one cancelled, some bank-cleared ----
        rent = await _leaf_account(db, company.id, ["Office Rent", "Rent"], "Expense")
        salary = await _leaf_account(db, company.id, ["Salary", "Salaries"], "Expense")
        admin_exp = await _leaf_account(
            db, company.id, ["Administrative Expenses", "Miscellaneous Expenses"], "Expense")
        marketing = await _leaf_account(
            db, company.id, ["Marketing Expenses", "Advertising"], "Expense")
        je_specs = [
            (rent, 45_000, "Office rent"),
            (salary, 380_000, "Monthly payroll"),
            (admin_exp, 12_500, "Utilities and sundries"),
            (marketing, 30_000, "Festival campaign"),
            (rent, 45_000, "Office rent"),
            (admin_exp, 8_200, "Stationery and couriers"),
        ]
        journal_entries = []
        # opening bank balance so HDFC stays positive (Dr bank / Cr equity), cleared
        # at go-live — otherwise the demo bank is all outflows and reads as an overdraft.
        shf = await db.scalar(
            select(Account).where(
                Account.company_id == company.id, Account.account_name == "Shareholders Funds"
            )
        )
        if shf is not None:
            opening_bank = await je_service.create_journal_entry(
                db,
                JournalEntryCreate(
                    posting_date=prior_end - timedelta(days=60),
                    remarks="Opening bank balance",
                    accounts=[
                        JournalEntryAccountIn(account_id=hdfc.id, debit=_d(1_000_000)),
                        JournalEntryAccountIn(account_id=shf.id, credit=_d(1_000_000)),
                    ],
                ),
                actor,
            )
            await je_service.submit_journal_entry(db, opening_bank.id, actor)
            await je_service.set_clearance_date(
                db, opening_bank.id, prior_end - timedelta(days=60), actor)
        # one entry in the prior FY: feeds the opening balances
        prior_rent = await je_service.create_journal_entry(
            db,
            JournalEntryCreate(
                posting_date=prior_end - timedelta(days=45),
                remarks="Office rent (last year)",
                accounts=[
                    JournalEntryAccountIn(account_id=rent.id, debit=_d(45_000)),
                    JournalEntryAccountIn(account_id=hdfc.id, credit=_d(45_000)),
                ],
            ),
            actor,
        )
        await je_service.submit_journal_entry(db, prior_rent.id, actor)
        await je_service.set_clearance_date(
            db, prior_rent.id, prior_end - timedelta(days=43), actor)

        for account, amount, remark in je_specs:
            entry = await je_service.create_journal_entry(
                db,
                JournalEntryCreate(
                    posting_date=_date_in_fy(fy.year_start_date),
                    remarks=remark,
                    accounts=[
                        JournalEntryAccountIn(account_id=account.id, debit=_d(amount)),
                        JournalEntryAccountIn(account_id=hdfc.id, credit=_d(amount)),
                    ],
                ),
                actor,
            )
            entry = await je_service.submit_journal_entry(db, entry.id, actor)
            journal_entries.append(entry)
            if rng.random() < 0.5:
                await je_service.set_clearance_date(
                    db, entry.id, min(entry.posting_date + timedelta(days=2), TODAY), actor)

        # a cancelled entry — shows append-only reversal rows in the GL
        wrong = await je_service.create_journal_entry(
            db,
            JournalEntryCreate(
                posting_date=recent(3),
                remarks="Posted to the wrong account — cancelled",
                accounts=[
                    JournalEntryAccountIn(account_id=marketing.id, debit=_d(9_999)),
                    JournalEntryAccountIn(account_id=cash.id, credit=_d(9_999)),
                ],
            ),
            actor,
        )
        await je_service.submit_journal_entry(db, wrong.id, actor)
        await je_service.cancel_journal_entry(db, wrong.id, actor)

        # one draft awaiting review
        await je_service.create_journal_entry(
            db,
            JournalEntryCreate(
                posting_date=TODAY,
                remarks="Depreciation accrual — draft",
                accounts=[
                    JournalEntryAccountIn(account_id=admin_exp.id, debit=_d(5_000)),
                    JournalEntryAccountIn(account_id=cash.id, credit=_d(5_000)),
                ],
            ),
            actor,
        )

        # --- budgets: enforcement on, generous caps so demo flows keep working --
        submitted_budget = await budget_service.create_budget(
            db,
            BudgetCreate(
                fiscal_year_id=fy.id,
                action_if_annual_budget_exceeded="Warn",
                accounts=[
                    BudgetAccountIn(account_id=rent.id, budget_amount=_d(600_000)),
                    BudgetAccountIn(account_id=marketing.id, budget_amount=_d(500_000)),
                    BudgetAccountIn(account_id=salary.id, budget_amount=_d(5_000_000)),
                ],
            ),
            actor,
        )
        await budget_service.submit_budget(db, submitted_budget.id, actor)
        await budget_service.create_budget(
            db,
            BudgetCreate(
                fiscal_year_id=fy.id,
                cost_center_id=company.default_cost_center_id,
                action_if_annual_budget_exceeded="Stop",
                accounts=[BudgetAccountIn(account_id=admin_exp.id, budget_amount=_d(200_000))],
            ),
            actor,
        )

        # --- demo bank statement, kept COHERENT with the books -------------------
        # The bank's view = one line for each UNCLEARED payment touching HDFC (so
        # every line matches its voucher), MINUS the oldest one (an outstanding
        # cheque the bank hasn't shown yet — a realistic residual in "Uncleared
        # Items"), PLUS a couple of bank-only lines (charges/interest) with no
        # voucher (the create-from-unmatched path). Manual journal entries against
        # the bank are opening/adjustment items, so they're marked cleared at
        # go-live rather than left to bloat the uncleared report.
        from app.core.naming import get_next_name  # noqa: PLC0415

        async def _btn(bank_account, on_date, desc, ref, deposit, withdrawal):
            name = await get_next_name(db, "ACC-BTN-.YYYY.-", company.id, on_date=on_date)
            db.add(BankTransaction(
                id=uuid.uuid4(), company_id=company.id, name=name,
                bank_account_id=bank_account.id, date=on_date, description=desc,
                reference_number=ref, deposit=deposit, withdrawal=withdrawal,
                status="Unreconciled",
            ))

        hdfc_jes = (await db.execute(
            select(JournalEntry).where(
                JournalEntry.company_id == company.id,
                JournalEntry.docstatus == 1,
                JournalEntry.clearance_date.is_(None),
                JournalEntry.id.in_(
                    select(JournalEntryAccount.journal_entry_id).where(
                        JournalEntryAccount.account_id == hdfc.id
                    )
                ),
            )
        )).scalars().all()
        for je in hdfc_jes:
            je.clearance_date = je.posting_date  # opening/adjustment — cleared at go-live

        hdfc_pes = (await db.execute(
            select(PaymentEntry).where(
                PaymentEntry.company_id == company.id,
                PaymentEntry.docstatus == 1,
                PaymentEntry.clearance_date.is_(None),
                (PaymentEntry.paid_from_id == hdfc.id) | (PaymentEntry.paid_to_id == hdfc.id),
            ).order_by(PaymentEntry.posting_date)
        )).scalars().all()
        for pe in hdfc_pes[1:]:  # leave the oldest uncleared as an outstanding cheque
            if pe.paid_to_id == hdfc.id:
                await _btn(hdfc_ba, pe.posting_date, f"Inward {pe.reference_no or 'transfer'}",
                           pe.reference_no, pe.received_amount, _d(0))
            else:
                await _btn(hdfc_ba, pe.posting_date, f"Outward {pe.reference_no or 'payment'}",
                           pe.reference_no, _d(0), pe.paid_amount)
        await _btn(hdfc_ba, recent(2), "Bank charges - quarterly", "CHG-Q1", _d(0), _d(354))
        await _btn(hdfc_ba, recent(5), "Savings interest credit", "INT-APR", _d(1180), _d(0))
        await db.flush()

        await seed_supply_chain(db, actor, customers, suppliers, income_account, recent, extras)

        # Assign each item the GST slab template that matches its HSN rate, so the
        # rate charged (via the item-tax-template override) agrees with the item's
        # HSN. Appliances/parts are 18%; the paper carton is 5%.
        await db.commit()
        await db.execute(
            text("UPDATE items SET item_tax_template_id = (SELECT id FROM item_tax_templates "
                 "WHERE title='GST 18%' AND company_id=items.company_id) WHERE company_id=:cid"),
            {"cid": str(company.id)},
        )
        for code, title in (("RM-PACKAGING-CARTON-L", "GST 5%"),):
            await db.execute(
                text("UPDATE items SET item_tax_template_id = (SELECT id FROM item_tax_templates "
                     "WHERE title=:title AND company_id=items.company_id) "
                     "WHERE company_id=:cid AND item_code=:code"),
                {"cid": str(company.id), "title": title, "code": code},
            )
        await db.commit()

        # --- summary -------------------------------------------------------------
        async def count(model, *where) -> int:
            return await db.scalar(select(func.count()).select_from(model).where(
                model.company_id == company.id, *where))

        si_total = await count(SalesInvoice)
        si_paid = await count(SalesInvoice, SalesInvoice.status == "Paid")
        si_overdue = await count(SalesInvoice, SalesInvoice.status == "Overdue")
        pe_unallocated = await count(PaymentEntry, PaymentEntry.unallocated_amount > 0,
                                     PaymentEntry.docstatus == 1)
        pe_uncleared = await count(PaymentEntry, PaymentEntry.clearance_date.is_(None),
                                   PaymentEntry.docstatus == 1)
        print(
            f"Seeded: {si_total} sales invoices ({si_paid} paid, {si_overdue} overdue), "
            f"{await count(PurchaseInvoice)} purchase invoices, "
            f"{await count(JournalEntry)} journal entries, "
            f"{await count(PaymentEntry)} payments "
            f"({pe_unallocated} unallocated for reconciliation, {pe_uncleared} uncleared in bank recon)."
        )
        from app.models.buying import PurchaseOrder as PO  # noqa: PLC0415
        from app.models.selling import Quotation as QTN, SalesOrder as SO  # noqa: PLC0415
        from app.models.stock import (  # noqa: PLC0415
            DeliveryNote as DN,
            Item,
            PurchaseReceipt as PRT,
            StockEntry as SE,
        )
        print(
            f"Supply chain: {await count(Item)} items, {await count(SE)} stock entries, "
            f"{await count(QTN)} quotations, {await count(SO)} sales orders, "
            f"{await count(DN)} delivery notes, {await count(PO)} purchase orders, "
            f"{await count(PRT)} purchase receipts."
        )
        print("Demo data complete.")
    await engine.dispose()


async def seed_supply_chain(db, actor, customers, suppliers, income_account, recent, extras=None) -> None:
    """Modules 03-05 demo data: items, warehouses, stock, the procurement chain
    (MR -> RFQ -> SQ -> PO -> PR -> PI) and the sales chain (QTN -> SO -> DN -> SI).

    ``extras`` (parity masters) is optional so the legacy --phase3-topup path
    still works; when present, the cycle documents showcase per-line discounts,
    payment terms and the selling More-Info fields."""
    pt = extras or {}
    # --- warehouses + item groups ---------------------------------------
    main_store = await stock_masters.create_warehouse(
        db, WarehouseCreate(warehouse_name="Main Store"), actor)
    showroom = await stock_masters.create_warehouse(
        db, WarehouseCreate(warehouse_name="Showroom"), actor)
    finished_group = await stock_masters.create_item_group(
        db, ItemGroupCreate(item_group_name="Finished Goods"), actor)
    raw_group = await stock_masters.create_item_group(
        db, ItemGroupCreate(item_group_name="Raw Materials"), actor)

    # --- items: finished goods (sales) + raw materials (purchase) --------
    finished_items = []
    for name, rate, hsn in SALES_ITEMS:
        code = name.upper().replace(" ", "-").replace(".", "")[:30]
        finished_items.append(await stock_masters.create_item(
            db,
            ItemCreate(
                item_code=code, item_name=name, item_group_id=finished_group.id,
                standard_rate=_d(rate), valuation_rate=_d(rate * 0.55),
                default_warehouse_id=main_store.id,
                income_account_id=income_account.id,
                hsn_sac_code=hsn, gst_treatment="Taxable",
            ),
            actor,
        ))
    raw_items = []
    for name, rate, hsn in PURCHASE_ITEMS:
        code = "RM-" + name.upper().replace(" ", "-")[:26]
        # Multi-UOM (Phase 4) example: cartons are bought in Box-of-25, stocked in Nos
        uom_kwargs: dict = (
            {"purchase_uom": "Box", "purchase_uom_factor": _d(25)}
            if name == "Packaging Carton L"
            else {}
        )
        raw_items.append(await stock_masters.create_item(
            db,
            ItemCreate(
                item_code=code, item_name=name, item_group_id=raw_group.id,
                valuation_rate=_d(rate), is_sales_item=False,
                default_warehouse_id=main_store.id,
                reorder_level=_d(50), reorder_qty=_d(200),
                hsn_sac_code=hsn, gst_treatment="Taxable",
                **uom_kwargs,
            ),
            actor,
        ))
    print(f"Stock: {len(finished_items)} finished items, {len(raw_items)} raw materials, 2 warehouses")

    # --- opening stock (Material Receipt at valuation rates) -------------
    opening = await se_service.create_stock_entry(
        db,
        StockEntryCreate(
            purpose="Material Receipt", posting_date=recent(45),
            to_warehouse_id=main_store.id,
            remarks="Opening stock",
            items=[
                StockEntryItemIn(item_id=item.id, qty=_d(rng.randint(25, 60)),
                                 basic_rate=item.valuation_rate)
                for item in finished_items
            ] + [
                StockEntryItemIn(item_id=item.id, qty=_d(rng.randint(100, 400)),
                                 basic_rate=item.valuation_rate)
                for item in raw_items
            ],
        ),
        actor,
    )
    await se_service.submit_stock_entry(db, opening.id, actor)

    # transfer some display units to the showroom + write off a damaged one
    transfer_se = await se_service.create_stock_entry(
        db,
        StockEntryCreate(
            purpose="Material Transfer", posting_date=recent(30),
            from_warehouse_id=main_store.id, to_warehouse_id=showroom.id,
            remarks="Display units for the showroom",
            items=[StockEntryItemIn(item_id=item.id, qty=_d(2)) for item in finished_items[:4]],
        ),
        actor,
    )
    await se_service.submit_stock_entry(db, transfer_se.id, actor)
    damaged = await se_service.create_stock_entry(
        db,
        StockEntryCreate(
            purpose="Material Issue", posting_date=recent(14),
            from_warehouse_id=showroom.id,
            remarks="Damaged in transit — written off",
            items=[StockEntryItemIn(item_id=finished_items[0].id, qty=_d(1))],
        ),
        actor,
    )
    await se_service.submit_stock_entry(db, damaged.id, actor)

    # --- physical count: a stock reconciliation adjusts to counted qty -------
    recon = await recon_service.create_stock_reconciliation(
        db,
        StockReconciliationCreate(
            purpose="Stock Reconciliation", posting_date=recent(7),
            set_warehouse_id=main_store.id,
            remarks="Quarterly physical count adjustment",
            items=[
                # counted qty differs from the books -> posts a difference
                StockReconciliationItemIn(item_id=finished_items[1].id, qty=_d(40)),
                StockReconciliationItemIn(item_id=raw_items[1].id, qty=_d(250)),
            ],
        ),
        actor,
    )
    await recon_service.submit_stock_reconciliation(db, recon.id, actor)

    # --- procurement: MR -> RFQ -> Supplier Quotation -> PO -> PR -> PI --
    mr = await mr_service.create_material_request(
        db,
        MaterialRequestCreate(
            posting_date=recent(20), schedule_date=recent(20) + timedelta(days=10),
            remarks="Restock motors and PCBs",
            items=[
                MaterialRequestItemIn(item_id=raw_items[0].id, qty=_d(100)),
                MaterialRequestItemIn(item_id=raw_items[4].id, qty=_d(150)),
            ],
        ),
        actor,
    )
    mr = await mr_service.submit_material_request(db, mr.id, actor)

    rfq = await rfq_service.create_rfq(
        db,
        RFQCreate(
            posting_date=recent(19),
            message_for_supplier="Please quote your best rates for the attached items.",
            items=[RFQItemIn(item_id=raw_items[0].id, qty=_d(100)),
                   RFQItemIn(item_id=raw_items[4].id, qty=_d(150))],
            supplier_ids=[suppliers[0].id, suppliers[2].id],
        ),
        actor,
    )
    await rfq_service.submit_rfq(db, rfq.id, actor)
    sq = await rfq_service.create_supplier_quotation(
        db,
        SupplierQuotationCreate(
            supplier_id=suppliers[0].id, posting_date=recent(17), rfq_id=rfq.id,
            items=[
                SupplierQuotationItemIn(item_id=raw_items[0].id, qty=_d(100),
                                        rate=_d(PURCHASE_ITEMS[0][1] * 0.97),
                                        rfq_item_id=rfq.items[0].id),
                SupplierQuotationItemIn(item_id=raw_items[4].id, qty=_d(150),
                                        rate=_d(PURCHASE_ITEMS[4][1] * 0.98),
                                        rfq_item_id=rfq.items[1].id),
            ],
        ),
        actor,
    )
    await rfq_service.submit_supplier_quotation(db, sq.id, actor)

    # PO #1: full cycle (receive everything, bill everything) -> Completed
    po1 = await po_service.create_purchase_order(
        db,
        PurchaseOrderCreate(
            supplier_id=suppliers[0].id, posting_date=recent(16),
            schedule_date=recent(16) + timedelta(days=7),
            supplier_quotation_id=sq.id,
            payment_terms_template_id=pt.get("ptt_net30"),
            items=[
                OrderItemIn(item_id=raw_items[0].id, qty=_d(100),
                            rate=_d(PURCHASE_ITEMS[0][1] * 0.97), discount_percentage=_d(5),
                            material_request_item_id=mr.items[0].id),
                OrderItemIn(item_id=raw_items[4].id, qty=_d(150),
                            rate=_d(PURCHASE_ITEMS[4][1] * 0.98),
                            material_request_item_id=mr.items[1].id),
            ],
        ),
        actor,
    )
    po1 = await po_service.submit_purchase_order(db, po1.id, actor)
    pr1 = await pr_service.create_purchase_receipt(
        db,
        PurchaseReceiptCreate(
            supplier_id=suppliers[0].id, posting_date=recent(10),
            items=[
                PurchaseReceiptItemIn(item_id=row.item_id, qty=row.qty,
                                      purchase_order_item_id=row.id)
                for row in po1.items
            ],
        ),
        actor,
    )
    pr1 = await pr_service.submit_purchase_receipt(db, pr1.id, actor)
    pi_cycle = await pi_service.create_purchase_invoice(
        db,
        PurchaseInvoiceCreate(
            supplier_id=suppliers[0].id, posting_date=recent(7),
            due_date=recent(7) + timedelta(days=30), bill_no="VEND-PO-1",
            payment_terms_template_id=pt.get("ptt_net30"),
            items=[
                InvoiceItemIn(
                    item_name=row.item_name or "", qty=row.qty, rate=row.rate,
                    item_id=row.item_id,
                    purchase_order_item_id=row.purchase_order_item_id,
                    purchase_receipt_item_id=row.id,
                )
                for row in pr1.items
            ],
        ),
        actor,
    )
    await pi_service.submit_purchase_invoice(db, pi_cycle.id, actor)

    # PO #2: submitted, partially received -> To Receive and Bill / To Bill
    po2 = await po_service.create_purchase_order(
        db,
        PurchaseOrderCreate(
            supplier_id=suppliers[3].id, posting_date=recent(6),
            schedule_date=TODAY + timedelta(days=5),
            items=[OrderItemIn(item_id=raw_items[1].id, qty=_d(200), rate=_d(PURCHASE_ITEMS[1][1])),
                   OrderItemIn(item_id=raw_items[5].id, qty=_d(500), rate=_d(PURCHASE_ITEMS[5][1]))],
        ),
        actor,
    )
    po2 = await po_service.submit_purchase_order(db, po2.id, actor)
    pr2 = await pr_service.create_purchase_receipt(
        db,
        PurchaseReceiptCreate(
            supplier_id=suppliers[3].id, posting_date=recent(2),
            items=[PurchaseReceiptItemIn(item_id=po2.items[0].item_id, qty=_d(120),
                                         purchase_order_item_id=po2.items[0].id)],
        ),
        actor,
    )
    await pr_service.submit_purchase_receipt(db, pr2.id, actor)

    # PO #3: draft awaiting approval
    await po_service.create_purchase_order(
        db,
        PurchaseOrderCreate(
            supplier_id=suppliers[4].id, posting_date=TODAY,
            items=[OrderItemIn(item_id=raw_items[3].id, qty=_d(80))],
        ),
        actor,
    )

    # --- sales: Quotation -> SO -> DN -> SI -------------------------------
    # Quotation #1 stays Open; #2 becomes a fully delivered+billed order
    qtn_open = await qtn_service.create_quotation(
        db,
        QuotationCreate(
            customer_id=customers[4].id, posting_date=recent(5),
            valid_till=TODAY + timedelta(days=15),
            items=[OrderItemIn(item_id=finished_items[2].id, qty=_d(6)),
                   OrderItemIn(item_id=finished_items[6].id, qty=_d(10))],
        ),
        actor,
    )
    await qtn_service.submit_quotation(db, qtn_open.id, actor)

    qtn2 = await qtn_service.create_quotation(
        db,
        QuotationCreate(
            customer_id=customers[0].id, posting_date=recent(15),
            campaign_id=pt.get("campaign"), territory_id=pt.get("territory"),
            customer_group_id=pt.get("customer_group"), sales_partner_id=pt.get("sales_partner"),
            payment_terms_template_id=pt.get("ptt_split"),
            items=[OrderItemIn(item_id=finished_items[0].id, qty=_d(10), discount_percentage=_d(10)),
                   OrderItemIn(item_id=finished_items[3].id, qty=_d(12))],
        ),
        actor,
    )
    qtn2 = await qtn_service.submit_quotation(db, qtn2.id, actor)

    so1 = await so_service.create_sales_order(
        db,
        SalesOrderCreate(
            customer_id=customers[0].id, posting_date=recent(13),
            delivery_date=recent(13) + timedelta(days=7),
            quotation_id=qtn2.id,
            campaign_id=pt.get("campaign"), territory_id=pt.get("territory"),
            payment_terms_template_id=pt.get("ptt_net30"),
            items=[
                OrderItemIn(item_id=row.item_id, qty=row.qty, rate=row.rate,
                            quotation_item_id=row.id)
                for row in qtn2.items
            ],
        ),
        actor,
    )
    so1, _ = await so_service.submit_sales_order(db, so1.id, actor)
    dn1 = await dn_service.create_delivery_note(
        db,
        DeliveryNoteCreate(
            customer_id=customers[0].id, posting_date=recent(9),
            items=[
                DeliveryNoteItemIn(item_id=row.item_id, qty=row.qty,
                                   sales_order_item_id=row.id)
                for row in so1.items
            ],
        ),
        actor,
    )
    dn1 = await dn_service.submit_delivery_note(db, dn1.id, actor)
    si_cycle = await si_service.create_sales_invoice(
        db,
        SalesInvoiceCreate(
            customer_id=customers[0].id, posting_date=recent(6),
            due_date=recent(6) + timedelta(days=15),
            payment_terms_template_id=pt.get("ptt_net30"),
            items=[
                InvoiceItemIn(
                    item_name=row.item_name or "", qty=row.qty, rate=row.rate,
                    item_id=row.item_id, cost_center_id=pt.get("cc_sales"),
                    sales_order_item_id=row.sales_order_item_id,
                    delivery_note_item_id=row.id,
                )
                for row in dn1.items
            ],
        ),
        actor,
    )
    await si_service.submit_sales_invoice(db, si_cycle.id, actor)

    # SO #2: submitted with reservations, half delivered -> To Deliver and Bill
    so2 = await so_service.create_sales_order(
        db,
        SalesOrderCreate(
            customer_id=customers[3].id, posting_date=recent(4),
            delivery_date=TODAY + timedelta(days=4),
            items=[OrderItemIn(item_id=finished_items[1].id, qty=_d(8)),
                   OrderItemIn(item_id=finished_items[4].id, qty=_d(6))],
        ),
        actor,
    )
    so2, _ = await so_service.submit_sales_order(db, so2.id, actor)
    dn2 = await dn_service.create_delivery_note(
        db,
        DeliveryNoteCreate(
            customer_id=customers[3].id, posting_date=recent(1),
            items=[DeliveryNoteItemIn(item_id=so2.items[0].item_id, qty=_d(4),
                                      sales_order_item_id=so2.items[0].id)],
        ),
        actor,
    )
    await dn_service.submit_delivery_note(db, dn2.id, actor)

    # SO #3: draft
    await so_service.create_sales_order(
        db,
        SalesOrderCreate(
            customer_id=customers[7].id, posting_date=TODAY,
            items=[OrderItemIn(item_id=finished_items[5].id, qty=_d(3))],
        ),
        actor,
    )

    # --- Phase 3 depth: returns, accepted/rejected split, landed cost -----
    company_id = suppliers[0].company_id
    # Sales return: the customer sends 2 units of DN #1 back (stock back in, COGS
    # reversed at the original delivery value, SO delivered_qty nets down).
    sret = await dn_service.create_delivery_note(
        db,
        DeliveryNoteCreate(
            customer_id=customers[0].id, posting_date=recent(3),
            is_return=True, return_against_id=dn1.id,
            items=[DeliveryNoteItemIn(
                item_id=dn1.items[0].item_id, qty=_d(2),
                warehouse_id=dn1.items[0].warehouse_id,
                sales_order_item_id=dn1.items[0].sales_order_item_id,
            )],
        ),
        actor,
    )
    await dn_service.submit_delivery_note(db, sret.id, actor)

    # Purchase return: send 5 units of PR #1 back to the supplier (stock out at the
    # original receipt rate, SRBNB reversed, PO received_qty nets down).
    pret = await pr_service.create_purchase_receipt(
        db,
        PurchaseReceiptCreate(
            supplier_id=suppliers[0].id, posting_date=recent(3),
            is_return=True, return_against_id=pr1.id, supplier_delivery_note="SDN-RET-001",
            items=[PurchaseReceiptItemIn(
                item_id=pr1.items[0].item_id, qty=_d(5),
                warehouse_id=pr1.items[0].warehouse_id,
                purchase_order_item_id=pr1.items[0].purchase_order_item_id,
            )],
        ),
        actor,
    )
    await pr_service.submit_purchase_receipt(db, pret.id, actor)

    # QC split: receive 40 good + 5 rejected; rejected lands in the Showroom warehouse.
    pr_qc = await pr_service.create_purchase_receipt(
        db,
        PurchaseReceiptCreate(
            supplier_id=suppliers[2].id, posting_date=recent(2),
            items=[PurchaseReceiptItemIn(
                item_id=raw_items[2].id, qty=_d(40), rate=_d(PURCHASE_ITEMS[2][1]),
                warehouse_id=main_store.id,
                rejected_qty=_d(5), rejected_warehouse_id=showroom.id,
            )],
        ),
        actor,
    )
    await pr_service.submit_purchase_receipt(db, pr_qc.id, actor)

    # Landed cost: a receipt with inbound freight folded into the incoming valuation.
    eiv = await _leaf_account(
        db, company_id, ["Expenses Included In Valuation"],
        account_type="Expenses Included In Valuation", root_type="Expense",
    )
    pr_lc = await pr_service.create_purchase_receipt(
        db,
        PurchaseReceiptCreate(
            supplier_id=suppliers[1].id, posting_date=recent(2),
            items=[PurchaseReceiptItemIn(
                item_id=raw_items[3].id, qty=_d(50), rate=_d(PURCHASE_ITEMS[3][1]),
                warehouse_id=main_store.id,
            )],
            charges=[PurchaseReceiptChargeIn(
                description="Inbound Freight", account_id=eiv.id, amount=_d(1500),
            )],
        ),
        actor,
    )
    await pr_service.submit_purchase_receipt(db, pr_lc.id, actor)

    print("Cycles: PO->PR->PI and QTN->SO->DN->SI seeded (plus returns, QC split, landed cost)")


async def topup_main() -> None:
    """Non-destructive: add only the Modules 03-05 demo data to the existing
    demo company (run `alembic upgrade head` against the target DB first)."""
    async with async_session_factory() as db:
        company = await db.scalar(
            select(Company).where(Company.company_name == ARGS.company_name))
        if company is None:
            sys.exit(f"Company '{ARGS.company_name}' not found — run a full seed instead.")
        from app.models.stock import Item  # noqa: PLC0415
        if await db.scalar(select(Item).where(Item.company_id == company.id)):
            sys.exit("Items already exist for the demo company — refusing to double-seed.")
        admin = await db.scalar(select(User).where(User.email == ARGS.admin_email.lower()))
        if admin is None:
            sys.exit(f"Admin user {ARGS.admin_email} not found.")
        actor = CurrentUser({
            "sub": str(admin.id), "email": admin.email,
            "company_id": str(company.id), "roles": ["System Manager"],
        })
        await set_company_context(db, company.id)
        await seed_permissions(db)  # adds the new Module 03-05 doctype grants

        # the demo users were created before Phase 3 — grant the new roles so
        # manager@/sales@ can actually use the Stock/Buying/Selling pages
        from app.models.core import UserRole  # noqa: PLC0415

        for email, roles in [
            ("manager@demo-erp.com", ["Stock Manager", "Purchase Manager", "Sales Manager"]),
            ("sales@demo-erp.com", ["Stock User"]),
        ]:
            demo_user = await db.scalar(select(User).where(User.email == email))
            if demo_user is None:
                continue
            for role in roles:
                existing = await db.scalar(
                    select(UserRole).where(
                        UserRole.user_id == demo_user.id,
                        UserRole.role == role,
                        UserRole.company_id == company.id,
                    )
                )
                if existing is None:
                    db.add(UserRole(user_id=demo_user.id, role=role, company_id=company.id))
        await db.commit()

        from app.models.buying import Supplier  # noqa: PLC0415
        from app.models.selling import Customer  # noqa: PLC0415

        customers = list((await db.execute(
            select(Customer).where(Customer.company_id == company.id)
            .order_by(Customer.creation))).scalars())
        suppliers = list((await db.execute(
            select(Supplier).where(Supplier.company_id == company.id)
            .order_by(Supplier.creation))).scalars())
        if len(customers) < 8 or len(suppliers) < 6:
            sys.exit("Demo parties not found — this top-up expects the standard demo seed.")
        income_account = await _leaf_account(db, company.id, ["Sales"], "Income")
        # the FY covering TODAY — GL posting fails for dates outside any fiscal
        # year, so latest-by-start would break once a new year has begun
        fy = await db.scalar(
            select(FiscalYear).where(
                FiscalYear.company_id == company.id,
                FiscalYear.year_start_date <= TODAY,
                FiscalYear.year_end_date >= TODAY,
            ))
        if fy is None:
            sys.exit("No fiscal year covers today — create one, then re-run the top-up.")

        def recent(days_ago: int) -> date:
            return max(fy.year_start_date, TODAY - timedelta(days=days_ago))

        await seed_supply_chain(db, actor, customers, suppliers, income_account, recent)
        print("Phase 3 top-up complete.")
    await engine.dispose()


if __name__ == "__main__":
    if ARGS.phase3_topup:
        asyncio.run(topup_main())
    else:
        if ARGS.reset_schema:
            asyncio.run(_reset_schema())
            _run_migrations()
        asyncio.run(main())
