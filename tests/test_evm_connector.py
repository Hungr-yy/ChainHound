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
INTERNAL_SENDER = "0xff7e80f18339d64ef80f8e787aad8fc5815ad323"
TOKEN_SENDER = "0x12e0e83c42502668eac983b9933b81b3d20ab840"
USDC_RECIP = "0x3333333333333333333333333333333333333333"
NFT_SENDER = "0x4444444444444444444444444444444444444444"
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
        if action == "tokentx":
            return fix["tokentx"]
        if action == "tokennfttx":
            return fix["tokennfttx"]
        if action == "txlistinternal":
            return fix["internal"]
        raise AssertionError(f"unexpected action {action!r}")

    return t


def _provider():
    return EvmProvider(transport=_transport(_fixtures()))


def test_get_address_transactions_skips_failed_and_normalizes_native():
    # SEED's successful outbound native (ETH) transfers; the isError=1 row dropped
    eth_out = {
        t.outputs[0].address: t
        for t in _provider().get_address_transactions(SEED)
        if t.outputs and t.outputs[0].asset == "ETH" and t.inputs[0].address == SEED
    }
    assert CP_OUT in eth_out
    assert FAIL_TO not in eth_out                       # reverted top-level tx
    assert eth_out[CP_OUT].outputs[0].value == 500_000_000_000_000_000


def test_internal_value_transfers_included_and_failed_skipped():
    txs = _provider().get_address_transactions(SEED)
    internal = [t for t in txs if t.inputs[0].address == INTERNAL_SENDER]
    assert len(internal) == 1                           # the value-bearing call
    assert internal[0].outputs[0].address == SEED
    assert internal[0].outputs[0].value == 58_731_976_624_673
    # the reverted internal call (from SEED) produced no transfer
    assert all(
        t.outputs[0].address != "0x5555555555555555555555555555555555555555"
        for t in txs
    )


def test_token_transfers_normalize_with_asset():
    by_asset = {}
    for t in _provider().get_address_transactions(SEED):
        by_asset.setdefault(t.outputs[0].asset, []).append(t)
    assert {"MOODENG", "USDC", "BAYC"} <= set(by_asset)
    moo = by_asset["MOODENG"][0]                 # ERC-20 inbound
    assert moo.inputs[0].address == TOKEN_SENDER
    assert moo.outputs[0].address == SEED
    assert moo.outputs[0].value == 8_888_000_000_000
    assert by_asset["BAYC"][0].outputs[0].value == 1   # one NFT, not a divisible value


def test_evm_exposure_rings_separate_native_and_token():
    def lookup(chain, address):
        if address in (CP_OUT, USDC_RECIP):
            return [Label(chain, address, "Sink", "exchange", "tagpack", "High")]
        return []

    rep = compute_exposure(_provider(), "ethereum", SEED, label_lookup=lookup,
                           hops=1, direction="out", max_fanout=10000)
    rings = {(r.category, r.direction, r.asset): r for r in rep.rings}
    assert rings[("exchange", "out", "ETH")].direct_value == 500_000_000_000_000_000
    assert rings[("exchange", "out", "USDC")].direct_value == 1_000_000


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
