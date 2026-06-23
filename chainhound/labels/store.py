"""Persist and query labels in the Postgres ``label`` table.

Bulk sources refresh idempotently via an ``ON CONFLICT`` upsert on the partial
unique index ``uq_label_addr_source_name`` — a re-pull updates a label in place
and bumps ``updated_at`` instead of piling up duplicates, and partial syncs of a
source merge rather than clearing it. ``connect`` is injectable for offline
tests.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from .. import db
from .base import Label, LabelSource

# Upsert on the partial unique index (chain, address, source, name). Address-less
# (cluster-only) labels are skipped by callers since the index is partial.
_UPSERT = (
    "INSERT INTO label (chain, address, name, category, source, confidence) "
    "VALUES (%s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (chain, address, source, name) WHERE address IS NOT NULL "
    "DO UPDATE SET category = EXCLUDED.category, "
    "confidence = EXCLUDED.confidence, updated_at = now()"
)
_SELECT = (
    "SELECT chain, address, name, category, source, confidence "
    "FROM label WHERE chain = %s AND address = %s"
)


def _rows(labels: list[Label]) -> list[tuple]:
    """Upsert parameter tuples for address-scoped labels (partial-index safe)."""
    return [
        (l.chain, l.address, l.name, l.category, l.source, l.confidence)
        for l in labels
        if l.address
    ]


def sync(
    database_url: str,
    source: LabelSource,
    *,
    text: Optional[str] = None,
    connect: Callable = db.connect,
) -> int:
    """Refresh all labels for ``source``; return the number written.

    ``text`` lets the caller supply an already-fetched document (offline/cron);
    otherwise the source loads itself (a single fetch, or a corpus walk). Rows
    are upserted, so a re-pull dedups in place; labels dropped upstream are not
    pruned (run a fresh DB, or add a prune step, if exact removal is needed).
    """
    labels = source.parse(text) if text is not None else source.load()
    rows = _rows(labels)
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            if rows:
                cur.executemany(_UPSERT, rows)
        conn.commit()
    return len(rows)


def lookup(
    database_url: str,
    chain: str,
    address: str,
    *,
    connect: Callable = db.connect,
) -> list[Label]:
    """Return all labels recorded for ``(chain, address)``."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT, (chain, address))
            rows = cur.fetchall()
    return [Label(*row) for row in rows]


# --- on-demand fetch cache + per-address label persistence -------------------

_CACHE_GET = (
    "SELECT body FROM fetch_cache "
    "WHERE source = %s AND request_key = %s "
    "AND (expires_at IS NULL OR expires_at > now())"
)
_CACHE_PUT = (
    "INSERT INTO fetch_cache (source, request_key, body, status, fetched_at, expires_at) "
    "VALUES (%s, %s, %s, %s, now(), now() + CAST(%s AS INTERVAL)) "
    "ON CONFLICT (source, request_key) "
    "DO UPDATE SET body = EXCLUDED.body, status = EXCLUDED.status, "
    "fetched_at = now(), expires_at = EXCLUDED.expires_at"
)


def cache_get(
    database_url: str,
    source: str,
    request_key: str,
    *,
    connect: Callable = db.connect,
) -> Optional[dict]:
    """Return a cached response body for ``(source, request_key)`` if unexpired."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CACHE_GET, (source, request_key))
            row = cur.fetchone()
    if not row or row[0] is None:
        return None
    body = row[0]
    # psycopg returns JSONB pre-parsed; tolerate a text round-trip too.
    return json.loads(body) if isinstance(body, str) else body


def cache_put(
    database_url: str,
    source: str,
    request_key: str,
    body: dict,
    status: int = 200,
    ttl_seconds: Optional[int] = None,
    *,
    connect: Callable = db.connect,
) -> None:
    """Store/refresh a cached response. ``ttl_seconds=None`` caches forever."""
    expires = None if ttl_seconds is None else f"{int(ttl_seconds)} seconds"
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                _CACHE_PUT, (source, request_key, json.dumps(body), status, expires)
            )
        conn.commit()


def replace_address(
    database_url: str,
    source: str,
    chain: str,
    address: str,
    labels: list[Label],
    *,
    connect: Callable = db.connect,
) -> None:
    """Replace this source's labels for one address (idempotent per re-check)."""
    rows = _rows(labels)
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            # Clear first so labels dropped for this address are removed, then
            # upsert the current set (tolerates duplicate keys within the set).
            cur.execute(
                "DELETE FROM label WHERE source = %s AND chain = %s AND address = %s",
                (source, chain, address),
            )
            if rows:
                cur.executemany(_UPSERT, rows)
        conn.commit()
