# Architecture

ChainHound turns the manual, cross-source investigation workflow (visit an
explorer, then a bridge site, then a labels source, reconcile by hand) into a
pipeline: every source becomes a **connector** that writes into one **canonical
store**, and a **correlation engine** runs clustering, change analysis,
exposure, and cross-chain matching as code.

## Layers

```
 OSINT sources    chain data (BigQuery, explorers) · bridge/swap explorers ·
                  label sources (OFAC, Chainabuse, Etherscan) · price (CoinGecko)
        |
 Connectors       one adapter per source; normalize to canonical models
        |
 Canonical store  addresses · transactions · transfers · clusters · labels ·
                  cross-chain links · cases   (Postgres; see sql/schema.sql)
        |
 Correlation      clustering · change analysis · cross-chain matching ·
   engine         exposure · signatures · attribution merge   (runs automatically)
        |
 Query API        triage · trace · exposure · monitor   (later phase)
        |
 Graph UI         canvas · exposure rings · transfer table   (later phase)
```

The connector + canonical-store boundary is the key design move: nothing
downstream sees a source-specific shape, so the engine aggregates across sources
without per-source glue code.

## Canonical model

`chainhound/models.py` defines chain-agnostic types. The unification trick is the
**transfer-centric** model (the double-entry-book pattern from blockchain-etl):

- a Bitcoin transaction fans out to many `transfer` rows;
- an EVM value/token transfer is a single `transfer` row;
- both look identical to every heuristic downstream.

Amounts are integers in the smallest unit (sats/wei) to avoid float error.
Every probabilistic conclusion carries a **confidence band**
(`Near Certainty -> Low`), never a boolean.

## Components

- `connectors/` — `Provider` interface + `BlockstreamBTC` (live, no creds) and
  `BigQueryBTC` (bulk/historical). `get_provider(name)` is the factory.
- `heuristics/` — `clustering` (co-spend + union-find), `coinjoin` (exclusion),
  `change_analysis` (six heuristics + noisy-OR combiner), `peel_chain`.
- `analysis/` — `triage` (build-a-picture) and `trace` (follow-the-money graph).
- `db.py` + `sql/schema.sql` — optional Postgres persistence; the schema already
  models clusters, labels, cross-chain links, and the case workspace for later
  phases.
- `cli.py` — `triage`, `trace`, `peel`, `initdb`.

## Tech choices

Python + Postgres (or DuckDB) + a thin CLI now; FastAPI + Cytoscape.js/sigma.js
UI later. Deliberately **not** GraphSense's Spark/Cassandra stack — that is built
to index whole chains and is operational overkill for OSINT-scope work. Read
GraphSense for the clustering/TagPack reference, but stay lean.

## Data flow for a trace

1. CLI resolves a `Provider` from config.
2. `trace_from_tx` fetches a transaction, runs `analyze_change` to label the
   change output, and follows the change forward N hops, emitting a graph whose
   edges are tagged `change`/`payment` with a confidence band.
3. (Persistence phase) verdicts, clusters, and links are written to Postgres so
   they accumulate across sessions and feed exposure/pathfinding.

## Interface plan

The end-state interface (Phase 5) is an analyst-driven, incremental
**graph-building canvas** in the style of TRM Graph Visualizer / Breadcrumbs —
not a static dashboard. The analyst grows the investigation by interaction:

- search a seed address/tx -> **triage panel** (build-a-picture)
- **expand** node by node to plot transactions and counterparties
- change trail auto-highlighted with its confidence band; **color + notes** for
  common ownership (graph hygiene)
- **exposure ring** panel (counterparty/indirect) + **transfer table** with
  find-in-table
- **save/load per case** via the `investigation` / `graph_element` / `case_note`
  tables; working copy vs presentation copy; court export (raw on-chain only)

Backend is a FastAPI query layer exposing triage/trace/exposure endpoints; the
canvas (Cytoscape.js or sigma.js) calls them as the analyst clicks. Through
Phases 1–4 the interface is the CLI + JSON graph output; an optional thin
read-only viewer (Phase 1.5) can render that JSON sooner if eyes-on-graph helps.
