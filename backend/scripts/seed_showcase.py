"""One-command showcase seed: two demo tenants plus the logins to present them.

This is the *orchestrator*. It does not duplicate any existing seeder — it runs
them in dependency order, once per tenant, remembers which ones already
succeeded, and finally wires up the demo login.

    python -m scripts.seed_showcase

Tenants
-------
Two companies are seeded so the demo shows real multi-tenancy rather than one
shared dataset:

* **Mango Appliances Demo** (MAD) — the administrator's company.
* **OptiReach Demo Pvt Ltd** (ODPL) — owned by the demo login.

Each gets its own parties, stock, documents, manufacturing, assets and tax year.
Row-level security keeps them apart, so signing in as the demo user shows only
ODPL data — which is the point of showing it.

Design notes
------------
* **Idempotent and resumable.** Each step is stamped in ``system_settings`` under
  the key ``demo_showcase_seed`` once it succeeds (per tenant, as ``step@ABBR``),
  so a re-run — every container start, in the Docker workflow — is a cheap no-op.
  Adding a step later means only that step runs on the next boot.
* **Never blocks startup.** A failing step is reported and left un-stamped so the
  next run retries it; the script still exits 0 so the API comes up regardless.
  Pass ``--strict`` to exit non-zero instead (useful in CI).
* **Sub-seeders run as subprocesses.** Several of them parse ``argv`` at import
  time, so importing them in-process would fight over arguments. Running them as
  ``python -m scripts.<name>`` keeps each one's documented CLI intact, and each
  is told explicitly which database and which company to seed.
* **Seeds as the owner role.** Company creation and cross-company masters are
  easiest under ``erp_owner`` (RLS does not apply to the table owner), so the
  DSN resolves to ``MIGRATIONS_DATABASE_URL`` before ``DATABASE_URL``.

Bump ``SEED_VERSION`` when a step's *content* changes and every environment
should pick the change up on its next start.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import NamedTuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

SEED_VERSION = 2  # v2: two tenants instead of one shared company
STAMP_KEY = "demo_showcase_seed"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--database-url",
        default=(
            os.environ.get("SEED_DATABASE_URL")
            or os.environ.get("MIGRATIONS_DATABASE_URL")
            or os.environ.get("DATABASE_URL")
        ),
        help="postgresql+asyncpg DSN to seed. Defaults to SEED_DATABASE_URL, "
        "then MIGRATIONS_DATABASE_URL (owner role), then DATABASE_URL.",
    )
    p.add_argument("--admin-email", default=os.environ.get("ADMIN_EMAIL", "admin@example.com"))
    p.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD", "ChangeMe!123"))
    p.add_argument("--demo-email", default=os.environ.get("DEMO_EMAIL", "demo@optireach.in"))
    p.add_argument("--demo-password", default=os.environ.get("DEMO_PASSWORD", "Demo@12345"))
    p.add_argument("--company-name", default=os.environ.get("DEMO_COMPANY", "Mango Appliances Demo"),
                   help="Name of the administrator's company (tenant 1).")
    p.add_argument("--company-abbr", default=os.environ.get("DEMO_COMPANY_ABBR", "MAD"))
    p.add_argument("--demo-company", default=os.environ.get("DEMO_TENANT", "OptiReach Demo Pvt Ltd"),
                   help="Name of the demo user's own company (tenant 2).")
    p.add_argument("--demo-abbr", default=os.environ.get("DEMO_TENANT_ABBR", "ODPL"))
    p.add_argument(
        "--single-tenant",
        action="store_true",
        help="Seed only the administrator's company and point the demo login at "
        "it (the pre-v2 behaviour).",
    )
    p.add_argument(
        "--only",
        help="Run only these comma-separated step names, for every tenant "
        "(ignores stamps). See --list for the available names.",
    )
    p.add_argument("--list", action="store_true", help="Print the step list and exit.")
    p.add_argument(
        "--force",
        action="store_true",
        help="Ignore stamps and re-run every step. Steps are individually "
        "idempotent, so this is safe — just slower.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any step fails (default: report and continue, so "
        "a failed demo seed never stops the API from starting).",
    )
    return p.parse_args()


ARGS = _parse_args()

# Which options each sub-seeder actually accepts — they do not agree (one
# *requires* --database-url, one takes none at all), so each is invoked with
# exactly what its own CLI declares. Company targeting that has no flag is
# passed through the DEMO_COMPANY environment variable instead.
DB_URL_ARG = "db-url"
COMPANY_ARG = "company"
ABBR_ARG = "abbr"
RNG_ARG = "rng"
VARIANT_ARG = "variant"

# Steps that must run on every start rather than once. Their seeders are
# idempotent top-ups, and skipping them silently withholds anything a new module
# added since the last run — a new role permission, say, which then looks like a
# 403 bug on an upgraded database rather than a missing seed.
ALWAYS_RUN = {"bootstrap", "statutory"}

# Run once for the whole instance, before any tenant exists.
GLOBAL_STEPS: list[tuple[str, str, list[str], set[str]]] = [
    ("bootstrap", "Masters, roles, permissions, admin user", ["scripts.seed"], set()),
    ("statutory", "Finance-Act statutory packs (income tax)", ["scripts.load_statutory"], set()),
]

# Run once per tenant, in this order.
TENANT_STEPS: list[tuple[str, str, list[str], set[str]]] = [
    ("core_demo", "Company, COA, parties, invoices, payments, stock, orders",
     ["scripts.seed_demo"], {DB_URL_ARG, COMPANY_ARG, ABBR_ARG, RNG_ARG, VARIANT_ARG}),
    ("manufacturing", "BOMs, work orders, CTP/reverse-schedule kit",
     ["scripts.seed_demo", "--manufacturing-topup"], {DB_URL_ARG, COMPANY_ARG}),
    ("assets", "Asset categories, assets, depreciation, maintenance",
     ["scripts.seed_assets_demo"], {DB_URL_ARG, COMPANY_ARG}),
    ("cm_planning", "Contribution-margin plans, cost drivers, what-if scenarios",
     ["scripts.seed_cm_planning_demo"], {DB_URL_ARG, COMPANY_ARG}),
    ("compliance", "GST returns + TDS returns source documents",
     ["scripts.seed_compliance_returns_demo"], set()),
    ("income_tax", "MSME income-tax year-end: computation, challans, credits",
     ["scripts.seed_income_tax_msme_demo"], {DB_URL_ARG, COMPANY_ARG}),
    ("extras", "RFQ, quality, job cards, subcontracting, subscriptions, shares",
     ["scripts.seed_showcase_extras"], {DB_URL_ARG, COMPANY_ARG}),
]

if ARGS.list:
    print(f"{'SCOPE':<9} {'STEP':<16} DESCRIPTION")
    for name, desc, _argv, _flags in GLOBAL_STEPS:
        print(f"{'global':<9} {name:<16} {desc}")
    for name, desc, _argv, _flags in TENANT_STEPS:
        print(f"{'tenant':<9} {name:<16} {desc}")
    print(f"{'final':<9} {'demo_user':<16} The showcase login, owning its own tenant")
    sys.exit(0)

if not ARGS.database_url:
    sys.exit("No database URL. Pass --database-url or set DATABASE_URL.")

# Sub-seeders read settings from the environment at import time — pin them here
# so every step (and this process) targets the same database.
os.environ["DATABASE_URL"] = ARGS.database_url
os.environ.setdefault("MIGRATIONS_DATABASE_URL", ARGS.database_url)
os.environ.setdefault("SECRET_KEY", "seed-showcase-secret-not-used-0123456789")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("REFRESH_COOKIE_SECURE", "false")
os.environ["ADMIN_EMAIL"] = ARGS.admin_email
os.environ["ADMIN_PASSWORD"] = ARGS.admin_password
# Sub-seeders print rupee signs and arrows; a default Windows console is cp1252
# and would kill the step with UnicodeEncodeError mid-seed.
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from sqlalchemy import select  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.core.security import CurrentUser  # noqa: E402
from app.models.core import Company, SystemSetting, User, UserRole  # noqa: E402
from app.schemas.core import UserCreate  # noqa: E402
from app.services.user import create_user  # noqa: E402


class Tenant(NamedTuple):
    name: str
    abbr: str
    rng_seed: int  # seed_demo is deterministic; differ it so the tenants do too
    variant: int  # which set of party/item names seed_demo should use


# Same document mix in both, but different customers, suppliers, products,
# quantities and dates — two companies holding the same names and figures read
# as one dataset seeded twice, which is exactly what a demo must not look like.
TENANTS: list[Tenant] = [Tenant(ARGS.company_name, ARGS.company_abbr, 42, 0)]
if not ARGS.single_tenant:
    TENANTS.append(Tenant(ARGS.demo_company, ARGS.demo_abbr, 2026, 1))

# The company the demo login owns: its own tenant, or the only one when
# --single-tenant is given.
DEMO_TENANT = TENANTS[-1]

# The showcase login sees every module, so it carries every module's manager
# role. System Manager alone would authorise it, but the explicit roles make
# the Users screen read the way a real deployment would.
DEMO_ROLES = [
    "System Manager",
    "Accounts Manager",
    "Stock Manager",
    "Purchase Manager",
    "Sales Manager",
    "Manufacturing Manager",
    "Quality Manager",
]


async def _load_stamp() -> dict:
    async with async_session_factory() as db:
        row = await db.scalar(
            select(SystemSetting).where(
                SystemSetting.key == STAMP_KEY, SystemSetting.company_id.is_(None)
            )
        )
        if row is None or not isinstance(row.value, dict):
            return {}
        if row.value.get("version") != SEED_VERSION:
            return {}  # content changed — treat every step as unrun
        return row.value


async def _stamp_step(name: str) -> None:
    async with async_session_factory() as db:
        row = await db.scalar(
            select(SystemSetting).where(
                SystemSetting.key == STAMP_KEY, SystemSetting.company_id.is_(None)
            )
        )
        if row is None:
            row = SystemSetting(id=uuid.uuid4(), key=STAMP_KEY, company_id=None, value={})
            db.add(row)
        current = row.value if isinstance(row.value, dict) else {}
        done = set(current.get("done", []))
        if current.get("version") != SEED_VERSION:
            done = set()
        done.add(name)
        # JSONB columns need a new object for SQLAlchemy to see the change
        row.value = {"version": SEED_VERSION, "done": sorted(done)}
        await db.commit()


async def _company_exists(name: str) -> bool:
    async with async_session_factory() as db:
        return bool(await db.scalar(select(Company).where(Company.company_name == name)))


def _run_step(argv: list[str], flags: set[str], tenant: Tenant | None) -> None:
    """Run a sub-seeder as `python -m <module> …`, streaming its output."""
    env = os.environ.copy()
    if tenant is not None:
        # Seeders with no --company-name flag of their own read this instead.
        env["DEMO_COMPANY"] = tenant.name
        if COMPANY_ARG in flags:
            argv = [*argv, "--company-name", tenant.name]
        if ABBR_ARG in flags:
            argv = [*argv, "--abbr", tenant.abbr]
        if RNG_ARG in flags:
            argv = [*argv, "--seed", str(tenant.rng_seed)]
        if VARIANT_ARG in flags:
            argv = [*argv, "--variant", str(tenant.variant)]
    if DB_URL_ARG in flags:
        argv = [*argv, "--database-url", ARGS.database_url]
    subprocess.run(  # noqa: S603 — fixed module list, no shell
        [sys.executable, "-m", *argv],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
    )


async def seed_demo_user() -> None:
    """Create the showcase login, owning its own tenant, and fix company owners.

    ``seed_demo`` points the administrator at whichever company it just built,
    so after seeding tenant 2 the admin would land in the demo user's company.
    This puts both users back where they belong.
    """
    async with async_session_factory() as db:
        demo_company = await db.scalar(
            select(Company).where(Company.company_name == DEMO_TENANT.name)
        )
        if demo_company is None:  # fall back to whatever the demo produced
            demo_company = await db.scalar(select(Company).order_by(Company.creation.asc()))
        if demo_company is None:
            raise RuntimeError("No company exists — the core_demo step must run first.")

        admin = await db.scalar(select(User).where(User.email == ARGS.admin_email.lower()))
        actor = (
            CurrentUser(
                {
                    "sub": str(admin.id),
                    "email": admin.email,
                    "company_id": str(demo_company.id),
                    "roles": ["System Manager"],
                }
            )
            if admin
            else None
        )

        email = ARGS.demo_email.lower()
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            user = await create_user(
                db,
                UserCreate(
                    email=email,
                    first_name="Demo",
                    last_name="User",
                    password=ARGS.demo_password,
                    default_company_id=demo_company.id,
                    roles=DEMO_ROLES,
                ),
                actor,
            )
            print(f"Demo user created: {email} / {ARGS.demo_password}")
        else:
            print(f"Demo user refreshed: {email}")

        # Land the demo login in its own tenant, with every role scoped there.
        user.default_company_id = demo_company.id
        existing = set(
            (
                await db.execute(
                    select(UserRole.role).where(
                        UserRole.user_id == user.id, UserRole.company_id == demo_company.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for role in DEMO_ROLES:
            if role not in existing:
                db.add(UserRole(user_id=user.id, role=role, company_id=demo_company.id))

        # Put the administrator back in tenant 1 (seed_demo moved it).
        if admin is not None and len(TENANTS) > 1:
            admin_company = await db.scalar(
                select(Company).where(Company.company_name == TENANTS[0].name)
            )
            if admin_company is not None:
                admin.default_company_id = admin_company.id
        await db.commit()


async def main() -> None:
    stamp = {} if ARGS.force else await _load_stamp()
    done: set[str] = set(stamp.get("done", []))
    only = {s.strip() for s in ARGS.only.split(",")} if ARGS.only else None

    print(f"Showcase seed v{SEED_VERSION} -> {ARGS.database_url.split('@')[-1]}")
    print(f"Tenants: {', '.join(f'{t.name} ({t.abbr})' for t in TENANTS)}")
    failed: list[str] = []

    async def run(
        step: tuple[str, str, list[str], set[str]], tenant: Tenant | None
    ) -> None:
        name, desc, argv, flags = step
        label = name if tenant is None else f"{name}@{tenant.abbr}"
        if only is not None and name not in only:
            return
        if only is None and label in done and name not in ALWAYS_RUN:
            print(f"  [ok] {label:<22} already seeded - skipped")
            return

        # seed_demo refuses to build a company that already exists; once it does,
        # the core demo for that tenant is done.
        if name == "core_demo" and tenant is not None and await _company_exists(tenant.name):
            print(f"  [ok] {label:<22} company exists - skipped")
            await _stamp_step(label)
            return

        print(f"  -> {label:<22} {desc}")
        try:
            _run_step(argv, flags, tenant)
        except Exception as exc:  # noqa: BLE001 — one bad step must not kill the rest
            print(f"  [!!] {label:<22} FAILED: {exc}")
            failed.append(label)
            return
        await _stamp_step(label)
        print(f"  [ok] {label:<22} done")

    for step in GLOBAL_STEPS:
        await run(step, None)
    for tenant in TENANTS:
        print(f"\n--- {tenant.name} ({tenant.abbr}) ---")
        for step in TENANT_STEPS:
            await run(step, tenant)

    print()
    if only is None and "demo_user" in done:
        print(f"  [ok] {'demo_user':<22} already seeded - skipped")
    elif only is None or "demo_user" in only:
        print(f"  -> {'demo_user':<22} The showcase login, owning its own tenant")
        try:
            await seed_demo_user()
            await _stamp_step("demo_user")
            print(f"  [ok] {'demo_user':<22} done")
        except Exception as exc:  # noqa: BLE001
            print(f"  [!!] {'demo_user':<22} FAILED: {exc}")
            failed.append("demo_user")

    if failed:
        print(f"\nSteps needing a retry (they will re-run next start): {', '.join(failed)}")
        if ARGS.strict:
            sys.exit(1)
    else:
        print(f"\nShowcase ready. Log in as {ARGS.demo_email} / {ARGS.demo_password}")
        print(f"  demo login lands in: {DEMO_TENANT.name}")
        print(f"  admin ({ARGS.admin_email}) lands in: {TENANTS[0].name}")


if __name__ == "__main__":
    asyncio.run(main())
