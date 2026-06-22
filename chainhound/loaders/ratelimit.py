"""Shared rate-limit + backoff primitives for on-demand label sources.

DESIGN.md requires the on-demand APIs (Chainabuse, later bridge/EVM explorers) to
be wrapped by "a token-bucket limiter, exponential backoff on 429, and a local
cache table so repeated lookups never re-hit the API." This module is the first
two; the cache table lives in `db.py` (`cache_get`/`cache_put`).

Every time/network dependency is injected (`now`, `sleep`, and for `HttpFetcher`
the transport), defaulting to the real ones. That keeps the limiter
deterministic and the tests fully offline — a fake clock and a fake transport
exercise the throttling and retry logic with no real waiting and no sockets,
matching the pure-logic test discipline in tests/test_change_analysis.py.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class TokenBucket:
    """Classic token bucket. `rate` tokens accrue per second up to `capacity`;
    `acquire` blocks (via the injected `sleep`) until a token is available.

    The clock is injected so tests advance time explicitly instead of waiting.
    """

    def __init__(
        self,
        rate: float,
        capacity: Optional[float] = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else rate)
        self._now = now
        self._sleep = sleep
        self._tokens = self.capacity
        self._last = now()

    def _refill(self) -> None:
        t = self._now()
        elapsed = t - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = t

    def acquire(self, n: float = 1.0) -> None:
        """Consume `n` tokens, sleeping until enough have accrued."""
        if n > self.capacity:
            raise ValueError("requested tokens exceed bucket capacity")
        self._refill()
        if self._tokens < n:
            deficit = n - self._tokens
            wait = deficit / self.rate
            self._sleep(wait)
            self._refill()
        self._tokens -= n


# HTTP statuses worth retrying: rate-limit + transient server errors.
RETRY_STATUSES = (429, 500, 502, 503, 504)


class RetryableStatus(Exception):
    """Raised internally to drive a backoff retry on a retryable HTTP status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"retryable status {status}")
        self.status = status


def backoff(
    fn: Callable[[], object],
    *,
    retries: int = 5,
    base: float = 0.5,
    cap: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
):
    """Call `fn`, retrying on `RetryableStatus` with exponential backoff
    (base * 2**attempt, capped at `cap`). Re-raises the last error once
    `retries` is exhausted. `sleep` is injected so tests never really wait.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except RetryableStatus:
            if attempt >= retries:
                raise
            sleep(min(cap, base * (2 ** attempt)))
            attempt += 1


class HttpFetcher:
    """A `requests.get` wrapped in a shared token bucket + 429/5xx backoff.

    `transport` defaults to `requests.get`; tests pass a fake returning objects
    with `.status_code` and `.json()`.
    """

    def __init__(
        self,
        bucket: TokenBucket,
        *,
        timeout: int = 20,
        retries: int = 5,
        transport: Optional[Callable[..., object]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if transport is None:
            if requests is None:
                raise RuntimeError("install 'requests' to use HttpFetcher")
            transport = requests.get
        self._bucket = bucket
        self._timeout = timeout
        self._retries = retries
        self._transport = transport
        self._sleep = sleep

    def get_json(self, url: str, **kwargs):
        """Rate-limited, retrying GET that returns parsed JSON."""

        def _once():
            self._bucket.acquire()
            resp = self._transport(url, timeout=self._timeout, **kwargs)
            if resp.status_code in RETRY_STATUSES:
                raise RetryableStatus(resp.status_code)
            return resp.json()

        return backoff(_once, retries=self._retries, sleep=self._sleep)
