# Developer setup

Run the same checks as GitHub CI on your own machine before every push. It is faster and does not use CI minutes.

## Windows (WSL, no Docker needed)

One-time, in a WSL Ubuntu shell:

```bash
sudo apt-get update && sudo apt-get install -y postgresql-16 python3-venv nodejs npm
sudo service postgresql start
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres'"
```

Then, from the repo in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ci-local.ps1          # full CI mirror
powershell -ExecutionPolicy Bypass -File scripts/ci-local.ps1 --skip-lint -k obligations   # faster, one test filter
```

Or directly inside WSL: `bash scripts/ci-local.sh`.

The script creates a throwaway database (`biz360x_ci`), applies the migrations, runs the database checks, regenerates and validates the OpenAPI contract, checks the screen map in `API_GUIDE.md`, and runs pytest. It ends with `LOCAL CI PASSED`.

## Linux / macOS

Install PostgreSQL 16, Python 3.12 and Node 20, make sure `psql` can connect as `postgres` with password `postgres` on localhost, then `bash scripts/ci-local.sh`.

## Docker alternative

`docker compose up -d db` loads the schema, then follow the commands in `CLAUDE.md`.

## Migrations

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/biz360x python scripts/migrate.py [--dry-run]
```

The runner applies `db/NN_*.sql` in order, records a checksum for each file in `schema_migrations`, and refuses to run if an applied file was edited. Add schema changes as a new numbered file (`db/12_*.sql`, …).

## Web

```bash
cd web && npm ci && npm run dev    # http://localhost:3000; start the API on :8000 first
```

`API_URL` (web server side) says where the API is; the browser only ever calls `/api/v1` on its own origin. The WSL local CI copies `web/` to a temp folder, so Windows and Linux `node_modules` never mix.

## Environment variables

Development needs only `DATABASE_URL`. Everything else has a development default that the server **refuses outside development** (see `docs/SECURITY.md`).

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection |
| `BIZ_ENV` | `dev` (default), `test`, `staging`, `production`; anything else counts as production |
| `JWT_SECRET`, `BIZ_FIELD_KEY` | Token signing, and the Fernet key for encrypted columns. Required in production |
| `BIZ_OTP_PROVIDER`, `BIZ_IDP_PROVIDER` | Sign-in providers; `sandbox` by default (dev code `000000`, identity code `sandbox:<email>`) |
| `BIZ_ADAPTERS` | Portal adapters, e.g. `gsp=acme,irp=acme` |
| `BIZ_ALERT_WEBHOOK` | Where the audit-chain check posts a failure |
| `BIZ_RATE_USER`, `BIZ_RATE_HEAVY` | Requests a minute per caller (600) and for portal-calling actions (60). The tests raise both in `server/tests/conftest.py` |

## Line endings

`.gitattributes` forces LF for scripts, SQL, Python and YAML so they work in WSL.
