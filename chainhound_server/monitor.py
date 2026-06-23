"""Run watched-address detectors and persist fired alerts.

This is the platform-layer glue between the pure :mod:`chainhound_server.detectors`
and the ``watch``/``alert`` store: fetch each watched address's current snapshot
via an engine provider, evaluate detectors against the stored baseline, write any
alerts, then roll the baseline forward. The provider is supplied as a factory so
this stays offline-testable; the actual polling loop lives in
:mod:`chainhound_server.poller` (a background worker — platform-only).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from chainhound.connectors.base import Provider

from . import detectors, store

logger = logging.getLogger(__name__)

# A provider for a watch's chain: (chain) -> Provider.
ProviderFor = Callable[[str], Provider]


def run_watch(
    database_url: str,
    watch: dict,
    provider: Provider,
    *,
    large_threshold: Optional[int] = None,
    connect: Callable = store.db.connect,
) -> list[dict]:
    """Evaluate one watch; return the alerts written this run (may be empty)."""
    summary = provider.get_address_summary(watch["address"])
    if summary is None:
        # Address not resolvable right now: still stamp the check, no baseline change.
        store.update_watch_baseline(
            database_url, watch["id"], watch.get("baseline") or {}, connect=connect
        )
        return []

    findings = detectors.evaluate(
        watch.get("baseline"), summary, large_threshold=large_threshold
    )
    written: list[dict] = []
    for f in findings:
        written.append(
            store.record_alert(
                database_url,
                watch["id"],
                f.detector,
                {**f.detail, "confidence": f.confidence},
                connect=connect,
            )
        )
    store.update_watch_baseline(
        database_url, watch["id"], detectors.snapshot(summary), connect=connect
    )
    return written


def run_all(
    database_url: str,
    *,
    provider_for: ProviderFor,
    large_threshold: Optional[int] = None,
    connect: Callable = store.db.connect,
) -> dict:
    """Evaluate every watch. Returns ``{checked, fired, alerts}``."""
    watches = store.list_watches(database_url, connect=connect)
    alerts: list[dict] = []
    for w in watches:
        try:
            provider = provider_for(w["chain"])
            alerts.extend(
                run_watch(
                    database_url,
                    w,
                    provider,
                    large_threshold=large_threshold,
                    connect=connect,
                )
            )
        except Exception:  # one bad watch must not abort the whole sweep
            logger.exception("watch %s (%s) failed", w.get("id"), w.get("address"))
    return {"checked": len(watches), "fired": len(alerts), "alerts": alerts}
