"""Canonical, chain-agnostic data models.

Everything downstream (heuristics, analysis, storage) speaks these types, so a
Bitcoin UTXO transaction and an EVM transfer look structurally identical once a
connector has normalized them. Amounts are always integers in the asset's
smallest unit (satoshis for BTC, wei for ETH) to avoid floating-point error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AddressType(str, Enum):
    P2PKH = "p2pkh"        # legacy, starts with "1"
    P2SH = "p2sh"          # starts with "3"
    P2WPKH = "p2wpkh"      # bech32 "bc1q", keyhash (~42 chars)
    P2WSH = "p2wsh"        # bech32 "bc1q", scripthash (~62 chars)
    P2TR = "p2tr"          # bech32m "bc1p", taproot
    EOA = "eoa"            # account-model externally owned account
    CONTRACT = "contract"  # account-model contract
    UNKNOWN = "unknown"


def classify_btc_address(addr: str) -> AddressType:
    """Best-effort Bitcoin address-type classification from the prefix/length.

    Mirrors the change-analysis 'address type' heuristic from the TRM course:
    change is usually the same type as the spending input.
    """
    if not addr:
        return AddressType.UNKNOWN
    if addr.startswith("1"):
        return AddressType.P2PKH
    if addr.startswith("3"):
        return AddressType.P2SH
    if addr.startswith("bc1q"):
        return AddressType.P2WSH if len(addr) > 50 else AddressType.P2WPKH
    if addr.startswith("bc1p"):
        return AddressType.P2TR
    return AddressType.UNKNOWN


@dataclass
class TxIO:
    """One side of a transfer: an input being spent or an output being created."""
    address: Optional[str]
    value: int                              # smallest unit (sats/wei)
    address_type: AddressType = AddressType.UNKNOWN
    is_multisig: bool = False
    multisig_m: Optional[int] = None        # e.g. 2 in a 2-of-3
    multisig_n: Optional[int] = None        # e.g. 3 in a 2-of-3
    # How many distinct transactions this (output) address has ever appeared in.
    # Populated opportunistically by connectors; enables the reuse heuristic.
    n_tx_seen: Optional[int] = None
    # For outputs: the txid that later spends this output, if known.
    spent_by_txid: Optional[str] = None

    @property
    def multisig_type(self) -> Optional[str]:
        if self.is_multisig and self.multisig_m and self.multisig_n:
            return f"{self.multisig_m}/{self.multisig_n}"
        return None


@dataclass
class Transaction:
    txid: str
    chain: str
    timestamp: int                          # unix seconds
    inputs: list[TxIO] = field(default_factory=list)
    outputs: list[TxIO] = field(default_factory=list)
    fee: int = 0
    is_coinbase: bool = False

    @property
    def input_addresses(self) -> set[str]:
        return {io.address for io in self.inputs if io.address}

    @property
    def total_in(self) -> int:
        return sum(io.value for io in self.inputs)

    @property
    def total_out(self) -> int:
        return sum(io.value for io in self.outputs)


@dataclass
class AddressSummary:
    """The output of triage: a quick on-chain picture of one address."""
    address: str
    chain: str
    address_type: AddressType = AddressType.UNKNOWN
    balance: int = 0
    total_received: int = 0
    total_sent: int = 0
    tx_count: int = 0
    sent_count: int = 0                     # times appeared as input
    received_count: int = 0                 # times appeared as output
    first_seen: Optional[int] = None        # unix seconds
    last_seen: Optional[int] = None
    is_multisig: bool = False
    multisig_type: Optional[str] = None
    labels: list[str] = field(default_factory=list)


# Confidence bands, shared by every probabilistic conclusion the engine emits.
# Mirrors TRM's "Near Certainty -> Low/Moderate" scale.
CONFIDENCE_BANDS: list[tuple[float, str]] = [
    (0.85, "Near Certainty"),
    (0.65, "High"),
    (0.40, "Moderate"),
    (0.00, "Low"),
]


def confidence_band(score: float) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if score >= threshold:
            return label
    return "Low"
