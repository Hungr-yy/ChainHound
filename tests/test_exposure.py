"""Exposure tests. Offline: synthetic transaction graphs, fake label lookup.

Builds canonical model objects directly (no network, no DB) and asserts the four
things that matter for Phase 2b: direction (in vs out), the hop bound, ring math
(value sums per category), and confidence degradation with distance.
"""
from chainhound.analysis.exposure import (
    DECAY_DEFAULT,
    compute_exposure,
    degrade_confidence,
)
from chainhound.labels.base import Label
from chainhound.models import Transaction, TxIO


def _tx(txid, ins, outs, asset="BTC"):
    return Transaction(
        txid=txid,
        chain="bitcoin",
        timestamp=0,
        inputs=[TxIO(address=a, value=v, asset=asset) for a, v in ins],
        outputs=[TxIO(address=a, value=v, asset=asset) for a, v in outs],
    )


class _FakeProvider:
    """Returns the synthetic txs an address appears in (as input or output)."""

    chain = "bitcoin"

    def __init__(self, txs):
        self._txs = txs

    def get_address_transactions(self, address, limit=50):
        return [
            t
            for t in self._txs
            if address in t.input_addresses
            or address in {o.address for o in t.outputs}
        ]

    def get_transaction(self, txid):
        return next((t for t in self._txs if t.txid == txid), None)


def _lookup(labels_by_addr):
    def lookup(chain, address):
        return labels_by_addr.get(address, [])

    return lookup


def _lbl(addr, name, category, confidence, source="tagpack"):
    return Label("bitcoin", addr, name, category, source, confidence)


# --- confidence degradation (unit) -------------------------------------------

def test_degrade_confidence_is_monotonic_and_preserves_direct():
    assert degrade_confidence("Near Certainty", 1) == "Near Certainty"
    assert degrade_confidence("Near Certainty", 2) == "Moderate"
    assert degrade_confidence("Near Certainty", 3) == "Low"
    assert degrade_confidence("High", 1) == "High"
    # never inflates and never rises with distance
    assert degrade_confidence("Moderate", 2) == "Low"


# --- direction ----------------------------------------------------------------

def test_direct_outbound_hit():
    txs = [_tx("t1", [("SEED", 1000)], [("EXCH", 700), ("SEED", 290)])]
    rep = compute_exposure(
        _FakeProvider(txs), "bitcoin", "SEED",
        label_lookup=_lookup({"EXCH": [_lbl("EXCH", "Acme", "exchange", "High")]}),
        hops=2, direction="out",
    )
    assert len(rep.findings) == 1
    f = rep.findings[0]
    assert (f.address, f.direction, f.distance, f.value) == ("EXCH", "out", 1, 700)
    assert f.exposure_confidence == "High"        # direct preserves the band
    assert f.path == ["SEED", "EXCH"]


def test_inbound_and_outbound_are_distinct():
    txs = [
        _tx("tin", [("MIX", 800)], [("SEED", 760)]),       # funds FROM MIX to SEED
        _tx("tout", [("SEED", 760)], [("EXCH", 700)]),     # funds FROM SEED to EXCH
    ]
    rep = compute_exposure(
        _FakeProvider(txs), "bitcoin", "SEED",
        label_lookup=_lookup({
            "MIX": [_lbl("MIX", "Mixer", "mixer", "High")],
            "EXCH": [_lbl("EXCH", "Acme", "exchange", "High")],
        }),
        hops=1, direction="both",
    )
    dirs = {f.address: f.direction for f in rep.findings}
    assert dirs == {"MIX": "in", "EXCH": "out"}
    assert next(f for f in rep.findings if f.address == "MIX").value == 760


# --- hop bound + degradation over distance -----------------------------------

def _chain_graph():
    # SEED -> A -> BAD(d2) -> DEEP(d3), all outbound
    return [
        _tx("t1", [("SEED", 1000)], [("A", 900), ("SEED", 90)]),
        _tx("t2", [("A", 900)], [("BAD", 850), ("A", 40)]),
        _tx("t3", [("BAD", 850)], [("DEEP", 800)]),
    ]


_CHAIN_LABELS = {
    "BAD": [_lbl("BAD", "Lazarus", "sanctioned", "Near Certainty", "ofac")],
    "DEEP": [_lbl("DEEP", "Lazarus2", "sanctioned", "Near Certainty", "ofac")],
}


