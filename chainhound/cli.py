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


def cmd_triage(args):
    print(json.dumps(triage_address(_provider(), args.address), indent=2, default=str))


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


def _label_source(name: str, cfg, url: str | None):
    if name == "ofac":
        from .labels.ofac import OFACSource, URL
        return OFACSource(url=url or cfg.ofac_url or URL)
    raise SystemExit(f"unknown label source: {name!r}")


def cmd_labels_sync(args):
    from .labels import store
    cfg = config.load()
    if not cfg.database_url:
        sys.exit("set CHAINHOUND_DATABASE_URL to sync labels")
    source = _label_source(args.source, cfg, args.url)
    text = None
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    n = store.sync(cfg.database_url, source, text=text)
    print(f"synced {n} {args.source} labels")


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

    db = sub.add_parser("initdb", help="create the Postgres schema")
    db.set_defaults(func=cmd_initdb)

    lb = sub.add_parser("labels", help="ingest and query attribution labels")
    lb_sub = lb.add_subparsers(dest="labels_cmd", required=True)

    ls = lb_sub.add_parser("sync", help="fetch a source and upsert its labels")
    ls.add_argument("--source", default="ofac", help="label source (default: ofac)")
    ls.add_argument("--file", help="parse a local document instead of fetching")
    ls.add_argument("--url", help="override the source URL")
    ls.set_defaults(func=cmd_labels_sync)

    ll = lb_sub.add_parser("lookup", help="show labels recorded for an address")
    ll.add_argument("address")
    ll.add_argument("--chain", default="bitcoin")
    ll.set_defaults(func=cmd_labels_lookup)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
