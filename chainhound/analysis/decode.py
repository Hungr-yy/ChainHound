"""Decode an EVM transaction's calldata to a function signature (glass-box).

Uses Sourcify's keyless 4byte signature service (selector -> text signature),
which absorbed openchain.xyz's database. Full ABI-based *argument* decoding
(Sourcify `/v2/contract` ABI) needs a keccak dependency to map selectors to ABI
entries and is deferred; the 4byte service already yields the signature keylessly.

``fetch`` is injectable so the parsing is unit-tested offline with captured JSON.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..labels.ondemand import RateLimited, fetch_with_backoff

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

FOURBYTE_URL = "https://api.4byte.sourcify.dev/signature-database/v1/lookup"


def selector(input_hex: Optional[str]) -> Optional[str]:
    """The 4-byte function selector (``0x`` + 8 hex) of calldata, or None."""
    if not input_hex:
        return None
    h = input_hex if input_hex.startswith("0x") else "0x" + input_hex
    return h[:10] if len(h) >= 10 else None


def _best(signatures: list) -> Optional[str]:
    """Pick the most trustworthy signature: backed by a verified contract first,
    then any unfiltered one, then whatever is left (4byte has spam collisions)."""
    for s in signatures:
        if s.get("hasVerifiedContract") and not s.get("filtered"):
            return s.get("name")
    for s in signatures:
        if not s.get("filtered"):
            return s.get("name")
    return signatures[0].get("name") if signatures else None


def decode_input(input_hex: Optional[str], *, fetch: Optional[Callable] = None) -> Optional[str]:
    """Return the function signature calldata invokes (e.g. ``transfer(address,
    uint256)``), or None for plain transfers / unknown selectors."""
    sel = selector(input_hex)
    if not sel or sel == "0x":
        return None
    data = (fetch or _fetch_4byte)(sel)
    if not data or not data.get("ok"):
        return None
    sigs = ((data.get("result") or {}).get("function") or {}).get(sel) or []
    return _best(sigs)


def _fetch_4byte(sel: str) -> dict:
    if requests is None:
        raise RuntimeError("install 'requests' to decode calldata")

    def go():
        resp = requests.get(FOURBYTE_URL, params={"function": sel}, timeout=20)
        if resp.status_code == 429:
            raise RateLimited("4byte rate limit")
        resp.raise_for_status()
        return resp.json()

    return fetch_with_backoff(go)
