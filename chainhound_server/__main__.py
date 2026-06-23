"""Boot the query API with uvicorn: ``python -m chainhound_server``.

Host/port come from ``CHAINHOUND_API_HOST`` / ``CHAINHOUND_API_PORT`` (defaults
127.0.0.1:8000 — loopback by default so the engine stays permission-light).
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from .app import create_app

    host = os.getenv("CHAINHOUND_API_HOST", "127.0.0.1")
    port = int(os.getenv("CHAINHOUND_API_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
