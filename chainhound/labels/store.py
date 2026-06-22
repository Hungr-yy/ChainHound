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
    otherwise the source fetches it live.
    """
    labels = source.parse(text if text is not None else source.fetch())
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
