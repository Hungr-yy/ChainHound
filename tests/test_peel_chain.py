"""Peel-chain tests — incl. following the trailing full-balance sweep to the
cash-out (a peel commonly ends by sweeping the consolidated remainder to a
deposit/bridge in a single-output tx; that destination is the cash-out)."""
from chainhound.heuristics.peel_chain import trace_peel_chain
from chainhound.models import AddressType, Transaction, TxIO

WP = AddressType.P2WPKH
PK = AddressType.P2PKH
TARGET = "bc1qCASHOUT"


def _hop(txid, in_addr, in_val, pay_addr, pay_val, chg_addr, chg_val):
    # input is bech32; the payment goes to a legacy type (differs) and the change
    # stays bech32 (matches the input) -> the address-type heuristic picks out1 as
    # change, and out1 is the dominant remainder (a peel step).
    return Transaction(
        txid=txid, chain="bitcoin", timestamp=0,
        inputs=[TxIO(address=in_addr, value=in_val, address_type=WP)],
        outputs=[
            TxIO(address=pay_addr, value=pay_val, address_type=PK),
            TxIO(address=chg_addr, value=chg_val, address_type=WP),
        ],
    )


def _three_hop_peel():
    h1 = _hop("h1", "bc1qIN", 100, "1Pay1", 5, "bc1qc1", 94)
    h2 = _hop("h2", "bc1qc1", 94, "1Pay2", 4, "bc1qc2", 89)
    h3 = _hop("h3", "bc1qc2", 89, "1Pay3", 3, "bc1qc3", 85)
    txs = {t.txid: t for t in (h1, h2, h3)}
    spends = {("h1", 1): ("h2", 0), ("h2", 1): ("h3", 0)}
    return txs, spends


def _run(txs, spends):
    return trace_peel_chain("h1", txs.get, lambda t, v: spends.get((t, v)))


def test_peel_followed_by_single_output_sweep_records_cashout():
    txs, spends = _three_hop_peel()
    txs["sweep"] = Transaction(
        txid="sweep", chain="bitcoin", timestamp=0,
        inputs=[TxIO(address="bc1qc3", value=85, address_type=WP)],
        outputs=[TxIO(address=TARGET, value=84, address_type=WP)],  # 1 output = sweep
    )
    spends[("h3", 1)] = ("sweep", 0)
    chain = _run(txs, spends)
    assert chain.is_peel_chain            # 3 peel hops
    assert chain.length == 3
    assert chain.cash_out is not None
    assert chain.cash_out.address == TARGET
    assert chain.cash_out.value == 84
    assert chain.cash_out.txid == "sweep"


def test_peel_ending_in_fanout_records_no_cashout():
    # near-miss: the remainder is split many ways (not a single-output sweep) ->
    # not a cash-out forward; cash_out must stay None.
    txs, spends = _three_hop_peel()
    txs["fan"] = Transaction(
        txid="fan", chain="bitcoin", timestamp=0,
        inputs=[TxIO(address="bc1qc3", value=85, address_type=WP)],
        outputs=[TxIO(address=f"bc1qf{i}", value=20, address_type=WP) for i in range(4)],
    )
    spends[("h3", 1)] = ("fan", 0)
    chain = _run(txs, spends)
    assert chain.length == 3
    assert chain.cash_out is None


def test_peel_with_unspent_terminal_has_no_cashout():
    txs, spends = _three_hop_peel()       # h3 change never spent
    chain = _run(txs, spends)
    assert chain.length == 3
    assert chain.cash_out is None
