"""FastAPI application factory for the ChainHound query layer."""

from __future__ import annotations

from fastapi import FastAPI

from .routes import router


def create_app() -> FastAPI:
    """Build the query-layer app. A factory (not a module-level singleton) so
    tests can construct fresh apps and apply ``dependency_overrides`` in
    isolation."""
    app = FastAPI(
        title="ChainHound query API",
        version="0.1.0",
        description=(
            "Stateless query layer over the ChainHound engine: triage, trace, "
            "peel, exposure, cross-chain, and label lookups."
        ),
    )
    app.include_router(router)
    return app
