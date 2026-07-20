"""Idempotent seed of Income Tax rate tables + adjustment categories for a company.

Usage (against a running stack / local .env):
  cd backend && python -m scripts.seed_income_tax_masters
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.database import async_session_factory, set_company_context
from app.models.core import Company, User
from app.services.income_tax_masters import ensure_income_tax_masters


async def main() -> None:
    async with async_session_factory() as db:
        company = await db.scalar(select(Company).order_by(Company.creation).limit(1))
        if company is None:
            sys.exit("No company found — run seed_demo first.")
        admin = await db.scalar(select(User).order_by(User.creation).limit(1))
        if admin is None:
            sys.exit("No user found.")
        await set_company_context(db, company.id)
        counts = await ensure_income_tax_masters(
            db, company_id=company.id, user_id=admin.id
        )
        await db.commit()
        print(
            f"Income Tax masters for {company.company_name}: "
            f"+{counts['rate_tables']} rate tables, "
            f"+{counts['adjustment_categories']} adjustment categories"
        )


if __name__ == "__main__":
    asyncio.run(main())
