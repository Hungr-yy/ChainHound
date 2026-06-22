"""Connector registry and factory."""
from __future__ import annotations

from .base import Provider


def get_provider(name: str, **kwargs) -> Provider:
    """Instantiate a provider by name. Imports are lazy so that optional
    dependencies (bigquery, requests) are only needed for the chosen backend."""
    name = name.lower()
    if name in ("blockstream", "esplora", "btc"):
        from .blockstream_btc import BlockstreamBTC
        return BlockstreamBTC(**kwargs)
    if name in ("bigquery", "bq"):
        from .bigquery_btc import BigQueryBTC
        return BigQueryBTC(**kwargs)
    raise ValueError(f"unknown provider: {name!r}")


__all__ = ["Provider", "get_provider"]
