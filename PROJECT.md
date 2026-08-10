# PROJECT.md — OptiReach ERP

> Handoff / context file for AI coding agents. Keep it current: when you finish a
> chunk of work, update **Current Transition State** and tick **Remaining Todo List**.
> Companion docs live in [`docs/`](docs/) — this file is the map, they are the detail.
> Cursor agent plans are archived under [`docs/plans/`](docs/plans/README.md).
>
> **Agent ship rules:** (1) On plan confirm, always save the plan into `docs/plans/`
> and index it in `docs/plans/README.md`. (2) After implementation, restart Docker
> containers so changes are live. (3) Always give the user numbered manual
> verification steps (UI/API path + expected result), not only automated tests.

---

## 1. Project Overview & Architecture

**OptiReach ERP is a clean re-architecture of ERPNext's business logic onto a modern async stack (FastAPI + Vue 3 + PostgreSQL), with zero Frappe dependency, targeting an India-ready multi-tenant SaaS.**

High-level flow:

```
Vue 3 SPA  ──HTTP/JSON──►  FastAPI (app/api routers, thin)
(Pinia stores,             │
 axios client,             ▼
 schema-driven forms)   Services layer  ── all business logic lives here
                           │  (GL posting, taxes & totals, stock moves,
                           │   pricing, naming series, workflow, audit)
                           ▼
                        SQLAlchemy 2.0 async ORM (models/, pure data)
                           │
                           ▼
                        PostgreSQL 16  ── UUID PKs, ltree trees,
                                          Row-Level-Security per company,
                                          double-entry + stock-ledger triggers
```

- **Frontend** talks only to `/api/v1/*`. In dev, Vite proxies `/api` → `localhost:8000`.
- **Backend** is strictly 4-layered: `models → schemas → services → api`. Routers are thin; **all logic is in `services/`**.
- **Multi-tenancy**: Company = tenant. The app connects as the non-owner Postgres role `erp_app`; each request sets a transaction-local GUC `app.company_id` from the JWT, and RLS policies (`company_isolation`) enforce isolation. Services also filter by `company_id` (defense in depth). Migrations/seeds run as the owner role `erp_owner`.
- **The "machine" (metadata engine)**: simple masters are rendered from a single **descriptor** in [`backend/app/registry/descriptors.py`](backend/app/registry/descriptors.py) (currently ~36) — no per-doctype code. Engine-served masters live at `/m/<slug>`. Only transaction/heavy-logic doctypes get bespoke services. See [`docs/ENGINE_GUIDE.md`](docs/ENGINE_GUIDE.md) and [`docs/metadata_engine_plan.md`](docs/metadata_engine_plan.md).

---

## 2. Current Transition State

- **Branch:** `develop`
- **Latest migration head:** `0092_drop_legacy_itr` (after `0091_tax_filings` / `0090` / …).
- **Exact spot:** **Manufacturing Phases 0–9 complete** (incl. live delivery-date chain + outbound transit days). True finite-capacity APS remains out of scope.
- **ITR enterprise rearchitecture** — [docs/plans/itr_enterprise_rearchitecture.plan.md](docs/plans/itr_enterprise_rearchitecture.plan.md) is the **master spec**. Architecture: [docs/TAXATION_ARCHITECTURE.md](docs/TAXATION_ARCHITECTURE.md).
  - **Phases 1–8 done** (`0085`–`0091`): statutory catalogue, registrations, kernel, computations/runs, challans/credits/GL, corporate depth, interest/calendar, ITR-6 filings.
  - **Phase 9 done**: Tax Workspace (`/tax/workspace`) with Heads/Adjustments/Depreciation/Set-off/MAT/Credits/Challans/Result/Runs·Audit/Form tabs; legacy `IncomeTaxView` removed.
  - **Phase 10 done** (`0092`): legacy engines/routers/descriptors/tables deleted; docs rewritten as `TAXATION_ARCHITECTURE.md`.
- **Contribution Margin** — GL actuals report (`0079`) + **pre-sales CM Planning** + **Cost Driver Framework** (`0083`) — [docs/plans/cm_cost_driver_framework.plan.md](docs/plans/cm_cost_driver_framework.plan.md).

---

## 3. Remaining Todo List (priority order)

