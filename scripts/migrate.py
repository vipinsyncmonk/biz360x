"""Minimal forward-only migration runner for db/NN_*.sql.

    DATABASE_URL=postgresql://postgres:postgres@localhost/biz360x python scripts/migrate.py [--dry-run]

- Applies db/[0-9][0-9]_*.sql in filename order, each in its own transaction.
- Records every applied file with a SHA-256 in schema_migrations.
- Refuses to run if an already-applied file has been edited (CLAUDE.md: once a
  migration has run anywhere, don't edit it - add a new numbered file).
- Needs a superuser connection because the schema creates roles and extensions.
"""
import hashlib
import os
import sys
from pathlib import Path

import psycopg

DB_DIR = Path(__file__).resolve().parent.parent / "db"


def digest(path: Path) -> str:
    # normalise line endings so Windows and Linux checkouts hash the same
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    files = sorted(p for p in DB_DIR.glob("[0-9][0-9]_*.sql"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                          filename text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
        applied = dict(conn.execute("SELECT filename, sha256 FROM schema_migrations").fetchall())
        edited = [p.name for p in files if p.name in applied and applied[p.name] != digest(p)]
        if edited:
            print("ERROR: applied migrations were edited (add a new numbered file instead): " + ", ".join(edited))
            return 1
        # a 10_ file must not slip in below an applied higher number
        pending = [p for p in files if p.name not in applied]
        if applied and pending and pending[0].name < max(applied):
            print(f"ERROR: {pending[0].name} sorts before already-applied {max(applied)}; renumber it.")
            return 1
        for p in pending:
            print(("would apply " if dry else "applying ") + p.name)
            if dry:
                continue
            with conn.transaction():
                conn.execute(p.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)", (p.name, digest(p)))
        print(f"{len(pending)} applied, {len(files) - len(pending)} already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
