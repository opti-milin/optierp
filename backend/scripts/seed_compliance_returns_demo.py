"""Additive demo data so GST Returns + TDS Returns show non-zero defaults.

Idempotent: keyed by remarks / bill_no. Safe to re-run against an existing demo DB
without --reset-schema.

    python -m scripts.seed_compliance_returns_demo
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app.core.database import async_session_factory, set_company_context  # noqa: E402
from app.core.security import CurrentUser  # noqa: E402
from app.models.accounts import Account, PurchaseInvoice, SalesInvoice, TaxWithholdingCategory  # noqa: E402
from app.models.buying import Supplier  # noqa: E402
from app.models.core import Company, User  # noqa: E402
from app.models.selling import Customer  # noqa: E402
from app.schemas.accounts import InvoiceItemIn, PurchaseInvoiceCreate, SalesInvoiceCreate  # noqa: E402
from app.services import purchase_invoice as pi_service  # noqa: E402
from app.services import sales_invoice as si_service  # noqa: E402

TODAY = date.today()
SI_REMARKS = ("GST returns demo SI #1", "GST returns demo SI #2", "GST returns demo SI #3")
PI_BILLS = ("VEND-TDS-2100", "VEND-TDS-2101")


async def _actor(company: Company) -> CurrentUser:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        user = await db.scalar(select(User).where(User.email == "admin@example.com"))
        if user is None:
            user = await db.scalar(select(User).order_by(User.creation.asc()))
        if user is None:
            raise RuntimeError("No user found")
        return CurrentUser(
            {
                "sub": str(user.id),
                "email": user.email,
                "company_id": str(company.id),
                "roles": ["Accounts Manager", "Purchase Manager", "System Manager"],
            }
        )


async def _leaf(company_id: uuid.UUID, names: list[str], *, root_type: str | None = None) -> Account:
    async with async_session_factory() as db:
        await set_company_context(db, company_id)
        for name in names:
            acct = await db.scalar(
                select(Account).where(
                    Account.company_id == company_id,
                    Account.account_name == name,
                    Account.is_group.is_(False),
                )
            )
            if acct is not None:
                return acct
        stmt = select(Account).where(Account.company_id == company_id, Account.is_group.is_(False))
        if root_type:
            stmt = stmt.where(Account.root_type == root_type)
        acct = await db.scalar(stmt)
        if acct is None:
            raise RuntimeError(f"No leaf account for {names}")
        return acct


async def main() -> None:
    async with async_session_factory() as db:
        # DEMO_COMPANY names the tenant to seed when more than one exists;
        # without it, fall back to the first company as before.
        wanted = os.environ.get("DEMO_COMPANY")
        company = None
        if wanted:
            company = await db.scalar(select(Company).where(Company.company_name == wanted))
        if company is None:
            company = await db.scalar(select(Company).order_by(Company.creation.asc()))
        if company is None:
            raise RuntimeError("No company found — run seed_demo first")

    actor = await _actor(company)
    q_start = date(TODAY.year, (TODAY.month - 1) // 3 * 3 + 1, 1)
    prev_month_last = TODAY.replace(day=1) - timedelta(days=1)
    prev_month_mid = prev_month_last.replace(day=min(15, prev_month_last.day))
    si_dates = (prev_month_mid, TODAY, max(q_start, TODAY - timedelta(days=10)))
    pi_dates = (max(q_start, TODAY - timedelta(days=20)), max(q_start, TODAY - timedelta(days=5)))

    income = await _leaf(company.id, ["Sales"], root_type="Income")
    expense = await _leaf(company.id, ["Cost of Goods Sold", "Office Rent", "Rent"], root_type="Expense")

    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        customers = (
            await db.scalars(
                select(Customer).where(Customer.company_id == company.id).order_by(Customer.creation)
            )
        ).all()
        suppliers = (
            await db.scalars(
                select(Supplier).where(Supplier.company_id == company.id).order_by(Supplier.creation)
            )
        ).all()
        if not customers or not suppliers:
            raise RuntimeError("Need customers and suppliers — run seed_demo first")
        gst_customer = next((c for c in customers if c.tax_category_id), customers[0])
        twc = await db.scalar(
            select(TaxWithholdingCategory).where(
                TaxWithholdingCategory.company_id == company.id,
                TaxWithholdingCategory.kind == "TDS",
                TaxWithholdingCategory.disabled.is_(False),
            )
        )
        if twc is None:
            raise RuntimeError("No TDS category — run seed_demo first")

        created_si = 0
        for remark, posting in zip(SI_REMARKS, si_dates, strict=True):
            existing = await db.scalar(
                select(SalesInvoice).where(
                    SalesInvoice.company_id == company.id,
                    SalesInvoice.remarks == remark,
                )
            )
            if existing is not None:
                continue
            doc = await si_service.create_sales_invoice(
                db,
                SalesInvoiceCreate(
                    customer_id=gst_customer.id,
                    posting_date=posting,
                    due_date=posting + timedelta(days=15),
                    remarks=remark,
                    items=[
                        InvoiceItemIn(
                            item_name="Mixer Grinder X200",
                            qty=Decimal("2"),
                            rate=Decimal("2850"),
                            account_id=income.id,
                            hsn_sac_code="85094000",
                        ),
                        InvoiceItemIn(
                            item_name="Induction Cooktop Pro",
                            qty=Decimal("1"),
                            rate=Decimal("3499"),
                            account_id=income.id,
                            hsn_sac_code="85166000",
                        ),
                    ],
                ),
                actor,
            )
            await si_service.submit_sales_invoice(db, doc.id, actor)
            created_si += 1

        created_pi = 0
        for bill_no, posting in zip(PI_BILLS, pi_dates, strict=True):
            existing = await db.scalar(
                select(PurchaseInvoice).where(
                    PurchaseInvoice.company_id == company.id,
                    PurchaseInvoice.bill_no == bill_no,
                )
            )
            if existing is not None:
                if existing.docstatus == 0:
                    await pi_service.submit_purchase_invoice(db, existing.id, actor)
                    created_pi += 1
                continue
            doc = await pi_service.create_purchase_invoice(
                db,
                PurchaseInvoiceCreate(
                    supplier_id=suppliers[0].id,
                    posting_date=posting,
                    due_date=posting + timedelta(days=30),
                    bill_no=bill_no,
                    bill_date=posting,
                    remarks="TDS returns demo PI",
                    tax_withholding_category_id=twc.id,
                    items=[
                        InvoiceItemIn(
                            item_name="Contract labour charges",
                            qty=Decimal("1"),
                            rate=Decimal("85000"),
                            account_id=expense.id,
                        )
                    ],
                ),
                actor,
            )
            await pi_service.submit_purchase_invoice(db, doc.id, actor)
            created_pi += 1

        await db.commit()
        print(
            {
                "company": company.company_name,
                "today": str(TODAY),
                "si_dates": [str(d) for d in si_dates],
                "pi_dates": [str(d) for d in pi_dates],
                "created_sales_invoices": created_si,
                "created_tds_purchase_invoices": created_pi,
                "tds_category": twc.category_name,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
