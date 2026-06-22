"""Follow-the-money tracing.

Starting from a transaction, walk outputs forward for a bounded number of hops,
applying change analysis at each step so the resulting graph distinguishes the
(likely) change trail from peeled-off payments. Deliberately bounded — the TRM
course warns against "endless clicking"; the value is in 1-2 well-understood
hops, not thousands of unanalysed ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..connectors.base import Provider
from ..heuristics.change_analysis import analyze_change


@dataclass
class GraphNode:
    id: str          # txid or address
    kind: str        # "tx" | "address"
    label: str = ""


@dataclass
class GraphEdge:
    src: str
    dst: str
    value: int
    asset: str = "BTC"
    role: str = ""          # "change" | "payment"
    confidence: str = ""    # confidence band when role == change


@dataclass
class TraceGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes.setdefault(node.id, node)

    def to_dict(self) -> dict:
        return {
            "nodes": [vars(n) for n in self.nodes.values()],
            "edges": [vars(e) for e in self.edges],
        }


def trace_from_tx(provider: Provider, start_txid: str, hops: int = 2) -> TraceGraph:
    """Build a trace graph forward from a transaction along the change trail."""
    graph = TraceGraph()
    frontier: list[str] = [start_txid]
    visited: set[str] = set()

    for _ in range(max(hops, 0) + 1):
        nxt: list[str] = []
        for txid in frontier:
            if txid in visited:
                continue
            visited.add(txid)
            tx = provider.get_transaction(txid)
            if tx is None:
                continue
            graph.add_node(GraphNode(txid, "tx", label=txid[:10]))
            verdict = analyze_change(tx)
            for i, o in enumerate(tx.outputs):
                if not o.address:
                    continue
                graph.add_node(GraphNode(o.address, "address", label=o.address[:10]))
                is_change = i == verdict.output_index
                graph.edges.append(GraphEdge(
                    src=txid, dst=o.address, value=o.value,
                    role="change" if is_change else "payment",
                    confidence=verdict.band if is_change else "",
                ))
                if is_change:
                    spend = provider.get_spending_tx(txid, i)
                    if spend:
                        nxt.append(spend[0])
        frontier = nxt
        if not frontier:
            break
    return graph
