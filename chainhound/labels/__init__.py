"""Attribution-label corpus: ingest public labels with source + confidence.

Bulk/scheduled sources (OFAC SDN, GraphSense TagPacks) and on-demand/cached
sources (Chainabuse) all normalize to the chain-agnostic ``Label`` and persist
through ``store``. Every label records its source and confidence band for
glass-box review (court export later strips third-party attribution).
"""
from __future__ import annotations

from .base import Label, LabelSource

__all__ = ["Label", "LabelSource"]
