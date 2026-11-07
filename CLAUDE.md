# CLAUDE.md — Biz360X

Biz360X is a business operating system for Indian companies, startups and MSMEs. Its core is a compliance engine that turns statute-linked rules and company facts into dated obligations (GST, TDS, MCA, EPF/ESI, PT). Around it sit books, sales and billing (GST e-invoice), purchases (MSME 45-day rule), payroll, legal, cap table, a document vault, and a copilot that only proposes actions.

Read `docs/API_GUIDE.md` before changing anything structural.

## Repository map

| Path | What it is |
|---|---|
| `db/01…11_*.sql` | PostgreSQL 16 schema, applied in filename order. 119 tables plus API support tables, row-level security, views. |
| `db/tests/demo_and_checks.sql` | Demo tenant (Nimbus Instruments) and 23 behaviour assertions. Run it after every schema change. |
| `api/openapi.yaml` | **Generated** OpenAPI 3.1 contract (270 operations). Do not hand-edit. |
| `api/build_openapi.py` | Source of truth for the contract. Edit this, then regenerate. |
| `server/` | FastAPI reference server + job worker (`app/worker.py`) + end-to-end tests (`tests/test_flows.py`). |
| `docs/API_GUIDE.md` | Architecture, conventions, permissions, flows, screen → endpoint map. |
| `web/` | Next.js front end (`web/README.md`): app shell, sign-in, Today, Compliance so far. Types are generated from the contract. |
| `design/screens/*.dc.html` | Exported screen designs (26 artboards). Use them for layout, copy and data shapes; the live canvas is the source of truth. |

## Commands

```bash
# local database (Docker)
docker compose up -d db                                   # loads db/*.sql on first start
docker compose exec db psql -U postgres -d biz360x -f /docker-entrypoint-initdb.d/tests/demo_and_checks.sql

# without Docker: a PostgreSQL 16 superuser connection is enough
for f in db/[0-9][0-9]_*.sql; do psql -d biz360x -v ON_ERROR_STOP=1 -f "$f"; done
psql -d biz360x -v ON_ERROR_STOP=1 -f db/tests/demo_and_checks.sql      # prints ALL CHECKS PASSED

# API
pip install -r server/requirements.txt
cd server && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/biz360x pytest -q   # end-to-end + engine unit tests
cd server && DATABASE_URL=… uvicorn app.main:app --reload                                    # :8000, spec at /v1/openapi.yaml
cd server && DATABASE_URL=… python -m app.worker

# web (needs the API running on :8000)
cd web && npm ci && npm run dev                           # :3000, forwards /api/v1 to the API
cd web && npm run typecheck && npm test && npm run gen:api # gen:api must leave no diff

# contract
python api/build_openapi.py api/openapi.yaml && npx @redocly/cli@1.25.0 lint api/openapi.yaml
npx @redocly/cli@1.25.0 build-docs api/openapi.yaml -o api/api-reference.html             # HTML reference (git-ignored)
```

The tests expect the demo data. Rebuild the database before a full test run, because the tests change it.

**Before every push run the local CI mirror:** `bash scripts/ci-local.sh` (WSL/Linux) or `powershell -ExecutionPolicy Bypass -File scripts/ci-local.ps1`. Setup: `docs/DEV_SETUP.md`. Migrations: `python scripts/migrate.py`.

## Rules that must hold

- **Tenancy is enforced by the database.** Every request runs in one transaction with `SET LOCAL ROLE biz_app` and `set_config('app.user_id', …)` (see `server/app/core.py: tx`). Never bypass row-level security in request handlers. Only workers and auth use `biz_worker`.
- **An invisible entity is a 404, never a 403.** `require(cur, entity_id, "module:action")` handles this.
- **Money** is `numeric` in the database and a two-decimal string in JSON (`"283200.00"`). Never use floats. Use `Decimal` and `tax.r2()`.
- **GST maths lives in `server/app/tax.py`.** The preview endpoint and the saved document must use the same function.
- **Errors** are RFC 9457 `application/problem+json` with a stable `code`. Prefer a database constraint and map its name in `CONSTRAINT_CODES` over duplicating the rule in Python.
- **Idempotency.** Writes that issue, file, pay, approve or record money require `Idempotency-Key`. Use `idempotency_begin` / `idempotency_finish` inside the same transaction.
- **Portal calls** (GSTN, IRP, e-way, MCA, TRACES, EPFO, ESIC, banks) never run inside a request. Return `202` with a `jobs` row; a worker adapter does the call.
- **Audit and events.** Write `audit()` and `emit()` in the same transaction as the change. `audit_log` is append-only and hash-chained, and `SELECT app.audit_verify()` must return NULL.
- **Two-person rule.** The requester of an approval can never decide it. The copilot only proposes; actions execute after an approval.
- **Compliance rules are versioned data.** Change a rule by inserting a new version with a new `effective` range. Never edit a published version.
- **Schema changes go in a new numbered migration** (`db/12_*.sql`, …). Once a migration has run anywhere, don't edit it.
- **Production start-up is guarded.** `security.check_environment()` refuses a default `JWT_SECRET`, a missing `BIZ_FIELD_KEY`, and any sandbox provider. Don't add a code path that works around it. API keys are service users, so they follow the same RLS; a key never gets approve/file/pay.
- **Indian law figures** (fees, thresholds, due dates, HSN/SAC) are a starter library and must be reviewed by a CA/CS. Don't present them as verified.

## When you add an endpoint

1. Add it to `api/build_openapi.py`. Regenerate and lint.
2. Implement it in `server/app/routes_*.py` with `require(...)`. Use the path shape and error codes from the spec.
3. Add an end-to-end test in `server/tests/` against the demo data, covering at least the happy path and one problem code.
4. Update the screen → endpoint map in `docs/API_GUIDE.md` if a screen uses it.

## Backlog (in order)

Done: compliance engine evaluator (`server/app/engine.py`, migrations 12-13); ledger posting for sales documents and receipts, quotes to cash, bank matching and period close (`ledger.py`, `routes_orders.py`, `routes_finance.py`, `receivables.py`). Bill posting, purchases, GST returns, 2B matching, TDS and advance tax are in (`routes_purchases.py`, `routes_gst.py`, `taxcalc.py`; migrations 14-15). Payroll (`routes_payroll.py`, `payroll.py`, `secretbox.py`; migration 16) is in; legal, secretarial, the vault and the notice desk are in (`routes_vault.py`, `routes_legal.py`, `storage.py`; migration 17). The portal adapters are in as interfaces and sandboxes (`adapters.py`, `routes_portals.py`, migration 18); the provider choice is `docs/PROVIDERS.md`. Auth and hardening are in (`auth.py`, `ratelimit.py`, `security.py`, `webhooks.py`, `routes_integrations.py`; migration 19; `docs/SECURITY.md`). The front end has started (M8): shell, sign-in, Today and Compliance are in `web/`; the other screens follow the design files, one milestone issue at a time.

3. Remaining spec operations (onboarding, quotes, purchases, GST returns, payroll, legal, documents, copilot).
4. ~~Real auth~~ - done except the vendors: a real OTP provider, Google/Microsoft/SAML identity providers and MFA enrolment still have to be chosen and written (`docs/SECURITY.md`). The development OTP `000000` is refused outside dev by the startup guard.
5. Frontend (Next.js) built from `design/screens`: in progress, see `web/README.md` for what exists.
6. Export–import module: shipping bills, bills of entry, e-BRC, LUT, RoDTEP/drawback.
