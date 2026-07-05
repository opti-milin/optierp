# Migrating OptiERP to a new machine

This project has **two separate things** you need to carry over:

| What | Where it lives | How it travels |
|------|----------------|----------------|
| **Code + full git history** | GitHub (`origin`) | `git clone` |
| **Your seeded/entered data** | PostgreSQL (Docker volume `pgdata`) — **not in git** | a `pg_dump` file in `backups/` |

The database is never stored in git (too large, changes constantly, holds real data).
It is exported to `backups/erp-YYYYMMDD.dump`. That folder is git-ignored on purpose,
but it sits inside the OneDrive-synced project folder, so it will sync to the new
machine automatically. (If OneDrive isn't set up there, just copy the `.dump` file over
by hand into `backups/`.)

---

## Prerequisites on the new machine

- **Git**
- **Docker Desktop** (running)

Nothing else — the whole stack (Postgres, Redis, backend, frontend, Mailhog) runs in
Docker, and `docker-compose.yml` already contains all the config it needs. You do **not**
need to create a `backend/.env` for the Docker workflow.

---

## 1. Get the code

```bash
git clone https://github.com/milinkanu/optierp-mig.git
cd optierp-mig
git checkout feat/manufacturing      # latest work — this branch contains ALL history
```

> `feat/manufacturing` is the tip of everything: every commit from `main`, the Assets
> module, all the GST/compliance work, and the new Manufacturing module are ancestors of
> it. Checking it out gives you the complete current state.

---

## 2. Get the data — pick ONE option

### Option A — Restore your EXACT current data  ✅ (what you want)

This reproduces the database exactly as it is on the old machine.

1. Make sure the dump file is present at `backups/erp-YYYYMMDD.dump`
   (synced by OneDrive, or copied in manually).

2. Start **only Postgres** first. This creates the `erp_owner` / `erp_app` roles and an
   empty `erp` database via `infra/init-db.sql`:

   ```bash
   docker compose up -d postgres
   docker compose ps          # wait until postgres is "healthy" (~5–10s)
   ```

3. Restore the dump into the empty `erp` database (container name is
   `optierp-mig-postgres-1`):

   ```bash
   docker exec -i optierp-mig-postgres-1 \
     pg_restore -U erp_owner -d erp < backups/erp-YYYYMMDD.dump
   ```

   You may see a couple of harmless notices like *"schema public already exists"* or
   *"extension ltree already exists"* — ignore them, they're expected because
   `init-db.sql` created those first.

4. Bring up the rest of the stack:

   ```bash
   docker compose up -d --build
   ```

   On startup the backend runs `alembic upgrade head` (a no-op — the restored DB is
   already at revision `0065_manufacturing`) and `python -m scripts.seed` (a no-op — the
   seeder is idempotent and your data is already there).

5. Open **http://localhost:8080** and log in with the **same credentials you use now**.

### Option B — Fresh demo data (fallback, reproducible from code)

If you don't need your exact data and just want a working system with demo content:

```bash
docker compose up --build
```

That's it — migrations + the idempotent seed build a fresh demo dataset. Log in with
`admin@example.com` / `ChangeMe!123`. **This does NOT restore your current data.**

---

## 3. Verify

- Frontend: http://localhost:8080
- API docs: http://localhost:8000/docs
- Dev mail catcher (Mailhog): http://localhost:8025

Spot-check a few records you know exist to confirm Option A restored correctly.

---

## Making a fresh dump later (on the old machine)

To re-export the current database at any time:

```bash
docker exec optierp-mig-postgres-1 pg_dump -U erp_owner -d erp -Fc \
  > "backups/erp-$(date +%Y%m%d).dump"
```

---

## Notes

- **Running the backend outside Docker?** Only then do you need `backend/.env`
  (copy `backend/.env.example`). The Docker stack ignores it and uses the values baked
  into `docker-compose.yml`.
- **Passwords** for the local dev DB roles are `milin` (see `infra/init-db.sql` and
  `docker-compose.yml`). These are local-dev only.
- The `erp_test` database is created empty by `init-db.sql` and is only used by the
  integration test suite; it does not need restoring.
