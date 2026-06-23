"""Case-persistence tests. Offline: the store runs against a scripted fake
connection, and the routes run with the store stubbed (no live Postgres).
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from chainhound.config import Config
from chainhound_server import store
from chainhound_server.app import create_app
from chainhound_server.deps import get_config, get_connect

DT = datetime(2026, 6, 23, tzinfo=timezone.utc)


# --- scripted fake connection -------------------------------------------------
class _FakeCursor:
    """Returns each scripted result-set in turn; one set is consumed per
    ``execute``, then served by the following ``fetchone``/``fetchall``."""

    def __init__(self, script):
        self.script = list(script)
        self.active = []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.active = self.script.pop(0) if self.script else []

    def fetchone(self):
        return self.active[0] if self.active else None

    def fetchall(self):
        return list(self.active)


class _FakeConn:
    def __init__(self, script):
        self.cur = _FakeCursor(script)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


def _connect(conn):
    return lambda _url: conn


# --- store unit tests ---------------------------------------------------------
def test_create_case_inserts_and_commits():
    conn = _FakeConn([[(1, "Op Tornado", DT)]])
    out = store.create_case("postgresql://x", "Op Tornado", connect=_connect(conn))
    assert out == {"case_id": 1, "name": "Op Tornado", "created_at": DT}
    assert conn.committed
    assert "INSERT INTO investigation" in conn.cur.executed[0][0]


def test_list_cases_newest_first():
    conn = _FakeConn([[(2, "b", DT), (1, "a", DT)]])
    out = store.list_cases("postgresql://x", connect=_connect(conn))
    assert [c["case_id"] for c in out] == [2, 1]


def test_get_case_loads_notes_and_elements():
    conn = _FakeConn(
        [
            [(1, "Op Tornado", DT)],  # investigation
            [(10, "bitcoin", "bc1q", "suspect deposit", DT)],  # case_note
            [("nodeA", "red", True, "infra")],  # graph_element
        ]
    )
    case = store.get_case("postgresql://x", 1, connect=_connect(conn))
    assert case["name"] == "Op Tornado"
    assert case["notes"][0]["ref"] == "bc1q"
    assert case["elements"][0] == {
        "element_id": "nodeA",
        "color": "red",
        "hidden": True,
        "note": "infra",
    }


def test_get_case_missing_returns_none():
    conn = _FakeConn([[]])  # no investigation row
    assert store.get_case("postgresql://x", 99, connect=_connect(conn)) is None


def test_delete_case_reports_removal():
    found = _FakeConn([[(1,)]])
    assert store.delete_case("postgresql://x", 1, connect=_connect(found)) is True
    missing = _FakeConn([[]])
    assert store.delete_case("postgresql://x", 9, connect=_connect(missing)) is False


def test_add_note_requires_existing_case():
    missing = _FakeConn([[]])  # existence check fails
    assert (
        store.add_note("postgresql://x", 9, body="hi", connect=_connect(missing))
        is None
    )
    ok = _FakeConn([[(1,)], [(5, "bitcoin", "bc1q", "hi", DT)]])
    note = store.add_note(
        "postgresql://x",
        1,
        chain="bitcoin",
        ref="bc1q",
        body="hi",
        connect=_connect(ok),
    )
    assert note["id"] == 5 and note["body"] == "hi"


def test_save_element_upserts():
    ok = _FakeConn([[(1,)], [("nodeA", "blue", False, None)]])
    el = store.save_element(
        "postgresql://x", 1, "nodeA", color="blue", connect=_connect(ok)
    )
    assert el == {"element_id": "nodeA", "color": "blue", "hidden": False, "note": None}
    assert "ON CONFLICT (case_id, element_id)" in ok.cur.executed[1][0]


# --- route tests --------------------------------------------------------------
def _client(*, database_url="postgresql://x"):
    app = create_app()
    cfg = Config()
    cfg.database_url = database_url
    app.dependency_overrides[get_config] = lambda: cfg
    app.dependency_overrides[get_connect] = (
        lambda: object()
    )  # never used (store stubbed)
    return app, TestClient(app)


def test_create_case_route(monkeypatch):
    monkeypatch.setattr(
        store,
        "create_case",
        lambda url, name, *, connect: {"case_id": 7, "name": name, "created_at": "t"},
    )
    _, client = _client()
    resp = client.post("/cases", json={"name": "Op Tornado"})
    assert resp.status_code == 201
    assert resp.json()["case_id"] == 7


def test_get_case_route_404(monkeypatch):
    monkeypatch.setattr(store, "get_case", lambda url, cid, *, connect: None)
    _, client = _client()
    assert client.get("/cases/123").status_code == 404


def test_delete_case_route_204_and_404(monkeypatch):
    monkeypatch.setattr(store, "delete_case", lambda url, cid, *, connect: True)
    _, client = _client()
    assert client.delete("/cases/1").status_code == 204
    monkeypatch.setattr(store, "delete_case", lambda url, cid, *, connect: False)
    assert client.delete("/cases/1").status_code == 404


def test_add_note_route_404_when_case_missing(monkeypatch):
    monkeypatch.setattr(
        store, "add_note", lambda url, cid, *, chain, ref, body, connect: None
    )
    _, client = _client()
    resp = client.post("/cases/9/notes", json={"body": "hi"})
    assert resp.status_code == 404


def test_save_element_route(monkeypatch):
    monkeypatch.setattr(
        store,
        "save_element",
        lambda url, cid, eid, *, color, hidden, note, connect: {
            "element_id": eid,
            "color": color,
            "hidden": hidden,
            "note": note,
        },
    )
    _, client = _client()
    resp = client.put("/cases/1/elements", json={"element_id": "nodeA", "color": "red"})
    assert resp.status_code == 200
    assert resp.json()["element_id"] == "nodeA"


def test_case_routes_503_without_database():
    _, client = _client(database_url=None)
    assert client.get("/cases").status_code == 503
    assert client.post("/cases", json={"name": "x"}).status_code == 503
