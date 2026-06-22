"""Address triage: build a quick on-chain picture before tracing.

Answers the TRM "building a picture" checklist for a suspect address: who/what
it looks like, how active it was, over what window, and whether it shows
service-like behaviour (heavy reuse, multisig, consolidation).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from ..connectors.base import Provider
from ..models import AddressSummary


def _fmt_ts(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def triage_address(provider: Provider, address: str) -> dict:
    """Return a triage report for an address, with simple service-likeness flags."""
    summary: AddressSummary | None = provider.get_address_summary(address)
    if summary is None:
        return {"address": address, "found": False}

    report = asdict(summary)
    report["found"] = True
    report["first_seen"] = _fmt_ts(summary.first_seen)
    report["last_seen"] = _fmt_ts(summary.last_seen)

    # Heuristic flags that suggest a service rather than a personal wallet.
    flags: list[str] = []
    if summary.tx_count >= 100:
        flags.append("high_activity")
    if summary.received_count >= 50 and summary.sent_count <= summary.received_count // 5:
        flags.append("consolidation_pattern")  # many in, few out -> deposit/VASP
    if summary.is_multisig:
        flags.append(f"multisig_{summary.multisig_type}")
    report["service_flags"] = flags
    report["likely_service"] = bool(flags) and "high_activity" in flags
    return report
