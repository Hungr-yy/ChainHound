"""CoinJoin / equal-output mixer detection.

The co-spend clustering heuristic assumes all inputs of a transaction share an
owner. That assumption breaks for CoinJoins (Wasabi, Whirlpool, JoinMarket),
where many independent parties contribute inputs to one transaction. We must
therefore detect and EXCLUDE these from clustering, or we will merge unrelated
people into one cluster.

Detection signal: a CoinJoin produces many equal-value outputs (the anonymity
set), funded by a comparable number of inputs from distinct parties.
"""
from __future__ import annotations

from collections import Counter

from ..models import Transaction


def detect_coinjoin(
    tx: Transaction,
    min_equal_outputs: int = 3,
    min_inputs: int = 3,
) -> bool:
    """Heuristically decide whether ``tx`` looks like a CoinJoin.

    A transaction is flagged when it has several inputs and at least
    ``min_equal_outputs`` outputs sharing one identical value. This is the
    structural fingerprint of equal-output CoinJoins; it is deliberately
    conservative to avoid false positives on ordinary batched payouts.
    """
    if len(tx.inputs) < min_inputs or len(tx.outputs) < min_equal_outputs:
        return False
    value_counts = Counter(io.value for io in tx.outputs)
    most_common_value, freq = value_counts.most_common(1)[0]
    if freq < min_equal_outputs:
        return False
    # The equal-output set should be backed by at least as many distinct
    # input addresses as there are equal outputs (independent participants).
    distinct_inputs = len({io.address for io in tx.inputs if io.address})
    return distinct_inputs >= min_equal_outputs
