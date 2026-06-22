"""Label-loader registry and factory.

One loader per attribution source, each normalizing to `LabelRecord`. Sources
split by ingestion mode (DESIGN.md): bulk/scheduled vs on-demand/cached. Imports
are lazy so optional deps (requests, PyYAML, psycopg) are only needed for the
loaders actually used.
"""
from __future__ import annotations

from .base import BulkLoader, Loader, OnDemandLoader

# Names the CLI iterates over. Kept here so adding a source is a one-line change.
BULK_SOURCES = ("ofac", "tagpacks")
ON_DEMAND_SOURCES = ("chainabuse",)


def get_loader(name: str, **kwargs) -> Loader:
    """Instantiate a loader by source name (lazy imports per source)."""
    name = name.lower()
    if name == "ofac":
        from .ofac import OFACLoader
        return OFACLoader(**kwargs)
    if name in ("tagpacks", "tagpack", "graphsense"):
        from .tagpacks import TagPackLoader
        return TagPackLoader(**kwargs)
    if name == "chainabuse":
        from .chainabuse import ChainabuseLoader
        return ChainabuseLoader(**kwargs)
    raise ValueError(f"unknown label source: {name!r}")


__all__ = [
    "Loader",
    "BulkLoader",
    "OnDemandLoader",
    "get_loader",
    "BULK_SOURCES",
    "ON_DEMAND_SOURCES",
]
