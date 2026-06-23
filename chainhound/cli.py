"""ChainHound command-line interface.

Examples:
    chainhound triage 3P91G6V8CurGLRtJgQmdNvkZ49s7GNMEcT
    chainhound trace  <txid> --hops 2
    chainhound peel   <txid> --max-hops 30
    chainhound initdb
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config
from .connectors import get_provider
from .analysis.triage import triage_address
from .analysis.trace import trace_from_tx
from .heuristics.peel_chain import trace_peel_chain


def _provider():
    cfg = config.load()
    return get_provider(cfg.provider, **cfg.provider_kwargs())


def _label_lookup(cfg):
    """Build a (chain, address) -> [name] hook from the label store, or None."""
    if not cfg.database_url:
        return None
    from .labels import store

    def lookup(chain: str, address: str) -> list[str]:
        return [lbl.name for lbl in store.lookup(cfg.database_url, chain, address)]

    return lookup


def cmd_triage(args):
    cfg = config.load()
    provider = get_provider(cfg.provider, **cfg.provider_kwargs())
    report = triage_address(provider, args.address, label_lookup=_label_lookup(cfg))
    print(json.dumps(report, indent=2, default=str))


def cmd_exposure(args):
    from .labels import store
    from .analysis.exposure import compute_exposure

    cfg = config.load()
    if not cfg.database_url:
        sys.exit("set CHAINHOUND_DATABASE_URL — exposure needs the label corpus")
    provider = get_provider(cfg.provider, **cfg.provider_kwargs())

    def lookup(chain, address):
        return store.lookup(cfg.database_url, chain, address)

    report = compute_exposure(
        provider, provider.chain, args.address,
        label_lookup=lookup, hops=args.hops, direction=args.direction,
    )
    print(json.dumps(report.to_dict(), indent=2, default=str))


def cmd_trace(args):
    graph = trace_from_tx(_provider(), args.txid, hops=args.hops)
    print(json.dumps(graph.to_dict(), indent=2, default=str))


def cmd_peel(args):
    p = _provider()
    chain = trace_peel_chain(
        args.txid, p.get_transaction, p.get_spending_tx, max_hops=args.max_hops
    )
    print(json.dumps({
        "is_peel_chain": chain.is_peel_chain,
        "length": chain.length,
        "hops": [vars(h) for h in chain.hops],
    }, indent=2, default=str))


def cmd_initdb(args):
    from .db import init_schema
    cfg = config.load()
    if not cfg.database_url:
        sys.exit("set CHAINHOUND_DATABASE_URL to initialise the schema")
    init_schema(cfg.database_url)
    print("schema initialised")


def cmd_labels_sync(args):
    from .labels import store, sources
    cfg = config.load()
    if not cfg.database_url:
        sys.exit("set CHAINHOUND_DATABASE_URL to sync labels")

    names = list(sources.ALL_BULK) if args.all else [args.source]
    text = None
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()

    total = 0
    for name in names:
        source = sources.bulk_source(
            name, cfg, url=args.url, path=args.path, manifest=args.manifest
        )
        n = store.sync(cfg.database_url, source, text=text)
        total += n
        print(f"synced {n} {name} labels")
    if len(names) > 1:
        print(f"total: {total} labels")


def cmd_labels_lookup(args):
    from .labels import store
    cfg = config.load()
    if not cfg.database_url:
        sys.exit("set CHAINHOUND_DATABASE_URL to look up labels")
    labels = store.lookup(cfg.database_url, args.chain, args.address)
    if not labels:
        print("no labels")
        return
    for lbl in labels:
        print(f"{lbl.name} ({lbl.category}, {lbl.source}, {lbl.confidence})")


def cmd_labels_check(args):
    """On-demand, cache-first lookup against a rate-limited API source."""
    from .labels import sources
    cfg = config.load()
    if not cfg.database_url:
        sys.exit("set CHAINHOUND_DATABASE_URL to cache on-demand lookups")
    source = sources.ondemand_source(args.source, cfg)
    labels = source.check(cfg.database_url, args.chain, args.address)
    if not labels:
        print("no labels")
        return
    for lbl in labels:
        print(f"{lbl.name} ({lbl.category}, {lbl.source}, {lbl.confidence})")


def cmd_labels_sources(args):
    from .labels import sources
    print("bulk (labels sync --source):    " + ", ".join(sources.BULK_SOURCES))
    print("on-demand (labels check --source): " + ", ".join(sources.ONDEMAND_SOURCES))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chainhound", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("triage", help="build an on-chain picture of an address")
    t.add_argument("address")
    t.set_defaults(func=cmd_triage)

    tr = sub.add_parser("trace", help="follow the money forward from a transaction")
    tr.add_argument("txid")
    tr.add_argument("--hops", type=int, default=2)
    tr.set_defaults(func=cmd_trace)

    pe = sub.add_parser("peel", help="detect/trace a peel chain from a transaction")
    pe.add_argument("txid")
    pe.add_argument("--max-hops", type=int, default=50)
    pe.set_defaults(func=cmd_peel)

    ex = sub.add_parser("exposure", help="labeled-entity exposure (direct + indirect)")
    ex.add_argument("address")
    ex.add_argument("--hops", type=int, default=2)
    ex.add_argument("--direction", choices=["in", "out", "both"], default="both")
    ex.set_defaults(func=cmd_exposure)

    db = sub.add_parser("initdb", help="create the Postgres schema")
    db.set_defaults(func=cmd_initdb)

    lb = sub.add_parser("labels", help="ingest and query attribution labels")
    lb_sub = lb.add_subparsers(dest="labels_cmd", required=True)

    ls = lb_sub.add_parser("sync", help="fetch a bulk source and upsert its labels")
    ls.add_argument("--source", default="ofac", help="bulk source (default: ofac)")
    ls.add_argument("--all", action="store_true", help="sync all default bulk sources")
    ls.add_argument("--file", help="parse a local document instead of fetching")
    ls.add_argument("--url", help="override the source URL (ofac)")
    ls.add_argument("--path", help="corpus path (tagpack tarball or directory)")
    ls.add_argument("--manifest", help="manifest path (repo source)")
    ls.set_defaults(func=cmd_labels_sync)

    ll = lb_sub.add_parser("lookup", help="show labels recorded for an address")
    ll.add_argument("address")
    ll.add_argument("--chain", default="bitcoin")
    ll.set_defaults(func=cmd_labels_lookup)

    lc = lb_sub.add_parser("check", help="on-demand, cached lookup via a rate-limited API")
    lc.add_argument("address")
    lc.add_argument("--source", default="chainabuse", help="on-demand source")
    lc.add_argument("--chain", default="ethereum")
    lc.set_defaults(func=cmd_labels_check)

    lsr = lb_sub.add_parser("sources", help="list available label sources")
    lsr.set_defaults(func=cmd_labels_sources)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
