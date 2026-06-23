"""FastAPI application factory for the ChainHound query layer."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cases import router as cases_router
from .routes import router as query_router

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Build the app. A factory (not a module-level singleton) so tests can
    construct fresh apps and apply ``dependency_overrides`` in isolation."""
    app = FastAPI(
        title="ChainHound API",
        version="0.1.0",
        description=(
            "Stateless query layer over the ChainHound engine (triage, trace, "
            "peel, exposure, cross-chain, labels) plus investigation-case "
            "persistence (save/load), with the analyst investigation canvas."
        ),
    )
    app.include_router(query_router)
    app.include_router(cases_router)

    # The investigation canvas: a vanilla-JS Cytoscape single-page app. Assets
    # are mounted under /static (so they never shadow API routes); the canvas is
    # served at "/". Mount only if the bundled assets are present.
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(_STATIC_DIR / "index.html")

    return app
