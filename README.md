# Biz360X

The business operating system for Indian companies, startups and MSMEs. It has one compliance engine, one ledger, and every module a company needs on top of them: GST and e-invoicing, TDS, MCA, payroll (PF/ESI/PT), sales and billing, purchases with the MSME 45-day rule, legal, cap table, a document vault, and a copilot that cites its sources.

| | |
|---|---|
| **Database** | PostgreSQL 16 · 125 tables · row-level security on every tenant table → [`db/`](db/README.md) |
| **API** | OpenAPI 3.1 · 270 operations · 16 webhook events → [`api/openapi.yaml`](api/openapi.yaml), [`docs/API_GUIDE.md`](docs/API_GUIDE.md) |
| **Reference server** | FastAPI + job worker + compliance engine + M2 ledger and sales-to-cash, 121 tests → [`server/`](server/) |
| **Designs** | 26 screens: landing, onboarding, dashboards, compliance engine, copilot, finance, payroll, GST, legal, vault, billing, mobile → [`design/`](design/README.md) |

## Quick start

```bash
docker compose up -d db                 # PostgreSQL 16 with the schema loaded
docker compose exec db psql -U postgres -d biz360x -f /docker-entrypoint-initdb.d/tests/demo_and_checks.sql
pip install -r server/requirements.txt
cd server && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/biz360x pytest -q
```

Or run everything: `docker compose up --build`. The API is then on http://localhost:8000 and the contract at `/v1/openapi.yaml`.

Developer setup (Windows/WSL, no Docker) and the local CI mirror: [`docs/DEV_SETUP.md`](docs/DEV_SETUP.md).

Working with Claude Code? Start with [`CLAUDE.md`](CLAUDE.md).
