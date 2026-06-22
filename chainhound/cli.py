"""ChainHound command-line interface.

Examples:
    chainhound triage 3P91G6V8CurGLRtJgQmdNvkZ49s7GNMEcT
    chainhound trace  <txid> --hops 2
    chainhound peel   <txid> --max-hops 30
    chainhound initdb
    chainhound labels sync --source ofac,tagpacks
    chainhound labels sync --source chainabuse --address <addr> --chain ethereum
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config
from .connectors import get_provider
from .loaders import BULK_SOURCES, ON_DEMAND_SOURCES, get_loader
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


def cmd_labels_sync(args):
    """Fetch labels from one or more sources and upsert them into the corpus.

    Bulk sources (ofac, tagpacks) download + parse a whole dataset; on-demand
    sources (chainabuse) require --address and are fetched lazily through the
    rate-limited, cached fetcher. --dry-run prints the parsed labels without a
    database (handy offline / without psycopg)."""
    cfg = config.load()
    sources = (
        [s.strip() for s in args.source.split(",") if s.strip()]
        if args.source else list(BULK_SOURCES)
    )

    records = []
    counts = {}
    for src in sources:
        if src in ON_DEMAND_SOURCES:
            if not args.address:
                sys.exit(f"source {src!r} is on-demand; pass --address")
            loader = get_loader(
                src, database_url=None if args.dry_run else cfg.database_url
            )
            recs = loader.fetch_for_address(args.address, chain=args.chain)
        else:
            recs = get_loader(src).sync()
        counts[src] = len(recs)
        records.extend(recs)

    if args.dry_run:
        print(json.dumps(
            {"counts": counts, "records": [vars(r) for r in records]},
            indent=2, default=str,
        ))
        return

    if not cfg.database_url:
        sys.exit("set CHAINHOUND_DATABASE_URL to upsert labels (or use --dry-run)")
    from .db import upsert_labels
    written = upsert_labels(cfg.database_url, records)
    print(json.dumps({"counts": counts, "upserted": written}, indent=2, default=str))


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

    lb = sub.add_parser("labels", help="manage the attribution label corpus")
    lbsub = lb.add_subparsers(dest="labels_cmd", required=True)
    sy = lbsub.add_parser("sync", help="fetch + upsert labels from sources")
    sy.add_argument(
        "--source",
        help="comma-separated sources (default: bulk sources "
             f"{','.join(BULK_SOURCES)}); on-demand: {','.join(ON_DEMAND_SOURCES)}",
    )
    sy.add_argument("--address", help="address to query for on-demand sources")
    sy.add_argument("--chain", default="unknown",
                    help="chain for on-demand address lookups")
    sy.add_argument("--dry-run", action="store_true",
                    help="print parsed labels as JSON; no database needed")
    sy.set_defaults(func=cmd_labels_sync)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
