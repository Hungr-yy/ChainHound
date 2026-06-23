"""Tests for the label store. Offline: a fake connection records executed SQL.

Verifies the idempotent bulk-refresh contract (delete-by-source then insert in
one transaction) and the lookup query, without a live Postgres.
"""
from chainhound.labels.base import Label, LabelSource
from chainhound.labels import store


class _FakeCursor:
    def __init__(self, rows=None):
        self.calls = []           # list of (sql, params)
        self.many = []            # list of (sql, seq_of_params)
        self._rows = rows or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def executemany(self, sql, seq):
        self.many.append((sql, list(seq)))

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows=None):
        self.cursor_obj = _FakeCursor(rows)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


class _StaticSource(LabelSource):
    source = "ofac"

    def __init__(self, labels):
        self._labels = labels

    def fetch(self):
        raise AssertionError("fetch must not be called when text is provided")

    def parse(self, text):
        return self._labels


def test_sync_upserts_in_one_txn():
    labels = [
        Label("bitcoin", "1AAA", "OFAC SDN: X", "sanctioned", "ofac", "Near Certainty"),
        Label("ethereum", "0xbbb", "OFAC SDN: X", "sanctioned", "ofac", "Near Certainty"),
    ]
    conn = _FakeConn()
    n = store.sync("postgresql://x", _StaticSource(labels), text="<ignored/>",
                   connect=lambda _url: conn)

    assert n == 2
    cur = conn.cursor_obj
    # No delete-by-source: rows are upserted on the partial unique index.
    assert all("DELETE" not in sql for sql, _ in cur.calls)
    assert len(cur.many) == 1
    sql, rows = cur.many[0]
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql
    assert len(rows) == 2
    assert conn.committed


def test_sync_skips_address_less_labels():
    labels = [
        Label("bitcoin", "1AAA", "OFAC SDN: X", "sanctioned", "ofac", "Near Certainty"),
        Label("bitcoin", None, "cluster only", "x", "ofac", "High"),  # partial-index exempt
    ]
    conn = _FakeConn()
    n = store.sync("postgresql://x", _StaticSource(labels), text="<ignored/>",
                   connect=lambda _url: conn)
    assert n == 1
    assert len(conn.cursor_obj.many[0][1]) == 1


def test_sync_with_no_labels_writes_nothing():
    conn = _FakeConn()
    n = store.sync("postgresql://x", _StaticSource([]), text="<empty/>",
                   connect=lambda _url: conn)
    assert n == 0
    assert conn.cursor_obj.calls == []   # no delete
    assert conn.cursor_obj.many == []    # no insert


def test_lookup_filters_on_chain_and_address():
    row = ("bitcoin", "1AAA", "OFAC SDN: X", "sanctioned", "ofac", "Near Certainty")
    conn = _FakeConn(rows=[row])
    labels = store.lookup("postgresql://x", "bitcoin", "1AAA",
                          connect=lambda _url: conn)
    sql, params = conn.cursor_obj.calls[0]
    assert "WHERE chain = %s AND address = %s" in sql
    assert params == ("bitcoin", "1AAA")
    assert labels[0].name == "OFAC SDN: X"
