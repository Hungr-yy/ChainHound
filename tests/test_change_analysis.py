"""Tests for the change-analysis suite. Pure logic, no network."""
from chainhound.models import Transaction, TxIO, AddressType
from chainhound.heuristics.change_analysis import analyze_change
from chainhound.heuristics.coinjoin import detect_coinjoin
from chainhound.heuristics.clustering import cluster_addresses


def _io(addr, value, atype=AddressType.P2WPKH, **kw):
    return TxIO(address=addr, value=value, address_type=atype, **kw)


def test_self_change_is_near_certain():
    tx = Transaction(
        txid="t1", chain="bitcoin", timestamp=0,
        inputs=[_io("bc1qAAA", 300_000)],
        outputs=[_io("bc1qBBB", 100_000), _io("bc1qAAA", 199_000)],
    )
    v = analyze_change(tx)
    assert v.output_index == 1            # the reused input address
    assert v.band == "Near Certainty"


def test_optimal_change_picks_smaller_than_min_input():
    # min input 200_000; only output 1 (50_000) is below it -> change
    tx = Transaction(
        txid="t2", chain="bitcoin", timestamp=0,
        inputs=[_io("bc1qA", 200_000), _io("bc1qB", 200_000)],
        outputs=[_io("bc1qPAY", 250_000), _io("bc1qCHG", 50_000)],
    )
    v = analyze_change(tx)
    assert v.output_index == 1


def test_address_type_change_matches_inputs():
    # inputs are bech32; payment goes to a legacy "1..." so change is the bech32 out
    tx = Transaction(
        txid="t3", chain="bitcoin", timestamp=0,
        inputs=[_io("bc1qA", 500_000)],
        outputs=[
            _io("1PaymentLegacyAddr", 300_000, AddressType.P2PKH),
            _io("bc1qChange", 199_000, AddressType.P2WPKH),
        ],
    )
    v = analyze_change(tx)
    assert v.output_index == 1


def test_round_payment_flags_nonround_as_change():
    # output 0 is a round 0.01 BTC payment; output 1 is the messy change
    tx = Transaction(
        txid="t4", chain="bitcoin", timestamp=0,
        inputs=[_io("bc1qA", 1_500_000)],
        outputs=[_io("bc1qPay", 1_000_000), _io("bc1qChg", 487_321)],
    )
    v = analyze_change(tx)
    assert v.output_index == 1


def test_reuse_picks_fresh_address():
    tx = Transaction(
        txid="t5", chain="bitcoin", timestamp=0,
        inputs=[_io("bc1qA", 800_000)],
        outputs=[
            _io("bc1qReused", 300_000, n_tx_seen=12),
            _io("bc1qFresh", 499_000, n_tx_seen=1),
        ],
    )
    v = analyze_change(tx)
    assert v.output_index == 1


def test_coinjoin_detection_and_cluster_skip():
    cj = Transaction(
        txid="cj", chain="bitcoin", timestamp=0,
        inputs=[_io(f"bc1qIn{i}", 1_000_000) for i in range(5)],
        outputs=[_io(f"bc1qOut{i}", 500_000) for i in range(5)],  # equal outputs
    )
    assert detect_coinjoin(cj) is True
    result = cluster_addresses([cj])
    assert "cj" in result.skipped_coinjoins
    # no inputs were merged because the coinjoin was skipped
    assert all(len(c) <= 1 for c in result.clusters.values())


def test_cospend_clusters_inputs_together():
    txs = [
        Transaction(txid="a", chain="bitcoin", timestamp=0,
                    inputs=[_io("bc1q1", 10), _io("bc1q2", 10)],
                    outputs=[_io("bc1qX", 18)]),
        Transaction(txid="b", chain="bitcoin", timestamp=0,
                    inputs=[_io("bc1q2", 5), _io("bc1q3", 5)],
                    outputs=[_io("bc1qY", 8)]),
    ]
    result = cluster_addresses(txs)
    # 1,2,3 should be in one cluster via the shared input bc1q2
    assert result.address_to_cluster["bc1q1"] == result.address_to_cluster["bc1q3"]