**Phase 4 — Operations (in progress)** — detail in [docs/MANUFACTURING_GAP_AND_PLAN.md](docs/MANUFACTURING_GAP_AND_PLAN.md)
- [x] Manufacturing Phase 0: valuation/GL, WIP UI, serial/batch Finish, Item mfg fields (`0069`), MR type Manufacture, Repack UI.
- [x] Manufacturing Phase 1: multi-level/phantom BOM, scrap, Finish alternate, BOM Explorer (`0070`).
- [x] Manufacturing Phase 2: Job Card (+ time logs); Operation/Routing/Workstation; Material Consumption; soft capacity (`0072`).
- [x] Manufacturing Phase 3: Production Plan (SO demand → WOs + Manufacture MRs) (`0073`).
- [x] Manufacturing Phase 4: Subcontracting — Subcontract Job + Send/Receipt SE (`0074`).
- [x] Manufacturing Phase 5: Quality Inspection gate on Finish / Subcontract Receive (`0075`).
- [x] Manufacturing Phase 6: WO Summary / Production Analytics + Manufacturing module flag.
- [x] Manufacturing Phase 7: CTP + reverse + pegging + demand forecast + what-if CTP + soft capacity board + FG-GEARBOX demo seed — [docs/MANUFACTURING_GAP_AND_PLAN.md](docs/MANUFACTURING_GAP_AND_PLAN.md). True APS/finite capacity still out of scope.
- [x] Manufacturing Phase 8: Planning Dashboard + SO/Quotation fulfillment check.
- [x] Manufacturing Phase 9: Live delivery-date chain (supply-aware CTP from open PO/MR/WO; SO stage timeline; suggest-only delivery date) — [docs/MANUFACTURING_GAP_AND_PLAN.md](docs/MANUFACTURING_GAP_AND_PLAN.md).
- [ ] CRM: Lead, Opportunity, Campaign, Contact/Address; Lead→Opportunity→Quotation hand-off (hook); pipeline report. *(Masters via descriptors.)*
- [ ] HR & Payroll: Employee, Leave (append-only Leave Ledger), Attendance, Salary Structure/Slip, Payroll Entry. **Source is `frappe/hrms`, not erpnext (`hr` moved out of `develop`).** Wire Salary Slips into taxation facts adapters when Individual-track depth lands.
- [ ] Projects: Project, Task (dependency/topological-sort validation), Timesheet, Gantt endpoint.

**Phase 5 — Supporting modules + SaaS layer**
- [ ] Quality Management: Quality Inspection (block Delivery Note on rejection), Goal/Procedure.
- [ ] Support: Issue, SLA timer + breach scheduler, Warranty Claim.
- [ ] Assets: parity polish (core Assets module already landed — depreciation schedules, disposal GL).
- [ ] SaaS: tenant onboarding wizard, subscription/billing hooks, feature-flag middleware, API-key management + rate limiting.

**Product USPs (see [docs/USP_AND_FUTURE_SCOPE.md](docs/USP_AND_FUTURE_SCOPE.md))**
- [ ] Marketing & Promotions workspace (reuse pricing engine).
- [ ] Customer order-tracking dashboard (read-only customer role + timeline over the doc-status chain).
- [ ] One-page reconciliation (sales · purchase · bank) with auto-match service.
- [ ] MCA (India company-law) compliance calendar + form pre-fill.
- [x] **Income Tax (entity ITR)** — enterprise rearchitecture Phases 1–10 complete. See [docs/TAXATION_ARCHITECTURE.md](docs/TAXATION_ARCHITECTURE.md). Follow-ons: Individual-track depth, live DSC/HTTPS e-file adapter.
- [ ] POS + loyalty (till + points ledger hooked into pricing engine).

**Technical debt / cross-cutting**
- [ ] Resolve `MANUAL_REVIEW:` stubs (PDF engine, email provider, payment gateway, salary formula sandbox — see migration prompt §7).
- [ ] Backfill unit + integration tests for newer modules (compliance, manufacturing).

---

## 4. Current Tech Stack & Versions

