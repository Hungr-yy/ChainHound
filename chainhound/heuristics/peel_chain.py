"""Peel-chain detection.

A peel chain launders a large balance by repeatedly "peeling" off a small
payment to some destination while the large remainder (change) rolls into the
next transaction, hop after hop. It is one of the oldest layering techniques and
still common. We detect it by walking the change trail and checking that each
hop has the peel shape: a small payment plus a dominant change output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..models import Transaction
from .change_analysis import analyze_change


@dataclass
class PeelHop:
    txid: str
    change_index: int
    change_value: int
    payment_value: int
    confidence: str


@dataclass
class PeelCashOut:
    """The destination of a trailing single-output sweep that forwards the peeled
    remainder in one move (typically a deposit/bridge) — the chain's cash-out."""
    txid: str
    address: Optional[str]
    value: int


@dataclass
class PeelChain:
    hops: list[PeelHop] = field(default_factory=list)
    cash_out: Optional[PeelCashOut] = None

    @property
    def length(self) -> int:
        return len(self.hops)

    @property
    def is_peel_chain(self) -> bool:
        return self.length >= 3


def is_peel_step(tx: Transaction, change_index: int,
                 dominance: float = 0.6) -> bool:
    """A hop is a peel step if it has a small number of outputs and the change
    output holds the dominant share of the spent value."""
    if change_index >= len(tx.outputs) or not (2 <= len(tx.outputs) <= 3):
        return False
    total_out = tx.total_out or 1
    change_value = tx.outputs[change_index].value
    return change_value / total_out >= dominance


def trace_peel_chain(
    start_txid: str,
    get_tx: Callable[[str], Optional[Transaction]],
    get_spending_tx: Callable[[str, int], Optional[tuple[str, int]]],
    max_hops: int = 50,
) -> PeelChain:
    """Follow the change output forward, hop by hop, while the peel shape holds.

    ``get_spending_tx(txid, vout)`` returns the (txid, input_index) that spends a
    given output, or None if unspent. Provided by the connector layer.
    """
    chain = PeelChain()
    txid = start_txid
    seen: set[str] = set()

    for _ in range(max_hops):
        if not txid or txid in seen:
            break
        seen.add(txid)
        tx = get_tx(txid)
        if tx is None:
            break

        # Trailing full-balance sweep: once we are inside a peel, a single-output
        # tx forwards the whole remainder (no change) — that destination is the
        # cash-out. Record it and stop (a sweep is not itself a peel hop).
        if chain.hops and len(tx.outputs) == 1:
            o = tx.outputs[0]
            chain.cash_out = PeelCashOut(tx.txid, o.address, o.value)
            break

        verdict = analyze_change(tx)
        if verdict.output_index is None:
            break
        cidx = verdict.output_index
        if not is_peel_step(tx, cidx):
            break

        change_value = tx.outputs[cidx].value
        payment_value = tx.total_out - change_value
        chain.hops.append(PeelHop(txid, cidx, change_value, payment_value, verdict.band))

        nxt = get_spending_tx(txid, cidx)
        if nxt is None:
            break
        txid = nxt[0]

    return chain
