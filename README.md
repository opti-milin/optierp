# OptiReach ERP

A clean re-architecture of ERPNext's business logic onto a modern stack — **FastAPI**
(async Python 3.12), **Vue 3** (Composition API + Pinia + Tailwind) and **PostgreSQL**
(UUID keys, ltree trees, row-level-security multi-tenancy). No Frappe dependency anywhere.

Migration source: [frappe/erpnext](https://github.com/frappe/erpnext) (`develop` branch),
per `docs/erpnext_migration_prompt.md`.

## Status

| Phase | Module | Status |
|---|---|---|
| 1 | **01 — Core / Setup** (Company + COA seeding, Users, RBAC, Currencies, UOM, Naming Series, Workflows, Audit, Auth) | ✅ done |
| 2 | **02 — Accounts** (GL + double-entry trigger, Journal/Payment Entries, Sales/Purchase Invoices, Tax Categories/Templates, Budgets, Payment & Bank Reconciliation, Period Closing, 7 financial reports, invoice PDFs) | ✅ done |
| 3 | **03–05 — Stock, Buying, Selling** (Items/Warehouses/Price Lists, append-only Stock Ledger + Bin with moving-average valuation, Stock Entries, Material Requests, RFQs + Supplier Quotations, Purchase Orders → Receipts → Invoices, Quotations → Sales Orders → Delivery Notes → Invoices, perpetual-inventory GL, credit-limit warnings, stock balance/ledger reports) | ✅ done |
| 4 | **06 — Manufacturing** (BOM → Work Order → Job Card, Production Plan, Subcontracting, QI gate, CTP/pegging soft USP) | ✅ done |
| 4 | 07–09 — CRM, HR*, Projects | ⏳ next |
| 5 | Assets (core landed), Quality/Support polish + SaaS layer | partial |
| USP | **Income Tax / Taxation** (entity ITR worksheet, rule engine, Form 16 handoff) | ✅ lean done |

\* Note: the `hr` module no longer exists in erpnext `develop` (moved to the separate
`frappe/hrms` app) — Module 08 will be migrated from HRMS sources.

## Quick start (Docker)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose v2).
First build downloads base images and can take several minutes.

```powershell
# From the repo root (PowerShell or bash)
docker compose up --build
```

Wait until the backend is healthy (compose prints `Application startup complete` / healthcheck passes), then open:

| Surface | URL |
|---|---|
| App | http://localhost:8080 |
| API docs | http://localhost:8000/docs |
| Mailhog (dev email) | http://localhost:8025 |

Logins — each lands in its **own company**, so the demo shows real tenant isolation:

| Account | Email | Password | Company |
|---|---|---|---|
| Showcase demo | `demo@optireach.in` | `Demo@12345` | OptiReach Demo Pvt Ltd |
| Administrator | `admin@example.com` | `ChangeMe!123` | Mango Appliances Demo |

(override with env vars `DEMO_EMAIL` / `DEMO_PASSWORD` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` before `up`.)

On backend start Compose runs migrations and then **auto-seeds the full showcase
dataset** into both companies — every module comes up populated, on any machine,
with no extra commands. It never wipes anything: each step is idempotent and is
skipped once it has succeeded, so restarts cost a few seconds (the first seed
takes ~2 minutes, since it posts real documents through the service layer).

Set `SEED_DEMO=false` before `up` for a bare install (masters + admin only).

**OCR (optional):** scanned invoice/PO → line items on Sales/Purchase forms. Set `OCR_API_KEY` in a repo-root `.env` (OpenAI-compatible vision) and restart the backend. See [`docs/OCR.md`](docs/OCR.md).

**Common issues**
- Port `5432` already in use (Windows PostgreSQL): stop the host service, or change the host mapping in `docker-compose.yml`.
- Frontend shows API errors for a few seconds: wait for backend health — frontend now waits on the API healthcheck.
- Stale DB after a bad migration: `docker compose down -v` then `docker compose up --build` (destructive — deletes the `pgdata` volume).

## Local development

**Backend**

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -e ".[dev]"
copy .env.example .env
docker compose up postgres redis -d
alembic upgrade head
python -m scripts.seed --admin-email admin@example.com --admin-password ChangeMe!123
uvicorn app.main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to :8000
```

**Demo data**

`scripts/seed_showcase.py` is the entry point — one idempotent command that
builds the whole showcase and the `demo@optireach.in` login. Compose runs it
automatically on backend start (see **Quick start**), so you only run it by hand
when seeding a database outside Compose:

```powershell
docker compose exec backend python -m scripts.seed_showcase
```

It seeds **two tenants** — `Mango Appliances Demo` (the admin's) and
`OptiReach Demo Pvt Ltd` (the demo user's) — each with its own parties, stock,
documents and tax year, and different figures in each. Pass `--single-tenant`
for one shared company instead.

It orchestrates the per-module seeders in dependency order and records each
completed step in `system_settings` (per tenant, as `step@ABBR`), so re-runs
skip finished work:

| Step | Seeds |
|---|---|
| `bootstrap` | Masters, roles, permissions, admin user |
| `statutory` | Finance-Act statutory packs |
| `core_demo` | Company + COA, parties, invoices, payments, stock, orders |
| `manufacturing` | BOMs, work orders, CTP/reverse-schedule kit |
| `assets` | Asset categories, assets, depreciation, maintenance |
| `cm_planning` | Contribution-margin plans, cost drivers, what-if scenarios |
| `compliance` | GST + TDS return source documents |
| `income_tax` | MSME year-end: computation, challans, credits |
| `extras` | RFQs, quality, job cards, subcontracting, service credits, reorder levels, share capital, subscriptions, payment requests |
| `demo_user` | The showcase login, with every module role |

Useful flags: `--list` (show steps), `--only extras` (run specific steps),
`--force` (re-run everything), `--strict` (exit non-zero on failure — for CI).
A step that fails is reported and left unstamped so the next run retries it; the
script still exits 0 so a demo-data problem never stops the API from starting.

`scripts/seed_demo.py` (the `core_demo` step) can also be run on its own. It
seeds through the service layer, so GL postings, naming series and statuses are
real: company + COA, users, GST tax categories/templates, customers/suppliers,
sales & purchase invoices across draft/unpaid/overdue/partly-paid/paid/cancelled
states, payments (allocated, on-account for the Reconciliation page, bank-cleared
and not), journal entries, budgets, plus a prior fiscal year so reports show
opening balances and deep AR/AP aging buckets.

Against the Docker Compose Postgres (password `milin` from `infra/init-db.sql`):

```powershell
docker compose exec backend python -m scripts.seed_demo `
  --database-url "postgresql+asyncpg://erp_owner:milin@postgres:5432/erp" `
  --admin-email admin@example.com --admin-password "ChangeMe!123"
```

Or from a local venv (match `.env` passwords to the Postgres you actually started —
Compose uses `milin`; `.env.example` uses `erp_owner_dev_pw`):

```powershell
cd backend
python -m scripts.seed_demo --reset-schema --database-url "postgresql+asyncpg://erp_owner:erp_owner_dev_pw@localhost:5432/erp" --admin-email admin@example.com --admin-password "ChangeMe!123"
```

- Connect as **erp_owner** (it also runs migrations when `--reset-schema` is given).
- `--reset-schema` is destructive: it drops schema `public` on the target DB,
  re-runs `alembic upgrade head`, then seeds. Omit it to seed additively into an
  already-migrated empty database; the script aborts if the demo company exists.
  After a partial run (network drop), recover with `--reset-schema`.
- Logins afterwards: the admin you passed, plus `manager@`/`books@`/`sales@demo-erp.com`
  (password `Demo!Pass123`) — change these on any shared instance.

*Seeding a PostgreSQL on another machine* (target needs PostgreSQL 13+ with the
contrib package, for `ltree`):

```powershell
# 1. once, on/against the target server, as a superuser (psql only — the file uses \connect)
#    !! edit the role passwords in infra/init-db.sql first: the defaults are public in this repo
psql "host=<REMOTE_IP> port=5432 user=postgres dbname=postgres" -v ON_ERROR_STOP=1 -f infra/init-db.sql

# 2. from this machine — one command migrates + seeds over the network
cd backend
python -m scripts.seed_demo --reset-schema --database-url "postgresql+asyncpg://erp_owner:<pw>@<REMOTE_IP>:5432/erp" --admin-email admin@example.com --admin-password "..."
```

Remote checklist: `init-db.sql` must run **before** migrations (the `erp_app`
grants are skipped silently if the role doesn't exist); the server must allow the
connection (`listen_addresses` in `postgresql.conf`, a `host ... scram-sha-256`
rule for your IP in `pg_hba.conf`, firewall port 5432); URL-encode special
characters in DSN passwords. The seeder makes hundreds of round-trips — on a slow
link, run it on the DB host or through an SSH tunnel instead.

*Upgrading an existing demo DB to Phase 3* (non-destructive — keeps every
invoice/payment already there and only adds items, warehouses, stock and the
order documents):

```powershell
cd backend
python -m alembic upgrade head        # uses MIGRATIONS_DATABASE_URL from .env
python -m scripts.seed_demo --phase3-topup --database-url "postgresql+asyncpg://erp_owner:<pw>@<HOST>:5432/erp" --admin-email <the admin email used when seeding>
```

**Tests**

```bash
cd backend
pytest tests/unit                                          # no DB needed
TEST_DATABASE_URL=postgresql+asyncpg://erp_owner:erp_owner_dev_pw@localhost:5432/erp_test \
  pytest tests/integration                                 # needs the compose postgres
```

## Architecture

- `backend/app/core/` — config, async DB + RLS tenant context, JWT auth, RBAC engine,
  naming-series engine, workflow state machine, notifications, scheduler, websocket
- `backend/app/{models,schemas,services,api}/` — the 4-layer rule: all business logic
  lives in services; routers stay thin; models are pure ORM
- `backend/data/coa/` — Chart of Accounts templates copied verbatim from ERPNext's
  verified charts (standard, India, UAE)
- `backend/print_formats/` — Jinja2 invoice templates rendered to PDF via WeasyPrint
  (`GET /sales-invoices/{id}/pdf`, `GET /purchase-invoices/{id}/pdf`)
- `backend/migrations/` — Alembic; one revision per module
- `frontend/src/` — Pinia stores, schema-driven `FormBuilder`, generic
  `useList`/`useDocument` composables, lazy-loaded module routes
- `frontend/public/brand/config.json` — **all** branding (name, colors, logo); zero
  hardcoded product strings in code
- `backend/app/registry/` — the **metadata engine** ("the machine"): one descriptor renders a
  master DocType's table, list, form, permissions and naming with no per-doctype code. See
  [`ENGINE_GUIDE.md`](docs/ENGINE_GUIDE.md) to add a master, and [`metadata_engine_plan.md`](docs/metadata_engine_plan.md)
  for the design. Engine-served masters live at `/m/<slug>`.
- **Child tables & the pricing engine** — line-item grids (Product Bundle, Blanket Order,
  Promotional Scheme) and the selling pricing pipeline (Pricing Rule, Coupon Code, Shipping Rule,
  Blanket Order, Promotional Scheme). Plain-language guide: [`PRICING_AND_CHILD_TABLES.md`](docs/PRICING_AND_CHILD_TABLES.md).

### Multi-tenancy

Company = tenant. The API connects as the non-owner `erp_app` role; every request sets
the transaction-local GUC `app.company_id` from the JWT, which the PostgreSQL
`company_isolation` RLS policies enforce. Services also filter by `company_id`
explicitly (defense in depth). Migrations run as `erp_owner`.

### ERPNext semantics preserved

- `docstatus` 0/1/2 (Draft/Submitted/Cancelled) on every table
- Naming series patterns (`SINV-.YYYY.-`) with atomic per-company counters
- DocType-style role permission matrix incl. `if_owner`
- Append-only audit log capturing user, company, before/after JSON and client IP
