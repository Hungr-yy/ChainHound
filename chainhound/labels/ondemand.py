"""On-demand/cached label sources — the rate-limited, lazy ingestion mode.

DESIGN.md calls for a shared fetcher with a token-bucket limiter, exponential
backoff on 429, and a local cache so repeated lookups never re-hit the API.
This module is that mechanism, source-agnostic; Chainabuse (and any future
per-address API) subclasses ``OnDemandSource`` and supplies ``_fetch``/``parse``.
"""
from __future__ import annotations

import abc
import time
from typing import Callable, Optional

from .. import db
from . import store
from .base import Label


class RateLimited(Exception):
    """Raised by a source's ``_fetch`` to signal a retryable rate-limit/transient error."""


class TokenBucket:
    """Classic token bucket. ``clock``/``sleeper`` are injectable for tests."""

    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleeper = sleeper
        self._last = clock()

    def acquire(self, n: int = 1) -> None:
        while True:
            now = self._clock()
            self._tokens = min(
                self.capacity, self._tokens + (now - self._last) * self.refill_per_sec
            )
            self._last = now
            if self._tokens >= n:
                self._tokens -= n
                return
            self._sleeper((n - self._tokens) / self.refill_per_sec)


def fetch_with_backoff(
    fn: Callable[[], str],
    *,
    retries: int = 3,
    base_delay: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> str:
    """Call ``fn``; on ``RateLimited`` retry with exponential backoff up to ``retries``."""
    attempt = 0
    while True:
        try:
            return fn()
        except RateLimited:
            if attempt >= retries:
                raise
            sleeper(base_delay * (2 ** attempt))
            attempt += 1


class OnDemandSource(abc.ABC):
    """A per-address API source: cache-first, rate-limited, with backoff."""

    source: str = "unknown"
    cache_ttl: int = 86_400  # seconds a cached response stays fresh

    def __init__(
        self,
        *,
        bucket: Optional[TokenBucket] = None,
        retries: int = 3,
        base_delay: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bucket = bucket or TokenBucket(capacity=1, refill_per_sec=0.2)
        self.retries = retries
        self.base_delay = base_delay
        self.sleeper = sleeper

    @abc.abstractmethod
    def _fetch(self, chain: str, address: str) -> str:
        """Return the raw API response text. Raise ``RateLimited`` to retry."""
        ...

    @abc.abstractmethod
    def parse(self, raw: str, chain: str, address: str) -> list[Label]:
        """Parse a raw response into labels. Pure; safe to unit-test."""
        ...

    def check(
        self,
        database_url: str,
        chain: str,
        address: str,
        *,
        connect: Callable = db.connect,
    ) -> list[Label]:
        """Return labels for an address, fetching (rate-limited) only on cache miss."""
        raw = store.cache_get(
            database_url, self.source, chain, address, self.cache_ttl, connect=connect
        )
        if raw is not None:
            return self.parse(raw, chain, address)

        self.bucket.acquire()
        raw = fetch_with_backoff(
            lambda: self._fetch(chain, address),
            retries=self.retries,
            base_delay=self.base_delay,
            sleeper=self.sleeper,
        )
        store.cache_put(database_url, self.source, chain, address, raw, connect=connect)
        labels = self.parse(raw, chain, address)
        store.replace_address(
            database_url, self.source, chain, address, labels, connect=connect
        )
        return labels
