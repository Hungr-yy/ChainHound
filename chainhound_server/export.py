"""Court export — the raw on-chain evidence underlying a case.

Per the project rule, a court export contains **raw on-chain data only**: the
canonical transactions and address summaries for every on-chain entity the
analyst referenced in the case, fetched fresh from the chain. It deliberately
excludes all attribution and analyst work-product — labels, clusters,
change/peel/exposure verdicts, confidence-banded inferences, inferred
cross-chain links, and the case's own colors/notes — so the bundle is
independently verifiable against the public ledger.

The pure assembly (``classify_ref``/``gather_references``/``build_export``) is
split from the chain I/O (``court_export``) so it stays offline-testable.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Callable, Optional

from chainhound.connectors.base import Provider
from chainhound.models import EVM_CHAINS  # noqa: F401  (documents EVM scope)

from . import store

DISCLAIMER = (
    "Raw on-chain data only. Contains no labels, clustering, change/peel/exposure "
    "verdicts, confidence-banded inferences, cross-chain inferences, or analyst "
    "annotations — independently verifiable against the public ledger."
)

ProviderFor = Callable[[str], Provider]

# Address vs transaction-id shapes, by string format. EVM uses 0x-prefixed hex
# (40 nibbles = address, 64 = tx hash); bare 64-hex is a Bitcoin txid; anything
# else (base58 / bech32) is a Bitcoin address.
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_EVM_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_EVM_TX = re.compile(r"^0x[0-9a-fA-F]{64}$")


def classify_ref(ref: str) -> tuple[str, str]:
    """Return ``(chain, kind)`` for an on-chain reference. ``kind`` is
    ``"tx"`` or ``"address"``; ``chain`` is ``"ethereum"`` or ``"bitcoin"``."""
    r = (ref or "").strip()
    if _EVM_TX.match(r):
        return "ethereum", "tx"
    if _EVM_ADDR.match(r):
        return "ethereum", "address"
    if _HEX64.match(r):
        return "bitcoin", "tx"
    return "bitcoin", "address"


def gather_references(case: dict) -> list[dict]:
    """Distinct on-chain references in a case (graph elements + pinned notes).

    Graph-element ids are classified by format; note refs use the note's own
    ``chain`` when present (else format). Hygiene colors/notes are ignored — they
    are analyst work-product, not evidence.
    """
    seen: set[tuple[str, str]] = set()
    refs: list[dict] = []

    def add(ref: str, chain: Optional[str] = None) -> None:
        if not ref:
            return
        guess_chain, kind = classify_ref(ref)
        chain = chain or guess_chain
        key = (chain, ref)
        if key in seen:
            return
        seen.add(key)
        refs.append({"chain": chain, "ref": ref, "kind": kind})

    for el in case.get("elements", []):
        add(el.get("element_id"))
    for note in case.get("notes", []):
        if note.get("ref"):
            add(note["ref"], note.get("chain"))
    return refs


def _address_evidence(summary) -> dict:
    """Raw address summary with attribution stripped (drops ``labels``)."""
    d = asdict(summary)
    d.pop("labels", None)
    return d


def build_export(
    case: dict,
    addresses: list[dict],
    transactions: list[dict],
    unresolved: list[dict],
    *,
    generated_at: Optional[str] = None,
) -> dict:
    """Assemble the export bundle from already-fetched raw data (pure)."""
    return {
        "court_export": {
            "case_id": case.get("case_id"),
            "case_name": case.get("name"),
            "generated_at": generated_at,
            "disclaimer": DISCLAIMER,
            "entity_count": len(addresses) + len(transactions),
        },
        "addresses": addresses,
        "transactions": transactions,
        "unresolved": unresolved,
    }


def court_export(
    database_url: str,
    case_id: int,
    *,
    provider_for: ProviderFor,
    connect: Callable = store.db.connect,
    generated_at: Optional[str] = None,
) -> Optional[dict]:
    """Load a case, fetch raw chain data for every referenced entity, and build
    the export. Returns None if the case does not exist."""
    case = store.get_case(database_url, case_id, connect=connect)
    if case is None:
        return None

    addresses: list[dict] = []
    transactions: list[dict] = []
    unresolved: list[dict] = []
    for r in gather_references(case):
        provider = provider_for(r["chain"])
        if r["kind"] == "tx":
            tx = provider.get_transaction(r["ref"])
            if tx is None:
                unresolved.append(r)
            else:
                transactions.append(asdict(tx))
        else:
            summary = provider.get_address_summary(r["ref"])
            if summary is None:
                unresolved.append(r)
            else:
                addresses.append(_address_evidence(summary))
    return build_export(
        case, addresses, transactions, unresolved, generated_at=generated_at
    )
