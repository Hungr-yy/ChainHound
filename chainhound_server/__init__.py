"""ChainHound platform layer — a FastAPI query API over the engine.

This package is the **server** layer. The dependency arrow points one way:
``chainhound_server`` imports ``chainhound`` (the engine); the engine must never
import this package. Keep all web-framework, server, and background-worker code
here, out of ``chainhound/``.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
