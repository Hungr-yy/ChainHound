"""Thin Postgres helper for loading the schema and persisting canonical records.

Persistence is optional in Phase 0/1 (the CLI can run purely against a live
provider), but the schema is the durable backbone the correlation engine grows
into. Uses psycopg (v3) if available.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def connect(database_url: str):
    if psycopg is None:
        raise RuntimeError("install 'psycopg[binary]' to use the database layer")
    return psycopg.connect(database_url)


def init_schema(database_url: str, schema_path: Optional[Path] = None) -> None:
    """Create all tables from sql/schema.sql (idempotent)."""
    sql = (schema_path or SCHEMA_PATH).read_text()
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
