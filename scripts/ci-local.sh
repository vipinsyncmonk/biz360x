#!/usr/bin/env bash
# Local mirror of .github/workflows/ci.yml, so a push does not burn CI minutes.
# Needs: PostgreSQL 16 running locally, python3 (venv), node/npx.
# Run from WSL/Linux:   bash scripts/ci-local.sh [--skip-lint] [--keep-db] [pytest args...]
# From Windows:         powershell -ExecutionPolicy Bypass -File scripts/ci-local.ps1
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_LINT=0; KEEP_DB=0; PYTEST_ARGS=()
for a in "$@"; do
  case "$a" in
    --skip-lint) SKIP_LINT=1 ;;
    --keep-db) KEEP_DB=1 ;;
    *) PYTEST_ARGS+=("$a") ;;
  esac
done

export PGHOST="${PGHOST:-localhost}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-postgres}" PGPASSWORD="${PGPASSWORD:-postgres}"
DB="${CI_DB:-biz360x_ci}"
export DATABASE_URL="postgresql://$PGUSER:$PGPASSWORD@$PGHOST:$PGPORT/$DB"
VENV="${CI_VENV:-$HOME/.venvs/biz360x}"

step() { printf '\n==> %s\n' "$*"; }

step "Postgres reachable"
if ! psql -d postgres -Atc 'select version()' >/dev/null 2>&1; then
  echo "Cannot connect as $PGUSER@$PGHOST. Start PostgreSQL and set the password once:"
  echo "  sudo service postgresql start"
  echo "  sudo -u postgres psql -c \"ALTER USER postgres PASSWORD 'postgres'\""
  exit 1
fi

step "Fresh database $DB"
psql -d postgres -v ON_ERROR_STOP=1 -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB"
cleanup() { [ "$KEEP_DB" = 1 ] || psql -d postgres -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1 || true; }
trap cleanup EXIT

step "Python dependencies"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -r server/requirements.txt pyyaml openapi-spec-validator pip-audit

step "Dependency vulnerability scan"
"$VENV/bin/pip-audit" -r server/requirements.txt

step "Load schema (migration runner, then a second run must be a no-op)"
"$VENV/bin/python" scripts/migrate.py
"$VENV/bin/python" scripts/migrate.py | tail -1 | grep -q '^0 applied' || { echo "migrate.py is not idempotent"; exit 1; }

step "Database behaviour checks"
psql -d "$DB" -v ON_ERROR_STOP=1 -q -f db/tests/demo_and_checks.sql

step "Contract is generated, valid and linted"
"$VENV/bin/python" api/build_openapi.py /tmp/openapi.yaml
diff -u --strip-trailing-cr api/openapi.yaml /tmp/openapi.yaml   # CRLF-tolerant: Windows checkouts convert line endings
"$VENV/bin/python" -c "import yaml; from openapi_spec_validator import validate; validate(yaml.safe_load(open('api/openapi.yaml')))"
if [ "$SKIP_LINT" = 1 ]; then
  echo "(redocly lint skipped)"
else
  npx -y @redocly/cli@1.25.0 lint api/openapi.yaml --config api/redocly.yaml
fi

step "Docs map references real operations"
"$VENV/bin/python" scripts/check_docs_map.py

step "End-to-end API tests"
(cd server && "$VENV/bin/python" -m pytest -q "${PYTEST_ARGS[@]}")

printf '\nLOCAL CI PASSED\n'
