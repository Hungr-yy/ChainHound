"""Label model and the source interface every loader implements.

A ``LabelSource`` separates ``fetch`` (network/git/file — not unit-tested live)
from ``parse`` (a pure function over the fetched text), so parsing stays offline
and deterministic for TDD. Loaders return chain-agnostic ``Label`` objects that
``store`` upserts into the ``label`` table.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class Label:
    """One attribution tag, mirroring the columns of the ``label`` table.

    ``confidence`` is a band string (see ``models.CONFIDENCE_BANDS``), never a
    raw float — labels are evidence, recorded with their provenance.
    """

    chain: str
    address: str
    name: str
    category: str
    source: str
    confidence: str


class LabelSource(abc.ABC):
    """A bulk attribution source (OFAC SDN, TagPacks, ...)."""

    #: short provenance tag stored on every label (e.g. ``"ofac"``).
    source: str = "unknown"

    @abc.abstractmethod
    def fetch(self) -> str:
        """Return the raw source document (network/git/file)."""
        ...

    @abc.abstractmethod
    def parse(self, text: str) -> list[Label]:
        """Parse a fetched document into labels. Pure; safe to unit-test."""
        ...
