"""Thin Postgres helper for loading the schema and persisting canonical records.

Persistence is optional in Phase 0/1 (the CLI can run purely against a live
provider), but the schema is the durable backbone the correlation engine grows
into. Uses psycopg (v3) if available.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from .models import LabelRecord

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


# --- Label ingestion (Phase 2a) -------------------------------------------

# Idempotent upsert on the partial unique index uq_label_addr_source_name. A
# re-sync of the same source refreshes mutable fields and bumps updated_at, so
# pulling a source repeatedly dedups instead of piling up duplicate rows.
_UPSERT_LABEL = """
INSERT INTO label (chain, address, cluster_id, name, category, source, confidence)
VALUES (%(chain)s, %(address)s, %(cluster_id)s, %(name)s,
        %(category)s, %(source)s, %(confidence)s)
ON CONFLICT (chain, address, source, name) WHERE address IS NOT NULL
DO UPDATE SET category   = EXCLUDED.category,
              confidence = EXCLUDED.confidence,
              cluster_id = COALESCE(EXCLUDED.cluster_id, label.cluster_id),
              updated_at = now()
"""


def upsert_labels(database_url: str, records: Iterable[LabelRecord]) -> int:
    """Idempotently insert/update address-scoped labels. Records with no address
    are skipped (the unique index is partial on address). Returns the number of
    rows sent to the upsert."""
    rows = [
        {
            "chain": r.chain,
            "address": r.address,
            "cluster_id": r.cluster_id,
            "name": r.name,
            "category": r.category,
            "source": r.source,
            "confidence": r.confidence,
        }
        for r in records
        if r.address
    ]
    if not rows:
        return 0
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_LABEL, rows)
        conn.commit()
    return len(rows)


# --- On-demand fetch cache (Phase 2a) -------------------------------------

def cache_get(database_url: str, source: str, request_key: str) -> Optional[dict]:
    """Return a cached response body if present and unexpired, else None."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT body FROM fetch_cache "
                "WHERE source = %s AND request_key = %s "
                "AND (expires_at IS NULL OR expires_at > now())",
                (source, request_key),
            )
            row = cur.fetchone()
    if not row or row[0] is None:
        return None
    body = row[0]
    # psycopg returns JSONB as a parsed object; tolerate a text round-trip too.
    return json.loads(body) if isinstance(body, str) else body


def cache_put(
    database_url: str,
    source: str,
    request_key: str,
    body: dict,
    status: int = 200,
    ttl_seconds: Optional[int] = None,
) -> None:
    """Store (or refresh) a cached response. ttl_seconds=None caches forever."""
    expires = None if ttl_seconds is None else f"{int(ttl_seconds)} seconds"
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fetch_cache "
                "(source, request_key, body, status, fetched_at, expires_at) "
                "VALUES (%s, %s, %s, %s, now(), "
                "        now() + CAST(%s AS INTERVAL)) "
                "ON CONFLICT (source, request_key) DO UPDATE "
                "SET body = EXCLUDED.body, status = EXCLUDED.status, "
                "    fetched_at = now(), expires_at = EXCLUDED.expires_at",
                (source, request_key, json.dumps(body), status, expires),
            )
        conn.commit()
