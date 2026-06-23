"""Court export: raw on-chain evidence only — no attribution. All offline."""

from fastapi.testclient import TestClient

from chainhound.config import Config
from chainhound.models import AddressSummary, Transaction, TxIO
from chainhound_server import export, store
from chainhound_server.app import create_app
from chainhound_server.deps import get_config, get_connect, get_provider_factory


# --- classify_ref -------------------------------------------------------------
def test_classify_ref_by_format():
    assert export.classify_ref("bc1qfoo") == ("bitcoin", "address")
    assert export.classify_ref("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == (
        "bitcoin",
        "address",
    )
    assert export.classify_ref("a" * 64) == ("bitcoin", "tx")
    assert export.classify_ref("0x" + "b" * 40) == ("ethereum", "address")
    assert export.classify_ref("0x" + "c" * 64) == ("ethereum", "tx")


# --- gather_references --------------------------------------------------------
def test_gather_dedups_and_honors_note_chain():
    case = {
        "elements": [
            {"element_id": "bc1qfoo"},
            {"element_id": "a" * 64},
            {"element_id": "bc1qfoo"},
        ],
        "notes": [
            {"ref": "0x" + "d" * 40, "chain": "ethereum"},
            {"ref": "bc1qfoo", "chain": None},  # dup of an element
            {"ref": None},  # ignored
        ],
    }
    refs = export.gather_references(case)
    keys = {(r["chain"], r["ref"], r["kind"]) for r in refs}
    assert keys == {
        ("bitcoin", "bc1qfoo", "address"),
        ("bitcoin", "a" * 64, "tx"),
        ("ethereum", "0x" + "d" * 40, "address"),
    }


# --- build_export -------------------------------------------------------------
def test_build_export_shape_and_disclaimer():
    bundle = export.build_export(
        {"case_id": 1, "name": "Op X"},
        [{"address": "bc1q"}],
        [{"txid": "t"}],
        [],
        generated_at="2026-06-24T00:00:00Z",
    )
    meta = bundle["court_export"]
    assert meta["case_id"] == 1 and meta["entity_count"] == 2
    assert "no labels" in meta["disclaimer"].lower()
    assert bundle["addresses"] and bundle["transactions"]


# --- orchestrator: attribution is stripped ------------------------------------
class _Provider:
    def __init__(self, summary=None, tx=None):
        self._summary = summary
        self._tx = tx

    def get_address_summary(self, address):
        return self._summary

    def get_transaction(self, txid):
        return self._tx


def test_court_export_strips_labels_and_resolves(monkeypatch):
    case = {
        "case_id": 5,
        "name": "Op Tornado",
        "elements": [{"element_id": "bc1qfoo"}, {"element_id": "f" * 64}],
        "notes": [],
    }
    monkeypatch.setattr(store, "get_case", lambda url, cid, *, connect: case)

    summary = AddressSummary(
        address="bc1qfoo",
        chain="bitcoin",
        balance=10,
        tx_count=2,
        labels=["OFAC SDN: Lazarus", "exchange:Binance"],  # MUST NOT appear
    )
    tx = Transaction(
        txid="f" * 64,
        chain="bitcoin",
        timestamp=1000,
        inputs=[TxIO(address="bc1qin", value=100)],
        outputs=[TxIO(address="bc1qout", value=99)],
    )
    provider = _Provider(summary=summary, tx=tx)

    bundle = export.court_export(
        "postgresql://x", 5, provider_for=lambda chain: provider, connect=object()
    )
    assert bundle["court_export"]["case_name"] == "Op Tornado"
    assert bundle["court_export"]["entity_count"] == 2
    # The raw address evidence carries no attribution.
    addr = bundle["addresses"][0]
    assert addr["address"] == "bc1qfoo" and "labels" not in addr
    # Raw tx preserved with inputs/outputs.
    assert bundle["transactions"][0]["txid"] == "f" * 64
    assert bundle["transactions"][0]["outputs"][0]["value"] == 99


def test_court_export_records_unresolved_and_missing_case(monkeypatch):
    monkeypatch.setattr(store, "get_case", lambda url, cid, *, connect: None)
    assert (
        export.court_export(
            "postgresql://x", 99, provider_for=lambda c: _Provider(), connect=object()
        )
        is None
    )

    case = {
        "case_id": 1,
        "name": "n",
        "elements": [{"element_id": "bc1qghost"}],
        "notes": [],
    }
    monkeypatch.setattr(store, "get_case", lambda url, cid, *, connect: case)
    bundle = export.court_export(
        "postgresql://x",
        1,
        provider_for=lambda c: _Provider(summary=None),
        connect=object(),
    )
    assert bundle["addresses"] == [] and bundle["unresolved"][0]["ref"] == "bc1qghost"


# --- route --------------------------------------------------------------------
def _client(*, database_url="postgresql://x"):
    app = create_app()
    cfg = Config()
    cfg.database_url = database_url
    app.dependency_overrides[get_config] = lambda: cfg
    app.dependency_overrides[get_connect] = lambda: object()
    app.dependency_overrides[get_provider_factory] = lambda: (
        lambda _cfg, _chain: _Provider()
    )
    return TestClient(app)


def test_export_route_downloads_attachment(monkeypatch):
    monkeypatch.setattr(
        export,
        "court_export",
        lambda url, cid, *, provider_for, connect, generated_at: {
            "court_export": {"case_id": cid, "disclaimer": "Raw on-chain data only."},
            "addresses": [],
            "transactions": [],
            "unresolved": [],
        },
    )
    resp = _client().get("/cases/7/export")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "case-7-court-export.json" in resp.headers["content-disposition"]
    assert resp.json()["court_export"]["case_id"] == 7


def test_export_route_404_and_503(monkeypatch):
    monkeypatch.setattr(
        export,
        "court_export",
        lambda url, cid, *, provider_for, connect, generated_at: None,
    )
    assert _client().get("/cases/9/export").status_code == 404
    assert _client(database_url=None).get("/cases/9/export").status_code == 503
