"""Watched-address detectors — pure, deterministic, confidence-banded.

Each detector compares an address's current on-chain snapshot against a stored
baseline and emits findings. The observations are factual (an address either
transacted again or it didn't), so findings carry **Near Certainty** with a
glass-box ``detail`` recording the evidence. No I/O here: the provider fetch and
alert persistence live in :mod:`chainhound_server.monitor`, so this stays
trivially offline-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from chainhound.models import AddressSummary

NEAR_CERTAINTY = "Near Certainty"


@dataclass
class DetectorFinding:
    detector: str
    confidence: str
    detail: dict


def snapshot(summary: AddressSummary) -> dict:
    """The baseline state a detector compares against, rolled forward each run."""
    return {
        "tx_count": summary.tx_count,
        "total_received": summary.total_received,
        "total_sent": summary.total_sent,
        "balance": summary.balance,
        "last_seen": summary.last_seen,
    }


def evaluate(
    baseline: Optional[dict],
    summary: AddressSummary,
    *,
    large_threshold: Optional[int] = None,
) -> list[DetectorFinding]:
    """Return findings for ``summary`` relative to ``baseline``.

    ``baseline is None`` is the first observation: establish it silently (no
    alerts on pre-existing history). ``large_threshold`` (smallest units of the
    chain) enables the inflow/outflow detectors; left unset, only new-activity
    runs (a single absolute threshold is meaningless across sats vs wei).
    """
    if baseline is None:
        return []

    findings: list[DetectorFinding] = []

    prev_txs = baseline.get("tx_count", 0)
    if summary.tx_count > prev_txs:
        findings.append(
            DetectorFinding(
                "new-activity",
                NEAR_CERTAINTY,
                {
                    "prev_tx_count": prev_txs,
                    "tx_count": summary.tx_count,
                    "delta": summary.tx_count - prev_txs,
                    "last_seen": summary.last_seen,
                },
            )
        )

    if large_threshold is not None:
        in_delta = summary.total_received - baseline.get("total_received", 0)
        if in_delta >= large_threshold:
            findings.append(
                DetectorFinding(
                    "large-inflow",
                    NEAR_CERTAINTY,
                    {
                        "delta": in_delta,
                        "threshold": large_threshold,
                        "total_received": summary.total_received,
                    },
                )
            )
        out_delta = summary.total_sent - baseline.get("total_sent", 0)
        if out_delta >= large_threshold:
            findings.append(
                DetectorFinding(
                    "large-outflow",
                    NEAR_CERTAINTY,
                    {
                        "delta": out_delta,
                        "threshold": large_threshold,
                        "total_sent": summary.total_sent,
                    },
                )
            )

    return findings
