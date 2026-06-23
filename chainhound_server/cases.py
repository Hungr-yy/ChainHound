"""Case-persistence endpoints — the investigation workspace's save/load.

Cases, pinned notes, and per-element graph-hygiene state, persisted to Postgres
via :mod:`chainhound_server.store`. Every route needs a database; without
``CHAINHOUND_DATABASE_URL`` they return 503. The ``connect`` callable is injected
(:func:`chainhound_server.deps.get_connect`) so these are offline-testable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from chainhound import config

from . import export, store
from .deps import ProviderFactory, get_config, get_connect, get_provider_factory
from .schemas import CaseCreate, ElementSave, NoteCreate

router = APIRouter(prefix="/cases")


def _require_db(cfg: config.Config) -> str:
    if not cfg.database_url:
        raise HTTPException(
            status_code=503,
            detail="case persistence needs a database — set CHAINHOUND_DATABASE_URL",
        )
    return cfg.database_url


@router.post("", status_code=201)
def create_case(
    req: CaseCreate,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> dict:
    return store.create_case(_require_db(cfg), req.name, connect=connect)


@router.get("")
def list_cases(
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> list:
    return store.list_cases(_require_db(cfg), connect=connect)


@router.get("/{case_id}")
def get_case(
    case_id: int,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> dict:
    case = store.get_case(_require_db(cfg), case_id, connect=connect)
    if case is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return case


@router.delete("/{case_id}", status_code=204)
def delete_case(
    case_id: int,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> Response:
    if not store.delete_case(_require_db(cfg), case_id, connect=connect):
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return Response(status_code=204)


@router.post("/{case_id}/notes", status_code=201)
def add_note(
    case_id: int,
    req: NoteCreate,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> dict:
    note = store.add_note(
        _require_db(cfg),
        case_id,
        chain=req.chain,
        ref=req.ref,
        body=req.body,
        connect=connect,
    )
    if note is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return note


@router.put("/{case_id}/elements")
def save_element(
    case_id: int,
    req: ElementSave,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
) -> dict:
    element = store.save_element(
        _require_db(cfg),
        case_id,
        req.element_id,
        color=req.color,
        hidden=req.hidden,
        note=req.note,
        connect=connect,
    )
    if element is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return element


@router.get("/{case_id}/export")
def court_export(
    case_id: int,
    cfg: config.Config = Depends(get_config),
    connect: Callable = Depends(get_connect),
    make_provider: ProviderFactory = Depends(get_provider_factory),
) -> JSONResponse:
    """Court export: the raw on-chain evidence for a case (no attribution).

    Downloads as an attachment so it can be filed verbatim.
    """
    db_url = _require_db(cfg)
    generated_at = datetime.now(timezone.utc).isoformat()
    bundle = export.court_export(
        db_url,
        case_id,
        provider_for=lambda chain: make_provider(cfg, chain),
        connect=connect,
        generated_at=generated_at,
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return JSONResponse(
        content=bundle,
        headers={
            "Content-Disposition": f'attachment; filename="case-{case_id}-court-export.json"'
        },
    )
