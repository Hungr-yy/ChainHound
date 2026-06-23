"""Inferred cross-chain matcher. Pure scoring, offline.

The novel deterministic heuristic: match an outflow on chain A to an inflow on
chain B by asset-equivalence + amount-within-fee-tolerance + time-window +
known-bridge, with confidence scaled by how tight the match is.
"""
from chainhound.analysis.crosschain import (
    KNOWN_BRIDGES,
    Transfer,
    infer_links,
    same_peg,
    score_match,
)

BTC8 = 8
# Pando hack hop (CASES.md C1): BTC 53.5 -> RENBTC 53.39 (both 8-decimal).
SRC = Transfer("bitcoin", "btctx", "bc1qr5kg", "BTC", 5_350_000_000, BTC8, 1_000_000)
DST = Transfer("ethereum", "ethtx", "0xd3f04", "RENBTC", 5_339_000_000, BTC8, 1_000_600)


def test_same_peg_groups_btc_pegged_assets():
    assert same_peg("BTC", "RENBTC") and same_peg("BTC", "WBTC")
    assert not same_peg("BTC", "ETH")
    assert same_peg("ETH", "WETH")


def test_btc_to_renbtc_within_tolerance_is_confident():
    m = score_match(SRC, DST)
    assert m is not None
    band, matched = m
    assert band in ("High", "Near Certainty", "Moderate")  # tight amount + prompt
    assert matched["rel_delta"] < 0.01            # 0.2% bridge fee
    assert matched["time_delta_s"] == 600
    assert matched["asset_src"] == "BTC" and matched["asset_dst"] == "RENBTC"


def test_different_peg_group_does_not_match():
    eth_dst = Transfer("ethereum", "x", "0x", "ETH", 5_339_000_000, 18, 1_000_600)
    assert score_match(SRC, eth_dst) is None       # gate: not asset-equivalent


def test_amount_past_fee_tolerance_does_not_match():
    far = Transfer("ethereum", "x", "0x", "RENBTC", 4_000_000_000, BTC8, 1_000_600)
    assert score_match(SRC, far) is None           # 25% off, beyond fee_tol


def test_destination_before_source_or_outside_window_does_not_match():
    before = Transfer("ethereum", "x", "0x", "RENBTC", 5_339_000_000, BTC8, 999_000)
    assert score_match(SRC, before) is None
    too_late = Transfer("ethereum", "x", "0x", "RENBTC", 5_339_000_000, BTC8, 1_000_000 + 999_999)
    assert score_match(SRC, too_late) is None


def test_known_bridge_touch_raises_confidence():
    # seed a known bridge address on the destination side
    bridge_dst = Transfer("ethereum", "x", next(iter(KNOWN_BRIDGES["thorchain"]["ethereum"])),
                          "RENBTC", 5_339_000_000, BTC8, 1_000_600)
    plain = score_match(SRC, DST)[0]
    bridged = score_match(SRC, bridge_dst)[0]
    rank = {"Low": 0, "Moderate": 1, "High": 2, "Near Certainty": 3}
    assert rank[bridged] >= rank[plain]
    assert rank[bridged] >= rank["High"]           # known bridge + tight + prompt


def test_infer_links_returns_best_first_inferred_method():
    far = Transfer("ethereum", "x", "0x", "RENBTC", 4_000_000_000, BTC8, 1_000_600)
    links = infer_links(SRC, [far, DST])
    assert links and links[0].dst_txid == "ethtx"
    assert all(l.method == "inferred" for l in links)
    assert links[0].matched_on["rel_delta"] < 0.01