**Backend** (`backend/pyproject.toml`, `requires-python >=3.12`)
| Component | Version (floor) |
|---|---|
| FastAPI | `>=0.115` |
| Uvicorn (standard) | `>=0.32` |
| SQLAlchemy (asyncio) | `>=2.0.36` |
| asyncpg | `>=0.30` |
| Alembic | `>=1.14` |
| Pydantic / pydantic-settings | `>=2.9` / `>=2.6` |
| PyJWT / bcrypt | `>=2.10` / `>=4.2` |
| structlog | `>=24.4` |
| Jinja2 | `>=3.1` |
| APScheduler | `>=3.10` |
| redis | `>=5.2` |
| aiosmtplib | `>=3.0` |
| WeasyPrint (PDF) | `>=63` |
| **dev:** pytest, pytest-asyncio, httpx, ruff | `>=8.3`, `>=0.24`, `>=0.28`, `>=0.8` |

**Frontend** (`frontend/package.json`)
| Component | Version |
|---|---|
| Vue | `^3.5.13` (Composition API only) |
| Vue Router | `^4.5.0` |
| Pinia | `^2.3.0` |
| axios | `^1.7.9` |
| Vite | `^6.0.7` |
| TypeScript | `~5.6.3` |
| Tailwind CSS | `^3.4.17` |
| vue-tsc | `^2.2.0` |

**Infra** (`docker-compose.yml`)
- PostgreSQL **16-alpine** (needs `ltree` / contrib), Redis **7-alpine**, Mailhog (dev SMTP catcher).
- **e2e** ([e2e/package.json](e2e/package.json)): Playwright `^1.49.0` (isolated browser harness, not part of the app build).

---

## 5. Directory Map & File Architecture

```
optierp-mig/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI factory: CORS, trace-id middleware, error envelope, lifespan (scheduler + ws)
│   │   ├── core/                  # config, database (async + RLS tenant ctx), security(JWT), permissions(RBAC),
│   │   │                          #   naming, workflow, notifications, scheduler, websocket, pdf, gst_states, num2words
│   │   ├── models/                # SQLAlchemy ORM (pure data). base.py + accounts/, stock, buying, selling,
│   │   │                          #   manufacturing, assets, core, types
│   │   ├── schemas/               # Pydantic Create/Update/Response/ListItem (mirrors models; accounts/ subpkg)
│   │   ├── services/              # ★ ALL business logic — one file per concern (gl, taxes_and_totals, sales_invoice,
│   │   │                          #   stock_ledger, pricing, bom, work_order, gst_returns, …) + financial_reports/
│   │   ├── api/v1/                # Thin routers, grouped: accounts/ stock/ buying/ selling/ manufacturing/
│   │   │                          #   compliance/ assets/ core/ + auth.py, registry.py, router.py (master)
│   │   ├── registry/              # The "machine": base.py (engine), descriptors.py (recipe cards, ~36 masters)
│   │   └── jobs/                  # Scheduled jobs: assets.py (depreciation), subscription.py
│   ├── migrations/versions/       # Alembic, one revision per feature — HEAD = 0067_itr_ay_partial_unique
│   ├── data/coa/                  # Chart-of-Accounts templates (standard, India, UAE), verbatim from ERPNext
│   ├── print_formats/             # Jinja2 → PDF (WeasyPrint) invoice templates
│   ├── scripts/                   # seed.py (bootstrap), seed_demo.py (full demo dataset)
│   ├── tests/                     # unit/ (no DB) + integration/ (needs compose postgres)
│   ├── pyproject.toml, alembic.ini, Dockerfile, .env.example
│
├── frontend/
│   └── src/
│       ├── api/                   # typed axios client + interceptors
│       ├── stores/                # Pinia: auth, core, accounts, stock, printSettings
│       ├── composables/           # useDocument, useList, usePermissions, useNamingSeries, useCompanyCurrency
│       ├── components/shared/     # generic DataTable, FormBuilder, StatusBadge, etc.
│       ├── views/                 # per module: accounts/ stock/ buying/ selling(trade)/ manufacturing/
│       │                          #   compliance/ assets/ core/ auth/ dashboard/ generic/ + ModuleWorkspace.vue
│       ├── layouts/, router/index.ts (lazy routes), types/, utils/, config/
│   └── public/brand/config.json   # ★ ALL branding (name, colors, logo) — no hardcoded product strings in code
│
├── e2e/                           # Playwright harness (capture/inspect running UI) — isolated from app build
├── docs/                          # migration prompt, engine plan, gap/parity reports, accounting guides, USPs
├── infra/init-db.sql              # creates erp_owner / erp_app roles + ltree; run BEFORE migrations
├── docker-compose.yml (+ .override.yml)
└── README.md
```

