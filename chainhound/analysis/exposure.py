"""Exposure + pathfinding — the consumer that makes the label corpus actionable.

Phase 2a answers "is THIS address labeled". Exposure answers "what labeled
entities does an address *reach*, directly and indirectly, and how much value
flows along those paths" — summed into TRM-style rings split by inbound/outbound
and category.

Traversal is a bounded, bidirectional counterparty BFS over the `Provider`
primitives (`get_address_transactions`). It deliberately does **not** reuse
`trace.trace_from_tx`, which is transaction-seeded and walks the change trail
forward only; exposure is address-seeded and bidirectional.

Glass-box: every finding records its path, the value, and the label's provenance
(source + base confidence). No bare scores.

**Confidence degrades with distance.** A sanctioned entity one hop away is not the
same claim as one five hops away, so the label's own band is decayed by distance:

    exposure_score = BAND_SCORE[base] * decay ** (distance - 1)
    exposure_confidence = confidence_band(exposure_score)

with `decay = DECAY_DEFAULT`. Distance 1 (direct) preserves the band; each hop
drops it, flooring to ``Low`` within a few hops. Monotonic — it never inflates.

**Value attribution.** Direct (distance 1) value is exact: the sats transferred
between the seed and the counterparty, summed over connecting txs. Indirect value
is the *bottleneck* — the min edge value along the path, an upper bound on what
could have flowed end to end. This is not precise taint (FIFO/haircut/poison is a
research problem, deferred); the full path is kept so the basis is auditable.
For inbound edges the received value is split equally across the funding inputs
(approximate; recorded so).
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field

from ..connectors.base import Provider
from ..models import CONFIDENCE_BANDS, confidence_band

logger = logging.getLogger(__name__)

DECAY_DEFAULT = 0.6

# Representative score per band, used to decay confidence with hop distance.
BAND_SCORE = {
    "Near Certainty": 0.90,
    "High": 0.70,
    "Moderate": 0.50,
    "Low": 0.20,
}
_BAND_RANK = {band: i for i, (_, band) in enumerate(CONFIDENCE_BANDS)}  # 0 = strongest


def degrade_confidence(base_band: str, distance: int, decay: float = DECAY_DEFAULT) -> str:
    """Decay a label's band by hop distance (distance 1 = direct, unchanged)."""
    score = BAND_SCORE.get(base_band, 0.20) * (decay ** max(distance - 1, 0))
    return confidence_band(score)


def _strongest(bands) -> str:
    return min(bands, key=lambda b: _BAND_RANK.get(b, 99), default="Low")


@dataclass
class ExposureFinding:
    address: str
    chain: str
    direction: str          # "in" (funds FROM here) | "out" (funds TO here)
    distance: int           # hops from the seed; 1 = direct
    value: int              # sats; exact at distance 1, bottleneck upper-bound beyond
    path: list[str]         # seed -> ... -> counterparty
    label_name: str
    category: str
    label_source: str
    base_confidence: str    # the label's own band
    exposure_confidence: str  # base band degraded by distance


@dataclass
class CategoryRing:
    category: str
    direction: str
    counterparties: int
    direct_value: int                 # exact (distance 1)
    indirect_value_upper_bound: int   # sum of bottlenecks (approximate; may double-count)
    strongest_confidence: str


@dataclass
class ExposureReport:
    seed: str
    chain: str
    hops: int
    direction: str
    findings: list[ExposureFinding] = field(default_factory=list)
    rings: list[CategoryRing] = field(default_factory=list)
    truncated: bool = False           # a bound (hops/nodes/fan-out) cut the search

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "chain": self.chain,
            "hops": self.hops,
            "direction": self.direction,
            "truncated": self.truncated,
            "findings": [asdict(f) for f in self.findings],
            "rings": [asdict(r) for r in self.rings],
        }