def test_indirect_hit_degrades_with_distance():
    rep = compute_exposure(
        _FakeProvider(_chain_graph()), "bitcoin", "SEED",
        label_lookup=_lookup(_CHAIN_LABELS), hops=3, direction="out",
    )
    bad = next(f for f in rep.findings if f.address == "BAD")
    assert bad.distance == 2
    assert bad.path == ["SEED", "A", "BAD"]
    assert bad.value == 850                        # bottleneck min(900, 850)
    assert bad.base_confidence == "Near Certainty"
    assert bad.exposure_confidence == "Moderate"   # NC @ distance 2
    deep = next(f for f in rep.findings if f.address == "DEEP")
    assert deep.distance == 3
    assert deep.exposure_confidence == "Low"       # NC @ distance 3


def test_near_miss_just_past_the_bound_does_not_register():
    rep = compute_exposure(
        _FakeProvider(_chain_graph()), "bitcoin", "SEED",
        label_lookup=_lookup(_CHAIN_LABELS), hops=2, direction="out",
    )
    addrs = {f.address for f in rep.findings}
    assert "BAD" in addrs            # distance 2, within bound
    assert "DEEP" not in addrs       # distance 3, just past the bound


# --- high fan-out bound -------------------------------------------------------

def test_high_fanout_node_is_not_expanded_through():
    fan = [(f"c{i}", 100) for i in range(6)] + [("BADHIDDEN", 100)]
    txs = [
        _tx("t1", [("SEED", 10000)], [("HUB", 9000), ("SEED", 900)]),
        _tx("t2", [("HUB", 9000)], fan),     # HUB fans out to 7 > max_fanout
    ]
    rep = compute_exposure(
        _FakeProvider(txs), "bitcoin", "SEED",
        label_lookup=_lookup({"BADHIDDEN": [_lbl("BADHIDDEN", "x", "sanctioned", "Near Certainty")]}),
        hops=3, direction="out", max_fanout=3,
    )
    assert "BADHIDDEN" not in {f.address for f in rep.findings}
    assert rep.truncated is True


# --- ring aggregation ---------------------------------------------------------

def test_rings_separate_in_out_and_sum_direct_value():
    txs = [
        _tx("o1", [("SEED", 1000)], [("EX1", 600), ("SEED", 390)]),
        _tx("o2", [("SEED", 500)], [("EX2", 480)]),
        _tx("i1", [("SANC", 2000)], [("SEED", 1950)]),
    ]
    rep = compute_exposure(
        _FakeProvider(txs), "bitcoin", "SEED",
        label_lookup=_lookup({
            "EX1": [_lbl("EX1", "Ex One", "exchange", "High")],
            "EX2": [_lbl("EX2", "Ex Two", "exchange", "High")],
            "SANC": [_lbl("SANC", "Lazarus", "sanctioned", "Near Certainty", "ofac")],
        }),
        hops=1, direction="both",
    )
    rings = {(r.category, r.direction): r for r in rep.rings}
    ex_out = rings[("exchange", "out")]
    assert ex_out.counterparties == 2
    assert ex_out.direct_value == 1080            # 600 + 480
    assert ex_out.strongest_confidence == "High"
    sanc_in = rings[("sanctioned", "in")]
    assert sanc_in.counterparties == 1
    assert sanc_in.direct_value == 1950
    assert sanc_in.strongest_confidence == "Near Certainty"
    assert ("exchange", "in") not in rings        # no inbound exchange


def test_rings_separate_by_asset():
    # Same category + direction, two assets -> two distinct rings (units differ).
    txs = [
        _tx("o1", [("SEED", 1000)], [("USDCEX", 600)], asset="USDC"),
        _tx("o2", [("SEED", 2_000)], [("ETHEX", 900)], asset="ETH"),
    ]
    rep = compute_exposure(
        _FakeProvider(txs), "ethereum", "SEED",
        label_lookup=_lookup({
            "USDCEX": [_lbl("USDCEX", "Usdc Ex", "exchange", "High")],
            "ETHEX": [_lbl("ETHEX", "Eth Ex", "exchange", "High")],
        }),
        hops=1, direction="out",
    )
    rings = {(r.category, r.direction, r.asset): r for r in rep.rings}
    assert ("exchange", "out", "USDC") in rings
    assert ("exchange", "out", "ETH") in rings
    assert rings[("exchange", "out", "USDC")].direct_value == 600
    assert {f.asset for f in rep.findings} == {"USDC", "ETH"}


def test_decay_default_is_documented_constant():
    assert 0 < DECAY_DEFAULT < 1
