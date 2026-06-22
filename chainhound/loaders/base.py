"""Label-loader interface.

A loader is to the label corpus what a connector is to chain data: one adapter
per source that normalizes to a canonical model — here `LabelRecord` — so the
ingestion path never sees source-specific structure (ARCHITECTURE.md, the
connector/canonical-store boundary).

DESIGN.md splits sources by *how they are fetched*, and that split is the type
hierarchy:

- `BulkLoader` — scheduled, no rate limit, small. Downloads a whole dataset and
  parses it. The `fetch_raw` (network) / `parse` (pure) split is deliberate:
  every parser is a pure function of bytes, so it is unit-tested offline exactly
  like the heuristics, with no network in the test path.
- `OnDemandLoader` — rate-limited, queried lazily for case addresses only, and
  cached so a repeat lookup never re-hits the API.
"""
from __future__ import annotations

import abc

from ..models import LabelRecord


class Loader(abc.ABC):
    """Common identity for every label source."""

    source: str = "unknown"          # provenance written to label.source
    mode: str = "unknown"            # 'bulk' | 'on_demand'


class BulkLoader(Loader):
    """A scheduled, rate-limit-free source loaded in one shot."""

    mode = "bulk"

    @abc.abstractmethod
    def fetch_raw(self) -> bytes:
        """Download the raw dataset (the only method that touches the network)."""
        ...

    @abc.abstractmethod
    def parse(self, raw: bytes) -> list[LabelRecord]:
        """Pure transform of raw bytes -> canonical labels. No I/O, so this is
        the offline-testable core of every bulk loader."""
        ...

    def sync(self) -> list[LabelRecord]:
        """fetch_raw -> parse. The unit a scheduler (cron) invokes."""
        return self.parse(self.fetch_raw())


class OnDemandLoader(Loader):
    """A rate-limited source queried lazily, per case address, with caching."""

    mode = "on_demand"

    @abc.abstractmethod
    def fetch_for_address(self, address: str) -> list[LabelRecord]:
        """Return labels for a single address (cache-first, rate-limited)."""
        ...