**Where things live (save context — go straight here):**
- New business rule → `backend/app/services/<concern>.py`. New endpoint → `backend/app/api/v1/<module>/`.
- New simple master (no logic) → add a descriptor in `backend/app/registry/descriptors.py` + thin model + migration + permission rows. **Do not** hand-write schema/router/store/views for it.
- New table → model in `backend/app/models/`, then a new Alembic revision in `backend/migrations/versions/`.
- Frontend list/form for engine masters is automatic at `/m/<slug>`; bespoke screens go in `frontend/src/views/<module>/`.

---

## 6. System Prompt & AI Behavior Rules

**Architecture boundaries (never cross):**
1. **4-layer rule:** logic lives **only** in `services/`. Routers stay thin; models are pure ORM; no business logic in models or routers.
2. **Machine-first decision tree** — classify every new DocType before writing code:
   - *Simple master* (no logic) → descriptor only. No bespoke code.
   - *Master + 1–2 rules* → descriptor + a `validate`/`before_insert` hook.
   - *Transaction / heavy logic* (GL posting, taxes & totals, stock moves, pricing priority, ledgers) → bespoke service, but list/form/permissions still come from a descriptor.
3. **Descriptor is the single source of truth** for engine-served masters. **Never** express GL posting, taxes & totals, `per_billed`/`per_delivered`, stock effects, pricing priority, or append-only ledgers in metadata — those stay in bespoke services.

**Hard constraints (from migration prompt §9):**
- **No Frappe dependency** — zero imports of `frappe`/`erpnext`; never add such a dependency.
- **Python 3.12+ syntax**, full type annotations on every signature (Python *and* TypeScript).
- **All DB access is async** — no synchronous DB calls in handlers or services.
- **Preserve ERPNext semantics exactly:** `docstatus` 0/1/2 (Draft/Submitted/Cancelled) on every transactional table; naming series (`SINV-.YYYY.-`) with atomic per-company counters; DocType-style role permission matrix incl. `if_owner`; append-only GL + Stock ledgers (INSERT only, never UPDATE/DELETE).
- **Multi-tenancy is non-negotiable:** every tenant table has `company_id UUID NOT NULL`; enforce at both RLS **and** service layer.
- **No hardcoded secrets** — config via `pydantic-settings` / env vars only.
- **Uniform error envelope** for all 4xx: `{"detail": ..., "code": "ERR_CODE", "field": ...}`.
- **Structured JSON logging** via `structlog` with `trace_id` on every line.
- **OpenAPI:** every endpoint has `summary`, `description`, `response_model`.

**Styling / frontend rules:**
- Vue 3 **Composition API only** (no Options API); TypeScript throughout; Pinia (no Vuex); Tailwind for styling.
- Every form is driven by a schema config object (reuse `FormBuilder` / `DataTable` / `useList` / `useDocument`).

**Never do this:**
- ❌ Never write user-visible strings "ERPNext" / "Frappe" / "frappe.io" — all branding comes from `public/brand/config.json`. Zero hardcoded product names in code.
- ❌ Never hand-code a simple master across 8 layers — use the engine.
- ❌ Never UPDATE/DELETE a GL entry or stock ledger entry.
- ❌ Never put business logic in a router or a model.
- ❌ Never bypass `company_id` filtering in a service.
- ❌ Never commit real secrets (note the dev DB password in `docker-compose.yml` is a placeholder — see §7).

---

## 7. Known Quirks & Environment Gotchas

