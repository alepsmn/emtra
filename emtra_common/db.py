"""FogStore connection and SQL migrations."""

from pathlib import Path

import psycopg

from emtra_common.config import ROOT, load_infra

MIGRATIONS_DIR = ROOT / "contracts" / "sql" / "fogstore"


def default_dsn() -> str:
    return load_infra().db_dsn


def connect(dsn: str | None = None, **kw: object) -> psycopg.Connection:
    return psycopg.connect(dsn or default_dsn(), **kw)  # type: ignore[arg-type]


def apply_migrations(dsn: str | None = None, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply, in name order and one transaction each, the files not yet recorded.

    Returns the names applied in this call.
    """
    applied: list[str] = []
    with connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(directory.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied
