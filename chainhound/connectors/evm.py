"""EVM (account-model) connector over the Etherscan-compatible REST shape.

Default backend is **Routescan (keyless)**; supply an Etherscan V2 base URL + key
for higher limits, or any Blockscout instance — all share the same
`module=account&action=txlist|...` shape, so this one adapter covers them.

Account-model normalization (see the Phase 3 plan): a native value transfer maps
to a single-transfer ``Transaction`` (`inputs=[from]`, `outputs=[to]`), so the
UTXO-shaped models and the provider-agnostic engines (`triage`, `compute_exposure`)
work unchanged. Failed txs (`isError=1`) and zero-value contract calls carry no
native value and are dropped from the value-flow view. Rate limiting / backoff is
the 2a fetcher (`labels/ondemand`) — no new throttling code here.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..labels.ondemand import RateLimited, TokenBucket, fetch_with_backoff
from ..models import AddressSummary, AddressType, Transaction, TxIO
from .base import Provider

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

# chain_id -> ChainHound chain name (matches TagPack / label corpus naming).
_CHAIN_NAMES = {
    1: "ethereum",
    10: "optimism",
    56: "bsc",
    137: "polygon",
    8453: "base",
    42161: "arbitrum",
    43114: "avalanche",
}

# chain_id -> native currency symbol (the `asset` tag for native value transfers).
_NATIVE_SYMBOL = {1: "ETH", 10: "ETH", 8453: "ETH", 42161: "ETH",
                  56: "BNB", 137: "POL", 43114: "AVAX"}


def _routescan_url(chain_id: int) -> str:
    return f"https://api.routescan.io/v2/network/mainnet/evm/{chain_id}/etherscan/api"


def _classify_evm(code: Optional[str]) -> AddressType:
    """EOA vs contract from eth_getCode. Empty code or an EIP-7702 delegation
    designator (0xef0100…) is an EOA; anything else is a contract."""
    if not code or code in ("0x", "0x0"):
        return AddressType.EOA
    if code.startswith("0xef0100"):
        return AddressType.EOA
    return AddressType.CONTRACT


class EvmProvider(Provider):
    chain = "ethereum"
    model = "account"

    def __init__(
        self,
        base_url: Optional[str] = None,
        chain_id: int = 1,
        api_key: Optional[str] = None,
        chain: Optional[str] = None,
        timeout: int = 20,
        transport: Optional[Callable[[dict], dict]] = None,
        bucket: Optional[TokenBucket] = None,
    ) -> None:
        self.chain_id = chain_id
        self.base_url = base_url or _routescan_url(chain_id)
        self.api_key = api_key
        self.chain = chain or _CHAIN_NAMES.get(chain_id, f"evm-{chain_id}")
        self.native_symbol = _NATIVE_SYMBOL.get(chain_id, "ETH")
        self.timeout = timeout
        self._transport = transport  # injectable for offline tests
        # Etherscan V2 selects the chain by query param; Routescan/Blockscout by path.
        self._chainid_param = "etherscan.io/v2" in self.base_url
        # Conservative default for the keyless Routescan free tier (2 rps).
        self.bucket = bucket or TokenBucket(capacity=2, refill_per_sec=2.0)

    # --- HTTP plumbing (reuses the 2a fetcher; no new throttle/backoff code) ---

    def _get(self, params: dict) -> dict:
        params = dict(params)
        if self.api_key:
            params["apikey"] = self.api_key
        if self._chainid_param:
            params["chainid"] = self.chain_id
        if self._transport is not None:
            return self._transport(params)
        if requests is None:
            raise RuntimeError("install 'requests' to use the EVM connector")
        self.bucket.acquire()
        return fetch_with_backoff(lambda: self._http(params))

    def _http(self, params: dict) -> dict:
        resp = requests.get(self.base_url, params=params, timeout=self.timeout)
        if resp.status_code == 429:
            raise RateLimited("evm explorer rate limit")
        resp.raise_for_status()
        return resp.json()

    def _account(self, params: dict):
        """Return the `result` of an account-module call ([] when none found)."""
        data = self._get({"module": "account", **params})
        if str(data.get("status")) != "1":
            msg = str(data.get("message", ""))
            if "No transactions found" in msg or data.get("result") in ([], None):
                return []
            if "rate limit" in msg.lower():
                raise RateLimited(msg)
            raise RuntimeError(f"EVM API error: {msg}: {data.get('result')}")
        return data.get("result")

    def _proxy(self, params: dict):
        return self._get({"module": "proxy", **params}).get("result")

    # --- normalization --------------------------------------------------------

    def _to_native_tx(self, row: dict) -> Optional[Transaction]:
        if str(row.get("isError")) == "1":
            return None  # reverted — no value moved
        to = (row.get("to") or "").lower()
        frm = (row.get("from") or "").lower()
        value = int(row.get("value", "0"))
        if not to or value == 0:
            return None  # contract creation / zero-value call: no native transfer
        fee = int(row.get("gasUsed", "0")) * int(row.get("gasPrice", "0"))
        return Transaction(
            txid=row.get("hash", ""),
            chain=self.chain,
            timestamp=int(row.get("timeStamp", "0")),
            inputs=[TxIO(address=frm, value=value, asset=self.native_symbol)],
            outputs=[TxIO(address=to, value=value, asset=self.native_symbol)],
            fee=fee,
            method=row.get("functionName") or None,
        )

    def _to_token_tx(self, row: dict, *, nft: bool) -> Optional[Transaction]:
        """One ERC-20/721 transfer -> a transfer-grained Transaction tagged with
        the token as ``asset`` (one transfer per Transaction so exposure never
        cross-links independent transfers inside the same on-chain tx)."""
        to = (row.get("to") or "").lower()
        frm = (row.get("from") or "").lower()
        if not to or not frm:
            return None
        asset = row.get("tokenSymbol") or row.get("contractAddress") or "TOKEN"
        # ERC-721 rows carry a tokenID, not a divisible value; count one unit.
        value = 1 if nft else int(row.get("value", "0"))
        return Transaction(
            txid=row.get("hash", ""),
            chain=self.chain,
            timestamp=int(row.get("timeStamp", "0")),
            inputs=[TxIO(address=frm, value=value, asset=asset)],
            outputs=[TxIO(address=to, value=value, asset=asset)],
            method=row.get("functionName") or None,
        )

    def _to_internal_tx(self, row: dict) -> Optional[Transaction]:
        """A value-bearing internal call (contract-mediated native transfer)."""
        if str(row.get("isError")) == "1":
            return None
        to = (row.get("to") or "").lower()
        frm = (row.get("from") or "").lower()
        value = int(row.get("value", "0"))
        if not to or not frm or value == 0:
            return None  # creates / zero-value calls carry no native transfer
        return Transaction(
            txid=row.get("hash", ""),
            chain=self.chain,
            timestamp=int(row.get("timeStamp", "0")),
            inputs=[TxIO(address=frm, value=value, asset=self.native_symbol)],
            outputs=[TxIO(address=to, value=value, asset=self.native_symbol)],
        )

    def _internal_txs(self, address: str, limit: int) -> list[Transaction]:
        rows = self._account(
            {
                "action": "txlistinternal",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": limit,
                "sort": "desc",
            }
        ) or []
        return [t for t in (self._to_internal_tx(r) for r in rows) if t]

    def _token_txs(self, address: str, limit: int) -> list[Transaction]:
        out: list[Transaction] = []
        for action, nft in (("tokentx", False), ("tokennfttx", True)):
            rows = self._account(
                {"action": action, "address": address, "page": 1,
                 "offset": limit, "sort": "desc"}
            ) or []
            out.extend(t for t in (self._to_token_tx(r, nft=nft) for r in rows) if t)
        return out

    def _txlist(self, address: str, limit: int) -> list:
        rows = self._account(
            {
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": limit,
                "sort": "desc",
            }
        )
        return rows or []

    def get_address_transactions(self, address: str, limit: int = 50) -> list[Transaction]:
        address = address.lower()
        native = [t for t in (self._to_native_tx(r) for r in self._txlist(address, limit)) if t]
        return native + self._internal_txs(address, limit) + self._token_txs(address, limit)

    def get_address_summary(self, address: str) -> Optional[AddressSummary]:
        address = address.lower()
        balance = int(self._account({"action": "balance", "address": address, "tag": "latest"}) or 0)
        code = self._proxy({"action": "eth_getCode", "address": address, "tag": "latest"})
        rows = self._txlist(address, 10000)

        def ok(r):
            return str(r.get("isError")) != "1"

        ts = [int(r.get("timeStamp", "0")) for r in rows] or [None]
        return AddressSummary(
            address=address,
            chain=self.chain,
            address_type=_classify_evm(code),
            balance=balance,
            total_received=sum(int(r.get("value", "0")) for r in rows if ok(r) and (r.get("to") or "").lower() == address),
            total_sent=sum(int(r.get("value", "0")) for r in rows if ok(r) and (r.get("from") or "").lower() == address),
            tx_count=len(rows),
            sent_count=sum(1 for r in rows if (r.get("from") or "").lower() == address),
            received_count=sum(1 for r in rows if (r.get("to") or "").lower() == address),
            first_seen=min(t for t in ts if t) if any(ts) else None,
            last_seen=max(t for t in ts if t) if any(ts) else None,
        )

    def get_transaction(self, txid: str) -> Optional[Transaction]:
        r = self._proxy({"action": "eth_getTransactionByHash", "txhash": txid})
        if not r:
            return None
        frm = (r.get("from") or "").lower()
        to = (r.get("to") or "").lower()
        value = int(r.get("value", "0x0"), 16)
        ts_hex = r.get("blockTimestamp")
        timestamp = int(ts_hex, 16) if ts_hex else 0
        outputs = [TxIO(address=to, value=value, asset=self.native_symbol)] if to else []
        return Transaction(
            txid=r.get("hash", txid),
            chain=self.chain,
            timestamp=timestamp,
            inputs=[TxIO(address=frm, value=value, asset=self.native_symbol)],
            outputs=outputs,
        )

    def get_spending_tx(self, txid: str, vout: int) -> Optional[tuple[str, int]]:
        # No UTXO outspend in the account model; the change-trail walk is BTC-only.
        return None
