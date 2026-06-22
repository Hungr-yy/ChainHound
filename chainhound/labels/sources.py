"""Registry that builds label sources by name from configuration.

Keeps the CLI free of per-source construction details. Bulk sources refresh via
``store.sync``; on-demand sources answer per-address via ``OnDemandSource.check``.
"""
from __future__ import annotations

from typing import Optional

from .base import LabelSource
from .ondemand import OnDemandSource

# Names usable with `labels sync`. `repo` needs a --manifest, so it is excluded
# from `--all` (which has no obvious default manifest).
BULK_SOURCES = ("ofac", "tagpack", "repo")
ALL_BULK = ("ofac", "tagpack")
ONDEMAND_SOURCES = ("chainabuse",)


def bulk_source(
    name: str,
    cfg,
    *,
    url: Optional[str] = None,
    path: Optional[str] = None,
    manifest: Optional[str] = None,
) -> LabelSource:
    if name == "ofac":
        from .ofac import OFACSource, URL

        return OFACSource(url=url or cfg.ofac_url or URL)
    if name == "tagpack":
        from .tagpacks import TagPackSource

        return TagPackSource(path=path or cfg.tagpacks_path)
    if name == "repo":
        from .repo import RepoSource

        if not manifest:
            raise SystemExit("the 'repo' source needs --manifest <path>")
        return RepoSource.from_manifest(manifest)
    raise SystemExit(f"unknown bulk source: {name!r} (choices: {', '.join(BULK_SOURCES)})")


def ondemand_source(name: str, cfg) -> OnDemandSource:
    if name == "chainabuse":
        from .chainabuse import ChainabuseSource

        return ChainabuseSource(api_key=cfg.chainabuse_key)
    raise SystemExit(
        f"unknown on-demand source: {name!r} (choices: {', '.join(ONDEMAND_SOURCES)})"
    )
