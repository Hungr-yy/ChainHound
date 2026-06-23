"""Integration test: the attribution loop against a real Postgres.

Skipped unless CHAINHOUND_DATABASE_URL is set (and psycopg is importable), so the
normal offline suite is unaffected. What the fake-connection unit tests cannot
cover is exercised here on live psycopg3: schema.sql applies, the delete-by-source
refresh + executemany, `make_interval(secs => %s)`, the
`ON CONFLICT (source,chain,address)` upsert, and `with connect()` commit/close.

Hermetic except for the DB: the OFAC loader is driven from the committed
``sample_sdn.xml`` fixture via ``store.sync(text=...)`` (no live network, no
brittle assertions on live OFAC content), and triage uses a fake provider so it
never touches Blockstream. Isolation is a dedicated schema dropped on teardown,
so reruns never collide or pollute.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from chainhound.analysis.triage import triage_address
from chainhound.labels import store
from chainhound.labels.base import Label
from chainhound.labels.ofac import OFACSource
from chainhound.models import AddressSummary

pytest.importorskip("psycopg", reason="integration test needs psycopg")

_DB_URL = os.getenv("CHAINHOUND_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DB_URL, reason="set CHAINHOUND_DATABASE_URL to run the DB integration test"
)

_SCHEMA = "chainhound_it"
_FIXTURES = Path(__file__).parent / "fixtures"
_SAMPLE_SDN = (_FIXTURES / "sample_sdn.xml").read_text()
# A known crypto address carried by the fixture (Lazarus XBT entry).
_KNOWN_ADDR = "1Q9UMz5aGanLxgqQ2j6t9JNQVSiCwGCi9b"


def _with_search_path(url: str, schema: str) -> str:
    """Return ``url`` with libpq options forcing ``search_path`` to ``schema``."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


class _FakeProvider:
    """Stand-in for a connector so triage never hits the live network."""

    def __init__(self, chain: str, address: str) -> None:
        self.chain = chain
        self._address = address

    def get_address_summary(self, address: str) -> AddressSummary:
        return AddressSummary(address=address, chain=self.chain, tx_count=1)


def _count_labels(url: str, source: str) -> int:
    import psycopg

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM label WHERE source = %s", (source,))
            return cur.fetchone()[0]


@pytest.fixture(scope="module")
def db_url():
    """Create an isolated schema, load the canonical schema into it, drop on exit."""
    import psycopg

    from chainhound.db import init_schema

    with psycopg.connect(_DB_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {_SCHEMA}")

    url = _with_search_path(_DB_URL, _SCHEMA)
    init_schema(url)  # schema.sql lands in the dedicated schema via search_path
    try:
        yield url
    finally:
        with psycopg.connect(_DB_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")


def test_schema_lands_in_isolated_schema(db_url):
    import psycopg

    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT current_schema()").fetchone()[0] == _SCHEMA
        assert conn.execute("SELECT to_regclass('label')").fetchone()[0] is not None


def test_attribution_loop_ofac_to_triage(db_url):
    # Bulk refresh from the committed fixture (hermetic; no network).
    n1 = store.sync(db_url, OFACSource(), text=_SAMPLE_SDN)
    assert n1 >= 1
    # Re-pull is idempotent: the ON CONFLICT upsert refreshes in place (no dupes).
    n2 = store.sync(db_url, OFACSource(), text=_SAMPLE_SDN)
    assert n2 == n1
    assert _count_labels(db_url, "ofac") == n1

    # The read primitive returns the known sanctioned address with provenance.
    labels = store.lookup(db_url, "bitcoin", _KNOWN_ADDR)
    assert labels, "known sanctioned address must be found after sync"
    assert any(l.source == "ofac" and l.confidence == "Near Certainty" for l in labels)

    # Closed loop: triage attaches the sanction via the real DB lookup.
    report = triage_address(
        _FakeProvider("bitcoin", _KNOWN_ADDR),
        _KNOWN_ADDR,
        label_lookup=lambda chain, addr: [
            l.name for l in store.lookup(db_url, chain, addr)
        ],
    )
    assert report["found"] is True
    assert any("OFAC SDN" in name for name in report["labels"])


def test_cache_upsert_and_make_interval(db_url):
    # Exercises ON CONFLICT (source,chain,address) and make_interval(secs => %s).
    store.cache_put(db_url, "chainabuse", "ethereum", "0xABC", '{"reports":[]}')
    assert (
        store.cache_get(db_url, "chainabuse", "ethereum", "0xABC", 3600)
        == '{"reports":[]}'
    )
    # max_age 0 -> the row is "older than now()" -> treated as stale.
    assert store.cache_get(db_url, "chainabuse", "ethereum", "0xABC", 0) is None
    # Second put upserts the same key rather than erroring/duplicating.
    store.cache_put(db_url, "chainabuse", "ethereum", "0xABC", '{"reports":[1]}')
    assert (
        store.cache_get(db_url, "chainabuse", "ethereum", "0xABC", 3600)
        == '{"reports":[1]}'
    )


def test_replace_address_is_idempotent(db_url):
    label = Label(
        "ethereum", "0xDEF", "Chainabuse: PHISHING", "scam", "chainabuse", "Low"
    )
    store.replace_address(db_url, "chainabuse", "ethereum", "0xDEF", [label])
    store.replace_address(db_url, "chainabuse", "ethereum", "0xDEF", [label])  # rerun
    got = [
        l for l in store.lookup(db_url, "ethereum", "0xDEF") if l.source == "chainabuse"
    ]
    assert len(got) == 1
