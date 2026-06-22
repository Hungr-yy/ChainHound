"""Change-output identification for UTXO transactions.

Given a transaction with multiple outputs, which one is change (returning to the
sender) and which is the intended payment? Knowing this lets us extend a cluster
along the change and keep "following the money" without losing the trail.

Each heuristic below is independent and emits zero or more ``ChangeSignal``s,
each voting for a candidate change output with a weight. ``analyze_change``
combines them with a noisy-OR so that agreeing heuristics reinforce one another,
and maps the result to a confidence band. This mirrors the TRM course guidance:
no single heuristic is sufficient; apply several and let confidence accumulate.

All heuristics are deterministic code, but their *conclusion* is probabilistic
(the optimal-change heuristic in particular can false-positive), hence the
confidence band rather than a boolean.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Transaction, confidence_band


@dataclass
class ChangeSignal:
    heuristic: str
    output_index: int
    weight: float        # 0..1, this heuristic's vote strength for that output
    rationale: str


@dataclass
class ChangeVerdict:
    output_index: int | None
    score: float
    band: str
    signals: list[ChangeSignal] = field(default_factory=list)
    per_output: dict[int, float] = field(default_factory=dict)


# --- individual heuristics -------------------------------------------------

def h_self_change(tx: Transaction) -> list[ChangeSignal]:
    """An input address reappearing as an output is, near-certainly, change."""
    in_addrs = tx.input_addresses
    out: list[ChangeSignal] = []
    for i, o in enumerate(tx.outputs):
        if o.address and o.address in in_addrs:
            out.append(ChangeSignal("self_change", i, 0.95,
                                    "input address reused as output"))
    return out


def h_optimal_change(tx: Transaction) -> list[ChangeSignal]:
    """Optimal-change (nominal spend): change is typically smaller than every
    input, because if it equalled or exceeded an input, that input would have
    been unnecessary to fund the payment."""
    if not tx.inputs or len(tx.outputs) < 2:
        return []
    min_input = min(io.value for io in tx.inputs)
    candidates = [i for i, o in enumerate(tx.outputs) if o.value < min_input]
    if len(candidates) == 1:
        return [ChangeSignal("optimal_change", candidates[0], 0.65,
                             "only output smaller than the smallest input")]
    # ambiguous: weak vote spread across candidates
    return [ChangeSignal("optimal_change", i, 0.2,
                         "smaller than smallest input (ambiguous)")
            for i in candidates]


def h_address_type(tx: Transaction) -> list[ChangeSignal]:
    """Change usually shares the spending input's address type. If exactly one
    output matches an input type and at least one output differs, the matching
    output is the likely change."""
    if len(tx.outputs) < 2:
        return []
    input_types = {io.address_type for io in tx.inputs}
    matching = [i for i, o in enumerate(tx.outputs) if o.address_type in input_types]
    differing = [i for i, o in enumerate(tx.outputs) if o.address_type not in input_types]
    if len(matching) == 1 and differing:
        return [ChangeSignal("address_type", matching[0], 0.55,
                             "output address type matches the inputs")]
    return []


def h_multisig(tx: Transaction) -> list[ChangeSignal]:
    """Change matches the multisig type (e.g. 2/3) of the inputs."""
    if len(tx.outputs) < 2:
        return []
    input_ms = {io.multisig_type for io in tx.inputs if io.multisig_type}
    if not input_ms:
        return []
    matching = [i for i, o in enumerate(tx.outputs) if o.multisig_type in input_ms]
    differing = [i for i, o in enumerate(tx.outputs) if o.multisig_type not in input_ms]
    if len(matching) == 1 and differing:
        return [ChangeSignal("multisig", matching[0], 0.6,
                             "output multisig type matches the inputs")]
    return []


def _roundness(value: int) -> int:
    """How 'round' a sat amount is: number of trailing zero sats, capped."""
    if value == 0:
        return 0
    zeros = 0
    v = value
    while v % 10 == 0 and zeros < 8:
        v //= 10
        zeros += 1
    return zeros


def h_round_payment(tx: Transaction) -> list[ChangeSignal]:
    """Humans pay round amounts; the leftover change is rarely round. If exactly
    one output is clearly round, the OTHER output is the likely change."""
    if len(tx.outputs) != 2:
        return []
    r0, r1 = _roundness(tx.outputs[0].value), _roundness(tx.outputs[1].value)
    # require a clear gap in roundness (>= ~0.001 BTC granularity difference)
    if r0 >= 5 and r1 < r0 - 1:
        return [ChangeSignal("round_payment", 1, 0.4,
                             "output 0 is a round payment, so 1 is change")]
    if r1 >= 5 and r0 < r1 - 1:
        return [ChangeSignal("round_payment", 0, 0.4,
                             "output 1 is a round payment, so 0 is change")]
    return []


def h_address_reuse(tx: Transaction) -> list[ChangeSignal]:
    """Change addresses are typically used once. If one output address has been
    seen in multiple transactions (reused -> likely payment) and another appears
    fresh, the fresh one is the likely change. Requires n_tx_seen on outputs."""
    if len(tx.outputs) < 2:
        return []
    known = [(i, o.n_tx_seen) for i, o in enumerate(tx.outputs) if o.n_tx_seen is not None]
    if len(known) < 2:
        return []
    reused = [i for i, n in known if n and n > 1]
    fresh = [i for i, n in known if n == 1]
    if len(fresh) == 1 and reused:
        return [ChangeSignal("address_reuse", fresh[0], 0.45,
                             "fresh output address; the other was reused")]
    return []


HEURISTICS = [
    h_self_change,
    h_optimal_change,
    h_address_type,
    h_multisig,
    h_round_payment,
    h_address_reuse,
]


def analyze_change(tx: Transaction) -> ChangeVerdict:
    """Run every heuristic and combine votes into a single verdict.

    Combination is noisy-OR per output: score = 1 - prod(1 - weight_i). Multiple
    independent heuristics agreeing on the same output drive confidence up.
    """
    signals: list[ChangeSignal] = []
    for fn in HEURISTICS:
        signals.extend(fn(tx))

    per_output: dict[int, float] = {}
    for s in signals:
        prior = per_output.get(s.output_index, 0.0)
        per_output[s.output_index] = 1 - (1 - prior) * (1 - s.weight)

    if not per_output:
        return ChangeVerdict(None, 0.0, "Low", signals, per_output)

    best_idx = max(per_output, key=per_output.get)
    best_score = per_output[best_idx]
    return ChangeVerdict(best_idx, best_score, confidence_band(best_score),
                         signals, per_output)
