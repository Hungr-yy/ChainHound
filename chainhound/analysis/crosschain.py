"""Cross-chain matching — link a fund flow across chains (Phase 4).

The inferred tier (the novel deterministic heuristic): given an outflow on chain A
and candidate inflows on chain B, match by **asset-equivalence + amount within a
fee tolerance + a time window + a known-bridge touch**, with confidence scaled by
how tight the match is. This automates the value/time matching analysts do by hand
across a bridge. Results carry glass-box provenance in ``matched_on`` and land in
the ``cross_chain_link`` table.

Scoring (documented; no thresholds are tuned to a case):

    gate  : same peg group; 0 <= dst.ts - src.ts <= window; amount rel <= fee_tol
    score : 0.40 + 0.20*(1 - rel/fee_tol) + 0.04*(1 - dt/window)  (+0.30 if a known
            bridge address is touched)

So amount+time alone top out at ~0.64 (**Moderate** — suggestive, never certain);
a known bridge lifts a tight, prompt match into **High / Near Certainty**. The api
tier (``bridges.py``) is separate and higher-confidence: the bridge confirms the
pair directly.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from .. import db
from ..models import confidence_band

logger = logging.getLogger(__name__)

# asset symbol -> (peg group, decimals). Cross-asset amounts compare on a
# human-decimal basis, so decimals matter (BTC 8-dec vs ETH 18-dec).
ASSETS: dict[str, tuple[str, int]] = {
    "BTC": ("btc", 8), "RENBTC": ("btc", 8), "WBTC": ("btc", 8), "TBTC": ("btc", 8),
    "ETH": ("eth", 18), "WETH": ("eth", 18),
    "USDC": ("usd", 6), "USDT": ("usd", 6), "DAI": ("usd", 18), "USDD": ("usd", 18),
    "RUNE": ("rune", 8),
}

# Known bridge addresses per bridge per chain (EVM lowercased to match the
# connector's normalization). Seed; extensible like the label corpus. THORChain's
# BTC inbound vaults rotate, so only its stable ETH Router is registered here.
KNOWN_BRIDGES: dict[str, dict[str, set]] = {
    "thorchain": {"ethereum": {"0xd37bbe5744d730a1d98d8dc97c42f0ca46ad7146"}},
}


@dataclass
class Transfer:
    chain: str
    txid: str
    address: Optional[str]
    asset: str
    amount: int          # smallest unit of `asset`
    decimals: int
    timestamp: int       # unix seconds


@dataclass
class CrossChainLink:
    src_chain: str
    src_txid: str
    dst_chain: str
    dst_txid: str
    bridge: Optional[str]
    method: str          # "inferred" | "api"
    confidence: str
    matched_on: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def peg_group(asset: str) -> str:
    return ASSETS.get(asset.upper(), (asset.lower(), 0))[0]


def same_peg(a: str, b: str) -> bool:
    return peg_group(a) == peg_group(b)


def _human(t: Transfer) -> float:
    return t.amount / (10 ** t.decimals)


def _known_bridge(src: Transfer, dst: Transfer) -> Optional[str]:
    for bridge, by_chain in KNOWN_BRIDGES.items():
        for t in (src, dst):
            if t.address and t.address in by_chain.get(t.chain, set()):
                return bridge
    return None


def score_match(
    src: Transfer,
    dst: Transfer,
    *,
    fee_tol: float = 0.02,
    time_window_s: int = 86_400,
) -> Optional[tuple[str, dict]]:
    """Score a candidate src->dst cross-chain match. Returns (band, matched_on) or
    None if a gate (asset equivalence / time window / fee tolerance) fails."""
    if not same_peg(src.asset, dst.asset):
        return None
    dt = dst.timestamp - src.timestamp
    if dt < 0 or dt > time_window_s:
        return None
    src_h, dst_h = _human(src), _human(dst)
    if src_h <= 0:
        return None
    rel = abs(dst_h - src_h) / src_h
    if rel > fee_tol:
        return None

    bridge = _known_bridge(src, dst)
    score = 0.40 + 0.20 * (1 - rel / fee_tol) + 0.04 * (1 - dt / time_window_s)
    if bridge:
        score += 0.30
    band = confidence_band(min(score, 0.99))
    matched_on = {
        "asset_src": src.asset,
        "asset_dst": dst.asset,
        "amount_src": src.amount,
        "amount_dst": dst.amount,
        "rel_delta": round(rel, 6),
        "time_delta_s": dt,
        "bridge": bridge,
    }
    return band, matched_on


def infer_links(
    src: Transfer,
    dst_candidates: list[Transfer],
    *,
    fee_tol: float = 0.02,
    time_window_s: int = 86_400,
) -> list[CrossChainLink]:
    """Score every candidate inflow; return matching links best (tightest) first."""
    scored = []
    for dst in dst_candidates:
        m = score_match(src, dst, fee_tol=fee_tol, time_window_s=time_window_s)
        if m is None:
            continue
        band, matched = m
        scored.append((matched["rel_delta"], band, matched, dst))
    scored.sort(key=lambda s: s[0])  # tightest amount match first
    return [
        CrossChainLink(
            src_chain=src.chain, src_txid=src.txid,
            dst_chain=dst.chain, dst_txid=dst.txid,
            bridge=matched["bridge"], method="inferred",
            confidence=band, matched_on=matched,
        )
        for _, band, matched, dst in scored
    ]


_INSERT = (
    "INSERT INTO cross_chain_link "
    "(src_chain, src_txid, dst_chain, dst_txid, bridge, method, confidence, matched_on) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def save_cross_chain_link(
    database_url: str, link: CrossChainLink, *, connect: Callable = db.connect
) -> None:
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_INSERT, (
                link.src_chain, link.src_txid, link.dst_chain, link.dst_txid,
                link.bridge, link.method, link.confidence, json.dumps(link.matched_on),
            ))
        conn.commit()
