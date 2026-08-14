"""Shared fixtures for Module 02 integration tests (PostgreSQL required).

Skipped unless TEST_DATABASE_URL is set; each test gets a fresh schema,
a seeded company (India COA) and an authenticated superuser client.
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

TEST_DB = os.environ.get("TEST_DATABASE_URL")

PW = "Passw0rd!xyz"


@pytest_asyncio.fixture()
async def ctx():
    """Schema + seeded company/admin; returns (client, company, headers)."""
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")

    from app.core.database import async_session_factory, engine
    from app.core.security import hash_password
    from app.main import app
    from app.models.base import Base
    from app.models.core import Currency, Role, User, UserRole

    async with engine.begin() as conn:
        # drop_all can't order DROPs across FK cycles (unnamed use_alter
        # constraints) — recreating the schema wholesale is cycle-proof
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS ltree"))
        # The statutory catalogue lives in its own schema (migration 0085) and
        # create_all only creates tables, never the schema that holds them.
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS statutory"))
        await conn.run_sync(Base.metadata.create_all)
        # the GL triggers live in migration 0002; create_all doesn't know them
        await conn.execute(text(
            """
            CREATE OR REPLACE FUNCTION fn_gl_entry_balance_check() RETURNS trigger AS $$
            DECLARE diff NUMERIC;
            BEGIN
              SELECT COALESCE(SUM(debit) - SUM(credit), 0) INTO diff
                FROM gl_entries
               WHERE voucher_type = NEW.voucher_type AND voucher_id = NEW.voucher_id;
              IF ABS(diff) > 0.005 THEN
                RAISE EXCEPTION 'GL voucher % is out of balance by %', NEW.voucher_no, diff;
              END IF;
              RETURN NULL;
            END $$ LANGUAGE plpgsql
            """
        ))
        await conn.execute(text(
            "CREATE CONSTRAINT TRIGGER trg_gl_entry_balance_check AFTER INSERT ON gl_entries "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fn_gl_entry_balance_check()"
        ))
        await conn.execute(text(
            """
            CREATE OR REPLACE FUNCTION fn_gl_entry_immutable() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'gl_entries is append-only: % not allowed', TG_OP;
            END $$ LANGUAGE plpgsql
            """
        ))
        await conn.execute(text(
            "CREATE TRIGGER trg_gl_entry_immutable BEFORE UPDATE OR DELETE ON gl_entries "
            "FOR EACH ROW EXECUTE FUNCTION fn_gl_entry_immutable()"
        ))

    async with async_session_factory() as db:
        db.add(Currency(code="INR", currency_name="Indian Rupee", symbol="₹"))
        db.add(Role(name="System Manager", is_system=True))
        admin = User(email="admin@test.io", first_name="Admin", hashed_password=hash_password(PW))
        db.add(admin)
        await db.flush()
        db.add(UserRole(user_id=admin.id, role="System Manager", company_id=None))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/login", json={"email": "admin@test.io", "password": PW})
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        resp = await client.post(
            "/api/v1/companies",
            json={"company_name": "Acme India", "abbr": "ACME", "default_currency": "INR",
                  "country_code": "IN"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        company = resp.json()

        # re-login so the JWT carries the new company context
        resp = await client.post(
            "/api/v1/auth/switch-company", json={"company_id": company["id"]}, headers=headers
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        yield client, company, headers
    await engine.dispose()


async def coa_account(client: AsyncClient, company: dict, headers: dict, name: str) -> dict:
    """Find an account by name in the company's chart of accounts."""
    resp = await client.get(f"/api/v1/companies/{company['id']}/chart-of-accounts", headers=headers)
    return next(a for a in resp.json() if a["account_name"] == name)
