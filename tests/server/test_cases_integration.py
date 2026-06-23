"""Integration test: the case-persistence store against a real Postgres.

Skipped unless CHAINHOUND_DATABASE_URL is set (and psycopg is importable), so the
offline suite is unaffected. Exercises what the fake-connection unit tests cannot:
schema.sql applies, the ``RETURNING`` inserts, the ``ON CONFLICT (case_id,
element_id)`` hygiene upsert, the full load (notes + elements), and the
``ON DELETE CASCADE`` cleanup. Isolation is a dedicated schema dropped on
teardown, mirroring tests/test_labels_integration.py.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from chainhound_server import store

pytest.importorskip("psycopg", reason="integration test needs psycopg")

_DB_URL = os.getenv("CHAINHOUND_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DB_URL, reason="set CHAINHOUND_DATABASE_URL to run the DB integration test"
)

_SCHEMA = "chainhound_cases_it"


def _with_search_path(url: str, schema: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


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


def test_case_save_load_round_trip(db_url):
    case = store.create_case(db_url, "Op Tornado")
    cid = case["case_id"]
    assert isinstance(cid, int) and case["name"] == "Op Tornado"

    store.add_note(db_url, cid, chain="bitcoin", ref="bc1qseed", body="suspect deposit")
    # Hygiene upsert: two writes to the same element collapse to one row.
    store.save_element(db_url, cid, "nodeA", color="red", note="mixer")
    store.save_element(db_url, cid, "nodeA", color="blue", hidden=True, note="mixer")

    loaded = store.get_case(db_url, cid)
    assert loaded["name"] == "Op Tornado"
    assert [n["ref"] for n in loaded["notes"]] == ["bc1qseed"]
    assert len(loaded["elements"]) == 1  # upserted in place, not duplicated
    el = loaded["elements"][0]
    assert el == {
        "element_id": "nodeA",
        "color": "blue",
        "hidden": True,
        "note": "mixer",
    }


def test_add_note_and_element_reject_unknown_case(db_url):
    assert store.add_note(db_url, 10_000_001, body="orphan") is None
    assert store.save_element(db_url, 10_000_001, "nodeX") is None


def test_delete_case_cascades(db_url):
    case = store.create_case(db_url, "Throwaway")
    cid = case["case_id"]
    store.add_note(db_url, cid, body="note")
    store.save_element(db_url, cid, "n1", color="green")

    assert store.delete_case(db_url, cid) is True
    assert store.get_case(db_url, cid) is None
    assert store.delete_case(db_url, cid) is False  # already gone

    import psycopg

    with psycopg.connect(db_url) as conn:
        notes = conn.execute(
            "SELECT count(*) FROM case_note WHERE case_id = %s", (cid,)
        ).fetchone()[0]
        els = conn.execute(
            "SELECT count(*) FROM graph_element WHERE case_id = %s", (cid,)
        ).fetchone()[0]
    assert notes == 0 and els == 0  # cascaded
