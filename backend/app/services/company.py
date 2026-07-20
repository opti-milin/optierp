"""Company service — Module 01.

Business logic preserved from erpnext/setup/doctype/company/company.py:
on creation a company gets its Chart of Accounts (from a country template),
a root + "Main" cost center, default accounts and a current fiscal year.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_company_context
from app.core.exceptions import DuplicateError, NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.accounts import CostCenter, FiscalYear
from app.models.core import Company, Currency, UserRole
from app.schemas.core import CompanyCreate, CompanyUpdate
from app.services import coa
from app.services.audit import log_audit, serialize_document
from app.services.pagination import paginate


def _default_fiscal_year_bounds(country_code: str | None, today: date) -> tuple[str, date, date]:
    """Current fiscal year for the company's country.

    Assumption: April–March for India, calendar year otherwise.
    MANUAL_REVIEW: extend per-country fiscal conventions as tenants need them.
    """
    if (country_code or "").upper() == "IN":
        start_year = today.year if today.month >= 4 else today.year - 1
        return (
            f"{start_year}-{start_year + 1}",
            date(start_year, 4, 1),
            date(start_year + 1, 3, 31),
        )
    return str(today.year), date(today.year, 1, 1), date(today.year, 12, 31)


async def create_company(db: AsyncSession, payload: CompanyCreate, user: CurrentUser) -> Company:
    # uniqueness checks (mirrors Company.validate_abbr / autoname by company_name)
    existing = await db.scalar(
        select(Company).where(
            (Company.company_name == payload.company_name) | (Company.abbr == payload.abbr)
        )
    )
    if existing is not None:
        field = "company_name" if existing.company_name == payload.company_name else "abbr"
        raise DuplicateError(f"A company with this {field} already exists", field=field)

    currency = await db.scalar(select(Currency).where(Currency.code == payload.default_currency))
    if currency is None:
        raise ValidationError(
            f"Unknown currency '{payload.default_currency}'", field="default_currency"
        )

    template_key = payload.chart_of_accounts_template
    if template_key == "standard" and payload.country_code:
        template_key = coa.template_for_country(payload.country_code)

    company = Company(
        id=uuid.uuid4(),
        company_name=payload.company_name,
        abbr=payload.abbr,
        country_code=payload.country_code,
        default_currency=payload.default_currency,
        tax_id=payload.tax_id,
        pan=payload.pan,
        tan=payload.tan,
        domain=payload.domain,
        date_of_establishment=payload.date_of_establishment,
        parent_company_id=payload.parent_company_id,
        chart_of_accounts_template=template_key,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(company)
    await db.flush()

    # Switch the RLS tenant context to the new company so the seeded rows
    # (accounts, cost centers, fiscal year) pass the WITH CHECK policies.
    await set_company_context(db, company.id)

    created_accounts = await coa.seed_chart_of_accounts(db, company, template_key)
    coa.resolve_default_accounts(company, created_accounts)

    # Root + "Main" cost center (ERPNext creates "Main - <abbr>")
    root_cc = CostCenter(
        id=uuid.uuid4(), company_id=company.id, cost_center_name=payload.company_name, is_group=True
    )
    db.add(root_cc)
    await db.flush()
    main_cc = CostCenter(
        id=uuid.uuid4(),
        company_id=company.id,
        cost_center_name="Main",
        parent_cost_center_id=root_cc.id,
        is_group=False,
    )
    db.add(main_cc)
    company.default_cost_center_id = main_cc.id

    if payload.create_default_fiscal_year:
        year, start, end = _default_fiscal_year_bounds(payload.country_code, date.today())
        db.add(
            FiscalYear(
                company_id=company.id,
                year=year,
                year_start_date=start,
                year_end_date=end,
                auto_created=True,
            )
        )

    # Give the creating user access to the new company with full rights.
    db.add(UserRole(user_id=user.id, role="System Manager", company_id=company.id))

    await db.flush()
    await log_audit(
        db,
        doctype="Company",
        document_id=company.id,
        action="INSERT",
        user_id=user.id,
        company_id=company.id,
        data_after=serialize_document(company),
    )
    await db.commit()
    await db.refresh(company)
    return company


async def get_company(db: AsyncSession, company_id: uuid.UUID) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company not found")
    return company


async def list_companies(
    db: AsyncSession, user: CurrentUser, page: int = 1, page_size: int = 20
) -> tuple[list[Company], int]:
    """Companies the user can access: any with a company-scoped role, or all
    for global System Managers."""
    stmt = select(Company).order_by(Company.company_name)
    if "System Manager" not in user.roles:
        accessible = select(UserRole.company_id).where(
            UserRole.user_id == user.id, UserRole.company_id.is_not(None)
        )
        stmt = stmt.where(Company.id.in_(accessible))
    return await paginate(db, stmt, page, page_size)


async def update_company(
    db: AsyncSession, company_id: uuid.UUID, payload: CompanyUpdate, user: CurrentUser
) -> Company:
    company = await get_company(db, company_id)
    before = serialize_document(company)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    company.modified_by = user.id
    await db.flush()
    await log_audit(
        db,
        doctype="Company",
        document_id=company.id,
        action="UPDATE",
        user_id=user.id,
        company_id=company.id,
        data_before=before,
        data_after=serialize_document(company),
    )
    await db.commit()
    await db.refresh(company)
    return company
