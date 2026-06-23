"""Chainabuse on-demand source — community-reported scam/abuse addresses.

Lazy and rate-limited (see ``ondemand``): the free API tier allows only a few
calls, so lookups are cached. Requires an API key (basic auth).

NOTE: the live response envelope is not documented, so ``parse`` is tolerant of
a top-level list or a ``reports``/``results``/``data`` wrapper, and ``_fetch`` is
isolated so the request shape is easy to adjust once exercised against a real
key. ``parse`` is fully unit-tested; ``_fetch`` is not live-tested here.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .base import Label
from .ondemand import OnDemandSource, RateLimited

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

API = "https://api.chainabuse.com/v0/reports"

# ChainHound chain name -> Chainabuse `chain` filter value.
_CHAIN_TO_CA = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "tron": "TRON",
    "solana": "SOL",
    "polygon": "POLYGON",
}


class ChainabuseSource(OnDemandSource):
    source = "chainabuse"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 20, **kwargs) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.timeout = timeout

    def _fetch(self, chain: str, address: str) -> dict:  # pragma: no cover - needs a key
        if requests is None:
            raise RuntimeError("install 'requests' to query Chainabuse")
        if not self.api_key:
            raise RuntimeError("set CHAINHOUND_CHAINABUSE_KEY to query Chainabuse")
        params = {"address": address, "perPage": 50}
        ca_chain = _CHAIN_TO_CA.get(chain)
        if ca_chain:
            params["chain"] = ca_chain
        resp = requests.get(
            API, params=params, auth=(self.api_key, self.api_key), timeout=self.timeout
        )
        if resp.status_code == 429:
            raise RateLimited("chainabuse rate limit")
        resp.raise_for_status()
        return resp.json()

    def parse(self, raw, chain: str, address: str) -> list[Label]:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            reports = data.get("reports") or data.get("results") or data.get("data") or []
        elif isinstance(data, list):
            reports = data
        else:
            reports = []

        # One label per distinct scam category. Community reports are the
        # noisiest source, so they never reach High (which would rival an OFAC
        # listing): anonymous reports floor at Low; a vetted (trusted/checked)
        # reporter lifts to Moderate.
        trusted_by_category: dict[str, bool] = {}
        for r in reports:
            if not isinstance(r, dict):
                continue
            category = r.get("scamCategory") or r.get("category") or "UNKNOWN"
            trusted = bool(r.get("trusted") or r.get("checked"))
            trusted_by_category[category] = trusted_by_category.get(category, False) or trusted

        return [
            Label(
                chain=chain,
                address=address,
                name=f"Chainabuse: {category}",
                category="scam",
                source=self.source,
                confidence="Moderate" if trusted else "Low",
            )
            for category, trusted in trusted_by_category.items()
        ]
