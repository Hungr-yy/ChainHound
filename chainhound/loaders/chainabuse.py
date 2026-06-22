"""Chainabuse scam/abuse reports (on-demand, rate-limited, cached).

Chainabuse is queried lazily, per case address — never bulk-ingested. Each lookup
is (1) served from the `fetch_cache` table if present, else (2) fetched through
the shared token-bucket + backoff `HttpFetcher` and cached with a TTL, then
(3) parsed to labels. Community-reported, so labels land at the lowest
confidence band (they raise suspicion; they are not authoritative).

`parse_reports` is pure and offline-tested; the network and DB are injected so
the throttle/cache logic is exercised without sockets or Postgres.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from ..models import LabelRecord
from .base import OnDemandLoader
from .ratelimit import HttpFetcher, TokenBucket

# Per-address query endpoint. Templated on the address; overridable for tests.
DEFAULT_URL = "https://api.chainabuse.com/v0/reports?address={address}"
DEFAULT_RATE = 2.0          # requests/second (conservative)
DEFAULT_TTL = 24 * 3600     # cache a per-address answer for a day


def parse_reports(address: str, payload, chain: str = "unknown") -> list[LabelRecord]:
    """Pure transform of a Chainabuse response into canonical labels."""
    if isinstance(payload, dict):
        reports = payload.get("reports", payload.get("data", []))
    else:
        reports = payload or []
    out: list[LabelRecord] = []
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        category = rep.get("category") or rep.get("scamCategory") or "scam"
        name = rep.get("title") or f"Chainabuse: {category}"
        out.append(LabelRecord(
            chain=chain,
            address=address,
            name=name,
            source="chainabuse",
            category=str(category).lower(),
            confidence="Low",
        ))
    return out


class ChainabuseLoader(OnDemandLoader):
    source = "chainabuse"

    def __init__(
        self,
        database_url: Optional[str] = None,
        url_template: str = DEFAULT_URL,
        fetcher: Optional[HttpFetcher] = None,
        ttl_seconds: int = DEFAULT_TTL,
    ) -> None:
        self.database_url = database_url
        self.url_template = url_template
        self.ttl_seconds = ttl_seconds
        # Lazily build a real fetcher only when one is not injected, so that
        # tests (and dry runs) never require `requests`.
        self._fetcher = fetcher

    @property
    def fetcher(self) -> HttpFetcher:
        if self._fetcher is None:
            self._fetcher = HttpFetcher(TokenBucket(rate=DEFAULT_RATE))
        return self._fetcher

    def fetch_for_address(self, address: str, chain: str = "unknown") -> list[LabelRecord]:
        payload = None
        if self.database_url:
            payload = db.cache_get(self.database_url, self.source, address)
        if payload is None:
            payload = self.fetcher.get_json(self.url_template.format(address=address))
            if self.database_url:
                db.cache_put(self.database_url, self.source, address, payload,
                             ttl_seconds=self.ttl_seconds)
        return parse_reports(address, payload, chain=chain)
