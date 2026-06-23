"""Monitoring: detectors (pure), the run glue, routes, and one poller tick.
All offline — fake providers and a recording fake store.
"""

from fastapi.testclient import TestClient

from chainhound.config import Config
from chainhound.models import AddressSummary
from chainhound_server import detectors, monitor, poller, store
from chainhound_server.app import create_app
from chainhound_server.deps import get_config, get_connect, get_provider_factory


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


# --- detectors (pure) ---------------------------------------------------------
def test_first_observation_is_silent():
    assert detectors.evaluate(None, _summary(tx_count=5)) == []


def test_new_activity_fires_on_tx_count_increase():
    base = detectors.snapshot(_summary(tx_count=3))
    out = detectors.evaluate(base, _summary(tx_count=5))
    assert [f.detector for f in out] == ["new-activity"]
    f = out[0]
    assert f.confidence == "Near Certainty"
    assert f.detail["delta"] == 2 and f.detail["tx_count"] == 5


def test_no_change_fires_nothing():
    base = detectors.snapshot(_summary(tx_count=3, total_received=100))
    assert detectors.evaluate(base, _summary(tx_count=3, total_received=100)) == []


def test_large_inflow_and_outflow_gated_on_threshold():
    base = detectors.snapshot(_summary(tx_count=1, total_received=0, total_sent=0))
    cur = _summary(tx_count=2, total_received=500, total_sent=400)
    # No threshold -> only new-activity.
    assert {f.detector for f in detectors.evaluate(base, cur)} == {"new-activity"}
    # With threshold 300 -> inflow (500) and outflow (400) both clear it.
    got = {f.detector for f in detectors.evaluate(base, cur, large_threshold=300)}
    assert got == {"new-activity", "large-inflow", "large-outflow"}
    # Threshold above the deltas -> inflow/outflow suppressed.
    got = {f.detector for f in detectors.evaluate(base, cur, large_threshold=1000)}
    assert got == {"new-activity"}


# --- recording fake store -----------------------------------------------------
class _FakeStore:
    """Captures alerts and baseline updates without a database."""

    def __init__(self, watches):
        self.watches = watches
        self.alerts = []
        self.baselines = {}

    def list_watches(self, url, *, connect):
        return self.watches

    def record_alert(self, url, watch_id, detector, detail, *, connect):
        row = {
            "id": len(self.alerts) + 1,
            "watch_id": watch_id,
            "detector": detector,
            "detail": detail,
        }
        self.alerts.append(row)
        return row

    def update_watch_baseline(self, url, watch_id, baseline, *, connect):
        self.baselines[watch_id] = baseline


class _Provider:
    def __init__(self, summary):
        self._summary = summary

    def get_address_summary(self, address):
        return self._summary


def test_run_watch_writes_alert_and_rolls_baseline(monkeypatch):
    fake = _FakeStore([])
    monkeypatch.setattr(monitor, "store", fake)
    watch = {
        "id": 7,
        "chain": "bitcoin",
        "address": "bc1qfoo",
        "baseline": detectors.snapshot(_summary(tx_count=1)),
    }
    written = monitor.run_watch(
        "postgresql://x", watch, _Provider(_summary(tx_count=4)), connect=object()
    )
    assert [a["detector"] for a in written] == ["new-activity"]
    assert written[0]["detail"]["confidence"] == "Near Certainty"
    assert fake.baselines[7]["tx_count"] == 4  # baseline rolled forward


def test_run_watch_first_check_is_silent(monkeypatch):
    fake = _FakeStore([])
    monkeypatch.setattr(monitor, "store", fake)
    watch = {"id": 1, "chain": "bitcoin", "address": "bc1qfoo", "baseline": None}
    assert (
        monitor.run_watch(
            "postgresql://x", watch, _Provider(_summary(tx_count=9)), connect=object()
        )
        == []
    )
    assert fake.baselines[1]["tx_count"] == 9  # baseline established


def test_run_all_isolates_a_failing_watch(monkeypatch):
    fake = _FakeStore(
        [
            {
                "id": 1,
                "chain": "bitcoin",
                "address": "good",
                "baseline": detectors.snapshot(_summary(tx_count=0)),
            },
            {"id": 2, "chain": "explode", "address": "bad", "baseline": None},
        ]
    )
    monkeypatch.setattr(monitor, "store", fake)

    def provider_for(chain):
        if chain == "explode":
            raise RuntimeError("connector down")
        return _Provider(_summary(tx_count=3))

    summary = monitor.run_all(
        "postgresql://x", provider_for=provider_for, connect=object()
    )
    assert summary["checked"] == 2 and summary["fired"] == 1  # bad watch isolated


# --- routes -------------------------------------------------------------------
def _client(*, database_url="postgresql://x"):
    app = create_app()
    cfg = Config()
    cfg.database_url = database_url
    app.dependency_overrides[get_config] = lambda: cfg
    app.dependency_overrides[get_connect] = lambda: object()
    app.dependency_overrides[get_provider_factory] = lambda: (
        lambda _cfg, _chain: _Provider(_summary(tx_count=2))
    )
    return TestClient(app)


def test_add_watch_route(monkeypatch):
    monkeypatch.setattr(
        store,
        "add_watch",
        lambda url, chain, address, *, case_id, connect: {
            "id": 1,
            "chain": chain,
            "address": address,
            "case_id": case_id,
        },
    )
    resp = _client().post("/watches", json={"chain": "bitcoin", "address": "bc1qfoo"})
    assert resp.status_code == 201 and resp.json()["address"] == "bc1qfoo"


def test_delete_watch_404(monkeypatch):
    monkeypatch.setattr(store, "delete_watch", lambda url, wid, *, connect: False)
    assert _client().delete("/watches/9").status_code == 404


def test_run_monitor_route(monkeypatch):
    monkeypatch.setattr(
        monitor,
        "run_all",
        lambda url, *, provider_for, large_threshold, connect: {
            "checked": 3,
            "fired": 1,
            "alerts": [{"detector": "new-activity"}],
        },
    )
    resp = _client().post("/monitor/run")
    assert resp.status_code == 200 and resp.json()["fired"] == 1


def test_monitor_routes_503_without_database():
    client = _client(database_url=None)
    assert client.get("/watches").status_code == 503
    assert client.post("/monitor/run").status_code == 503


# --- poller (one deterministic tick) ------------------------------------------
def test_poller_runs_one_iteration(monkeypatch):
    calls = {"polled": 0, "slept": []}
    monkeypatch.setattr(poller.config, "load", lambda: Config(), raising=False)

    cfg = Config()
    cfg.database_url = "postgresql://x"
    monkeypatch.setattr(poller.config, "load", lambda: cfg)
    monkeypatch.setattr(
        poller,
        "poll_once",
        lambda c, *, large_threshold=None: calls.__setitem__(
            "polled", calls["polled"] + 1
        )
        or {"checked": 1, "fired": 0},
    )
    # should_continue True once, then False -> exactly one poll, no sleep.
    gen = iter([True, False, False])
    poller.run(
        interval=1.0,
        sleep=lambda s: calls["slept"].append(s),
        should_continue=lambda: next(gen),
    )
    assert calls["polled"] == 1 and calls["slept"] == []
