"""Monitoring endpoints: manage watched addresses, run detectors, read alerts.

Watch/alert state is persisted via :mod:`chainhound_server.store`; ``POST
/monitor/run`` evaluates every watch now (the trigger a scheduler or the bundled
poller calls). Every route needs a database — without ``CHAINHOUND_DATABASE_URL``
they return 503. ``connect`` and the provider factory are injected so the routes
are offline-testable.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from chainhound import config

from . import monitor, store
from .deps import ProviderFactory, get_config, get_connect, get_provider_factory
from .schemas import WatchCreate

router = APIRouter()


def _require_db(cfg: config.Config) -> str:
    if not cfg.database_url:
        raise HTTPException(
            status_code=503,
            detail="monitoring needs a database — set CHAINHOUND_DATABASE_URL",
        )
    return cfg.database_url


@router.post("/watches", status_code=201)
def add_watch(
    req: WatchCreate,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> dict:
    return store.add_watch(
        _require_db(cfg), req.chain, req.address, case_id=req.case_id, connect=connect
    )


@router.get("/watches")
def list_watches(
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> list:
    return store.list_watches(_require_db(cfg), connect=connect)


@router.delete("/watches/{watch_id}", status_code=204)
def delete_watch(
    watch_id: int,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> Response:
    if not store.delete_watch(_require_db(cfg), watch_id, connect=connect):
        raise HTTPException(status_code=404, detail=f"watch {watch_id} not found")
    return Response(status_code=204)


@router.get("/alerts")
def list_alerts(
    watch_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=1000),
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> list:
    return store.list_alerts(
        _require_db(cfg), watch_id=watch_id, limit=limit, connect=connect
    )


@router.post("/monitor/run")
def run_monitor(
    large_threshold: Optional[int] = Query(
        None, ge=0, description="smallest units; enables inflow/outflow detectors"
    ),
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
    make_provider: ProviderFactory = Depends(get_provider_factory),
) -> dict:
    db_url = _require_db(cfg)
    return monitor.run_all(
        db_url,
        provider_for=lambda chain: make_provider(cfg, chain),
        large_threshold=large_threshold,
        connect=connect,
    )
