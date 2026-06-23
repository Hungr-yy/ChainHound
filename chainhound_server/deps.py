"""Engine-resolution dependencies for the query layer.

These mirror the provider/label-lookup wiring in ``chainhound/cli.py`` but expose
it as FastAPI dependencies so tests can override them with fake providers via
``app.dependency_overrides`` — the same dependency-injection ethos the engine's
analysis functions already use.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from chainhound import config
from chainhound.connectors import get_provider
from chainhound.connectors.base import Provider

logger = logging.getLogger(__name__)

LabelLookup = Callable[[str, str], list]

_BTC_ALIASES = ("bitcoin", "btc")
_EVM_ALIASES = ("ethereum", "eth", "evm")


def get_config() -> config.Config:
    """The active runtime configuration (env-driven). Overridable in tests."""
    return config.load()


def provider_for_chain(cfg: config.Config, chain: str) -> Provider:
    """Resolve a keyless provider for a named chain.

    BTC -> Blockstream (keyless Esplora); EVM -> the configured explorer. Raises
    ``ValueError`` for an unknown chain so the route layer can map it to HTTP 400.
    """
    chain = (chain or "").lower()
    if chain in _BTC_ALIASES:
        return get_provider("blockstream")
    if chain in _EVM_ALIASES:
        kw: dict = {"chain_id": cfg.evm_chain_id, "api_key": cfg.etherscan_key}
        if cfg.evm_provider_url:
            kw["base_url"] = cfg.evm_provider_url
        return get_provider("ethereum", **kw)
    raise ValueError(f"no provider configured for chain {chain!r}")


def label_lookup_for(cfg: config.Config) -> Optional[LabelLookup]:
    """A ``(chain, address) -> [Label]`` hook backed by the label store, or None
    when no database is configured."""
    if not cfg.database_url:
        return None
    from chainhound.labels import store

    def lookup(chain: str, address: str) -> list:
        return store.lookup(cfg.database_url, chain, address)

    return lookup


# --- FastAPI dependency seams -------------------------------------------------
# Routes inject these *factories* (not concrete providers) because the target
# chain is only known per-request. Tests override them via
# ``app.dependency_overrides`` to inject fake providers / label hooks offline.

ProviderFactory = Callable[[config.Config, str], Provider]


def get_provider_factory() -> ProviderFactory:
    return provider_for_chain


def get_label_lookup_factory() -> Callable[[config.Config], Optional[LabelLookup]]:
    return label_lookup_for


def get_connect() -> Callable:
    """The psycopg connect callable used by the case store. Overridden in tests
    to inject a fake connection (no live Postgres)."""
    from chainhound import db

    return db.connect
