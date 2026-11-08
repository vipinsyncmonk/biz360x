#!/usr/bin/env bash
# Start what the browser tests need besides the web app: a fresh database with the demo data, the API and the worker.
#   bash scripts/e2e-stack.sh start     # reset the database, then start the API (:8000) and the worker
#   bash scripts/e2e-stack.sh stop
# Needs PostgreSQL running locally. In CI the dependencies are already installed; locally it uses ~/.venvs/biz360x.
set -euo pipefail
cd "$(dirname "$0")/.."

export PGHOST="${PGHOST:-localhost}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-postgres}" PGPASSWORD="${PGPASSWORD:-postgres}"
DB="${E2E_DB:-biz360x_e2e}"
export DATABASE_URL="postgresql://$PGUSER:$PGPASSWORD@$PGHOST:$PGPORT/$DB"
PY="${E2E_PYTHON:-$HOME/.venvs/biz360x/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
PORT="${API_PORT:-8000}"
mkdir -p var
ROOT="$(pwd)"
PIDS="$ROOT/var/e2e.pids"

stop() {
  if [ -f "$PIDS" ]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDS"
    rm -f "$PIDS"
  fi
}

case "${1:-start}" in
  stop) stop; exit 0 ;;
  start) ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac

stop
psql -d postgres -v ON_ERROR_STOP=1 -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB"
"$PY" scripts/migrate.py >/dev/null
psql -d "$DB" -v ON_ERROR_STOP=1 -q -f db/tests/demo_and_checks.sql >/dev/null


cd "$ROOT/server"
nohup "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" > "$ROOT/var/e2e-api.log" 2>&1 &
echo $! >> "$PIDS"
nohup "$PY" -m app.worker > "$ROOT/var/e2e-worker.log" 2>&1 &
echo $! >> "$PIDS"
cd "$ROOT"

for _ in $(seq 1 60); do
  if curl -fs "http://localhost:$PORT/v1/openapi.yaml" >/dev/null 2>&1; then echo "API ready on :$PORT"; exit 0; fi
  sleep 1
done
echo "The API did not start. See var/e2e-api.log"; tail -20 var/e2e-api.log; exit 1