- **Two Postgres roles, on purpose.** The app connects as **`erp_app`** (non-owner → RLS is *enforced*); Alembic migrations and `seed_demo` connect as **`erp_owner`** (owner → runs DDL). Using the wrong role either bypasses RLS or fails to migrate. `infra/init-db.sql` creates both — **it must run before migrations** (the `erp_app` grants are silently skipped if the role doesn't exist).
- **`ltree` extension required** (COA tree, Item Group, Warehouse). Target Postgres needs the **contrib** package (Postgres 13+; compose uses 16).
- **DB password mismatch between envs:** `docker-compose.yml` uses password `milin` for `erp_app`/`erp_owner`, while `.env.example` uses `erp_app_dev_pw` / `erp_owner_dev_pw`. Match your `.env` to whichever Postgres you actually started (compose vs. local). Passwords in the repo are dev placeholders — rotate before any shared/remote instance.
- **Windows PostgreSQL on :5432:** if `postgresql-x64-*` is also installed, host `localhost:5432` may hit that server (no `erp_owner`), not Docker. Prefer migrations inside Compose: `docker compose exec backend alembic upgrade head`. Full stack: `docker compose up` (backend already runs `alembic upgrade head` on start via override).
- **`scripts/seed_demo.py --reset-schema` is destructive** — it **drops schema `public`**, re-runs `alembic upgrade head`, then seeds. Omit `--reset-schema` to seed additively (aborts if the demo company already exists). After a partial/interrupted seed, recover with `--reset-schema`. There's a non-destructive `--phase3-topup` path for upgrading existing demo DBs.
- **WeasyPrint needs native libs** (Pango / Cairo / gdk-pixbuf). They're in the backend Docker image; on bare Windows/local, PDF endpoints (`/sales-invoices/{id}/pdf`, `/purchase-invoices/{id}/pdf`) will fail until those libs are installed. Flagged `MANUAL_REVIEW` vs. wkhtmltopdf.
- **HR module source differs:** `hr` no longer exists in erpnext `develop` — it moved to the separate `frappe/hrms` app. Module 08 must be migrated from HRMS sources, not erpnext.
- **Windows-first dev environment.** README quick-start uses PowerShell (`.venv\Scripts\activate`, `copy .env.example .env`). The local `.venv` is running **Python 3.13** (`.pyc` = `cpython-313`) even though `pyproject.toml` floors at 3.12 — fine, just be aware.
- **Email in dev goes to Mailhog** (compose service), not a real inbox — view at `http://localhost:8025`. SMTP points at `mailhog:1025`.
- **Realtime/WebSocket is optional locally** — `main.py` logs `websocket_redis_unavailable` and continues if Redis isn't up; don't treat that warning as a failure.
- **Scheduler** is on by default (`SCHEDULER_ENABLED=true`); disable via env in tests to avoid background jobs firing.
- **Seeding over a network is slow** — the seeder makes hundreds of round-trips; run it on the DB host or via SSH tunnel for remote targets, and URL-encode special chars in DSN passwords.

---

## 8. Verification & Testing Commands

**Run the whole stack (Docker — easiest):**
```bash
docker compose up --build
# Frontend  http://localhost:8080   |   API docs  http://localhost:8000/docs   |   Mailhog  http://localhost:8025
# Login: admin@example.com / ChangeMe!123   (override via ADMIN_EMAIL / ADMIN_PASSWORD)
```

**Backend (local dev, PowerShell / Windows):**
```powershell
cd backend
python -m venv .venv; .venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
docker compose up postgres redis -d          # from repo root
alembic upgrade head                          # migrate (as erp_owner via MIGRATIONS_DATABASE_URL)
python -m scripts.seed --admin-email admin@example.com --admin-password ChangeMe!123
uvicorn app.main:app --reload                 # http://localhost:8000
```

**Full demo dataset (real GL postings, statuses, aging):**
```powershell
cd backend
python -m scripts.seed_demo --reset-schema `
  --database-url "postgresql+asyncpg://erp_owner:<pw>@localhost:5432/erp" `
  --admin-email admin@example.com --admin-password "ChangeMe!123"
# Demo logins: manager@ / books@ / sales@demo-erp.com  (password Demo!Pass123)
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 (proxies /api → :8000)
npm run build          # vue-tsc type-check + vite build  ← use to VERIFY types compile
npm run preview        # serve the production build
```

**Tests:**
```bash
cd backend
pytest tests/unit                                             # no DB needed
TEST_DATABASE_URL=postgresql+asyncpg://erp_owner:erp_owner_dev_pw@localhost:5432/erp_test \
  pytest tests/integration                                    # needs the compose postgres
```

**Lint / health checks:**
```bash
cd backend && ruff check .            # lint (line-length 110, target py312)
curl http://localhost:8000/health     # → {"status":"ok"}
```

**E2E (Playwright, from `e2e/`):**
```bash
cd e2e && npm install && npx playwright install
npm test                               # runs against a running app (default :5173)
```
