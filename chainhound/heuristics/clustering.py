"""Co-spend (multi-input) clustering for UTXO chains.

Heuristic: if two addresses appear together as inputs to the same transaction,
the same entity controls the private keys for both, so they belong to one
cluster. Applied transitively across many transactions via union-find, a single
seed address expands into the full wallet.

CoinJoins violate the assumption and are skipped (see coinjoin.py).
Confidence is "Near Certainty" for ordinary co-spends, the strongest heuristic
in the toolkit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..models import Transaction
from .coinjoin import detect_coinjoin


class UnionFind:
    """Disjoint-set structure with path compression and union by rank."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.rank: dict[str, int] = {}

    def add(self, x: str) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        # path compression
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


@dataclass
class ClusterResult:
    address_to_cluster: dict[str, int] = field(default_factory=dict)
    clusters: dict[int, set[str]] = field(default_factory=dict)
    skipped_coinjoins: list[str] = field(default_factory=list)

    def cluster_of(self, address: str) -> set[str]:
        cid = self.address_to_cluster.get(address)
        return self.clusters.get(cid, {address}) if cid is not None else {address}


def cluster_addresses(
    transactions: Iterable[Transaction],
    skip_coinjoin: bool = True,
) -> ClusterResult:
    """Build co-spend clusters from a set of transactions."""
    uf = UnionFind()
    skipped: list[str] = []

    for tx in transactions:
        if skip_coinjoin and detect_coinjoin(tx):
            skipped.append(tx.txid)
            continue
        in_addrs = [io.address for io in tx.inputs if io.address]
        for addr in in_addrs:
            uf.add(addr)
        # union every input with the first input
        for addr in in_addrs[1:]:
            uf.union(in_addrs[0], addr)

    # materialize clusters with stable integer ids
    roots: dict[str, int] = {}
    address_to_cluster: dict[str, int] = {}
    clusters: dict[int, set[str]] = {}
    for addr in uf.parent:
        root = uf.find(addr)
        cid = roots.setdefault(root, len(roots))
        address_to_cluster[addr] = cid
        clusters.setdefault(cid, set()).add(addr)

    return ClusterResult(address_to_cluster, clusters, skipped)
