"""Seed a real entity-books income-tax demo for the current company.

Creates actual accounting records through the service layer:
- submitted Journal Entries that produce P&L income and expense
- a submitted Purchase Invoice with TDS to drive 26Q / TDS credit
- matching income-tax settings / rate tables
- an EntityBooks computation for AY 2025-26 refreshed from books

Safe to re-run: demo vouchers are created once, then the computation is refreshed.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app.core.database import async_session_factory, set_company_context  # noqa: E402
from app.core.security import CurrentUser  # noqa: E402
from app.models.accounts import Account, FiscalYear, JournalEntry, PurchaseInvoice, TaxWithholdingCategory  # noqa: E402
from app.models.buying import Supplier  # noqa: E402
from app.models.compliance import IncomeTaxComputation, IncomeTaxRateTable  # noqa: E402
from app.models.core import Company, User  # noqa: E402
from app.schemas.accounts import AccountCreate, InvoiceItemIn, JournalEntryAccountIn, JournalEntryCreate, PurchaseInvoiceCreate  # noqa: E402
from app.schemas.compliance import IncomeTaxComputationCreate, IncomeTaxComputationUpdate, IncomeTaxSettings  # noqa: E402
from app.services import accounts_masters as accounts_masters_service  # noqa: E402
from app.services import income_tax_computation as income_tax_service  # noqa: E402
from app.services import income_tax_masters as income_tax_masters_service  # noqa: E402
from app.services import journal_entry as journal_entry_service  # noqa: E402
from app.services import purchase_invoice as purchase_invoice_service  # noqa: E402
from app.services.income_tax_settings import save_income_tax_settings  # noqa: E402


DEMO_AY = "2025-26"
DEMO_FROM = date(2024, 4, 1)
DEMO_TO = date(2025, 3, 31)
ZERO = Decimal("0")


async def _leaf_account(
    company_id: uuid.UUID,
    candidates: list[str],
    *,
    root_type: str | None = None,
    account_type: str | None = None,
) -> Account:
    async with async_session_factory() as db:
        await set_company_context(db, company_id)
        for name in candidates:
            row = await db.scalar(
                select(Account).where(
                    Account.company_id == company_id,
                    Account.account_name == name,
                    Account.is_group.is_(False),
                )
            )
            if row is not None:
                return row
        stmt = select(Account).where(Account.company_id == company_id, Account.is_group.is_(False))
        if account_type is not None:
            stmt = stmt.where(Account.account_type == account_type)
        elif root_type is not None:
            stmt = stmt.where(Account.root_type == root_type)
        row = await db.scalar(stmt)
        if row is None:
            raise RuntimeError(f"No account found for {candidates}")
        return row


async def _group_account(company_id: uuid.UUID, candidates: list[str]) -> Account:
    async with async_session_factory() as db:
        await set_company_context(db, company_id)
        for name in candidates:
            row = await db.scalar(
                select(Account).where(
                    Account.company_id == company_id,
                    Account.account_name == name,
                    Account.is_group.is_(True),
                )
            )
            if row is not None:
                return row
        raise RuntimeError(f"No group account found for {candidates}")


async def _actor_for_company(company: Company) -> CurrentUser:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        user = await db.scalar(select(User).order_by(User.creation.asc()))
        if user is None:
            raise RuntimeError("No user found for company")
        return CurrentUser(
            {
                "sub": str(user.id),
                "email": user.email,
                "company_id": str(company.id),
                "roles": ["Accounts Manager", "Purchase Manager", "System Manager"],
            }
        )


async def _ensure_tds_category(company: Company, actor: CurrentUser) -> TaxWithholdingCategory:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        existing = await db.scalar(
            select(TaxWithholdingCategory).where(
                TaxWithholdingCategory.company_id == company.id,
                TaxWithholdingCategory.category_name == "TDS 194C - Demo Contractor 2%",
            )
        )
        if existing is not None:
            return existing

        duties = await _group_account(company.id, ["Duties and Taxes", "Duties & Taxes"])
        tds_payable = await db.scalar(
            select(Account).where(
                Account.company_id == company.id,
                Account.account_name == "TDS Payable",
                Account.is_group.is_(False),
            )
        )
        if tds_payable is None:
            tds_payable = await accounts_masters_service.create_account(
                db,
                AccountCreate(
                    account_name="TDS Payable",
                    parent_account_id=duties.id,
                    account_type="Tax",
                ),
                actor,
            )
        category = TaxWithholdingCategory(
            id=uuid.uuid4(),
            company_id=company.id,
            category_name="TDS 194C - Demo Contractor 2%",
            kind="TDS",
            rate=Decimal("2"),
            threshold=ZERO,
            account_id=tds_payable.id,
            owner=actor.id,
            modified_by=actor.id,
        )
        db.add(category)
        await db.commit()
        return category


async def _ensure_supplier(company: Company, actor: CurrentUser) -> Supplier:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        existing = await db.scalar(
            select(Supplier).where(
                Supplier.company_id == company.id,
                Supplier.supplier_name == "Demo Contractor Services",
            )
        )
        if existing is not None:
            return existing
        supplier = Supplier(
            id=uuid.uuid4(),
            company_id=company.id,
            supplier_name="Demo Contractor Services",
            supplier_type="Company",
            tax_id="27ABCDE1234F1Z5",
            payable_account_id=company.default_payable_account_id,
            owner=actor.id,
            modified_by=actor.id,
        )
        db.add(supplier)
        await db.commit()
        return supplier


async def _ensure_fiscal_year(company: Company, actor: CurrentUser) -> FiscalYear:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        existing = await db.scalar(
            select(FiscalYear).where(
                FiscalYear.company_id == company.id,
                FiscalYear.year == "FY 2024-25",
            )
        )
        if existing is not None:
            return existing
        fy = FiscalYear(
            id=uuid.uuid4(),
            company_id=company.id,
            year="FY 2024-25",
            year_start_date=DEMO_FROM,
            year_end_date=DEMO_TO,
            owner=actor.id,
            modified_by=actor.id,
        )
        db.add(fy)
        await db.commit()
        return fy


async def _ensure_journal_entry(
    company: Company,
    actor: CurrentUser,
    remarks: str,
    posting_date: date,
    rows: list[JournalEntryAccountIn],
) -> None:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        existing = await db.scalar(
            select(JournalEntry).where(
                JournalEntry.company_id == company.id,
                JournalEntry.remarks == remarks,
            )
        )
        if existing is not None:
            if existing.docstatus == 0:
                await journal_entry_service.submit_journal_entry(db, existing.id, actor)
            return
        doc = await journal_entry_service.create_journal_entry(
            db,
            JournalEntryCreate(
                posting_date=posting_date,
                remarks=remarks,
                accounts=rows,
            ),
            actor,
        )
        await journal_entry_service.submit_journal_entry(db, doc.id, actor)


async def _ensure_purchase_invoice(
    company: Company,
    actor: CurrentUser,
    supplier: Supplier,
    tds_category: TaxWithholdingCategory,
    expense_account_id: uuid.UUID,
) -> None:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        existing = await db.scalar(
            select(PurchaseInvoice).where(
                PurchaseInvoice.company_id == company.id,
                PurchaseInvoice.bill_no == "DEMO-TDS-PI-2025",
            )
        )
        if existing is not None:
            if existing.docstatus == 0:
                await purchase_invoice_service.submit_purchase_invoice(db, existing.id, actor)
            return
        doc = await purchase_invoice_service.create_purchase_invoice(
            db,
            PurchaseInvoiceCreate(
                supplier_id=supplier.id,
                posting_date=date(2024, 9, 30),
                bill_no="DEMO-TDS-PI-2025",
                bill_date=date(2024, 9, 30),
                remarks="Income-tax entity-books demo PI with TDS",
                tax_withholding_category_id=tds_category.id,
                items=[
                    InvoiceItemIn(
                        item_name="Contract labour charges",
                        description="Demo contractor bill to generate TDS credit",
                        qty=Decimal("1"),
                        rate=Decimal("100000"),
                        account_id=expense_account_id,
                    )
                ],
            ),
            actor,
        )
        await purchase_invoice_service.submit_purchase_invoice(db, doc.id, actor)


async def _ensure_computation(company: Company, actor: CurrentUser) -> IncomeTaxComputation:
    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        existing = await db.scalar(
            select(IncomeTaxComputation).where(
                IncomeTaxComputation.company_id == company.id,
                IncomeTaxComputation.assessment_year == DEMO_AY,
                IncomeTaxComputation.assessee_mode == "EntityBooks",
                IncomeTaxComputation.docstatus != 2,
            )
        )
        if existing is None:
            return await income_tax_service.create_computation(
                db,
                IncomeTaxComputationCreate(
                    assessment_year=DEMO_AY,
                    from_date=DEMO_FROM,
                    to_date=DEMO_TO,
                    assessee_mode="EntityBooks",
                    seed_from_books=True,
                    advance_tax_paid=Decimal("30000"),
                    remarks="FY 2024-25 entity books - seeded demo data",
                    adjustments=[],
                ),
                actor,
            )
        if existing.docstatus == 0:
            return await income_tax_service.update_computation(
                db,
                existing.id,
                IncomeTaxComputationUpdate(
                    reseeds_from_books=True,
                    resolve_rate_from_settings=True,
                    advance_tax_paid=Decimal("30000"),
                    remarks="FY 2024-25 entity books - seeded demo data",
                ),
                actor,
            )
        return existing


async def main() -> None:
    async with async_session_factory() as db:
        company = await db.scalar(select(Company).order_by(Company.creation.asc()))
        if company is None:
            raise RuntimeError("No company found")

    actor = await _actor_for_company(company)
    await _ensure_fiscal_year(company, actor)

    sales_bank = await _leaf_account(company.id, ["Cash", "HDFC Current A/C", "ICICI Savings A/C"], account_type="Bank")
    income_account = await _leaf_account(company.id, ["Sales"], root_type="Income")
    rent_account = await _leaf_account(company.id, ["Office Rent", "Rent"], root_type="Expense")
    salary_account = await _leaf_account(company.id, ["Salary", "Salaries"], root_type="Expense")

    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        await save_income_tax_settings(
            db,
            IncomeTaxSettings(
                entity_type="Company",
                filing_regime="Normal",
                default_assessment_year=DEMO_AY,
            ),
            actor,
        )
        await income_tax_masters_service.ensure_income_tax_masters(
            db,
            company_id=company.id,
            user_id=actor.id,
        )
        await db.commit()

    await _ensure_journal_entry(
        company,
        actor,
        "Income-tax demo JE - operating income",
        date(2024, 7, 15),
        [
            JournalEntryAccountIn(account_id=sales_bank.id, debit=Decimal("950000")),
            JournalEntryAccountIn(account_id=income_account.id, credit=Decimal("950000")),
        ],
    )
    await _ensure_journal_entry(
        company,
        actor,
        "Income-tax demo JE - rent expense",
        date(2024, 8, 10),
        [
            JournalEntryAccountIn(account_id=rent_account.id, debit=Decimal("180000")),
            JournalEntryAccountIn(account_id=sales_bank.id, credit=Decimal("180000")),
        ],
    )
    await _ensure_journal_entry(
        company,
        actor,
        "Income-tax demo JE - salary expense",
        date(2024, 12, 20),
        [
            JournalEntryAccountIn(account_id=salary_account.id, debit=Decimal("220000")),
            JournalEntryAccountIn(account_id=sales_bank.id, credit=Decimal("220000")),
        ],
    )

    supplier = await _ensure_supplier(company, actor)
    tds_category = await _ensure_tds_category(company, actor)
    await _ensure_purchase_invoice(company, actor, supplier, tds_category, rent_account.id)

    computation = await _ensure_computation(company, actor)

    async with async_session_factory() as db:
        await set_company_context(db, company.id)
        rate = await db.scalar(
            select(IncomeTaxRateTable).where(
                IncomeTaxRateTable.company_id == company.id,
                IncomeTaxRateTable.assessment_year == DEMO_AY,
                IncomeTaxRateTable.entity_type == "Company",
                IncomeTaxRateTable.filing_regime == "Normal",
            )
        )
        print(
            {
                "company": company.company_name,
                "assessment_year": DEMO_AY,
                "rate_table_found": rate is not None,
                "computation_id": str(computation.id),
                "computation_name": computation.name,
                "book_profit": str(computation.book_profit),
                "tds_credit": str(computation.tds_credit),
                "taxable_income": str(computation.taxable_income),
                "total_tax": str(computation.total_tax),
                "tax_payable": str(computation.tax_payable),
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