def _counterparties(provider: Provider, node: str, direction: str) -> dict[str, int]:
    """Aggregate a node's counterparties and the value on each edge, by direction.

    out: txs where ``node`` is an input -> output addresses (value = output value).
    in:  txs where ``node`` is an output -> input addresses (value = received,
         split equally across the funding inputs).
    """
    agg: dict[str, int] = defaultdict(int)
    for tx in provider.get_address_transactions(node):
        if direction == "out":
            if node in tx.input_addresses:
                for o in tx.outputs:
                    if o.address and o.address != node:
                        agg[o.address] += o.value
        else:  # "in"
            if node in {o.address for o in tx.outputs}:
                received = sum(o.value for o in tx.outputs if o.address == node)
                inputs = [i.address for i in tx.inputs if i.address and i.address != node]
                distinct = list(dict.fromkeys(inputs))
                if distinct:
                    share = received // len(distinct)
                    for a in distinct:
                        agg[a] += share
    return dict(agg)


def _traverse(
    provider, chain, seed, label_lookup, direction, hops, max_nodes, max_fanout,
    decay, findings, state,
) -> None:
    visited: set[str] = set()
    seen: set[tuple] = set()
    queue = deque([(seed, 0, [seed], None)])  # (addr, distance, path, bottleneck)
    expanded = 0

    while queue:
        node, dist, path, bottleneck = queue.popleft()
        if node in visited or dist >= hops:
            continue
        visited.add(node)
        if expanded >= max_nodes:
            state["truncated"] = True
            break
        expanded += 1

        cps = {
            a: v
            for a, v in _counterparties(provider, node, direction).items()
            if a not in path
        }
        if len(cps) > max_fanout:
            state["truncated"] = True  # a hub — don't expand through it
            continue

        for cp, val in cps.items():
            cp_dist = dist + 1
            cp_bottleneck = val if bottleneck is None else min(bottleneck, val)
            for lbl in label_lookup(chain, cp):
                key = (direction, cp, lbl.name)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    ExposureFinding(
                        address=cp,
                        chain=chain,
                        direction=direction,
                        distance=cp_dist,
                        value=cp_bottleneck,
                        path=path + [cp],
                        label_name=lbl.name,
                        category=lbl.category,
                        label_source=lbl.source,
                        base_confidence=lbl.confidence,
                        exposure_confidence=degrade_confidence(lbl.confidence, cp_dist, decay),
                    )
                )
            if cp_dist < hops:
                queue.append((cp, cp_dist, path + [cp], cp_bottleneck))


def _build_rings(findings: list[ExposureFinding]) -> list[CategoryRing]:
    groups: dict[tuple, list[ExposureFinding]] = defaultdict(list)
    for f in findings:
        groups[(f.category, f.direction)].append(f)
    rings = []
    for (category, direction), fs in sorted(groups.items()):
        rings.append(
            CategoryRing(
                category=category,
                direction=direction,
                counterparties=len({f.address for f in fs}),
                direct_value=sum(f.value for f in fs if f.distance == 1),
                indirect_value_upper_bound=sum(f.value for f in fs if f.distance > 1),
                strongest_confidence=_strongest([f.exposure_confidence for f in fs]),
            )
        )
    return rings


def compute_exposure(
    provider: Provider,
    chain: str,
    seed: str,
    *,
    label_lookup,
    hops: int = 2,
    direction: str = "both",
    max_nodes: int = 500,
    max_fanout: int = 50,
    decay: float = DECAY_DEFAULT,
) -> ExposureReport:
    """Compute direct + indirect labeled exposure for a seed address.

    ``label_lookup(chain, address)`` returns the labels on an address (injected so
    this stays offline-testable, mirroring ``triage_address``). Bounds are hard
    and explicit: ``hops`` (depth), ``max_nodes`` (total expanded), ``max_fanout``
    (a node with more counterparties is a hub and is not expanded through).
    """
    if direction not in ("in", "out", "both"):
        raise ValueError(f"direction must be in|out|both, got {direction!r}")
    directions = ["in", "out"] if direction == "both" else [direction]

    findings: list[ExposureFinding] = []
    state = {"truncated": False}
    for d in directions:
        _traverse(
            provider, chain, seed, label_lookup, d, max(hops, 0),
            max_nodes, max_fanout, decay, findings, state,
        )
    return ExposureReport(
        seed=seed,
        chain=chain,
        hops=hops,
        direction=direction,
        findings=findings,
        rings=_build_rings(findings),
        truncated=state["truncated"],
    )
