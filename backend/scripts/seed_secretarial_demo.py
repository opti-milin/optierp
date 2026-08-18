"""Set up two realistic logins for trying the Secretarial module.

The module has two audiences and the difference only becomes real with two
*separate, non-superuser* accounts:

  * a business — a company that sells something and also has statutory duties;
  * a CS practice — a firm whose whole job is other people's statutory duties.

Why not just use the admin account: ``System Manager`` short-circuits
``has_permission()`` and is allowed everything, everywhere. Testing the delegated
access ladder with it would prove nothing — every check would pass regardless of
whether the roles were right. These two users hold ordinary company-scoped roles,
so what they can and cannot do is the real answer.

    python -m scripts.seed_secretarial_demo

Idempotent: re-running updates the roles and leaves existing users alone.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import async_session_factory, set_company_context  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.core import Company, User, UserRole  # noqa: E402
from app.models.secretarial import SecretarialSettings  # noqa: E402

# Note: do not use .test/.local domains here — email-validator rejects reserved TLDs.
DEFAULT_PASSWORD = "Demo!Pass123"

# (email, first name, company name, roles)
DEMO_USERS = [
    (
        "owner@mangoappliances.com",
        "Priya",
        "Mango Appliances Demo",
        ["Company Secretary", "Accounts Manager"],
    ),
    (
        "cs@optireachsecretarial.com",
        "Rahul",
        "OptiReach Demo Pvt Ltd",
        ["Company Secretary"],
    ),
]


async def _company(db, name: str) -> Company | None:
    return await db.scalar(select(Company).where(Company.company_name == name))


async def _ensure_user(db, email: str, first_name: str, company: Company, roles: list[str], password: str) -> None:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            first_name=first_name,
            hashed_password=hash_password(password),
            default_company_id=company.id,
        )
        db.add(user)
        await db.flush()
        print(f"  created {email}")
    else:
        user.default_company_id = company.id
        print(f"  {email} already exists — roles refreshed")

    existing = set(
        (
            await db.execute(
                select(UserRole.role).where(
                    UserRole.user_id == user.id, UserRole.company_id == company.id
                )
            )
        )
        .scalars()
        .all()
    )
    for role in roles:
        if role not in existing:
            # Company-scoped, never global: a global row would follow this user
            # into every tenant they can reach.
            db.add(UserRole(user_id=user.id, role=role, company_id=company.id))
    await db.flush()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()

    async with async_session_factory() as db:
        for email, first_name, company_name, roles in DEMO_USERS:
            company = await _company(db, company_name)
            if company is None:
                print(f"! company '{company_name}' not found — run scripts.seed_showcase first")
                continue
            print(f"{company_name}:")
            await _ensure_user(db, email, first_name, company, roles, args.password)

            # secretarial_settings is RLS-protected, so the tenant context has to
            # be set before writing — the policy applies to INSERT too.
            await set_company_context(db, company.id)
            settings = await db.scalar(
                select(SecretarialSettings).where(SecretarialSettings.company_id == company.id)
            )
            if settings is None:
                settings = SecretarialSettings(company_id=company.id)
                db.add(settings)
            if email.startswith("cs@"):
                settings.profile = "practice"
                settings.practice_name = "OptiReach Secretarial LLP"
            else:
                settings.profile = "business"
            await db.flush()

        await db.commit()

    print(
        f"\nLogins (password: {args.password})\n"
        f"  owner@mangoappliances.com        — the appliance business, does its own secretarial work\n"
        f"  cs@optireachsecretarial.com      — the CS practice, works on clients\n\n"
        "Neither is a System Manager, so permission checks actually apply to them."
    )


if __name__ == "__main__":
    asyncio.run(main())
