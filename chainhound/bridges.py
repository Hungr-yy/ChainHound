"""Bridge-explorer connectors — the `api` tier of cross-chain matching.

A bridge explorer that indexes a bridge/cross-chain-swap can return the src->dst
pair directly, so the link is high-confidence (the bridge confirms it). Each
explorer implements ``BridgeExplorer``; the first is THORChain's keyless Midgard
v2 (a native cross-chain DEX). Wormholescan fits the same shape (a future impl).
"""
from __future__ import annotations

import abc
import logging
from typing import Callable, Optional

from .analysis.crosschain import CrossChainLink
from .labels.ondemand import RateLimited, TokenBucket, fetch_with_backoff

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

# THORChain chain code -> ChainHound chain name.
_THOR_CHAIN = {
    "BTC": "bitcoin", "ETH": "ethereum", "BCH": "bitcoin-cash", "LTC": "litecoin",
    "DOGE": "dogecoin", "AVAX": "avalanche", "BSC": "bsc", "BASE": "base",
    "GAIA": "cosmos", "ATOM": "cosmos", "THOR": "thorchain",
}
_EVM_CHAINS = {"ethereum", "avalanche", "bsc", "base", "polygon", "arbitrum", "optimism"}


def _parse_asset(asset: str) -> tuple[str, str]:
    """`ETH.USDC-0x..` / `BTC.BTC` / `ETH~ETH` -> (chain_name, SYMBOL)."""
    code, _, rest = asset.replace("~", ".").partition(".")
    symbol = (rest.split("-")[0] if rest else code).upper()
    return _THOR_CHAIN.get(code.upper(), code.lower()), symbol


def _norm_txid(chain: str, txid: str) -> str:
    t = txid.lower()
    if chain in _EVM_CHAINS and not t.startswith("0x"):
        t = "0x" + t
    return t


class BridgeExplorer(abc.ABC):
    bridge: str = "unknown"

    @abc.abstractmethod
    def lookup(self, src_chain: str, src_txid: str) -> Optional[CrossChainLink]:
        """Return the api-confirmed src->dst link for a transaction, or None."""
        ...


class ThorchainMidgard(BridgeExplorer):
    bridge = "thorchain"
    URL = "https://midgard.thorchain.network/v2/actions"

    def __init__(self, transport: Optional[Callable] = None, timeout: int = 20,
                 bucket: Optional[TokenBucket] = None) -> None:
        self._transport = transport  # injectable for offline tests
        self.timeout = timeout
        self.bucket = bucket or TokenBucket(capacity=5, refill_per_sec=2.0)

    def _get(self, params: dict) -> dict:
        if self._transport is not None:
            return self._transport(params)
        if requests is None:
            raise RuntimeError("install 'requests' to query Midgard")
        self.bucket.acquire()
        return fetch_with_backoff(lambda: self._http(params))

    def _http(self, params: dict) -> dict:
        resp = requests.get(self.URL, params=params, timeout=self.timeout)
        if resp.status_code == 429:
            raise RateLimited("midgard rate limit")
        resp.raise_for_status()
        return resp.json()

    def lookup(self, src_chain: str, src_txid: str) -> Optional[CrossChainLink]:
        data = self._get({"txid": src_txid})
        want = src_txid.lower()
        for act in data.get("actions", []):
            if act.get("type") != "swap" or not act.get("in"):
                continue
            if want not in {t.get("txID", "").lower() for t in act["in"]}:
                continue
            in_coin = act["in"][0]["coins"][0]
            s_chain, _ = _parse_asset(in_coin["asset"])
            # the dst is the first settled out (non-empty txID) on a non-THOR chain;
            # the RUNE intermediate leg has an empty txID and is skipped.
            for out in act.get("out", []):
                if not out.get("txID"):
                    continue
                d_chain, _d_sym = _parse_asset(out["coins"][0]["asset"])
                if d_chain == "thorchain":
                    continue
                status = act.get("status")
                return CrossChainLink(
                    src_chain=s_chain or src_chain,
                    src_txid=_norm_txid(s_chain or src_chain, src_txid),
                    dst_chain=d_chain,
                    dst_txid=_norm_txid(d_chain, out["txID"]),
                    bridge=self.bridge,
                    method="api",
                    confidence="Near Certainty" if status == "success" else "Moderate",
                    matched_on={
                        "in": in_coin,
                        "out": out["coins"][0],
                        "pools": act.get("pools"),
                        "status": status,
                    },
                )
        return None
