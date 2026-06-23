"""Integration test: monitoring against a real Postgres.

Skipped unless CHAINHOUND_DATABASE_URL is set. Exercises what the fakes cannot:
the JSONB baseline/detail round-trip, ``ON DELETE CASCADE`` from watch to alert,
and the silent-first-check -> fire-on-change flow end to end. Isolated schema,
dropped on teardown (mirrors the other *_integration tests).
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from chainhound.models import AddressSummary
from chainhound_server import monitor, store

pytest.importorskip("psycopg", reason="integration test needs psycopg")

_DB_URL = os.getenv("CHAINHOUND_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DB_URL, reason="set CHAINHOUND_DATABASE_URL to run the DB integration test"
)

_SCHEMA = "chainhound_monitor_it"


def _with_search_path(url: str, schema: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


class _Provider:
    def __init__(self, summary):
        self._summary = summary

    def get_address_summary(self, address):
        return self._summary


@pytest.fixture(scope="module")
def db_url():
    import psycopg

    from chainhound.db import init_schema

    with psycopg.connect(_DB_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {_SCHEMA}")

    url = _with_search_path(_DB_URL, _SCHEMA)
    init_schema(url)
    try:
        yield url
    finally:
        with psycopg.connect(_DB_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")


def _summary(**kw):
    base = dict(
        address="bc1qfoo",
        chain="bitcoin",
        tx_count=0,
        total_received=0,
        total_sent=0,
        balance=0,
    )
    base.update(kw)
    return AddressSummary(**base)


def test_monitor_silent_first_then_fires(db_url):
    w = store.add_watch(db_url, "bitcoin", "bc1qfoo")
    assert w["baseline"] is None

    pf = lambda chain: _Provider(_summary(tx_count=2))
    first = monitor.run_all(db_url, provider_for=pf)
    assert first["fired"] == 0  # baseline established silently

    # The baseline JSONB persisted and re-parsed.
    reloaded = store.list_watches(db_url)[0]
    assert reloaded["baseline"]["tx_count"] == 2

    # New activity on the next sweep fires a Near-Certainty alert.
    pf2 = lambda chain: _Provider(_summary(tx_count=6, total_received=999))
    second = monitor.run_all(db_url, provider_for=pf2, large_threshold=500)
    detectors_fired = {a["detector"] for a in second["alerts"]}
    assert {"new-activity", "large-inflow"} <= detectors_fired

    alerts = store.list_alerts(db_url, watch_id=w["id"])
    assert alerts and alerts[0]["detail"]["confidence"] == "Near Certainty"


def test_delete_watch_cascades_alerts(db_url):
    w = store.add_watch(db_url, "bitcoin", "bc1qbar")
    store.record_alert(db_url, w["id"], "new-activity", {"delta": 1})
    assert store.list_alerts(db_url, watch_id=w["id"])

    assert store.delete_watch(db_url, w["id"]) is True
    assert store.list_alerts(db_url, watch_id=w["id"]) == []  # cascaded
    assert store.delete_watch(db_url, w["id"]) is False
