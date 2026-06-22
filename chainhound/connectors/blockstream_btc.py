"""Bitcoin connector backed by the Esplora REST API (blockstream.info).

Good default for interactive single-address work: no credentials, returns
inputs/outputs/spend status directly. For bulk/historical analytics use the
BigQuery connector instead. Network access to blockstream.info is required at
runtime (add it to your egress allowlist if sandboxed).
"""
from __future__ import annotations

from typing import Optional

from ..models import AddressSummary, Transaction, TxIO, classify_btc_address
from .base import Provider

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

API = "https://blockstream.info/api"


class BlockstreamBTC(Provider):
    chain = "bitcoin"

    def __init__(self, base_url: str = API, timeout: int = 20) -> None:
        if requests is None:
            raise RuntimeError("install 'requests' to use the Blockstream connector")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str):
        r = requests.get(f"{self.base_url}{path}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _to_io(self, vout: dict) -> TxIO:
        addr = vout.get("scriptpubkey_address")
        return TxIO(
            address=addr,
            value=int(vout.get("value", 0)),
            address_type=classify_btc_address(addr or ""),
        )

    def get_transaction(self, txid: str) -> Optional[Transaction]:
        data = self._get(f"/tx/{txid}")
        inputs = [
            self._to_io(vin["prevout"])
            for vin in data.get("vin", [])
            if vin.get("prevout")
        ]
        outputs = [self._to_io(v) for v in data.get("vout", [])]
        status = data.get("status", {})
        return Transaction(
            txid=data["txid"],
            chain=self.chain,
            timestamp=int(status.get("block_time", 0)),
            inputs=inputs,
            outputs=outputs,
            fee=int(data.get("fee", 0)),
            is_coinbase=bool(data.get("vin", [{}])[0].get("is_coinbase")),
        )

    def get_address_summary(self, address: str) -> Optional[AddressSummary]:
        data = self._get(f"/address/{address}")
        chain_stats = data.get("chain_stats", {})
        funded = int(chain_stats.get("funded_txo_sum", 0))
        spent = int(chain_stats.get("spent_txo_sum", 0))
        return AddressSummary(
            address=address,
            chain=self.chain,
            address_type=classify_btc_address(address),
            balance=funded - spent,
            total_received=funded,
            total_sent=spent,
            tx_count=int(chain_stats.get("tx_count", 0)),
            received_count=int(chain_stats.get("funded_txo_count", 0)),
            sent_count=int(chain_stats.get("spent_txo_count", 0)),
        )

    def get_address_transactions(self, address: str, limit: int = 50) -> list[Transaction]:
        rows = self._get(f"/address/{address}/txs")[:limit]
        return [self.get_transaction(r["txid"]) for r in rows]

    def get_spending_tx(self, txid: str, vout: int) -> Optional[tuple[str, int]]:
        info = self._get(f"/tx/{txid}/outspend/{vout}")
        if info.get("spent"):
            return info["txid"], int(info.get("vin", 0))
        return None
