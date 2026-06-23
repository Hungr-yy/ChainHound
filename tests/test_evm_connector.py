"""EVM connector normalization. Offline: a fake transport feeds captured JSON.

Never hits a live API. Proves an Etherscan-compatible response normalizes into the
canonical models AND that compute_exposure works on EVM-normalized data with no
exposure changes (the Phase 3 acceptance test, at the unit level).
"""
import json
from pathlib import Path

from chainhound.analysis.exposure import compute_exposure
from chainhound.connectors.evm import EvmProvider
from chainhound.labels.base import Label
from chainhound.models import AddressType

_FIX = Path(__file__).parent / "fixtures" / "evm"
SEED = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
CONTRACT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
CP_IN = "0xa9801117912b5849867378dabe8e12c725f7bf28"
CP_OUT = "0x1111111111111111111111111111111111111111"
FAIL_TO = "0x2222222222222222222222222222222222222222"
ONE_ETH = 1_000_000_000_000_000_000


def _fixtures():
    return {p.stem: json.loads(p.read_text()) for p in _FIX.glob("*.json")}


def _transport(fix):
    def t(params):
        action = params.get("action")
        if action == "balance":
            return fix["balance"]
        if action == "txlist":
            return fix["txlist"]
        if action == "eth_getCode":
            is_contract = params.get("address", "").lower() == CONTRACT
            return fix["getcode_contract"] if is_contract else fix["getcode_eoa"]
        if action == "eth_getTransactionByHash":
            return fix["gettx"]
        raise AssertionError(f"unexpected action {action!r}")

    return t


def _provider():
    return EvmProvider(transport=_transport(_fixtures()))


def test_get_address_transactions_skips_failed_and_normalizes_native():
    txs = _provider().get_address_transactions(SEED)
    # the isError=1 row is dropped; two successful transfers remain
    assert len(txs) == 2
    by_to = {t.outputs[0].address: t for t in txs}
    assert FAIL_TO not in by_to
    out = by_to[CP_OUT]
    assert out.chain == "ethereum"
    assert out.inputs[0].address == SEED
    assert out.outputs[0].value == 500_000_000_000_000_000
    assert out.inputs[0].value == 500_000_000_000_000_000


def test_address_summary_balance_type_and_value_sums():
    s = _provider().get_address_summary(SEED)
    assert s.balance == 5_690_864_439_481_747_771
    assert s.address_type == AddressType.EOA          # 0xef0100… delegated EOA
    assert s.total_received == ONE_ETH                # only the successful inbound
    assert s.total_sent == 500_000_000_000_000_000    # failed 9 ETH excluded


def test_contract_code_classifies_as_contract():
    s = _provider().get_address_summary(CONTRACT)
    assert s.address_type == AddressType.CONTRACT


def test_get_transaction_parses_hex_native_value():
    tx = _provider().get_transaction(
        "0xaaa0000000000000000000000000000000000000000000000000000000000001"
    )
    assert tx.inputs[0].address == CP_IN
    assert tx.outputs[0].address == SEED
    assert tx.outputs[0].value == ONE_ETH             # 0xde0b6b3a7640000


def test_get_spending_tx_is_none_on_evm():
    assert _provider().get_spending_tx("0xanything", 0) is None


def test_exposure_works_on_evm_with_zero_engine_changes():
    # The load-bearing acceptance test: compute_exposure consumes EVM-normalized
    # txs unchanged. SEED sent 0.5 ETH to a sanctioned CP_OUT -> outbound ring.
    def lookup(chain, address):
        if address == CP_OUT:
            return [Label(chain, address, "Bad Actor", "sanctioned", "ofac", "Near Certainty")]
        return []

    rep = compute_exposure(_provider(), "ethereum", SEED,
                           label_lookup=lookup, hops=1, direction="out")
    f = next(f for f in rep.findings if f.address == CP_OUT)
    assert f.direction == "out"
    assert f.value == 500_000_000_000_000_000
    assert f.exposure_confidence == "Near Certainty"
    ring = next(r for r in rep.rings if r.category == "sanctioned" and r.direction == "out")
    assert ring.direct_value == 500_000_000_000_000_000
