"""Background poller: run the monitor sweep on an interval.

A platform-layer worker (the engine forbids background workers) and a thin wrapper
around :func:`chainhound_server.monitor.run_all` — the same work ``POST
/monitor/run`` does, on a loop. Run it as a sidecar to the API:

    CHAINHOUND_DATABASE_URL=... python -m chainhound_server.poller

Interval and large-transfer threshold come from ``CHAINHOUND_POLL_INTERVAL`` (secs,
default 300) and ``CHAINHOUND_POLL_THRESHOLD`` (smallest units, optional).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional

from chainhound import config

from . import monitor
from .deps import provider_for_chain

logger = logging.getLogger(__name__)


def poll_once(cfg: config.Config, *, large_threshold: Optional[int] = None) -> dict:
    """One sweep over all watches. Returns the run summary."""
    return monitor.run_all(
        cfg.database_url,
        provider_for=lambda chain: provider_for_chain(cfg, chain),
        large_threshold=large_threshold,
    )


def run(
    *,
    interval: float = 300.0,
    large_threshold: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Loop ``poll_once`` every ``interval`` seconds.

    ``sleep`` and ``should_continue`` are injectable so a test can run exactly one
    iteration deterministically.
    """
    cfg = config.load()
    if not cfg.database_url:
        raise SystemExit("set CHAINHOUND_DATABASE_URL to run the poller")
    logger.info("poller started (interval=%ss)", interval)
    while should_continue():
        try:
            summary = poll_once(cfg, large_threshold=large_threshold)
            logger.info(
                "swept %s watch(es), %s alert(s) fired",
                summary["checked"],
                summary["fired"],
            )
        except Exception:
            logger.exception("poll sweep failed")
        if not should_continue():
            break
        sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    interval = float(os.getenv("CHAINHOUND_POLL_INTERVAL", "300"))
    threshold_env = os.getenv("CHAINHOUND_POLL_THRESHOLD")
    threshold = int(threshold_env) if threshold_env else None
    run(interval=interval, large_threshold=threshold)


if __name__ == "__main__":
    main()
