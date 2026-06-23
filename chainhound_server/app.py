"""FastAPI application factory for the ChainHound query layer."""

from __future__ import annotations

from fastapi import FastAPI

from .cases import router as cases_router
from .routes import router as query_router


def create_app() -> FastAPI:
    """Build the app. A factory (not a module-level singleton) so tests can
    construct fresh apps and apply ``dependency_overrides`` in isolation."""
    app = FastAPI(
        title="ChainHound API",
        version="0.1.0",
        description=(
            "Stateless query layer over the ChainHound engine (triage, trace, "
            "peel, exposure, cross-chain, labels) plus investigation-case "
            "persistence (save/load)."
        ),
    )
    app.include_router(query_router)
    app.include_router(cases_router)
    return app
