"""Offline tests for the FastAPI query layer.

No live network: fake providers are injected via ``app.dependency_overrides``
(matching the engine's dependency-injection ethos), and canonical model objects
are built directly. Mirrors the fake-connection style used elsewhere in tests/.
"""

import pytest
from fastapi.testclient import TestClient

from chainhound.config import Config
from chainhound.models import AddressSummary, Transaction, TxIO
from chainhound_server.app import create_app
from chainhound_server.deps import (
    get_config,
    get_label_lookup_factory,
    get_provider_factory,
)


# --- fakes --------------------------------------------------------------------
class _FakeProvider:
    """A canned provider; only the methods a given route touches are populated."""

    def __init__(self, *, chain="bitcoin", model="utxo", summary=None, txs=None):
        self.chain = chain
        self.model = model
        self._summary = summary
        self._txs = {t.txid: t for t in (txs or [])}
        self._addr_txs = txs or []

    def get_address_summary(self, address):
        return self._summary

    def get_transaction(self, txid):
        return self._txs.get(txid)

    def get_spending_tx(self, txid, vout):
        return None

    def get_address_transactions(self, address, limit=50):
        return self._addr_txs


def _client(provider=None, *, database_url=None, label_lookup=None):
    app = create_app()
    cfg = Config()
    cfg.database_url = database_url
    app.dependency_overrides[get_config] = lambda: cfg
    if provider is not None:
        app.dependency_overrides[get_provider_factory] = lambda: (
            lambda _cfg, _chain: provider
        )
    if label_lookup is not None:
        app.dependency_overrides[get_label_lookup_factory] = lambda: (
            lambda _cfg: label_lookup
        )
    return TestClient(app)


# --- health -------------------------------------------------------------------
def test_health_ok():
    assert _client().get("/health").json() == {"status": "ok"}


# --- triage -------------------------------------------------------------------
def test_triage_happy_path():
    summary = AddressSummary(address="bc1qfoo", chain="bitcoin", tx_count=3)
    client = _client(_FakeProvider(summary=summary))
    resp = client.get("/triage", params={"address": "bc1qfoo", "chain": "bitcoin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True and body["address"] == "bc1qfoo"
    assert "service_flags" in body


def test_triage_not_found_is_404():
    client = _client(_FakeProvider(summary=None))
    resp = client.get("/triage", params={"address": "bc1qmissing"})
    assert resp.status_code == 404


def test_triage_unknown_chain_is_400():
    # No provider override -> real factory runs and rejects the chain.
    client = _client()
    resp = client.get("/triage", params={"address": "x", "chain": "dogecoin"})
    assert resp.status_code == 400


# --- trace --------------------------------------------------------------------
def test_trace_happy_path():
    tx = Transaction(
        txid="tx0",
        chain="bitcoin",
        timestamp=1_000,
        inputs=[TxIO(address="bc1qin", value=100)],
        outputs=[TxIO(address="bc1qpay", value=40), TxIO(address="bc1qchg", value=58)],
    )
    client = _client(_FakeProvider(txs=[tx]))
    resp = client.get("/trace", params={"txid": "tx0", "hops": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert any(n["id"] == "tx0" for n in body["nodes"])
    assert body["edges"]


def test_trace_account_model_is_422():
    client = _client(_FakeProvider(chain="ethereum", model="account"))
    resp = client.get("/trace", params={"txid": "0xabc"})
    assert resp.status_code == 422


# --- peel ---------------------------------------------------------------------
def test_peel_single_tx_is_not_a_peel_chain():
    tx = Transaction(
        txid="tx0",
        chain="bitcoin",
        timestamp=1_000,
        inputs=[TxIO(address="bc1qin", value=100)],
        outputs=[TxIO(address="bc1qpay", value=40), TxIO(address="bc1qchg", value=58)],
    )
    client = _client(_FakeProvider(txs=[tx]))
    resp = client.get("/peel", params={"txid": "tx0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_peel_chain"] is False
    assert "hops" in body and "cash_out" in body


# --- exposure -----------------------------------------------------------------
def test_exposure_without_database_is_503():
    client = _client(_FakeProvider(), database_url=None)
    resp = client.get("/exposure", params={"address": "bc1qfoo"})
    assert resp.status_code == 503


def test_exposure_happy_path_empty():
    # DB present + injected label hook; a provider with no txs -> empty report.
    client = _client(
        _FakeProvider(txs=[]),
        database_url="postgresql://fake",
        label_lookup=lambda chain, addr: [],
    )
    resp = client.get("/exposure", params={"address": "bc1qfoo", "hops": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["seed"] == "bc1qfoo"
    assert body["findings"] == [] and body["rings"] == []


# --- labels -------------------------------------------------------------------
def test_labels_without_database_is_503():
    resp = _client(database_url=None).get("/labels", params={"address": "bc1qfoo"})
    assert resp.status_code == 503


# --- crosschain ---------------------------------------------------------------
def test_crosschain_inferred_missing_fields_is_400():
    client = _client(_FakeProvider())
    resp = client.post(
        "/crosschain",
        json={
            "mode": "inferred",
            "src_chain": "bitcoin",
            "src_txid": "btctx",
        },
    )
    assert resp.status_code == 400


def test_crosschain_inferred_matches_dst_inflow():
    # Pando C1 hop, mirroring tests/test_crosschain.py: BTC 53.5 -> RENBTC 53.39.
    dst_tx = Transaction(
        txid="ethtx",
        chain="ethereum",
        timestamp=1_000_600,
        inputs=[],
        outputs=[TxIO(address="0xd3f04", value=5_339_000_000, asset="RENBTC")],
    )
    src_tx = Transaction(
        txid="btctx",
        chain="bitcoin",
        timestamp=1_000_000,
        inputs=[TxIO(address="bc1qr5kg", value=5_350_000_000)],
        outputs=[TxIO(address="0xd3f04", value=5_350_000_000)],
    )
    provider = _FakeProvider(chain="ethereum", txs=[dst_tx])
    provider._txs["btctx"] = src_tx  # src timestamp lookup
    client = _client(provider)
    resp = client.post(
        "/crosschain",
        json={
            "mode": "inferred",
            "src_chain": "bitcoin",
            "src_txid": "btctx",
            "src_asset": "BTC",
            "src_amount": 53.5,
            "dst_chain": "ethereum",
            "dst_address": "0xd3f04",
        },
    )
    assert resp.status_code == 200
    links = resp.json()
    assert links and links[0]["dst_txid"] == "ethtx"
    assert links[0]["method"] == "inferred"


def test_crosschain_api_mode_uses_bridge_explorer(monkeypatch):
    from chainhound.analysis.crosschain import CrossChainLink

    fake_link = CrossChainLink(
        src_chain="bitcoin",
        src_txid="btctx",
        dst_chain="ethereum",
        dst_txid="ethtx",
        bridge="thorchain",
        method="api",
        confidence="Near Certainty",
        matched_on={},
    )

    class _FakeBridge:
        def lookup(self, src_chain, src_txid):
            return fake_link

    monkeypatch.setattr("chainhound.bridges.ThorchainMidgard", _FakeBridge)
    client = _client(_FakeProvider())
    resp = client.post(
        "/crosschain",
        json={
            "mode": "api",
            "src_chain": "bitcoin",
            "src_txid": "btctx",
        },
    )
    assert resp.status_code == 200
    links = resp.json()
    assert links and links[0]["method"] == "api"
