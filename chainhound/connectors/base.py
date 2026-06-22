"""Connector (data-source provider) interface.

Every data source — BigQuery, a block explorer, a bridge explorer — implements
this interface and returns canonical models. The rest of the system never sees
source-specific shapes, which is what lets the correlation engine aggregate
across sources without manual reconciliation.
"""
from __future__ import annotations

import abc
from typing import Optional

from ..models import AddressSummary, Transaction


class Provider(abc.ABC):
    """A read interface over one chain's public data."""

    chain: str = "unknown"

    @abc.abstractmethod
    def get_transaction(self, txid: str) -> Optional[Transaction]:
        ...

    @abc.abstractmethod
    def get_address_summary(self, address: str) -> Optional[AddressSummary]:
        ...

    @abc.abstractmethod
    def get_address_transactions(
        self, address: str, limit: int = 50
    ) -> list[Transaction]:
        ...

    @abc.abstractmethod
    def get_spending_tx(
        self, txid: str, vout: int
    ) -> Optional[tuple[str, int]]:
        """Return (spending_txid, input_index) for an output, or None if unspent."""
        ...
