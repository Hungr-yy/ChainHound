"""Persist and query labels in the Postgres ``label`` table.

Bulk sources refresh idempotently: each ``sync`` deletes the source's existing
rows and re-inserts the current set in one transaction, so re-pulls dedup
without a unique constraint or migration. ``connect`` is injectable for offline
tests.
"""
from __future__ import annotations

from typing import Callable, Optional

from .. import db
from .base import Label, LabelSource

_INSERT = (
    "INSERT INTO label (chain, address, name, category, source, confidence) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
_SELECT = (
    "SELECT chain, address, name, category, source, confidence "
    "FROM label WHERE chain = %s AND address = %s"
)


def sync(
    database_url: str,
    source: LabelSource,
    *,
    text: Optional[str] = None,
    connect: Callable = db.connect,
) -> int:
    """Refresh all labels for ``source``; return the number written.

    ``text`` lets the caller supply an already-fetched document (offline/cron);
    otherwise the source loads itself (a single fetch, or a corpus walk).
    """
    labels = source.parse(text) if text is not None else source.load()
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM label WHERE source = %s", (source.source,))
            if labels:
                cur.executemany(
                    _INSERT,
                    [
                        (l.chain, l.address, l.name, l.category, l.source, l.confidence)
                        for l in labels
                    ],
                )
        conn.commit()
    return len(labels)


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


# --- on-demand cache + per-address label persistence -------------------------

_CACHE_GET = (
    "SELECT raw FROM label_cache "
    "WHERE source = %s AND chain = %s AND address = %s "
    "AND fetched_at > now() - make_interval(secs => %s)"
)
_CACHE_PUT = (
    "INSERT INTO label_cache (source, chain, address, raw, fetched_at) "
    "VALUES (%s, %s, %s, %s, now()) "
    "ON CONFLICT (source, chain, address) "
    "DO UPDATE SET raw = EXCLUDED.raw, fetched_at = now()"
)


def cache_get(
    database_url: str,
    source: str,
    chain: str,
    address: str,
    max_age: int,
    *,
    connect: Callable = db.connect,
) -> Optional[str]:
    """Return the cached raw response if present and younger than ``max_age`` seconds."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CACHE_GET, (source, chain, address, max_age))
            row = cur.fetchone()
    return row[0] if row else None


def cache_put(
    database_url: str,
    source: str,
    chain: str,
    address: str,
    raw: str,
    *,
    connect: Callable = db.connect,
) -> None:
    """Store/refresh a raw response for ``(source, chain, address)``."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CACHE_PUT, (source, chain, address, raw))
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
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM label WHERE source = %s AND chain = %s AND address = %s",
                (source, chain, address),
            )
            if labels:
                cur.executemany(
                    _INSERT,
                    [
                        (l.chain, l.address, l.name, l.category, l.source, l.confidence)
                        for l in labels
                    ],
                )
        conn.commit()
