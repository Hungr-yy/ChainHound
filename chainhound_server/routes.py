"""Stateless query endpoints wrapping the engine's read operations.

Each handler resolves a provider for the requested chain, calls the matching
engine function, and serializes the result. Handlers are defined with ``def``
(not ``async def``) so FastAPI runs the blocking, network-bound engine calls in
its threadpool. Errors map to actionable HTTP codes: bad input -> 400,
not-found -> 404, account-model trace -> 422, missing database -> 503.

Provider and label-lookup resolution arrive as injected factories
(:mod:`chainhound_server.deps`) so tests can override them with fakes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from chainhound import config

from .deps import (
    ProviderFactory,
    get_config,
    get_label_lookup_factory,
    get_provider_factory,
)
from .schemas import CrossChainRequest

router = APIRouter()


def _resolve(make_provider: ProviderFactory, cfg: config.Config, chain: str):
    try:
        return make_provider(cfg, chain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/triage")
def triage(
    address: str = Query(..., min_length=1),
    chain: str = "bitcoin",
    cfg: config.Config = Depends(get_config),
    make_provider: ProviderFactory = Depends(get_provider_factory),
    label_lookup_for=Depends(get_label_lookup_factory),
) -> dict:
    from chainhound.analysis.triage import triage_address

    provider = _resolve(make_provider, cfg, chain)
    # triage expects a (chain, address) -> [name] hook; adapt the Label-returning
    # store lookup to names.
    raw_lookup = label_lookup_for(cfg)
    names_lookup = None
    if raw_lookup is not None:
        names_lookup = lambda ch, ad: [lbl.name for lbl in raw_lookup(ch, ad)]
    try:
        report = triage_address(provider, address, label_lookup=names_lookup)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not report.get("found"):
        raise HTTPException(status_code=404, detail=f"address {address!r} not found")
    return report


@router.get("/trace")
def trace(
    txid: str = Query(..., min_length=1),
    hops: int = Query(2, ge=0, le=10),
    cfg: config.Config = Depends(get_config),
    make_provider: ProviderFactory = Depends(get_provider_factory),
) -> dict:
    from chainhound.analysis.trace import trace_from_tx

    provider = _resolve(make_provider, cfg, "bitcoin")
    try:
        graph = trace_from_tx(provider, txid, hops=hops)
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return graph.to_dict()


@router.get("/peel")
def peel(
    txid: str = Query(..., min_length=1),
    max_hops: int = Query(50, ge=1, le=200),
    cfg: config.Config = Depends(get_config),
    make_provider: ProviderFactory = Depends(get_provider_factory),
) -> dict:
    from chainhound.heuristics.peel_chain import trace_peel_chain

    provider = _resolve(make_provider, cfg, "bitcoin")
    chain = trace_peel_chain(
        txid, provider.get_transaction, provider.get_spending_tx, max_hops=max_hops
    )
    return {
        "is_peel_chain": chain.is_peel_chain,
        "length": chain.length,
        "hops": [vars(h) for h in chain.hops],
        "cash_out": vars(chain.cash_out) if chain.cash_out else None,
    }


@router.get("/exposure")
def exposure(
    address: str = Query(..., min_length=1),
    chain: str = "bitcoin",
    hops: int = Query(2, ge=1, le=5),
    direction: str = Query("both", pattern="^(in|out|both)$"),
    cfg: config.Config = Depends(get_config),
    make_provider: ProviderFactory = Depends(get_provider_factory),
    label_lookup_for=Depends(get_label_lookup_factory),
) -> dict:
    lookup = label_lookup_for(cfg)
    if lookup is None:
        raise HTTPException(
            status_code=503,
            detail="exposure needs the label corpus — set CHAINHOUND_DATABASE_URL",
        )
    from chainhound.analysis.exposure import compute_exposure

    provider = _resolve(make_provider, cfg, chain)
    try:
        report = compute_exposure(
            provider,
            provider.chain,
            address,
            label_lookup=lookup,
            hops=hops,
            direction=direction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report.to_dict()


@router.get("/labels")
def labels(
    address: str = Query(..., min_length=1),
    chain: str = "bitcoin",
    cfg: config.Config = Depends(get_config),
) -> dict:
    if not cfg.database_url:
        raise HTTPException(
            status_code=503,
            detail="label lookup needs a database — set CHAINHOUND_DATABASE_URL",
        )
    from dataclasses import asdict

    from chainhound.labels import store

    found = store.lookup(cfg.database_url, chain, address)
    return {
        "chain": chain,
        "address": address,
        "labels": [asdict(lbl) for lbl in found],
    }


@router.post("/crosschain")
def crosschain(
    req: CrossChainRequest,
    cfg: config.Config = Depends(get_config),
    make_provider: ProviderFactory = Depends(get_provider_factory),
) -> list:
    from chainhound.analysis import crosschain as cc

    if req.mode == "api":
        from chainhound.bridges import ThorchainMidgard

        link = ThorchainMidgard().lookup(req.src_chain, req.src_txid)
        return [link.to_dict()] if link else []

    # inferred mode
    if not (req.dst_chain and req.dst_address and req.src_asset and req.src_amount):
        raise HTTPException(
            status_code=400,
            detail="inferred mode needs src_asset, src_amount, dst_chain, dst_address",
        )
    s_asset = req.src_asset.upper()
    s_dec = cc.ASSETS.get(s_asset, (None, 8))[1]
    src_ts = req.src_time
    if src_ts is None:
        stx = _resolve(make_provider, cfg, req.src_chain).get_transaction(req.src_txid)
        src_ts = stx.timestamp if stx else 0
    src = cc.Transfer(
        req.src_chain,
        req.src_txid,
        None,
        s_asset,
        int(round(req.src_amount * 10**s_dec)),
        s_dec,
        src_ts,
    )
    dst_addr = req.dst_address.lower()
    evm = req.dst_chain in ("ethereum", "eth", "evm")
    dp = _resolve(make_provider, cfg, req.dst_chain)
    candidates = []
    for tx in dp.get_address_transactions(dst_addr):
        for o in tx.outputs:
            if o.address == dst_addr:
                dec = cc.ASSETS.get((o.asset or "").upper(), (None, 18 if evm else 8))[
                    1
                ]
                candidates.append(
                    cc.Transfer(
                        req.dst_chain,
                        tx.txid,
                        o.address,
                        o.asset,
                        o.value,
                        dec,
                        tx.timestamp,
                    )
                )
    return [link.to_dict() for link in cc.infer_links(src, candidates)]
